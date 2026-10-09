import argparse
import re
import sys

HEADER_RE = re.compile(
    r'^(?:Initialization|Assumption(?:\s+Achieve)?|'
    r'Goal(?:\s+(?:Maintain|Achieve))?)\s+\[[^]]+\]$'
)
VARIABLE_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")
OPERATORS = {'AND', 'OR', 'NOT', 'F', 'G', 'True', 'False', 'next'}

def flatten_predicate(match):
    """Convert Predicate(a, b) into Predicate_a_b for Boolean Slugs atoms."""
    predicate = match.group(1)
    args = re.sub(r'\s*,\s*', '_', match.group(2).strip())
    return f"{predicate}_{args}"

def clean_formula(formula):
    """Flatten atoms and normalize the infix operators."""
    cleaned = re.sub(r'([A-Za-z0-9_]+)\(([^)]+)\)', flatten_predicate, formula)
    return cleaned.replace('&&', '&').replace('||', '|').strip()

def extract_variables(formula_str):
    """Return Boolean atoms, ignoring operators and next-state primes."""
    return {
        word for word in VARIABLE_RE.findall(formula_str)
        if word not in OPERATORS
    }

def prime_formula(formula_str, variables):
    """Wraps variables in next(...) for synthesized liveness transitions."""
    primed = formula_str
    for var in sorted(variables, key=len, reverse=True):
        primed = re.sub(fr'\b{var}\b', f"next({var})", primed)
    return primed

def response_monitor(monitor_name, lhs, rhs):
    """Encode lhs -> F rhs with a pending-obligation GR(1) monitor."""
    rhs_primed = prime_formula(rhs, extract_variables(rhs))
    pending = f"({monitor_name} | (({lhs}) & !({rhs}))) & !({rhs_primed})"
    return f"(!next({monitor_name}) | ({pending})) & (next({monitor_name}) | !({pending}))"

def parse_blocks(content):
    """Read model entries without inferring FormalDef from arbitrary text."""
    parsed = []
    current = None
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue
        if HEADER_RE.match(line):
            if current is not None:
                parsed.append(current)
            current = {'header': line, 'formal_def': ''}
        elif current is not None and line.startswith('FormalDef:'):
            current['formal_def'] = line.removeprefix('FormalDef:').strip()
    if current is not None:
        parsed.append(current)
    return parsed

def split_response(formula):
    """Return (antecedent, consequent) for the restricted A -> F B pattern."""
    match = re.fullmatch(r'(.+?)\s*->\s*F\s+(.+)', formula)
    return match.groups() if match else None

def make_monitor_name(header, owner):
    goal_name = re.search(r'\[([^]]+)\]', header)
    if goal_name is None:
        raise ValueError(f"Cannot name monitor for malformed header: {header}")
    safe_name = re.sub(r'[^A-Za-z0-9_]+', '_', goal_name.group(1)).strip('_')
    return f"{owner}_monitor_{safe_name}"

def add_owned_variables(formula, owner, owners):
    for variable in extract_variables(clean_formula(formula)):
        previous_owner = owners.setdefault(variable, owner)
        if previous_owner != owner:
            raise ValueError(
                f"Variable {variable!r} is assigned to both "
                f"{previous_owner} and {owner}"
            )

def unwrap_G(formula_str):
    s = formula_str.strip()
    if s.startswith('G ') or s.startswith('G('):
        s = s[1:].strip()
        if s.startswith('(') and s.endswith(')'):
            lvl = 0
            for i, c in enumerate(s):
                if c == '(':
                    lvl += 1
                elif c == ')':
                    lvl -= 1
                if lvl == 0 and i < len(s) - 1:
                    break
            else:
                s = s[1:-1].strip()
        return s
    return s

def replace_prime_with_next(formula):
    # If the user directly wrote var', replace it with next(var)
    # We'll use a regex to find var' and turn it into next(var)
    def repl(m):
        return f"next({m.group(1)})"
    return re.sub(r"\b([A-Za-z_][A-Za-z0-9_]*)\'", repl, formula)

def generate_spectra(input_file='LabSamples.gm', output_file='LabSamples.spectra'):
    with open(input_file, 'r') as f:
        content = f.read()

    blocks = parse_blocks(content)

    inputs = set()
    outputs = set()
    env_trans = []
    env_liveness = []
    sys_trans = []
    sys_liveness = []
    env_init = []
    sys_init = []
    
    owners = {}
    for block in blocks:
        header = block['header']
        formal_def = block['formal_def']
        if formal_def and header.startswith('Initialization'):
            owner = 'environment' if 'Environment' in header else 'system'
            add_owned_variables(formal_def, owner, owners)

    if not owners:
        raise ValueError('Goal model has no initialization ownership declarations')

    inputs.update(var for var, owner in owners.items() if owner == 'environment')
    outputs.update(var for var, owner in owners.items() if owner == 'system')

    for block in blocks:
        header = block['header']
        formal_def = block['formal_def']
        if not formal_def:
            continue

        flat_formula = clean_formula(formal_def)
        variables = extract_variables(flat_formula)
        unknown = variables - owners.keys()
        if unknown:
            raise ValueError(
                f"{header} refers to variables without initialization ownership: "
                + ', '.join(sorted(unknown))
            )

        flat_formula = replace_prime_with_next(flat_formula)

        if header.startswith("Initialization"):
            init_conditions = [c.strip() for c in flat_formula.split('&')]
            if "Environment" in header:
                env_init.extend(init_conditions)
            else:
                sys_init.extend(init_conditions)
            continue

        unwrapped_formula = unwrap_G(flat_formula)

        response = split_response(unwrapped_formula)
        if response is None and re.match(r'^F\s+', unwrapped_formula):
            clean_live = re.sub(r'^F\s+', '', unwrapped_formula).strip()
            if header.startswith("Assumption"):
                env_liveness.append(clean_live)
            else:
                sys_liveness.append(clean_live)

        elif response is not None:
            lhs = response[0].strip()
            rhs = response[1].strip()
            
            if header.startswith("Assumption"):
                monitor_name = make_monitor_name(header, "env")
                inputs.add(monitor_name)
                env_init.append(f"!{monitor_name}")
                env_trans.append(response_monitor(monitor_name, lhs, rhs))
                env_liveness.append(f"!{monitor_name}")
            else:
                monitor_name = make_monitor_name(header, "sys")
                outputs.add(monitor_name)
                sys_init.append(f"!{monitor_name}")
                sys_trans.append(response_monitor(monitor_name, lhs, rhs))
                sys_liveness.append(f"!{monitor_name}")
                
        elif header.startswith('Goal') or header.startswith('Assumption'):
            if "->" in unwrapped_formula:
                parts = unwrapped_formula.split("->", 1)
                lhs = parts[0].strip()
                rhs = parts[1].strip()
                formula = f"!({lhs}) | ({rhs})"
            else:
                formula = unwrapped_formula
                
            if header.startswith("Assumption"):
                env_trans.append(formula)
            else:
                sys_trans.append(formula)
        else:
            raise ValueError(f"Unsupported goal model leaf: {header}")

    # Generate Spectra syntax
    module_name = 'LabSamples'
    
    with open(output_file, 'w') as f:
        f.write(f"module {module_name}\n\n")
        
        for var in sorted(inputs - outputs):
            f.write(f"env boolean {var};\n")
        
        for var in sorted(outputs):
            f.write(f"sys boolean {var};\n")
            
        f.write("\n")
        
        for i, eq in enumerate(env_init):
            f.write(f"asm env_init_{i}:\n  {eq};\n")
            
        for i, eq in enumerate(sys_init):
            f.write(f"gar sys_init_{i}:\n  {eq};\n")
            
        for i, eq in enumerate(env_trans):
            f.write(f"asm env_trans_{i}:\n  G ({eq});\n")
            
        for i, eq in enumerate(env_liveness):
            f.write(f"asm env_live_{i}:\n  GF ({eq});\n")
            
        for i, eq in enumerate(sys_trans):
            f.write(f"gar sys_trans_{i}:\n  G ({eq});\n")
            
        for i, eq in enumerate(sys_liveness):
            f.write(f"gar sys_live_{i}:\n  GF ({eq});\n")

    print(f"Goal Model successfully compiled to {output_file}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Compile a goal model into Spectra format.')
    parser.add_argument('input_file', nargs='?', default='LabSamples.gm')
    parser.add_argument('output_file', nargs='?', default='LabSamples.spectra')
    arguments = parser.parse_args()
    generate_spectra(arguments.input_file, arguments.output_file)

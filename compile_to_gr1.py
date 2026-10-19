import argparse
import re

from mission_config import mission_paths


HEADER_RE = re.compile(
    r'^(?:Initialization|Domain(?:\s+)?Property|Domain Assumption|'
    r'Assumption(?:\s+Achieve)?|'
    r'Goal(?:\s+(?:Maintain|Achieve))?)\s+\[[^]]+\]$'
)
VARIABLE_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")
OPERATORS = {'AND', 'OR', 'NOT', 'F', 'G', 'True', 'False'}


def flatten_predicate(match):
    """Convert Predicate(a, b) into Predicate_a_b for Boolean Slugs atoms."""
    predicate = match.group(1)
    args = re.sub(r'\s*,\s*', '_', match.group(2).strip())
    return f"{predicate}_{args}"


def clean_formula(formula):
    """Flatten atoms and normalize the infix operators accepted by Slugs."""
    cleaned = re.sub(r'([A-Za-z0-9_]+)\(([^)]+)\)', flatten_predicate, formula)
    # We no longer indiscriminately strip [FG] here so we can safely parse G(...) wrappers later
    return cleaned.replace('&&', '&').replace('||', '|').strip()


def extract_variables(formula_str):
    """Return Boolean atoms, ignoring operators and next-state primes."""
    return {
        word for word in VARIABLE_RE.findall(formula_str)
        if word not in OPERATORS
    }

def prime_formula(formula_str, variables):
    """Adds next-state operator (') to variables for synthesized liveness transitions."""
    primed = formula_str
    for var in sorted(variables, key=len, reverse=True):
        primed = re.sub(fr'\b{var}\b', f"{var}'", primed)
    return primed

def response_monitor(monitor_name, lhs, rhs):
    """Encode G(lhs -> F rhs) with the requested pending-state monitor."""
    pending = f"({monitor_name} | (({lhs}) & !({rhs}))) & !({rhs})"
    return [
        f"(!{monitor_name}' | ({pending}))",
        f"(!({pending}) | {monitor_name}')",
    ]


def maintain_monitor(monitor_name, lhs, rhs):
    """Encode G(lhs -> G rhs) with an input latch monitor."""
    latched = f"({monitor_name} | ({lhs}))"
    return [
        f"(!{monitor_name}' | {latched})",
        f"(!{latched} | {monitor_name}')",
        f"!({latched}) | ({rhs})",
    ]

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


def split_maintain(formula):
    """Return (antecedent, consequent) for G(A -> G B)."""
    match = re.fullmatch(r'(.+?)\s*->\s*G\s+(.+)', formula)
    return match.groups() if match else None


def goal_consequent_owner(consequent, owners):
    """Return the player owning the consequent of a temporal goal."""
    variables = extract_variables(consequent)
    if not variables:
        raise ValueError("Temporal goal consequent must contain an owned variable")
    consequent_owners = {owners.get(variable) for variable in variables}
    if None in consequent_owners:
        unknown = sorted(variable for variable in variables if variable not in owners)
        raise ValueError(
            "Temporal goal consequent refers to variables without initialization "
            "ownership: " + ", ".join(unknown)
        )
    return "system" if "system" in consequent_owners else "environment"


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
    """Safely removes an outer 'G' operator and its matching parentheses if they wrap the entire formula."""
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
                
                # If parentheses level drops to 0 before the end, 
                # they do not wrap the whole formula (e.g., "(A) & (B)")
                if lvl == 0 and i < len(s) - 1:
                    break
            else:
                # Loop completed without breaking, meaning the outer parentheses match perfectly
                s = s[1:-1].strip()
        return s
    return s

def generate_slugs(input_file=None, output_file=None, mission='LabSamples'):
    paths = mission_paths(mission)
    input_file = input_file or paths['goal_model']
    output_file = output_file or paths['structured_slugs']
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

        # Initialization leaves define the initial valuation for one player.
        if header.startswith("Initialization"):
            init_conditions = [c.strip() for c in flat_formula.split('&')]
            if "Environment" in header:
                env_init.extend(init_conditions)
            else:
                sys_init.extend(init_conditions)
            continue

        # Safely extract formula from G(...) wrappers
        unwrapped_formula = unwrap_G(flat_formula)

        is_domain_property = header.startswith(("DomainProperty", "Domain Property"))
        is_assumption = header.startswith("Assumption") or header.startswith(
            "Domain Assumption"
        )
        is_goal_maintain = header.startswith("Goal Maintain")
        is_goal_achieve = header.startswith("Goal Achieve")

        # Domain properties are immutable physical facts. They are compiled as
        # environment constraints, but are kept distinct for review tooling.
        if is_domain_property:
            env_trans.append(unwrap_G(flat_formula))
            continue

        # 2. Pure Liveness Assumption or System Guarantee
        response = split_response(unwrapped_formula)
        if response is None and re.match(r'^F\s+', unwrapped_formula):
            clean_live = re.sub(r'^F\s+', '', unwrapped_formula).strip()
            if is_assumption:
                env_liveness.append(clean_live)
            else:
                sys_liveness.append(clean_live)

        # 3. Response Pattern (Liveness: G(A -> F B))
        elif response is not None:
            lhs = response[0].strip()
            rhs = response[1].strip()
            owner = "environment" if is_assumption else goal_consequent_owner(rhs, owners)
            monitor_name = make_monitor_name(header, "pending")
            inputs.add(monitor_name)
            env_init.append(f"!{monitor_name}")
            transitions = response_monitor(monitor_name, lhs, rhs)
            env_trans.extend(transitions)
            (env_liveness if owner == "environment" else sys_liveness).append(
                f"!{monitor_name}"
            )

        # 4. Latch Pattern (Safety: G(A -> G B))
        elif is_goal_maintain and (maintain := split_maintain(unwrapped_formula)):
            lhs, rhs = (part.strip() for part in maintain)
            owner = goal_consequent_owner(rhs, owners)
            monitor_name = make_monitor_name(header, "latch")
            inputs.add(monitor_name)
            env_init.append(f"!{monitor_name}")
            transitions = maintain_monitor(monitor_name, lhs, rhs)
            env_trans.extend(transitions[:2])
            (env_trans if owner == "environment" else sys_trans).append(transitions[2])

        # 4. Safety, Invariants, Mutexes, and Frame Conditions
        elif header.startswith('Goal') or is_assumption:
            if "->" in unwrapped_formula:
                parts = unwrapped_formula.split("->", 1)
                lhs = parts[0].strip()
                rhs = parts[1].strip()
                formula = f"({lhs}) -> ({rhs})"
            else:
                formula = unwrapped_formula
                
            if is_assumption:
                env_trans.append(formula)
            else:
                sys_trans.append(formula)
        else:
            raise ValueError(f"Unsupported goal model leaf: {header}")

    with open(output_file, 'w') as f:
        f.write("[INPUT]\n")
        for var in sorted(inputs - outputs):
            f.write(f"{var}\n")
        
        f.write("\n[OUTPUT]\n")
        for var in sorted(outputs):
            f.write(f"{var}\n")
            
        f.write("\n[ENV_INIT]\n")
        for eq in env_init:
            f.write(f"{eq}\n")
            
        f.write("\n[SYS_INIT]\n")
        for eq in sys_init:
            f.write(f"{eq}\n")
            
        f.write("\n[ENV_TRANS]\n")
        for eq in env_trans:
            f.write(f"{eq}\n")
            
        f.write("\n[ENV_LIVENESS]\n")
        for eq in env_liveness:
            f.write(f"{eq}\n")
            
        f.write("\n[SYS_TRANS]\n")
        for eq in sys_trans:
            f.write(f"{eq}\n")
            
        f.write("\n[SYS_LIVENESS]\n")
        for eq in sys_liveness:
            f.write(f"{eq}\n")

    print(f"Goal Model successfully compiled to {output_file}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Compile a goal model into structured Slugs input.')
    parser.add_argument('input_file', nargs='?')
    parser.add_argument('output_file', nargs='?')
    parser.add_argument('--mission', default='LabSamples')
    arguments = parser.parse_args()
    generate_slugs(arguments.input_file, arguments.output_file, arguments.mission)

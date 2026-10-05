import re

def flatten_predicate(match):
    """Converts Predicate(a, b) into Predicate_a_b to create valid Boolean variables."""
    predicate = match.group(1)
    args = match.group(2).replace(', ', '_').replace(',', '_')
    return f"{predicate}_{args}"

def clean_formula(formula):
    """Flattens predicates and normalizes logical operators for Slugs."""
    cleaned = re.sub(r'([A-Za-z0-9_]+)\(([^)]+)\)', flatten_predicate, formula)
    cleaned = cleaned.replace('F ', '').replace('G ', '')
    cleaned = cleaned.replace('&&', '&').replace('||', '|')
    return cleaned

def extract_variables(formula_str):
    """Extracts unique boolean variables. Naturally ignores next-state primes (')."""
    words = set(re.findall(r'[A-Za-z_][A-Za-z0-9_]*', formula_str))
    operators = {'AND', 'OR', 'NOT', 'F', 'G', 'True', 'False'}
    return words - operators

def prime_formula(formula_str, variables):
    """Adds next-state operator (') to variables for synthesized liveness transitions."""
    primed = formula_str
    for var in sorted(variables, key=len, reverse=True):
        primed = re.sub(fr'\b{var}\b', f"{var}'", primed)
    return primed

def response_monitor(monitor_name, lhs, rhs):
    """Encode lhs -> F rhs with a pending-obligation GR(1) monitor."""
    rhs_primed = prime_formula(rhs, extract_variables(rhs))
    pending = f"({monitor_name} | (({lhs}) & !({rhs}))) & !({rhs_primed})"
    return f"(!{monitor_name}' | ({pending})) & ({monitor_name}' | !({pending}))"

def parse_blocks(content):
    blocks = re.split(r'\n\s*\n', content.strip())
    parsed = []
    for block in blocks:
        lines = [
            line.strip()
            for line in block.splitlines()
            if line.strip() and not line.strip().startswith('#')
        ]
        if not lines:
            continue

        formal_def = next(
            (
                line.replace('FormalDef:', '').strip()
                for line in reversed(lines)
                if any(token in line for token in ('->', '|', '&', "'", 'F ', 'G '))
            ),
            '',
        )
        parsed.append((lines[0], formal_def))
    return parsed

def generate_slugs(input_file, output_file):
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
    
    monitor_counter = 0

    environment_vars = set()
    system_vars = set()
    for header, formal_def in blocks:
        if not header.startswith('Initialization') or not formal_def:
            continue
        variables = extract_variables(clean_formula(formal_def))
        if 'Environment' in header:
            environment_vars.update(variables)
        else:
            system_vars.update(variables)

    env_keywords = {
        'Collected', 'Authorized', 'SpecificRobot', 'At_d_loc', 
        'ValidLocation', 'InTransit', 'PathClear', 'CollectedBy'
    }

    for header, formal_def in blocks:
        if not formal_def:
            continue

        flat_formula = clean_formula(formal_def)
        variables = extract_variables(flat_formula)

        for var in variables:
            if var in system_vars:
                outputs.add(var)
            elif var in environment_vars:
                inputs.add(var)
            elif var in env_keywords or any(var.startswith(k + '_') for k in env_keywords):
                inputs.add(var)
            else:
                outputs.add(var)

        # 1. Parse Initial States (NEW)
        if header.startswith("Initialization"):
            init_conditions = [c.strip() for c in flat_formula.split('&')]
            if "Environment" in header:
                env_init.extend(init_conditions)
            else:
                sys_init.extend(init_conditions)
            continue

        # 2. Pure Liveness Assumption or System Guarantee
        if "F " in formal_def and "->" not in formal_def:
            pure_live = formal_def.replace('F ', '').strip()
            clean_live = clean_formula(pure_live)
            if header.startswith("Assumption"):
                env_liveness.append(clean_live)
            else:
                sys_liveness.append(clean_live)

        # 3. Response Pattern (Liveness: A -> F B)
        elif "->" in flat_formula and "F " in formal_def:
            parts = flat_formula.split("->")
            lhs = parts[0].strip()
            rhs = parts[1].strip()
            
            if header.startswith("Assumption"):
                monitor_name = f"env_monitor_{monitor_counter}"
                inputs.add(monitor_name)
                env_init.append(f"!{monitor_name}")
                env_trans.append(response_monitor(monitor_name, lhs, rhs))
                env_liveness.append(f"!{monitor_name}")
            else:
                monitor_name = f"sys_monitor_{monitor_counter}"
                outputs.add(monitor_name)
                sys_init.append(f"!{monitor_name}")
                sys_trans.append(response_monitor(monitor_name, lhs, rhs))
                sys_liveness.append(f"!{monitor_name}")
                
            monitor_counter += 1
            
        # 4. Safety, Invariants, Mutexes, and Frame Conditions
        else:
            if "->" in flat_formula:
                parts = flat_formula.split("->")
                lhs = parts[0].strip()
                rhs = parts[1].replace('G ', '').strip()
                formula = f"!({lhs}) | ({rhs})"
            else:
                formula = flat_formula
                
            if header.startswith("Assumption"):
                env_trans.append(formula)
            else:
                sys_trans.append(formula)

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
    generate_slugs('LabSamples.gm', 'LabSamples.structuredslugs')

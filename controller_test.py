import json
import os
from pathlib import Path


MONITOR_PREFIXES = ("env_monitor_", "sys_monitor_")


def parse_sections(slugs_file):
    sections = {}
    current = None
    for raw_line in Path(slugs_file).read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line
            sections[current] = []
        elif current is not None:
            sections[current].append(line)
    return sections


def load_controller(json_file="controller.json", slugs_file="LabSamples.slugsin"):
    with open(json_file, encoding="utf-8") as stream:
        controller = json.load(stream)

    sections = parse_sections(slugs_file)
    input_variables = set(sections.get("[INPUT]", []))
    output_variables = set(sections.get("[OUTPUT]", []))
    variables = controller.get("variables", [])
    if set(variables) != input_variables | output_variables:
        raise ValueError("controller.json variables do not match LabSamples.slugsin")
    if input_variables & output_variables:
        raise ValueError("Slugs input/output variables overlap")

    return controller, input_variables, output_variables


def split_state(variables, state, input_variables):
    values = dict(zip(variables, state))
    if len(values) != len(variables):
        raise ValueError("Controller state length does not match its variable list")
    inputs = {name: values[name] for name in input_variables}
    outputs = {name: values[name] for name in values if name not in input_variables}
    return inputs, outputs


def physical_inputs(input_variables):
    return {
        name for name in input_variables
        if not name.startswith("env_monitor_")
    }


def environment_step(step, current_inputs, current_outputs, params):
    next_inputs = dict(current_inputs)
    events = []

    request_step = params.get("request_step", 1)
    authorization_step = params.get("authorization_step", 1)
    pickup_step = params.get("human_pickup_step")

    next_inputs["request"] = int(step + 1 >= request_step)
    next_inputs["auth_present"] = int(step + 1 >= authorization_step)
    next_inputs["human_pickup"] = int(pickup_step is not None and step + 1 == pickup_step)

    if current_outputs.get("goto_lab", 0):
        next_inputs["at_floor"] = 0
        next_inputs["at_lab"] = 1
        events.append("arrived at laboratory")
    elif current_outputs.get("goto_floor", 0):
        next_inputs["at_floor"] = 1
        next_inputs["at_lab"] = 0
        events.append("returned to patient floor")

    if current_outputs.get("scan", 0) and current_inputs.get("auth_present", 0):
        next_inputs["barcode_ok"] = 1
        events.append("barcode accepted")
    elif current_outputs.get("load_machine", 0) or current_inputs.get("human_pickup", 0):
        next_inputs["barcode_ok"] = 0
        events.append("sample consumed")
    else:
        next_inputs["barcode_ok"] = current_inputs["barcode_ok"]

    if next_inputs["at_floor"] and next_inputs["at_lab"]:
        raise ValueError("Environment violates location mutual exclusion")
    if next_inputs["human_pickup"] and not params.get("allow_human_pickup", False):
        raise ValueError("Unexpected human pickup in this mission")

    if (
        current_inputs["at_floor"]
        and not current_outputs.get("goto_lab", 0)
        and not next_inputs["at_floor"]
    ):
        raise ValueError("Environment violates floor inertia")
    if (
        current_inputs["at_lab"]
        and not current_outputs.get("goto_floor", 0)
        and not next_inputs["at_lab"]
    ):
        raise ValueError("Environment violates laboratory inertia")
    if (
        not current_inputs["barcode_ok"]
        and not current_outputs.get("scan", 0)
        and next_inputs["barcode_ok"]
    ):
        raise ValueError("Environment violates barcode reader causality")
    if (
        current_inputs["barcode_ok"]
        and not current_outputs.get("load_machine", 0)
        and not current_inputs.get("human_pickup", 0)
        and not next_inputs["barcode_ok"]
    ):
        raise ValueError("Environment violates barcode persistence")
    if (
        (current_outputs.get("load_machine", 0) or current_inputs.get("human_pickup", 0))
        and next_inputs["barcode_ok"]
    ):
        raise ValueError("Environment violates sample consumption")

    return next_inputs, events


def validate_system_state(inputs, outputs):
    if outputs.get("goto_floor", 0) and outputs.get("goto_lab", 0):
        raise ValueError("System violates exclusive navigation")
    if outputs.get("scan", 0) and not (
        inputs["at_floor"]
        and inputs["auth_present"]
        and not inputs["barcode_ok"]
        and not inputs["human_pickup"]
    ):
        raise ValueError("System scans outside its precondition")
    if outputs.get("goto_lab", 0) and not (
        inputs["barcode_ok"] or not inputs["at_floor"]
    ):
        raise ValueError("System navigates to the lab without a valid sample")
    if outputs.get("load_machine", 0) and not (
        inputs["at_lab"]
        and inputs["barcode_ok"]
        and not inputs["human_pickup"]
    ):
        raise ValueError("System loads outside its precondition")
    if (
        inputs["at_lab"]
        and inputs["barcode_ok"]
        and not inputs["human_pickup"]
        and not outputs.get("load_machine", 0)
    ):
        raise ValueError("System failed to load a ready sample")
    if (
        outputs.get("scan", 0)
        or outputs.get("goto_floor", 0)
        or outputs.get("goto_lab", 0)
        or outputs.get("load_machine", 0)
    ) and not outputs.get("log_event", 0):
        raise ValueError("System action was not logged")


def choose_successor(node, nodes, variables, input_variables, expected_inputs):
    candidates = []
    physical = physical_inputs(input_variables)
    for successor_id in node.get("trans", []):
        successor_id = str(successor_id)
        successor = nodes.get(successor_id)
        if successor is None:
            continue
        successor_inputs, successor_outputs = split_state(
            variables, successor["state"], input_variables
        )
        if all(successor_inputs[name] == expected_inputs[name] for name in physical):
            candidates.append((successor_id, successor_outputs))

    if not candidates:
        raise ValueError("No controller transition matches the environment valuation")

    # The explicit strategy may offer monitor-only alternatives. Pick the
    # controller action that advances the compact mission, without inventing
    # any output values outside a strategy edge.
    priority = ("load_machine", "scan", "goto_lab", "goto_floor", "log_event")
    return max(
        candidates,
        key=lambda candidate: tuple(candidate[1].get(name, 0) for name in priority),
    )[0]


def mission_complete(trace):
    consumed = any(
        item["outputs"].get("load_machine", 0) or item["inputs"].get("human_pickup", 0)
        for item in trace
    )
    returned = bool(trace) and trace[-1]["inputs"].get("at_floor", 0)
    return consumed and returned


def run_single_mission(mission_config, controller, input_variables, output_variables, max_steps=30):
    variables = controller["variables"]
    nodes = controller["nodes"]
    current_id = "0"
    trace = []
    events = []

    for step in range(max_steps):
        node = nodes.get(current_id)
        if node is None:
            raise ValueError(f"Controller node {current_id} does not exist")
        current_inputs, current_outputs = split_state(
            variables, node["state"], input_variables
        )
        trace.append({
            "step": step,
            "node": current_id,
            "inputs": current_inputs,
            "outputs": current_outputs,
            "is_goal": mission_complete(trace),
        })

        if mission_complete(trace):
            return {"status": "SUCCESS", "reason": "Mission completed", "trace": trace, "events": events}

        expected_inputs, step_events = environment_step(
            step, current_inputs, current_outputs, mission_config
        )
        events.extend(f"[S{step}] {event}" for event in step_events)
        successor_id = choose_successor(
            node, nodes, variables, input_variables, expected_inputs
        )
        next_inputs, next_outputs = split_state(
            variables, nodes[successor_id]["state"], input_variables
        )
        validate_system_state(current_inputs, current_outputs)
        validate_system_state(next_inputs, next_outputs)
        current_id = successor_id

    return {"status": "FAILED", "reason": "Mission did not complete before timeout", "trace": trace, "events": events}


def export_trace_to_dot(trace, dot_filename):
    with open(dot_filename, "w", encoding="utf-8") as stream:
        stream.write("digraph Trace {\n  rankdir=LR;\n")
        for item in trace:
            active_inputs = [name for name, value in item["inputs"].items() if value]
            active_outputs = [name for name, value in item["outputs"].items() if value]
            label = f"Step {item['step']} (Node {item['node']})\\nIN: {', '.join(active_inputs)}\\nOUT: {', '.join(active_outputs)}"
            stream.write(f'  step_{item["step"]} [label="{label}"];\n')
        for previous, current in zip(trace, trace[1:]):
            stream.write(f'  step_{previous["step"]} -> step_{current["step"]};\n')
        stream.write("}\n")


def print_trace(trace):
    print("\nMission trace")
    print("Step | Node | Inputs                         | Outputs                     | Goal")
    print("-----+------+-------------------------------+----------------------------+-----")
    for item in trace:
        active_inputs = ", ".join(
            name for name, value in item["inputs"].items() if value
        ) or "-"
        active_outputs = ", ".join(
            name for name, value in item["outputs"].items() if value
        ) or "-"
        print(f"{item['step']:>4} | {item['node']:>4} | IN: {active_inputs} | OUT: {active_outputs} | {'yes' if item['is_goal'] else 'no'}")


def run_single_example_mission(json_file="controller.json", slugs_file="LabSamples.slugsin"):
    controller, input_variables, output_variables = load_controller(json_file, slugs_file)
    mission = {
        "request_step": 1,
        "authorization_step": 1,
        "allow_human_pickup": False,
    }
    result = run_single_mission(
        mission, controller, input_variables, output_variables
    )
    export_trace_to_dot(result["trace"], "sim_example/EXAMPLE_MISSION.dot")
    print_trace(result["trace"])
    print(f"\nFinal status: {result['status']} - {result['reason']}")
    print(result["status"], result["reason"])
    for event in result["events"]:
        print(event)
    return result


if __name__ == "__main__":
    os.makedirs("sim_example", exist_ok=True)
    run_single_example_mission()

import json
import os
import random
from html import escape
from pathlib import Path

from mission_config import load_simulation_config, mission_paths


MONITOR_PREFIXES = ("env_monitor_", "sys_monitor_")

LAB_SAMPLE_MISSION = {
    "request_step": 1,
    "authorization_step": 1,
    "allow_human_pickup": False,
    "barcode_validation_delay": 1,
    "lab_arrival_delay": 2,
    "floor_arrival_delay": 2,
}

SCENARIO_TRIALS = 50
DASHBOARD_SEED = 20261007
DASHBOARD_FILE = "sim_results/controller_dashboard.html"
SCENARIO_DESCRIPTIONS = {
    "NoAdversity": "Normal simulation flow with no changes.",
    "ScanFault": "Scanner failure; barcode_ok is never activated.",
    "AbandonedRequest": "No authorized personnel; auth_present is never activated.",
    "LabPickup": "A human always intervenes in the laboratory.",
    "RandomStress": "Random scanner failure or human intervention with delayed responses.",
}


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


def load_controller(json_file=None, slugs_file=None, mission="LabSamples"):
    paths = mission_paths(mission)
    json_file = json_file or paths["controller"]
    slugs_file = slugs_file or paths["slugs_input"]
    with open(json_file, encoding="utf-8") as stream:
        controller = json.load(stream)

    sections = parse_sections(slugs_file)
    input_variables = set(sections.get("[INPUT]", []))
    output_variables = set(sections.get("[OUTPUT]", []))
    variables = controller.get("variables", [])
    if set(variables) != input_variables | output_variables:
        raise ValueError("Controller variables do not match the selected mission's SLUGS input")
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

    barcode_delay = max(1, params.get("barcode_validation_delay", 1))
    lab_delay = max(1, params.get("lab_arrival_delay", 1))
    floor_delay = max(1, params.get("floor_arrival_delay", 1))

    request_step = params.get("request_step", 1)
    authorization_step = params.get("authorization_step", 1)
    pickup_step = params.get("human_pickup_step")

    next_inputs["request"] = int(step + 1 >= request_step)
    next_inputs["auth_present"] = int(
        not params.get("authorization_unavailable", False)
        and step + 1 >= authorization_step
    )
    next_inputs["human_pickup"] = int(
        (pickup_step is not None and step + 1 == pickup_step)
        or (
            params.get("human_pickup_mode") == "always"
            and current_inputs["at_lab"]
        )
    )

    if (
        current_outputs.get("goto_lab", 0)
        and not current_inputs["at_lab"]
        and "lab_command_step" not in params
    ):
        params["lab_command_step"] = step
    if (
        current_outputs.get("goto_floor", 0)
        and not current_inputs["at_floor"]
        and "floor_command_step" not in params
    ):
        params["floor_command_step"] = step

    lab_command_step = params.get("lab_command_step")
    if (
        current_outputs.get("goto_lab", 0)
        and lab_command_step is not None
        and step - lab_command_step + 1 >= lab_delay
    ):
        next_inputs["at_floor"] = 0
        next_inputs["at_lab"] = 1
        params.pop("lab_command_step", None)
        events.append("arrived at laboratory")
    elif (
        current_outputs.get("goto_floor", 0)
        and params.get("floor_command_step") is not None
        and step - params["floor_command_step"] + 1 >= floor_delay
    ):
        next_inputs["at_floor"] = 1
        next_inputs["at_lab"] = 0
        params.pop("floor_command_step", None)
        events.append("returned to patient floor")

    if (
        current_outputs.get("scan", 0)
        and current_inputs.get("auth_present", 0)
        and not params.get("scanner_failure", False)
    ):
        params.setdefault("scan_step", step)
    elif current_outputs.get("scan", 0) and params.get("scanner_failure", False):
        events.append("scanner failure prevented barcode validation")

    scan_step = params.get("scan_step")
    if (
        scan_step is not None
        and not current_inputs["barcode_ok"]
        and step - scan_step + 1 >= barcode_delay
    ):
        next_inputs["barcode_ok"] = 1
        params.pop("scan_step", None)
        events.append("barcode accepted")
    elif current_outputs.get("load_machine", 0) or current_inputs.get("human_pickup", 0):
        next_inputs["barcode_ok"] = 0
        params.pop("scan_step", None)
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
    mission_started = any(
        item["inputs"].get("request", 0)
        and item["inputs"].get("auth_present", 0)
        for item in trace
    )
    consumed = any(
        item["outputs"].get("load_machine", 0) or item["inputs"].get("human_pickup", 0)
        for item in trace
    )
    returned = bool(trace) and trace[-1]["inputs"].get("at_floor", 0)
    return mission_started and consumed and returned


def run_single_mission(mission_config, controller, input_variables, output_variables, max_steps=30):
    mission_config = dict(mission_config)
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


def run_lab_sample_mission(json_file=None, slugs_file=None, mission_config=None, mission="LabSamples"):
    controller, input_variables, output_variables = load_controller(json_file, slugs_file, mission)
    result = run_single_mission(
        mission_config or LAB_SAMPLE_MISSION, controller, input_variables, output_variables
    )
    export_trace_to_dot(result["trace"], "sim_example/EXAMPLE_MISSION.dot")
    print_trace(result["trace"])
    print(f"\nFinal status: {result['status']} - {result['reason']}")
    print(result["status"], result["reason"])
    for event in result["events"]:
        print(event)
    return result


def run_bad_case_missions(
    json_file=None,
    slugs_file=None,
    trials=SCENARIO_TRIALS,
    seed=None,
    mission_config=None,
    mission="LabSamples",
):
    controller, input_variables, output_variables = load_controller(json_file, slugs_file, mission)
    rng = random.Random(seed)
    results = []

    for trial in range(1, trials + 1):
        mission = dict(mission_config or LAB_SAMPLE_MISSION)
        mission.update({
            "request_step": rng.randint(1, 3),
            "authorization_step": rng.randint(1, 3),
            "lab_arrival_delay": rng.randint(2, 5),
            "floor_arrival_delay": rng.randint(2, 5),
        })
        result = run_single_mission(
            mission,
            controller,
            input_variables,
            output_variables,
        )
        results.append(result)
        if result["status"] != "SUCCESS":
            print(f"Trial {trial} failed: {result['reason']}")

    successful = sum(result["status"] == "SUCCESS" for result in results)
    print(f"Bad-case trials: {successful}/{trials} successful (seed={seed})")
    return results


def make_scenario_mission(scenario, rng, mission_config=None):
    mission = dict(mission_config or LAB_SAMPLE_MISSION)
    if scenario == "NoAdversity":
        return mission
    if scenario == "ScanFault":
        mission["scanner_failure"] = True
        return mission
    if scenario == "AbandonedRequest":
        mission["authorization_unavailable"] = True
        return mission
    if scenario == "LabPickup":
        mission["allow_human_pickup"] = True
        mission["human_pickup_mode"] = "always"
        return mission
    if scenario == "RandomStress":
        mission.update({
            "request_step": rng.randint(1, 3),
            "authorization_step": rng.randint(1, 3),
            "lab_arrival_delay": rng.randint(2, 5),
            "floor_arrival_delay": rng.randint(2, 5),
            "scanner_failure": rng.random() < 0.35,
        })
        if not mission["scanner_failure"] and rng.random() < 0.35:
            mission["allow_human_pickup"] = True
            mission["human_pickup_mode"] = "always"
        return mission
    raise ValueError(f"Unknown simulation scenario: {scenario}")


def run_scenario(mission, controller, input_variables, output_variables):
    try:
        return run_single_mission(
            mission, controller, input_variables, output_variables
        )
    except ValueError as error:
        return {
            "status": "VIOLATION",
            "reason": str(error),
            "trace": [],
            "events": [],
        }


def run_dashboard(
    mission="LabSamples",
    json_file=None,
    slugs_file=None,
    trials=SCENARIO_TRIALS,
    seed=DASHBOARD_SEED,
    dashboard_file=DASHBOARD_FILE,
):
    paths = mission_paths(mission)
    json_file = json_file or str(paths["controller"])
    slugs_file = slugs_file or str(paths["slugs_input"])
    base_mission = dict(LAB_SAMPLE_MISSION)
    base_mission.update(load_simulation_config(paths))
    controller, input_variables, output_variables = load_controller(json_file, slugs_file)
    rng = random.Random(seed)
    simulations = []

    scenarios = (
        "NoAdversity",
        "ScanFault",
        "AbandonedRequest",
        "LabPickup",
        "RandomStress",
    )
    for scenario in scenarios:
        for trial in range(1, trials + 1):
            mission = make_scenario_mission(scenario, rng, base_mission)
            result = run_scenario(
                mission, controller, input_variables, output_variables
            )
            simulations.append({
                "scenario": scenario,
                "name": f"{scenario} {trial:02d}",
                "mission": mission,
                "result": result,
            })

    write_dashboard(simulations, seed, dashboard_file)
    successful = sum(simulation["result"]["status"] == "SUCCESS" for simulation in simulations)
    print(
        f"Dashboard written to {dashboard_file} "
        f"({successful}/{len(simulations)} simulations successful, seed={seed})"
    )
    return simulations


def write_dashboard(simulations, seed, dashboard_file):
    os.makedirs(os.path.dirname(dashboard_file), exist_ok=True)
    successful = sum(
        simulation["result"]["status"] == "SUCCESS"
        for simulation in simulations
    )
    failed = sum(simulation["result"]["status"] == "FAILED" for simulation in simulations)
    violations = sum(simulation["result"]["status"] == "VIOLATION" for simulation in simulations)
    total_steps = sum(
        len(simulation["result"]["trace"]) for simulation in simulations
    )
    total_events = sum(
        len(simulation["result"]["events"]) for simulation in simulations
    )
    scenario_counts = {}
    for simulation in simulations:
        counts = scenario_counts.setdefault(
            simulation["scenario"],
            {"total": 0, "success": 0, "failed": 0, "violations": 0},
        )
        counts["total"] += 1
        status = simulation["result"]["status"]
        if status == "SUCCESS":
            counts["success"] += 1
        elif status == "FAILED":
            counts["failed"] += 1
        else:
            counts["violations"] += 1
    scenario_rows = "".join(
        "<tr>"
        f"<td>{escape(scenario)}</td><td>{escape(SCENARIO_DESCRIPTIONS[scenario])}</td>"
        f"<td>{counts['total']}</td>"
        f"<td>{counts['success']}</td><td>{counts['failed']}</td>"
        f"<td>{counts['violations']}</td>"
        "</tr>"
        for scenario, counts in scenario_counts.items()
    )

    cards = []
    for index, simulation in enumerate(simulations):
        result = simulation["result"]
        trace_rows = []
        for item in result["trace"]:
            active_inputs = ", ".join(
                name for name, value in item["inputs"].items() if value
            ) or "-"
            active_outputs = ", ".join(
                name for name, value in item["outputs"].items() if value
            ) or "-"
            monitors = ", ".join(
                name
                for name, value in item["inputs"].items()
                if value and "monitor" in name
            ) or "-"
            trace_rows.append(
                "<tr>"
                f"<td>{item['step']}</td><td>{escape(str(item['node']))}</td>"
                f"<td>{escape(active_inputs)}</td><td>{escape(active_outputs)}</td>"
                f"<td>{escape(monitors)}</td><td>{'yes' if item['is_goal'] else 'no'}</td>"
                "</tr>"
            )

        event_list = "".join(
            f"<li>{escape(event)}</li>" for event in result["events"]
        ) or "<li>No environment events</li>"
        raw_data = escape(json.dumps({
            "mission": simulation["mission"],
            "status": result["status"],
            "reason": result["reason"],
            "events": result["events"],
            "trace": result["trace"],
        }, indent=2))
        status_class = "success" if result["status"] == "SUCCESS" else "failure"
        open_attribute = " open" if index == 0 else ""
        cards.append(f"""
                <details class="simulation {status_class}"{open_attribute}>
          <summary><strong>{escape(simulation['name'])}</strong>
                        <span>{escape(simulation['scenario'])}</span>
            <span>{escape(result['status'])}</span>
            <span>{len(result['trace'])} steps</span>
          </summary>
          <div class="simulation-content">
            <p><strong>Reason:</strong> {escape(result['reason'])}</p>
            <h3>Mission parameters</h3>
            <pre>{escape(json.dumps(simulation['mission'], indent=2))}</pre>
            <h3>Environment events</h3>
            <ul>{event_list}</ul>
            <h3>State and action trace</h3>
            <table><thead><tr><th>Step</th><th>Node</th><th>Inputs</th>
              <th>Outputs</th><th>Active monitors</th><th>Goal</th></tr></thead>
              <tbody>{''.join(trace_rows)}</tbody>
            </table>
            <h3>Raw simulation record</h3>
            <pre>{raw_data}</pre>
          </div>
        </details>""")

    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Controller simulation dashboard</title>
<style>
  :root {{ color-scheme: dark; font-family: system-ui, sans-serif; }}
  body {{ margin: 0; background: #111827; color: #e5e7eb; }}
  main {{ max-width: 1500px; margin: auto; padding: 32px; }}
  h1 {{ margin-top: 0; }}
  .meta {{ color: #9ca3af; }}
  .summary {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin: 24px 0; }}
  .metric, details {{ background: #1f2937; border: 1px solid #374151; border-radius: 8px; }}
  .metric {{ padding: 18px; }}
  .metric strong {{ display: block; font-size: 1.8rem; }}
  .success {{ border-left: 4px solid #34d399; }}
  .failure {{ border-left: 4px solid #f87171; }}
  details {{ margin: 10px 0; }}
  summary {{ cursor: pointer; display: flex; gap: 18px; padding: 14px 16px; }}
  summary span {{ color: #9ca3af; }}
  .simulation-content {{ padding: 0 16px 20px; overflow-x: auto; }}
  table {{ width: 100%; border-collapse: collapse; font-size: .9rem; }}
  th, td {{ text-align: left; vertical-align: top; padding: 8px; border-bottom: 1px solid #374151; }}
  th {{ color: #93c5fd; }}
  pre {{ white-space: pre-wrap; background: #111827; padding: 12px; border-radius: 6px; overflow: auto; }}
  @media (max-width: 800px) {{ .summary {{ grid-template-columns: repeat(2, 1fr); }} main {{ padding: 16px; }} }}
</style></head><body><main>
<h1>Controller simulation dashboard</h1>
<p class="meta">Generated with seed {seed}. Every simulation includes its complete mission configuration, events, state trace, actions, and monitor activity.</p>
<section class="summary">
  <div class="metric"><strong>{len(simulations)}</strong>Total simulations</div>
  <div class="metric"><strong>{successful}</strong>Successful</div>
    <div class="metric"><strong>{failed}</strong>Failed / {violations} violations</div>
    <div class="metric"><strong>{total_steps}</strong>Total steps / {total_events} events</div>
</section>
<h2>Scenario statistics</h2>
<table><thead><tr><th>Scenario</th><th>Description</th><th>Runs</th><th>Success</th>
    <th>Failed</th><th>Violations</th></tr></thead>
    <tbody>{scenario_rows}</tbody></table>
<h2>Complete simulation records</h2>
{''.join(cards)}
</main></body></html>"""
    Path(dashboard_file).write_text(html, encoding="utf-8")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run controller simulations for a mission.")
    parser.add_argument("--mission", default="LabSamples", help="Mission name or mission package directory.")
    parser.add_argument("--trials", type=int, default=SCENARIO_TRIALS)
    parser.add_argument("--seed", type=int, default=DASHBOARD_SEED)
    parser.add_argument("--dashboard", default=DASHBOARD_FILE)
    arguments = parser.parse_args()
    run_dashboard(arguments.mission, trials=arguments.trials, seed=arguments.seed, dashboard_file=arguments.dashboard)

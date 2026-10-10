import json
import random
import sys
from pathlib import Path

from controller_test import load_controller, physical_inputs, split_state


LOCATIONS = ("at_base", "at_room")
MISSION_ACTIONS = ("goto_room", "clean_room", "abort_mission", "goto_base")


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
            candidates.append((successor_id, successor_inputs, successor_outputs))
    if not candidates:
        raise AssertionError(
            f"no controller transition matches node {node.get('id', '?')} "
            f"and environment {expected_inputs}"
        )
    priority = tuple(reversed(MISSION_ACTIONS))
    return max(
        candidates,
        key=lambda item: tuple(item[2].get(name, 0) for name in priority),
    )


def validate_state(inputs, outputs):
    active_locations = [name for name in LOCATIONS if inputs.get(name, 0)]
    if len(active_locations) > 1:
        raise AssertionError(f"multiple locations active: {active_locations}")
    if outputs.get("clean_room", 0) and outputs.get("abort_mission", 0):
        raise AssertionError("clean_room and abort_mission active together")
    if outputs.get("goto_room", 0) and not inputs.get("request", 0):
        raise AssertionError("goto_room issued without a request")
    if outputs.get("clean_room", 0) and not (
        inputs.get("at_room", 0)
        and inputs.get("request", 0)
        and not inputs.get("room_occupied", 0)
        and not inputs.get("room_cleaned", 0)
    ):
        raise AssertionError("clean_room issued outside its precondition")
    if outputs.get("abort_mission", 0) and not (
        inputs.get("at_room", 0)
        and inputs.get("request", 0)
        and inputs.get("room_occupied", 0)
    ):
        raise AssertionError("abort_mission issued outside its precondition")
    if outputs.get("goto_base", 0) and inputs.get("request", 0):
        raise AssertionError("goto_base issued while request is active")


def environment_step(inputs, outputs, request_arrives, config):
    next_inputs = dict(inputs)
    
    # New requests arrive
    if request_arrives and not config.get("request_sent"):
        next_inputs["request"] = 1
        config["request_sent"] = True
        
    # Navigation physics (simulator teleports robot to location)
    if outputs.get("goto_room", 0):
        config.setdefault("room_command_step", config["step"])
        if config["step"] - config["room_command_step"] + 1 >= config["room_arrival_delay"]:
            next_inputs["at_base"] = 0
            next_inputs["at_room"] = 1
            next_inputs["room_occupied"] = int(config.get("room_occupied", False))
            config.pop("room_command_step", None)
    if outputs.get("goto_base", 0):
        next_inputs["at_base"] = 1
        next_inputs["at_room"] = 0
        
    # Cleaning action causality
    if outputs.get("clean_room", 0):
        next_inputs["room_cleaned"] = 1
        next_inputs["request"] = 0
        
    # Abort action causality
    if outputs.get("abort_mission", 0):
        next_inputs["room_occupied"] = 0
        next_inputs["request"] = 0
        
    # State resets: if there was no active request in the previous step,
    # the room is naturally reset to "not clean" for the next cycle.
    if not inputs.get("request", 0):
        next_inputs["room_cleaned"] = 0
        
    return next_inputs


def run(config=None, max_steps=30):
    config = dict(config or {
        "request_step": 1,
        "room_arrival_delay": 1,
    })
    controller, input_variables, _ = load_controller(mission="KeepingClean")
    variables = controller["variables"]
    nodes = controller["nodes"]
    current_id = "0"
    inputs, outputs = split_state(variables, nodes[current_id]["state"], input_variables)
    trace = []

    for step in range(max_steps):
        validate_state(inputs, outputs)
        trace.append({"step": step, "node": current_id, "inputs": inputs, "outputs": outputs})
        if (
            config.get("request_sent")
            and inputs.get("at_base", 0)
            and not inputs.get("request", 0)
            and step > 0
        ):
            return {
                "status": "PASS",
                "reason": "request completed and robot returned to base",
                "trace": trace,
            }
        config["step"] = step
        expected_inputs = environment_step(
            inputs,
            outputs,
            step + 1 >= config["request_step"],
            config,
        )
        try:
            current_id, inputs, outputs = choose_successor(
                nodes[current_id], nodes, variables, input_variables, expected_inputs
            )
        except AssertionError as error:
            return {
                "status": "PARTIAL",
                "reason": str(error),
                "trace": trace,
            }

    return {
        "status": "PARTIAL",
        "reason": "step limit exceeded",
        "trace": trace,
    }


def main():
    trials = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 20261010
    rng = random.Random(seed)
    results = []
    for trial in range(1, trials + 1):
        scenario_roll = rng.random()
        occupied = scenario_roll < 0.25
        scenario = "OccupiedRoom" if occupied else "NormalCleaning"
        config = {
            "request_step": rng.randint(1, 4),
            "room_arrival_delay": rng.randint(1, 4),
            "room_occupied": occupied,
            "scenario": scenario,
            "expected_actions": (
                ["goto_room", "abort_mission", "goto_base"]
                if occupied
                else ["goto_room", "clean_room", "goto_base"]
            ),
        }
        result = run(config)
        trace = result["trace"]
        actions = [
            next(name for name, value in item["outputs"].items() if value)
            for item in trace
            if any(item["outputs"].get(name, 0) for name in MISSION_ACTIONS)
        ]
        observed = list(dict.fromkeys(actions))
        expected = config.get("expected_actions", ["goto_room", "clean_room", "goto_base"])
        if observed[:len(expected)] != expected:
            result["status"] = "FAIL"
            result["reason"] = f"unexpected action sequence: {observed}; expected {expected}"
        result.update({"trial": trial, "seed": seed, "config": config, "actions": observed})
        results.append(result)
        print(f"trial {trial:02d} {scenario}: {result['status']} {' -> '.join(observed)}")

    passed = sum(result["status"] == "PASS" for result in results)
    report = Path("missions/KeepingClean/controller_test_results.json")
    report.write_text(json.dumps({"seed": seed, "trials": results}, indent=2) + "\n", encoding="utf-8")
    print(f"KeepingClean controller tests: {passed}/{trials} passed (seed={seed})")
    print(f"Report written to {report}")
    return 0 if passed == trials else 1


if __name__ == "__main__":
    raise SystemExit(main())

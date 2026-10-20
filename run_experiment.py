import argparse
import itertools
import json
import math
import os
import re
import statistics
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

from scipy.stats import ttest_rel

from mission_config import mission_paths


MISSION_ASSUMPTIONS = {
    "LabSamples": {
        "BarcodeReaderCausality": "G ((!barcode_ok & !scan) -> !barcode_ok')",
        "BarcodePersistence": "G ((barcode_ok & !load_machine & !human_pickup) -> barcode_ok')",
        "SampleConsumed": "G ((load_machine | human_pickup) -> !barcode_ok')",
        "LaboratoryArrival": "G (goto_lab -> F at_lab)",
        "FloorArrival": "G (goto_floor -> F at_floor)",
        "BarcodeBecomesValid": "G ((auth_present & scan) -> F barcode_ok)",
        "RequestPersistence": "G ((request & !load_machine & !human_pickup) -> request')",
        "AuthorizationPersistence": "G ((auth_present & !barcode_ok & !human_pickup) -> auth_present')",
    },
    "KeepingClean": {
        "CleanConsumed": "G (clean_room -> room_cleaned')",
        "CleanPersistence": "G ((room_cleaned & request) -> room_cleaned')",
        "CleanReset": "G (!request -> !room_cleaned')",
        "NotCleanPersistence": "G ((!room_cleaned & !clean_room & request) -> !room_cleaned')",
        "OccupiedConsumed": "G (abort_mission -> !room_occupied')",
        "OccupiedPersistence": "G ((room_occupied & !abort_mission) -> room_occupied')",
        "RequestConsumed": "G ((clean_room | abort_mission) -> !request')",
        "RequestPersistence": "G ((request & !clean_room & !abort_mission) -> request')",
        "RoomArrival": "G (goto_room -> F at_room)",
        "BaseArrival": "G (goto_base -> F at_base)",
    },
}

MISSION_ASSUMPTION_CONTEXT = {
    "LabSamples": {
        "BarcodeReaderCausality": (
            "barcode", "bar-code", "scan", "scanner", "scanning", "reader",
            "read", "decode", "identify", "identification", "status", "valid",
            "invalid", "ok", "causal", "cause", "trigger", "only if",
        ),
        "BarcodePersistence": (
            "barcode", "bar-code", "valid", "validated", "ok", "remain",
            "stays", "persist", "persistence", "unchanged", "until",
            "load", "loaded", "machine", "analysis", "human", "personnel",
            "pickup", "pick up", "collected", "consumed", "invalidated",
        ),
        "SampleConsumed": (
            "sample", "specimen", "material", "barcode", "valid", "invalidate",
            "invalidated", "consume", "consumed", "consumption", "used",
            "load", "loaded", "loading", "machine", "analysis", "human",
            "personnel", "pickup", "pick up", "collect", "collection",
            "next cycle", "next sample",
        ),
        "LaboratoryArrival": (
            "goto_lab", "go to lab", "laboratory", "lab", "destination",
            "arrive", "arrival", "arrives", "reach", "reaches", "deliver",
            "delivery", "transport", "transported", "command", "commanded",
            "navigation", "navigate", "eventually", "eventual", "response",
        ),
        "FloorArrival": (
            "goto_floor", "go to floor", "floor", "patient floor", "destination",
            "arrive", "arrival", "arrives", "reach", "reaches", "return",
            "navigate", "navigation", "command", "commanded", "eventually",
            "eventual", "response", "transport",
        ),
        "BarcodeBecomesValid": (
            "barcode", "bar-code", "valid", "validated", "becomes valid",
            "ok", "authorized", "authorization", "authoriz", "permission",
            "credential", "scan", "scanned", "scanning", "reader", "read",
            "eventually", "eventual", "status", "recognize", "recognition",
        ),
        "RequestPersistence": (
            "request", "delivery request", "sample request", "mission", "task",
            "job", "active", "remain", "remains", "persist", "persistence",
            "ongoing", "open", "pending", "until", "load", "loaded", "machine",
            "pickup", "pick up", "human", "personnel", "resolved", "complete",
        ),
        "AuthorizationPersistence": (
            "authorization", "authorized", "auth", "permission", "credential",
            "access", "approval", "barcode", "valid", "validated", "scan",
            "scanned", "reading", "remain", "remains", "persist", "persistence",
            "until", "human", "personnel", "pickup", "pick up", "collected",
            "present", "available",
        ),
    },
    "KeepingClean": {
        "CleanConsumed": (
            "clean", "cleaned", "cleaning", "cleaner", "sanitize", "sanitized",
            "sanitization", "sterilize", "sterilized", "sterilization", "disinfect",
            "disinfection", "hygiene", "wash", "washed", "scrub", "scrubbed",
            "room", "room_cleaned", "space", "area", "completion", "complete",
            "completed", "finish", "finished", "status", "update", "updated",
            "result", "effect",
        ),
        "CleanPersistence": (
            "clean", "cleaned", "cleaning", "sanitized", "sterilized", "hygiene",
            "room", "room_cleaned", "space", "request", "mission", "active",
            "ongoing", "remain", "remains", "persist", "persistence", "stable",
            "state", "status", "until", "duration",
        ),
        "CleanReset": (
            "clean", "cleaned", "unclean", "not_clean", "dirty", "soiled",
            "contaminated", "room", "room_cleaned", "space", "request",
            "mission", "reset", "resets", "clear", "cleared", "cycle",
            "next", "new", "future", "subsequent", "again",
        ),
        "NotCleanPersistence": (
            "not_clean", "unclean", "dirty", "soiled", "contaminated", "room",
            "room_cleaned", "clean_room", "space", "clean", "cleaning",
            "request", "mission", "remain", "remains", "persist", "persistence",
            "unchanged", "state", "status", "no change",
        ),
        "OccupiedConsumed": (
            "occupied", "occupancy", "room", "in_use", "in use", "unavailable",
            "blocked", "person", "patient", "human", "resident", "abort",
            "aborted", "cancel", "cancelled", "terminate", "terminated",
            "clear", "cleared", "release", "released", "vacate", "handle",
        ),
        "OccupiedPersistence": (
            "occupied", "occupancy", "room", "in_use", "in use", "unavailable",
            "blocked", "patient", "person", "human", "resident", "abort",
            "aborted", "remain", "remains", "persist", "persistence", "stable",
            "until", "state", "status", "continues",
        ),
        "RequestConsumed": (
            "request", "mission", "task", "job", "clean", "cleaning", "sanitize",
            "abort", "aborted", "cancel", "cancelled", "consume", "consumed",
            "resolve", "resolved", "complete", "completed", "finish", "clear",
            "cleared", "cycle", "handled", "satisfied",
        ),
        "RequestPersistence": (
            "request", "mission", "task", "job", "clean", "cleaning", "sanitize",
            "abort", "cancel", "remain", "remains", "persist", "persistence",
            "active", "ongoing", "open", "pending", "unresolved", "cycle",
            "until", "state", "status",
        ),
        "RoomArrival": (
            "room", "destination", "target", "navigate", "navigation", "travel",
            "move", "moving", "go", "arrive", "arrival", "reaches", "reach",
            "goto_room", "command", "commanded", "route", "path", "eventually",
            "eventual", "location",
        ),
        "BaseArrival": (
            "base", "station", "home", "dock", "recharge", "charging", "charge",
            "return", "navigate", "navigation", "travel", "move", "moving",
            "arrive", "arrival", "reaches", "reach", "goto_base", "command",
            "commanded", "route", "path", "eventually", "eventual", "location",
        ),
    },
}


def run(command):
    started = time.perf_counter()
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    return result, time.perf_counter() - started


def mission_assumptions(paths):
    try:
        return MISSION_ASSUMPTIONS[paths["name"]]
    except KeyError as error:
        raise ValueError(f"No hardcoded experiment assumptions configured for mission {paths['name']!r}") from error


def mission_assumption_context(paths):
    try:
        return MISSION_ASSUMPTION_CONTEXT[paths["name"]]
    except KeyError as error:
        raise ValueError(f"No hardcoded experiment context configured for mission {paths['name']!r}") from error


def write_goal_variant(source, destination, removed):
    lines = Path(source).read_text(encoding="utf-8").splitlines(keepends=True)
    output = []
    skipping = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(("Assumption [", "Domain Assumption [")):
            match = re.search(r"(?:Domain )?Assumption \[([^\]]+)\]", stripped)
            name = match.group(1) if match else ""
            skipping = name in removed
        elif stripped and not stripped.startswith("#") and re.match(
            r"^(?:Initialization|Domain(?:\s+)?Property|Domain Assumption|"
            r"Assumption(?:\s+Achieve)?|Goal(?:\s+(?:Maintain|Achieve))?)\s+\[[^]]+\]$",
            stripped,
        ):
            skipping = False
        if not skipping:
            output.append(line)
    Path(destination).write_text("".join(output), encoding="utf-8")

def expected_metrics(report, removed, assumptions, concepts):
    findings = report.get("findings", [])
    detected = {}
    proposed = {}
    exact_detected = {}
    exact_proposed = {}
    graded = {}

    def relevant_text(finding):
        return json.dumps(finding, ensure_ascii=True).lower()

    def formula_variables(formula):
        return {
            token.lower()
            for token in re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", formula)
            if token not in {"f", "g", "true", "false"}
        }

    for name, formula in assumptions.items():
        normalized_formula = formula.lower().replace("'", "")
        expected_variables = formula_variables(formula)
        expected_concepts = concepts[name]
        best_idea_score = 0.0
        best_proposal_score = 0.0
        has_exact_idea = False
        has_exact_proposal = False
        for finding in findings:
            text = relevant_text(finding)
            proposal = finding.get("proposal", {})
            formal_def = proposal.get("formal_def", "").lower().replace("'", "")
            variable_overlap = expected_variables & formula_variables(text)
            concept_overlap = {
                concept for concept in expected_concepts if concept in text
            }
            if name.lower() in text or normalized_formula in text:
                idea_score = 1.0
                has_exact_idea = True
            elif expected_concepts and len(concept_overlap) == len(expected_concepts):
                idea_score = 0.75
            elif expected_variables and len(variable_overlap) == len(expected_variables):
                idea_score = 0.75
            elif (
                expected_concepts
                and len(concept_overlap) >= (len(expected_concepts) + 1) // 2
            ) or (
                expected_variables
                and len(variable_overlap) >= (len(expected_variables) + 1) // 2
            ):
                idea_score = 0.5
            else:
                idea_score = 0.0
            if proposal.get("action") == "add_assumption" and normalized_formula in formal_def:
                proposal_score = 1.0
                has_exact_proposal = True
            elif proposal.get("action") == "add_assumption" and idea_score:
                proposal_score = 0.5
            else:
                proposal_score = 0.0
            best_idea_score = max(best_idea_score, idea_score)
            best_proposal_score = max(best_proposal_score, proposal_score)
        detected[name] = best_idea_score > 0
        proposed[name] = best_proposal_score > 0
        exact_detected[name] = has_exact_idea
        exact_proposed[name] = has_exact_proposal
        graded[name] = max(best_idea_score, best_proposal_score)
    expected = sorted(removed)
    graded_credit = statistics.mean(graded[name] for name in expected) if expected else 0.0
    return {
        "finding_count": len(findings),
        "detects_expected_omissions": {name: detected[name] for name in expected},
        "proposes_expected_omissions": {name: proposed[name] for name in expected},
        "exact_detects_expected_omissions": {name: exact_detected[name] for name in expected},
        "exact_proposes_expected_omissions": {name: exact_proposed[name] for name in expected},
        "graded_credit_by_assumption": {name: graded[name] for name in expected},
        "evaluated": bool(expected),
        "graded_credit": round(graded_credit, 4) if expected else None,
        "any_credit": bool(expected) and graded_credit > 0,
        "strict_correctness": bool(expected)
        and all(exact_detected[name] and exact_proposed[name] for name in expected),
    }


def run_local_review(case, model, rules_file):
    command = [
        sys.executable,
        "llm_checker.py",
        "--goal-model", str(case["goal_model"]),
        "--mission-text", str(case["mission_text"]),
        "--rules-file", str(rules_file),
        "--structured-slugs", str(case["structured_slugs"]),
        "--slugs-input", str(case["slugs_input"]),
        "--counter-strategy", str(case["counter_strategy"]),
        "--report", str(case["report"]),
        "--markdown", str(case["markdown"]),
        "--model", model,
    ]
    if not case["include_goal_model"]:
        command.append("--without-goal-model")
    for document in case["omitted_documents"]:
        command.append(f"--without-{document}")
    return run(command)


def make_cases(args, root, assumptions):
    paths = mission_paths(args.mission)
    names = sorted(assumptions)
    goal_variants = [tuple()]
    for size in range(1, args.max_assumptions_removed + 1):
        goal_variants.extend(itertools.combinations(names, size))

    cases = []
    for trial in range(1, args.trials + 1):
        trial_root = root / f"trial_{trial:03d}"
        models_root = trial_root / "goal_models"
        models_root.mkdir(parents=True, exist_ok=True)
        for removed in goal_variants:
            model_tag = "full" if not removed else "without_" + "_and_".join(removed)
            goal_model = models_root / f"{model_tag}.gm"
            if not goal_model.exists():
                write_goal_variant(paths["goal_model"], goal_model, set(removed))
            include_goal_conditions = [True, False]
            for include_goal_model in include_goal_conditions:
                goal_tag = "with_goal_model" if include_goal_model else "without_goal_model"
                case_tag = f"{model_tag}__{goal_tag}"
                artifact_root = trial_root / case_tag
                artifact_root.mkdir(parents=True, exist_ok=True)
                cases.append({
                    "trial": trial,
                    "case": case_tag,
                    "removed_assumptions": list(removed),
                    "omitted_documents": [],
                    "include_goal_model": include_goal_model,
                    "goal_model": goal_model,
                    "mission_text": paths["mission_text"],
                    "structured_slugs": artifact_root / "model.structuredslugs",
                    "slugs_input": artifact_root / "model.slugsin",
                    "counter_strategy": artifact_root / "counter_strategy.txt",
                    "report": artifact_root / "review.json",
                    "markdown": artifact_root / "review.md",
                })
    return cases


def summarize(results):
    groups = defaultdict(list)
    for result in results:
        key = (
            tuple(result["removed_assumptions"]),
            result["include_goal_model"],
            tuple(result["omitted_documents"]),
        )
        groups[key].append(result)
    summaries = []
    for key, items in sorted(groups.items(), key=str):
        evaluated = [item for item in items if item["metrics"]["evaluated"]]
        summaries.append({
            "removed_assumptions": list(key[0]),
            "include_goal_model": key[1],
            "omitted_documents": list(key[2]),
            "trials": len(items),
            "successful_reviews": sum(item["review_available"] for item in items),
            "review_success_rate": round(sum(item["review_available"] for item in items) / len(items), 4),
            "evaluated_trials": len(evaluated),
            "any_credit_trials": sum(item["metrics"].get("any_credit", False) for item in evaluated),
            "any_credit_rate": round(
                sum(item["metrics"].get("any_credit", False) for item in evaluated) / len(evaluated), 4
            ) if evaluated else None,
            "mean_graded_credit": round(
                statistics.mean(item["metrics"].get("graded_credit", 0) for item in evaluated), 4
            ) if evaluated else None,
            "strict_correct_trials": sum(item["metrics"]["strict_correctness"] for item in evaluated),
            "strict_correctness_rate": round(
                sum(item["metrics"]["strict_correctness"] for item in evaluated) / len(evaluated), 4
            ) if evaluated else None,
            "mean_findings": round(statistics.mean(item["metrics"].get("finding_count", 0) for item in items), 3),
        })
    return summaries


def summarize_goal_model_tests(results):
    groups = defaultdict(dict)
    for result in results:
        if (
            not result["review_available"]
            or result["omitted_documents"]
            or not result["removed_assumptions"]
        ):
            continue
        key = tuple(result["removed_assumptions"])
        groups[key][(result["trial"], result["include_goal_model"])] = result["metrics"]

    tests = []
    for removed, trial_metrics in sorted(groups.items(), key=str):
        paired = []
        for trial in sorted({trial for trial, _ in trial_metrics}):
            with_goal = trial_metrics.get((trial, True))
            without_goal = trial_metrics.get((trial, False))
            if with_goal is not None and without_goal is not None:
                paired.append((with_goal, without_goal))

        test = {
            "removed_assumptions": list(removed),
            "paired_trials": len(paired),
            "note": "Positive differences mean the without-goal-model condition scored higher.",
        }
        for metric in ("graded_credit", "any_credit", "strict_correctness"):
            without_goal = [float(pair[1].get(metric, 0.0) or 0.0) for pair in paired]
            with_goal = [float(pair[0].get(metric, 0.0) or 0.0) for pair in paired]
            if len(paired) < 2:
                test[metric] = {"insufficient_pairs": True}
                continue
            differences = [without - with_goal for without, with_goal in zip(without_goal, with_goal)]
            difference_variance = statistics.pvariance(differences)
            t_statistic_is_infinite = False
            if math.isclose(difference_variance, 0.0, abs_tol=1e-12):
                if differences[0] == 0:
                    t_statistic, p_value = 0.0, 1.0
                else:
                    t_statistic, t_statistic_is_infinite = None, True
                    p_value = 0.0
            else:
                statistic = ttest_rel(without_goal, with_goal)
                t_statistic, p_value = float(statistic.statistic), float(statistic.pvalue)
            test[metric] = {
                "mean_with_goal_model": round(statistics.mean(with_goal), 4),
                "mean_without_goal_model": round(statistics.mean(without_goal), 4),
                "mean_difference_without_minus_with": round(statistics.mean(differences), 4),
                "t_statistic": None if t_statistic_is_infinite else round(t_statistic, 4),
                "t_statistic_is_infinite": t_statistic_is_infinite,
                "degrees_of_freedom": len(paired) - 1,
                "p_value_two_sided": round(p_value, 6),
            }
        tests.append(test)
    return tests


def main():
    parser = argparse.ArgumentParser(description="Repeated LLM review and ablation experiment.")
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--max-assumptions-removed", type=int, default=1)
    parser.add_argument("--model", default=os.environ.get("OLLAMA_MODEL", "gemma4:26b"))
    parser.add_argument("--mission", default="LabSamples", help="Mission name or mission package directory.")
    parser.add_argument("--output-dir", help="Override the mission-specific experiment directory.")
    parser.add_argument("--metrics", help="Override the mission-specific metrics JSON path.")
    parser.add_argument("--limit", type=int, help="Run only the first N generated cases.")
    parser.add_argument("--dry-run", action="store_true", help="Write the manifest without calling Ollama.")
    parser.add_argument(
        "--reuse-existing-reports",
        action="store_true",
        help="Reuse existing review.json files instead of calling the LLM again.",
    )
    args = parser.parse_args()
    if args.trials < 1 or args.max_assumptions_removed < 0:
        parser.error("--trials must be positive and --max-assumptions-removed cannot be negative")

    paths = mission_paths(args.mission)
    if not paths["goal_model"].exists() or not paths["mission_text"].exists():
        parser.error(f"mission {args.mission!r} must provide {paths['goal_model']} and {paths['mission_text']}")

    if args.output_dir is None:
        args.output_dir = str(paths["directory"] / "experiment_runs")
    if args.metrics is None:
        args.metrics = str(paths["directory"] / "experiment_metrics.json")

    assumptions = mission_assumptions(paths)
    concepts = mission_assumption_context(paths)

    root = Path(args.output_dir)
    root.mkdir(parents=True, exist_ok=True)
    cases = make_cases(args, root, assumptions)
    if args.limit:
        cases = cases[: args.limit]
    manifest = [{key: (str(value) if isinstance(value, Path) else value) for key, value in case.items()} for case in cases]
    if args.dry_run:
        output = {"mode": "dry-run", "case_count": len(cases), "cases": manifest}
        Path(args.metrics).write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(output, indent=2))
        return 0

    results = []
    for index, case in enumerate(cases, start=1):
        if args.reuse_existing_reports and case["report"].exists():
            review = subprocess.CompletedProcess([], 0, "Reused existing report.\n", "")
            elapsed = 0.0
        else:
            review, elapsed = run_local_review(
                case, args.model, Path(__file__).resolve().parent / "rulesgm.txt"
            )
        report_available = review.returncode == 0 and case["report"].exists()
        result = {
            "trial": case["trial"],
            "case": case["case"],
            "removed_assumptions": case["removed_assumptions"],
            "omitted_documents": sorted(case["omitted_documents"]),
            "include_goal_model": case["include_goal_model"],
            "elapsed_seconds": round(elapsed, 3),
            "exit_code": review.returncode,
            "review_available": report_available,
            "report": str(case["report"]),
        }
        if report_available:
            result["metrics"] = expected_metrics(
                json.loads(case["report"].read_text(encoding="utf-8")),
                case["removed_assumptions"],
                assumptions,
                concepts,
            )
        else:
            result["metrics"] = {
                "finding_count": 0,
                "evaluated": bool(case["removed_assumptions"]),
                "strict_correctness": False,
            }
            result["error"] = (review.stdout + review.stderr).strip()
        results.append(result)
        print(f"[{index}/{len(cases)}] {case['case']} review={'ok' if report_available else 'failed'}")

    output = {
        "experiment": "repeated goal-model and document ablation review",
        "model": args.model,
        "mission": paths["name"],
        "trials_requested": args.trials,
        "max_assumptions_removed": args.max_assumptions_removed,
        "case_count": len(cases),
        "assumptions": assumptions,
        "results": results,
        "by_condition": summarize(results),
        "goal_model_significance": summarize_goal_model_tests(results),
        "completed_at_epoch": time.time(),
    }
    Path(args.metrics).write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "case_count": len(cases),
        "by_condition": output["by_condition"],
        "goal_model_significance": output["goal_model_significance"],
    }, indent=2))
    return 1 if any(item["exit_code"] for item in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())

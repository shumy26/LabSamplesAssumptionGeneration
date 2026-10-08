import argparse
import json
import subprocess
import sys
import time
from pathlib import Path


EXPECTED = {
    "omitted_assumption": "BarcodeBecomesValid",
    "omitted_formula": "(auth_present & scan) -> F barcode_ok",
}


def run(command):
    started = time.perf_counter()
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    return result, time.perf_counter() - started


def report_metrics(report):
    findings = report.get("findings", [])
    serialized = json.dumps(findings).lower()
    assumption_finding = any(
        item.get("category") == "missing_assumption" for item in findings
    )
    references_omission = (
        "barcode becomes valid" in serialized
        or "barcodebecomesvalid" in serialized
        or "barcode_ok" in serialized and "scan" in serialized
    )
    valid_candidate = any(
        item.get("category") == "missing_assumption"
        and item.get("proposal", {}).get("action") == "add_assumption"
        and "barcode_ok" in item.get("proposal", {}).get("formal_def", "")
        for item in findings
    )
    return {
        "finding_count": len(findings),
        "detects_missing_assumption": assumption_finding,
        "references_expected_omission": references_omission,
        "proposes_expected_formula_shape": valid_candidate,
        "strict_correctness": assumption_finding and references_omission and valid_candidate,
    }


def main():
    parser = argparse.ArgumentParser(description="Run and measure local and Gemini GR(1) reviews.")
    parser.add_argument("--local-report", default="llm_review.json")
    parser.add_argument("--gemini-report", default="gemini_review.json")
    parser.add_argument("--metrics", default="experiment_metrics.json")
    args = parser.parse_args()

    metrics = {
        "experiment": "intentional omission detection",
        "expected_ground_truth": EXPECTED,
        "started_at_epoch": time.time(),
        "local": {},
        "gemini": {},
    }

    pipeline_started = time.perf_counter()
    compile_result, _ = run([sys.executable, "compile_to_gr1.py"])
    parser_result, _ = run([
        sys.executable,
        "slugs/tools/StructuredSlugsParser/compiler.py",
        "LabSamples.structuredslugs",
    ])
    Path("LabSamples.slugsin").write_text(parser_result.stdout, encoding="utf-8")
    slugs_result, _ = run(["./slugs/src/slugs", "LabSamples.slugsin"])
    pipeline_seconds = time.perf_counter() - pipeline_started
    pipeline = slugs_result
    if "Specification is unrealizable" in slugs_result.stdout + slugs_result.stderr:
        counter_result, _ = run(["./slugs/src/slugs", "--counterStrategy", "LabSamples.slugsin"])
        Path("counter_strategy.txt").write_text(
            counter_result.stdout + counter_result.stderr,
            encoding="utf-8",
        )
    if compile_result.returncode or parser_result.returncode or slugs_result.returncode:
        metrics["pipeline_error"] = (
            compile_result.stderr + parser_result.stderr + slugs_result.stderr
        )
    metrics["pipeline_seconds"] = round(pipeline_seconds, 3)
    metrics["slugs_result"] = (
        "unrealizable"
        if "Specification is unrealizable" in pipeline.stdout + pipeline.stderr
        else "unknown"
    )
    metrics["pipeline_exit_code"] = pipeline.returncode

    Path(args.local_report).unlink(missing_ok=True)
    local, local_seconds = run([
        sys.executable,
        "llm_checker.py",
        "--report",
        args.local_report,
        "--markdown",
        "llm_review.md",
    ])
    metrics["local"]["elapsed_seconds"] = round(local_seconds, 3)
    metrics["local"]["exit_code"] = local.returncode
    metrics["local"]["report_available"] = local.returncode == 0 and Path(args.local_report).exists()
    if metrics["local"]["report_available"]:
        metrics["local"].update(report_metrics(json.loads(Path(args.local_report).read_text())))

    Path(args.gemini_report).unlink(missing_ok=True)
    gemini, gemini_seconds = run([
        sys.executable,
        "gemini_checker.py",
        "--report",
        args.gemini_report,
    ])
    metrics["gemini"]["elapsed_seconds"] = round(gemini_seconds, 3)
    metrics["gemini"]["exit_code"] = gemini.returncode
    metrics["gemini"]["status"] = (
        "completed" if gemini.returncode == 0 else "skipped_or_failed"
    )
    metrics["gemini"]["report_available"] = gemini.returncode == 0 and Path(args.gemini_report).exists()
    if metrics["gemini"]["report_available"]:
        metrics["gemini"].update(report_metrics(json.loads(Path(args.gemini_report).read_text())))

    metrics["correctness_definition"] = (
        "Strict correctness requires a missing_assumption finding that references "
        "the omitted barcode responsiveness assumption and proposes its expected formula shape."
    )
    metrics["completed_at_epoch"] = time.time()
    Path(args.metrics).write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(metrics, indent=2))
    return 0 if pipeline.returncode == 0 else pipeline.returncode


if __name__ == "__main__":
    raise SystemExit(main())

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from llm_checker import (
    build_review_prompt,
    classify_slugs,
    compile_goal_model,
    parse_json_response,
    run_command,
)


def load_env_value(name):
    value = os.environ.get(name)
    if value:
        return value
    env_file = Path(".env")
    if not env_file.exists():
        return None
    for line in env_file.read_text(encoding="utf-8").splitlines():
        key, separator, candidate = line.partition("=")
        if separator and key.strip() == name:
            return candidate.strip().strip("\"'")
    return None


def build_context(args):
    compile_goal_model(args.goal_model, args.structured_slugs, args.parser)
    status, slugs_output = classify_slugs(args.slugs, args.slugs_input)
    counter_strategy = ""
    if status == "unrealizable":
        code, counter_strategy = run_command([
            args.slugs,
            "--counterStrategy",
            args.slugs_input,
        ])
        if code:
            raise RuntimeError(counter_strategy)
        Path(args.counter_strategy).write_text(counter_strategy, encoding="utf-8")

    excerpt = counter_strategy
    if len(excerpt) > args.counter_strategy_chars:
        excerpt = excerpt[: args.counter_strategy_chars] + (
            "\n[repetitive counter-strategy states omitted; see counter_strategy.txt]\n"
        )
    context = {
        "mission_text": Path(args.mission_text).read_text(encoding="utf-8"),
        "goal_model": Path(args.goal_model).read_text(encoding="utf-8"),
        "structured_slugs": Path(args.structured_slugs).read_text(encoding="utf-8"),
        "slugs_input": Path(args.slugs_input).read_text(encoding="utf-8"),
        "realizability_result": slugs_output,
        "counter_strategy": counter_strategy,
        "counter_strategy_excerpt": excerpt,
        "context_size": args.context_size,
    }
    return context, status


def ask_gemini(model, api_key, context):
    endpoint = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={api_key}"
    )
    payload = json.dumps({
        "contents": [{"parts": [{"text": build_review_prompt(context)}]}],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 2500,
            "responseMimeType": "application/json",
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        started = time.perf_counter()
        with urllib.request.urlopen(request, timeout=300) as response:
            result = json.load(response)
        elapsed = time.perf_counter() - started
    except urllib.error.HTTPError as error:
        details = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Gemini rejected the request ({error.code}): {details}") from error
    except (urllib.error.URLError, TimeoutError) as error:
        raise RuntimeError(f"Could not reach Gemini: {error}") from error

    try:
        content = result["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as error:
        raise RuntimeError(f"Gemini returned no text candidate: {result}") from error
    return parse_json_response(content), elapsed


def main():
    parser = argparse.ArgumentParser(description="Review a GR(1) specification with Gemini.")
    parser.add_argument("--goal-model", default="LabSamples.gm")
    parser.add_argument("--mission-text", default="LabSamplesNL.txt")
    parser.add_argument("--counter-strategy", default="counter_strategy.txt")
    parser.add_argument("--counter-strategy-chars", type=int, default=3500)
    parser.add_argument("--structured-slugs", default="LabSamples.structuredslugs")
    parser.add_argument("--slugs-input", default="LabSamples.slugsin")
    parser.add_argument("--parser", default="slugs/tools/StructuredSlugsParser/compiler.py")
    parser.add_argument("--slugs", default="./slugs/src/slugs")
    parser.add_argument("--model", default="gemini-3.8-flash")
    parser.add_argument("--api-key-env", default="GEMINI_API_KEY")
    parser.add_argument("--context-size", type=int, default=8192)
    parser.add_argument("--report", default="gemini_review.json")
    args = parser.parse_args()

    api_key = load_env_value(args.api_key_env)
    if not api_key:
        print(f"Gemini review skipped: environment variable {args.api_key_env} is not set")
        return 3

    started = time.perf_counter()
    context, status = build_context(args)
    report, request_seconds = ask_gemini(args.model, api_key, context)
    report.update({
        "model": args.model,
        "provider": "gemini",
        "realizability": status,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "request_seconds": round(request_seconds, 3),
    })
    Path(args.report).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Gemini review written to {args.report}; findings: {len(report.get('findings', []))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

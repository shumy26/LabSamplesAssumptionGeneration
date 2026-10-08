import argparse
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path


def run_command(command):
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    return result.returncode, result.stdout + result.stderr


def classify_slugs(slugs_executable, slugs_input):
    _, output = run_command([slugs_executable, slugs_input])
    if "Specification is realizable" in output:
        return "realizable", output
    if "Specification is unrealizable" in output:
        return "unrealizable", output
    raise RuntimeError(f"Slugs returned an unknown result:\n{output}")


def compile_goal_model(goal_model, structured_slugs, parser):
    code, output = run_command([
        sys.executable,
        "compile_to_gr1.py",
        goal_model,
        structured_slugs,
    ])
    if code:
        raise RuntimeError(output)
    code, output = run_command([sys.executable, parser, structured_slugs])
    if code:
        raise RuntimeError(output)


def parse_json_response(content):
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
    if fenced:
        content = fenced.group(1)
    else:
        start = content.find("{")
        end = content.rfind("}")
        if start < 0 or end <= start:
            raise ValueError(f"Model did not return a JSON object:\n{content}")
        content = content[start : end + 1]
    return json.loads(content)


def build_review_prompt(context, include_goal_model=True):
    goal_model = context["goal_model"] if include_goal_model else "[Goal Model omitted from LLM context.]"
    return f"""You are a reviewer for a research workflow that translates robotic mission descriptions into GR(1).
Analyze the supplied Goal Model, GR(1) specification, Slugs result, and counter-strategy if present.
Do not modify files and do not assume that realizability means correctness.

Look for four kinds of findings:
1. missing_assumption: an omitted physical or environmental constraint;
2. missing_atomic_proposition: a state or event needed to express the mission;
3. contradiction: conflicting assumptions or requirements exposed by the result/counter-strategy;
4. unjustified_strengthening: an assumption that makes synthesis easier but is not justified by the mission.

For every finding, connect the evidence to the Goal Model or mission text. Candidate changes are suggestions only:
the human engineer must decide whether to implement them. Do not claim that Slugs proves semantic correctness.
Use concise evidence and rationale instead of hidden chain-of-thought.
Return at most three findings; keep each evidence and rationale under 40 words.

Return ONLY valid JSON with this shape:
{{
  "summary": "short overall assessment",
  "findings": [
    {{
      "category": "missing_assumption|missing_atomic_proposition|contradiction|unjustified_strengthening",
      "severity": "low|medium|high",
      "evidence": "specific formula, variable, or counter-strategy state",
      "rationale": "why this matters for the mission",
      "proposal": {{
        "action": "add_assumption|add_atomic_proposition|revise_assumption|none",
        "header": "proposed or existing name",
        "informal_def": "human-readable proposal",
        "formal_def": "candidate one-line formula or empty string",
        "variables": ["existing_or_proposed_variable"]
      }},
      "confidence": 0.0
    }}
  ],
  "human_questions": ["questions the engineer should answer before editing"]
}}

GOAL MODEL:
{goal_model}

STRUCTURED GR(1) MODEL:
{context['structured_slugs']}

NATURAL-LANGUAGE MISSION:
{context['mission_text']}

SLUGS INPUT:
{context['slugs_input']}

REALIZABILITY RESULT:
{context['realizability_result']}

COUNTER-STRATEGY:
{context['counter_strategy_excerpt'] or 'Not available because the specification is realizable.'}
"""


def ask_ollama(model, endpoint, context, include_goal_model=True):
    prompt = build_review_prompt(context, include_goal_model)
    payload = json.dumps({
        "model": model,
        "stream": False,
        "format": "json",
        "messages": [
            {"role": "system", "content": "Return only the requested JSON review report."},
            {"role": "user", "content": prompt},
        ],
        "options": {
            "temperature": 0.1,
            "num_ctx": context["context_size"],
            "num_predict": 2000,
        },
        "think": False,
    }).encode("utf-8")
    request = urllib.request.Request(
        endpoint.rstrip("/") + "/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        details = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Ollama rejected the review request ({error.code}): {details or error.reason}"
        ) from error
    except (urllib.error.URLError, TimeoutError) as error:
        raise RuntimeError(f"Could not reach Ollama at {endpoint}: {error}") from error
    return parse_json_response(result.get("message", {}).get("content", ""))


def write_markdown(report, output_file):
    lines = ["# LLM Specification Review", "", report.get("summary", ""), ""]
    findings = report.get("findings", [])
    if not findings:
        lines.append("No findings were returned.")
    for index, finding in enumerate(findings, start=1):
        lines.extend([
            f"## {index}. {finding.get('category', 'unknown')} ({finding.get('severity', 'unknown')})",
            f"**Evidence:** {finding.get('evidence', '')}",
            f"**Rationale:** {finding.get('rationale', '')}",
            "**Candidate proposal:**",
            "```json",
            json.dumps(finding.get("proposal", {}), indent=2),
            "```",
            f"**Confidence:** {finding.get('confidence', '')}",
            "",
        ])
    lines.append("## Human Review Questions")
    lines.extend(f"- {question}" for question in report.get("human_questions", []))
    Path(output_file).write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_review(args):
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

    counter_strategy_excerpt = counter_strategy
    if len(counter_strategy_excerpt) > args.counter_strategy_chars:
        counter_strategy_excerpt = (
            counter_strategy_excerpt[: args.counter_strategy_chars]
            + "\n[repetitive counter-strategy states omitted; see counter_strategy.txt]\n"
        )

    context = {
        "mission_text": Path(args.mission_text).read_text(encoding="utf-8"),
        "goal_model": Path(args.goal_model).read_text(encoding="utf-8"),
        "structured_slugs": Path(args.structured_slugs).read_text(encoding="utf-8"),
        "slugs_input": Path(args.slugs_input).read_text(encoding="utf-8"),
        "realizability_result": slugs_output,
        "counter_strategy": counter_strategy,
        "counter_strategy_excerpt": counter_strategy_excerpt,
        "context_size": args.context_size,
    }
    report = ask_ollama(
        args.model,
        args.endpoint,
        context,
        include_goal_model=args.include_goal_model,
    )
    report["realizability"] = status
    report["model"] = args.model
    Path(args.report).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    write_markdown(report, args.markdown)
    print(f"LLM review written to {args.report} and {args.markdown}")
    print(f"Review status: {status}; findings: {len(report.get('findings', []))}")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Review a GR(1) specification with Ollama without changing it."
    )
    parser.add_argument("--goal-model", default="LabSamples.gm")
    parser.add_argument("--mission-text", default="LabSamplesNL.txt")
    parser.add_argument("--counter-strategy", default="counter_strategy.txt")
    parser.add_argument("--counter-strategy-chars", type=int, default=3500)
    parser.add_argument("--structured-slugs", default="LabSamples.structuredslugs")
    parser.add_argument("--slugs-input", default="LabSamples.slugsin")
    parser.add_argument("--parser", default="slugs/tools/StructuredSlugsParser/compiler.py")
    parser.add_argument("--slugs", default="./slugs/src/slugs")
    parser.add_argument("--model", default="gemma4:26b")
    parser.add_argument(
        "--without-goal-model",
        dest="include_goal_model",
        action="store_false",
        help="Omit the Goal Model from the LLM prompt while still using it for compilation.",
    )
    parser.set_defaults(include_goal_model=True)
    parser.add_argument("--endpoint", default="http://127.0.0.1:11434")
    parser.add_argument("--context-size", type=int, default=8192)
    parser.add_argument("--report", default="llm_review.json")
    parser.add_argument("--markdown", default="llm_review.md")
    return run_review(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())

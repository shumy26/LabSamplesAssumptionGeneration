import argparse
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

from mission_config import mission_paths


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
    return output


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


def build_review_prompt(context, include_goal_model=True, included_documents=None):
    included_documents = included_documents or {}

    def document(name, label):
        if included_documents.get(name, True):
            return context[name]
        return f"[{label} omitted from this review variant.]"

    goal_model = document("goal_model", "Goal Model") if include_goal_model else "[Goal Model omitted from this review variant.]"

    return f"""You are an expert reviewer for a research workflow translating robotic mission descriptions into GR(1) specifications via KAOS Goal Models.

Use every supplied artifact as evidence: the natural-language mission, Goal Model, design rules, compiler contract, generated structured SLUGS model, parser output, realizability result, and counter-strategy. Do not assume that a realizable specification is physically correct. The generated artifacts describe what the current workflow actually synthesizes; identify mismatches between them and the mission.

Critically evaluate the specification for operational resilience against non-ideal physical realities. Real-world environments are imperfect: sensors may fail to read, human operators may abandon tasks, and expected environmental triggers might never occur. Look for missing timeouts, fallback states, or boundary conditions needed to prevent the system from getting permanently stuck (starvation) when the ideal sequence of events is disrupted.

Identify at most three findings. Prioritize missing assumptions and missing atomic propositions that are directly supported by the supplied artifacts.

Identify all your findings from these categories:
1. missing_assumption: an omitted physical constraint (e.g., location mutex).
2. missing_atomic_proposition: a system state or event needed but not defined.
3. contradiction: conflicting assumptions or requirements causing unrealizability.
4. unjustified_strengthening: an assumption that forces realizability by cheating physics or ignoring the mission.

Return ONLY valid JSON. Give concise, externally checkable reasoning in each finding; do not provide hidden chain-of-thought or a state-by-state private trace.

JSON SCHEMA:
{{
  "summary": "Short overall assessment of the specification's health.",
  "findings": [
    {{
      "category": "missing_assumption|missing_atomic_proposition|contradiction|unjustified_strengthening",
      "severity": "low|medium|high",
      "evidence": "Specific formula, variable, or step in the counter-strategy.",
      "rationale": "Why this matters for the robot's physical mission.",
      "proposal": {{
        "action": "add_assumption|add_atomic_proposition|revise_assumption|none",
                "header": "One accepted KAOS header, or an existing header to revise",
                "informal_def": "Human-readable proposal consistent with the mission",
                "formal_def": "One-line candidate formula accepted by the compiler, or empty string",
        "variables": ["list_of_variables"]
            }},
            "confidence": 0.0
    }}
  ],
  "human_questions": ["Questions the engineer must answer before accepting these changes."]
}}

DESIGN AND COMPILATION RULES FROM rulesgm.txt:
{document('rules', 'Design and compilation rules')}

NATURAL-LANGUAGE MISSION:
{document('mission_text', 'Natural-language mission')}

GOAL MODEL:
{goal_model}

GENERATED STRUCTURED SLUGS MODEL:
{document('structured_slugs', 'Generated structured SLUGS model')}

PARSER OUTPUT:
{document('slugs_input', 'Parser output')}

REALIZABILITY RESULT:
{context['realizability_result']}

COUNTER-STRATEGY:
{document('counter_strategy_excerpt', 'Counter-strategy') if context['counter_strategy_excerpt'] else 'Not available. Specification is realizable.'}

PROPOSAL REQUIREMENTS:
- Match the existing Goal Model vocabulary and the design rules; do not invent a different modeling style.
- Put environment facts and physical responses under Assumption, and controller commands under Goal, with ownership established by Initialization. An Environment 'Assumption' can ONLY guarantee environment inputs. It CANNOT force a system output (like load_machine) to occur. A System 'Goal' CANNOT force an environment input to occur.
- Preserve the current workflow's exact accepted headers and one-line FormalDef syntax.
- Prefer the smallest change that explains the evidence. If a proposed variable is necessary, specify its owner, initialization value, and where it belongs in the Goal Model.
- Never propose a formula that the compiler contract rejects or that would make the model realizable by assuming away the mission.
"""


REVIEW_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "findings", "human_questions"],
    "properties": {
        "summary": {"type": "string"},
        "findings": {
            "type": "array",
            "maxItems": 3,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["category", "severity", "evidence", "rationale", "proposal", "confidence"],
                "properties": {
                    "category": {
                        "type": "string",
                        "enum": [
                            "missing_assumption",
                            "missing_atomic_proposition",
                            "contradiction",
                            "unjustified_strengthening",
                        ],
                    },
                    "severity": {"type": "string", "enum": ["low", "medium", "high"]},
                    "evidence": {"type": "string"},
                    "rationale": {"type": "string"},
                    "proposal": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["action", "header", "informal_def", "formal_def", "variables"],
                        "properties": {
                            "action": {
                                "type": "string",
                                "enum": [
                                    "add_assumption",
                                    "add_atomic_proposition",
                                    "revise_assumption",
                                    "none",
                                ],
                            },
                            "header": {"type": "string"},
                            "informal_def": {"type": "string"},
                            "formal_def": {"type": "string"},
                            "variables": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
            },
        },
        "human_questions": {
            "type": "array",
            "maxItems": 5,
            "items": {"type": "string"},
        },
    },
}


def request_ollama(model, endpoint, messages, context_size):
    payload = json.dumps({
        "model": model,
        "stream": False,
        "format": REVIEW_RESPONSE_SCHEMA,
        "messages": messages,
        "options": {
            "temperature": 0.1,
            "num_ctx": context_size,
            "num_predict": 3000,
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
    return result.get("message", {}).get("content", "")


def ask_ollama(model, endpoint, context, include_goal_model=True, included_documents=None):
    prompt = build_review_prompt(context, include_goal_model, included_documents)
    content = request_ollama(
        model,
        endpoint,
        [
            {"role": "system", "content": "Return only the requested JSON review report."},
            {"role": "user", "content": prompt},
        ],
        context["context_size"],
    )
    try:
        return parse_json_response(content)
    except (ValueError, json.JSONDecodeError) as first_error:
        repair_prompt = f"""Convert the following malformed model response into one valid JSON object matching the required review schema.
Do not add analysis, markdown, comments, or new findings. Preserve the available findings and use empty strings or empty arrays only when a required field is missing.

MALFORMED RESPONSE:
{content}
"""
        repaired_content = request_ollama(
            model,
            endpoint,
            [
                {"role": "system", "content": "Return only valid JSON matching the supplied response schema."},
                {"role": "user", "content": repair_prompt},
            ],
            context["context_size"],
        )
        try:
            return parse_json_response(repaired_content)
        except (ValueError, json.JSONDecodeError) as repair_error:
            raise ValueError(
                f"Model returned invalid JSON and the repair pass also failed: {repair_error}"
            ) from first_error


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
    parser_output = compile_goal_model(args.goal_model, args.structured_slugs, args.parser)
    Path(args.slugs_input).write_text(parser_output, encoding="utf-8")
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
        "rules": Path(args.rules_file).read_text(encoding="utf-8"),
        "context_size": args.context_size,
    }
    report = ask_ollama(
        args.model,
        args.endpoint,
        context,
        include_goal_model=args.include_goal_model,
        included_documents={
            "mission_text": args.include_mission_text,
            "rules": args.include_rules,
            "structured_slugs": args.include_structured_slugs,
            "slugs_input": args.include_slugs_input,
            "counter_strategy_excerpt": args.include_counter_strategy,
        },
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
    parser.add_argument("--mission", default="LabSamples", help="Mission name or mission package directory.")
    parser.add_argument("--goal-model")
    parser.add_argument("--mission-text")
    parser.add_argument("--counter-strategy")
    parser.add_argument("--counter-strategy-chars", type=int, default=3500)
    parser.add_argument("--rules-file", default="rulesgm.txt")
    parser.add_argument("--structured-slugs")
    parser.add_argument("--slugs-input")
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
    for option, destination, label in [
        ("mission-text", "include_mission_text", "Omit the natural-language mission"),
        ("rules", "include_rules", "Omit the design and compilation rules"),
        ("structured-slugs", "include_structured_slugs", "Omit the generated structured SLUGS model"),
        ("slugs-input", "include_slugs_input", "Omit the parser output"),
        ("counter-strategy", "include_counter_strategy", "Omit the counter-strategy"),
    ]:
        parser.add_argument(
            f"--without-{option}",
            dest=destination,
            action="store_false",
            help=label + ".",
        )
        parser.set_defaults(**{destination: True})
    parser.add_argument("--endpoint", default="http://127.0.0.1:11434")
    parser.add_argument("--context-size", type=int, default=16384)
    parser.add_argument("--report", default="llm_review.json")
    parser.add_argument("--markdown", default="llm_review.md")
    args = parser.parse_args()
    paths = mission_paths(args.mission)
    args.goal_model = args.goal_model or str(paths["goal_model"])
    args.mission_text = args.mission_text or str(paths["mission_text"])
    args.counter_strategy = args.counter_strategy or str(paths["counter_strategy"])
    args.structured_slugs = args.structured_slugs or str(paths["structured_slugs"])
    args.slugs_input = args.slugs_input or str(paths["slugs_input"])
    return run_review(args)


if __name__ == "__main__":
    raise SystemExit(main())

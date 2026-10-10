import html
import json
import math
import statistics
import sys
import argparse
from pathlib import Path

from scipy.stats import ttest_rel


def percent(value):
    return f"{value * 100:.0f}%"


def score(value):
    return f"{value:.2f}"


def signed_delta(without_goal, with_goal, formatter):
    return formatter(without_goal - with_goal)


def p_value_label(value):
    if value is None:
        return "n/a"
    if value < 0.001:
        return "< 0.001"
    return f"{value:.3f}"


def delta_class(value):
    if value < 0:
        return "helped"
    if value > 0:
        return "hurt"
    return "neutral"


def collect_conditions(data):
    conditions = {}
    for condition in data.get("by_condition", []):
        removed = condition.get("removed_assumptions", [])
        if len(removed) != 1 or condition.get("omitted_documents", []):
            continue
        conditions.setdefault(removed[0], {})[condition.get("include_goal_model", False)] = condition
    return conditions


def print_scorecard(data):
    conditions = collect_conditions(data)
    print("# Goal Model Ablation Results")
    print()
    print("_A compact scorecard: higher is better. Delta means without Goal Model minus with Goal Model._")
    print()
    print("| Omitted assumption | Any credit: without | Any credit: with | Delta | Graded credit: without | Graded credit: with | Delta | Strict: without | Strict: with |")
    print("| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")

    rows = []
    for assumption, variants in sorted(conditions.items()):
        without = variants.get(False, {})
        with_goal = variants.get(True, {})
        without_any = without.get("any_credit_rate", 0.0)
        with_any = with_goal.get("any_credit_rate", 0.0)
        without_graded = without.get("mean_graded_credit", 0.0)
        with_graded = with_goal.get("mean_graded_credit", 0.0)
        without_strict = without.get("strict_correctness_rate", 0.0)
        with_strict = with_goal.get("strict_correctness_rate", 0.0)
        rows.append({
            "assumption": assumption,
            "any_without": without_any,
            "any_with": with_any,
            "graded_without": without_graded,
            "graded_with": with_graded,
            "strict_without": without_strict,
            "strict_with": with_strict,
        })
        print(
            f"| **{assumption}** | {percent(without_any)} | {percent(with_any)} | "
            f"{signed_delta(without_any, with_any, percent)} | "
            f"{score(without_graded)} | {score(with_graded)} | "
            f"{signed_delta(without_graded, with_graded, score)} | "
            f"{percent(without_strict)} | {percent(with_strict)} |"
        )

    if rows:
        average = lambda key: sum(row[key] for row in rows) / len(rows)
        print(
            f"| **AVERAGE** | **{percent(average('any_without'))}** | "
            f"**{percent(average('any_with'))}** | "
            f"**{signed_delta(average('any_without'), average('any_with'), percent)}** | "
            f"**{score(average('graded_without'))}** | **{score(average('graded_with'))}** | "
            f"**{signed_delta(average('graded_without'), average('graded_with'), score)}** | "
            f"**{percent(average('strict_without'))}** | **{percent(average('strict_with'))}** |"
        )
    else:
        print("| _No single-assumption results found._ | | | | | | | | |")


def paired_test(pairs, metric):
    if len(pairs) < 2:
        return {"insufficient_pairs": True}

    without_goal = [float(pair[False].get(metric, 0.0) or 0.0) for pair in pairs]
    with_goal = [float(pair[True].get(metric, 0.0) or 0.0) for pair in pairs]
    differences = [without - with_goal for without, with_goal in zip(without_goal, with_goal)]
    variance = statistics.pvariance(differences)
    if math.isclose(variance, 0.0, abs_tol=1e-12):
        t_statistic = None if differences[0] else 0.0
        p_value = 0.0 if differences[0] else 1.0
        infinite = bool(differences[0])
    else:
        result = ttest_rel(without_goal, with_goal)
        t_statistic = round(float(result.statistic), 4)
        p_value = round(float(result.pvalue), 6)
        infinite = False

    return {
        "mean_difference_without_minus_with": round(statistics.mean(differences), 4),
        "t_statistic": t_statistic,
        "t_statistic_is_infinite": infinite,
        "degrees_of_freedom": len(pairs) - 1,
        "p_value_two_sided": p_value,
    }


def derive_significance(data):
    grouped = {}
    for result in data.get("results", []):
        if (
            not result.get("review_available", False)
            or result.get("omitted_documents", [])
            or not result.get("removed_assumptions", [])
        ):
            continue
        removed = tuple(result.get("removed_assumptions", []))
        trial = result.get("trial")
        grouped.setdefault(removed, {}).setdefault(trial, {})[
            bool(result.get("include_goal_model", False))
        ] = result.get("metrics", {})

    tests = []
    for removed, trials in sorted(grouped.items(), key=str):
        pairs = [pair for pair in trials.values() if True in pair and False in pair]
        tests.append({
            "removed_assumptions": list(removed),
            "paired_trials": len(pairs),
            "any_credit": paired_test(pairs, "any_credit"),
            "graded_credit": paired_test(pairs, "graded_credit"),
            "strict_correctness": paired_test(pairs, "strict_correctness"),
        })
    return tests


def print_significance(data):
    print()
    print("## Statistical Significance")
    print()
    print("_Paired Student t-tests across trials. Negative delta means the Goal Model helped._")
    print()
    print("| Omitted assumption | Metric | Trials | Mean delta | t | df | p-value |")
    print("| :--- | :--- | ---: | ---: | ---: | ---: | ---: |")

    found = False
    tests = data.get("goal_model_significance") or derive_significance(data)
    for test in tests:
        assumption = ", ".join(test.get("removed_assumptions", [])) or "All assumptions"
        for metric, label in (("any_credit", "Any credit"), ("graded_credit", "Graded credit"), ("strict_correctness", "Strict correctness")):
            result = test.get(metric, {})
            if result.get("insufficient_pairs"):
                print(f"| **{assumption}** | {label} | < 2 | n/a | n/a | n/a | n/a |")
                continue
            found = True
            print(
                f"| **{assumption}** | {label} | {test.get('paired_trials', 0)} | "
                f"{result.get('mean_difference_without_minus_with', 0):+.2f} | "
                f"{result.get('t_statistic', 'n/a') if result.get('t_statistic') is not None else 'infinite'} | "
                f"{result.get('degrees_of_freedom', 'n/a')} | {p_value_label(result.get('p_value_two_sided'))} |"
            )
    if not found:
        print("| _No per-trial results found; significance cannot be reconstructed._ | | | | | | |")


def significance_rows(data):
    tests = data.get("goal_model_significance") or derive_significance(data)
    rows = []
    for test in tests:
        assumption = ", ".join(test.get("removed_assumptions", [])) or "All assumptions"
        for metric, label in (
            ("any_credit", "Any credit"),
            ("graded_credit", "Graded credit"),
            ("strict_correctness", "Strict correctness"),
        ):
            result = test.get(metric, {})
            rows.append({
                "assumption": assumption,
                "metric": label,
                "trials": test.get("paired_trials", 0),
                "delta": result.get("mean_difference_without_minus_with"),
                "t": result.get("t_statistic"),
                "df": result.get("degrees_of_freedom"),
                "p": result.get("p_value_two_sided"),
                "insufficient": (
                    result.get("insufficient_pairs", False)
                    or result.get("mean_difference_without_minus_with") is None
                ),
            })
    return rows


def render_html(data):
    conditions = collect_conditions(data)
    score_rows = []
    for assumption, variants in sorted(conditions.items()):
        without = variants.get(False, {})
        with_goal = variants.get(True, {})
        row = {
            "assumption": assumption,
            "any_without": without.get("any_credit_rate", 0.0),
            "any_with": with_goal.get("any_credit_rate", 0.0),
            "graded_without": without.get("mean_graded_credit", 0.0),
            "graded_with": with_goal.get("mean_graded_credit", 0.0),
            "strict_without": without.get("strict_correctness_rate", 0.0),
            "strict_with": with_goal.get("strict_correctness_rate", 0.0),
        }
        row["any_delta"] = row["any_without"] - row["any_with"]
        row["graded_delta"] = row["graded_without"] - row["graded_with"]
        score_rows.append(row)

    if score_rows:
        average = {"assumption": "AVERAGE"}
        for key in ("any_without", "any_with", "graded_without", "graded_with", "strict_without", "strict_with"):
            average[key] = sum(row[key] for row in score_rows) / len(score_rows)
        average["any_delta"] = average["any_without"] - average["any_with"]
        average["graded_delta"] = average["graded_without"] - average["graded_with"]
        score_rows.append(average)

    score_html = []
    for row in score_rows:
        row_class = " class='average'" if row["assumption"] == "AVERAGE" else ""
        score_html.append(
            f"<tr{row_class}><th>{html.escape(row['assumption'])}</th>"
            f"<td>{percent(row['any_without'])}</td><td>{percent(row['any_with'])}</td>"
            f"<td class='{delta_class(row['any_delta'])}'>{signed_delta(row['any_without'], row['any_with'], percent)}</td>"
            f"<td>{score(row['graded_without'])}</td><td>{score(row['graded_with'])}</td>"
            f"<td class='{delta_class(row['graded_delta'])}'>{signed_delta(row['graded_without'], row['graded_with'], score)}</td>"
            f"<td>{percent(row['strict_without'])}</td><td>{percent(row['strict_with'])}</td></tr>"
        )
    if not score_html:
        score_html.append("<tr><td colspan='9' class='empty'>No single-assumption results found.</td></tr>")

    significance_html = []
    for row in significance_rows(data):
        if row["insufficient"]:
            cells = "<td colspan='4' class='muted'>insufficient paired trials</td>"
        else:
            p = row["p"]
            p_class = "significant" if p is not None and p < 0.05 else ""
            t_value = "infinite" if row["t"] is None else f"{row['t']:.4f}"
            cells = (
                f"<td class='{delta_class(row['delta'])}'>{row['delta']:+.2f}</td>"
                f"<td>{t_value}</td><td>{row['df']}</td>"
                f"<td class='{p_class}'>{p_value_label(p)}</td>"
            )
        significance_html.append(
            f"<tr><th>{html.escape(row['assumption'])}</th><td>{html.escape(row['metric'])}</td>"
            f"<td>{row['trials']}</td>{cells}</tr>"
        )
    if not significance_html:
        significance_html.append("<tr><td colspan='7' class='empty'>No significance tests found.</td></tr>")

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Goal Model Ablation Results</title>
<style>
:root {{ --ink:#182230; --muted:#687587; --line:#dfe5ec; --paper:#f5f7fb; --card:#fff; --good:#167653; --good-bg:#e8f6ef; --bad:#ad3d35; --bad-bg:#fff0ee; }}
* {{ box-sizing:border-box; }} body {{ margin:0; color:var(--ink); background:var(--paper); font:15px/1.5 Georgia,serif; }}
main {{ max-width:1440px; margin:auto; padding:48px 28px 72px; }}
.eyebrow {{ color:#315c9b; font:700 12px system-ui,sans-serif; letter-spacing:.12em; text-transform:uppercase; }}
h1 {{ margin:8px 0; font-size:clamp(30px,4vw,52px); line-height:1.05; }} h2 {{ margin:0 0 5px; font-size:24px; }}
.intro,.note {{ color:var(--muted); }} .intro {{ max-width:720px; font-size:17px; }}
section {{ margin-top:28px; padding:24px; background:var(--card); border:1px solid var(--line); border-radius:10px; box-shadow:0 8px 24px rgba(30,48,72,.06); overflow:auto; }}
table {{ width:100%; border-collapse:collapse; min-width:760px; font:13px system-ui,sans-serif; }}
th,td {{ padding:12px 10px; border-bottom:1px solid var(--line); text-align:right; white-space:nowrap; }} th:first-child,td:first-child {{ text-align:left; }}
thead th {{ color:var(--muted); font-size:11px; letter-spacing:.05em; text-transform:uppercase; border-bottom:2px solid var(--ink); }}
tbody th {{ font-weight:650; }} tbody tr:hover {{ background:#f8fafc; }} .average {{ background:#f0f4fa; font-weight:700; }}
.helped {{ color:var(--good); background:var(--good-bg); font-weight:700; }} .hurt {{ color:var(--bad); background:var(--bad-bg); font-weight:700; }}
.neutral,.muted,.empty {{ color:var(--muted); }} .significant {{ color:var(--good); font-weight:800; }} .empty {{ text-align:center !important; }}
footer {{ margin-top:24px; color:var(--muted); font:12px system-ui,sans-serif; }}
</style></head><body><main>
<header><div class="eyebrow">Experiment report</div><h1>Goal Model Ablation Results</h1>
<p class="intro">A comparison of review quality with and without the Goal Model. Negative deltas mean the Goal Model improved the score.</p></header>
<section><h2>Scorecard</h2><p class="note">Higher scores are better. Green deltas favor including the Goal Model.</p>
<table><thead><tr><th>Omitted assumption</th><th>Any credit<br>without</th><th>Any credit<br>with</th><th>Delta</th><th>Graded<br>without</th><th>Graded<br>with</th><th>Delta</th><th>Strict<br>without</th><th>Strict<br>with</th></tr></thead><tbody>{''.join(score_html)}</tbody></table></section>
<section><h2>Statistical significance</h2><p class="note">Paired Student t-tests across trials. P-values below 0.05 are highlighted.</p>
<table><thead><tr><th>Omitted assumption</th><th>Metric</th><th>Trials</th><th>Mean delta</th><th>t</th><th>df</th><th>p-value</th></tr></thead><tbody>{''.join(significance_html)}</tbody></table></section>
<footer>Generated by information_extraction.py</footer></main></body></html>"""


def process_metrics(filepath, html_filepath="experiment_results.html"):
    try:
        with open(filepath, encoding="utf-8") as stream:
            data = json.load(stream)
    except (OSError, json.JSONDecodeError) as error:
        print(f"Error loading JSON: {error}")
        return

    print_scorecard(data)
    print_significance(data)
    Path(html_filepath).write_text(render_html(data), encoding="utf-8")
    print(f"\nHTML report written to {html_filepath}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Render an experiment metrics JSON as an HTML dashboard.")
    parser.add_argument("metrics", nargs="?", help="Metrics JSON path.")
    parser.add_argument("html", nargs="?", help="HTML dashboard path.")
    parser.add_argument("--mission", help="Use the mission package's default metrics and dashboard paths.")
    arguments = parser.parse_args()
    if arguments.mission:
        from mission_config import mission_paths

        mission = mission_paths(arguments.mission)
        metrics_file = arguments.metrics or str(mission["directory"] / "experiment_metrics.json")
        html_file = arguments.html or str(mission["directory"] / "experiment_results.html")
    else:
        metrics_file = arguments.metrics or "experiment_metrics.json"
        html_file = arguments.html or "experiment_results.html"
    process_metrics(metrics_file, html_file)
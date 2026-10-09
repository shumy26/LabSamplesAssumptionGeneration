import json
import sys


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


def print_significance(data):
    print()
    print("## Statistical Significance")
    print()
    print("_Paired Student t-tests across trials. Negative delta means the Goal Model helped._")
    print()
    print("| Omitted assumption | Metric | Trials | Mean delta | t | df | p-value |")
    print("| :--- | :--- | ---: | ---: | ---: | ---: | ---: |")

    found = False
    for test in data.get("goal_model_significance", []):
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
        print("| _No significance tests found. Run the updated experiment first._ | | | | | | |")


def process_metrics(filepath):
    try:
        with open(filepath, encoding="utf-8") as stream:
            data = json.load(stream)
    except (OSError, json.JSONDecodeError) as error:
        print(f"Error loading JSON: {error}")
        return

    print_scorecard(data)
    print_significance(data)


if __name__ == "__main__":
    process_metrics(sys.argv[1] if len(sys.argv) > 1 else "experiment_metrics.json")
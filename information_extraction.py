import json

def process_metrics(filepath):
    try:
        with open(filepath, 'r') as f:
            data = json.load(f)
    except Exception as e:
        print(f"Error loading JSON: {e}")
        return

    results = []

    for condition in data.get('by_condition', []):
        removed_assumptions = condition.get('removed_assumptions', [])

        # Only process conditions where exactly one assumption was removed
        if len(removed_assumptions) != 1:
            continue

        omitted_documents = condition.get('omitted_documents', [])

        # Skip rows where additional documents were omitted to keep the table clean
        if omitted_documents:
            continue

        assumption = removed_assumptions[0]
        has_goal_model = condition.get('include_goal_model', False)

        # Extract metrics
        semantic_rate = condition.get('any_credit_rate', 0.0) * 100
        mean_credit = condition.get('mean_graded_credit', 0.0)
        strict_rate = condition.get('strict_correctness_rate', 0.0) * 100

        # Find or create the dictionary for this assumption
        entry = next((item for item in results if item["assumption"] == assumption), None)
        if not entry:
            entry = {
                "assumption": assumption,
                "semantic_wo": 0.0,
                "semantic_w": 0.0,
                "mean_credit_wo": 0.0,
                "mean_credit_w": 0.0,
                "strict_wo": 0.0,
                "strict_w": 0.0
            }
            results.append(entry)

        # Populate the specific column data based on the goal model condition
        if has_goal_model:
            entry["semantic_w"] = semantic_rate
            entry["mean_credit_w"] = mean_credit
            entry["strict_w"] = strict_rate
        else:
            entry["semantic_wo"] = semantic_rate
            entry["mean_credit_wo"] = mean_credit
            entry["strict_wo"] = strict_rate

    # Print the Markdown table
    print("| Omitted Assumption | Any Credit (w/o Goal Model) | Any Credit (w/ Goal Model) | Graded Credit (w/o Goal Model) | Graded Credit (w/ Goal Model) | Strict Correctness (w/o Goal Model) | Strict Correctness (w/ Goal Model) |")
    print("| :--- | :--- | :--- | :--- | :--- | :--- | :--- |")

    # Variables to accumulate sums for the average row
    sum_semantic_wo = 0
    sum_semantic_w = 0
    sum_mean_credit_wo = 0
    sum_mean_credit_w = 0
    sum_strict_wo = 0
    sum_strict_w = 0

    for row in sorted(results, key=lambda x: x["assumption"]):
        print(f"| **{row['assumption']}** | {row['semantic_wo']:.0f}% | {row['semantic_w']:.0f}% | {row['mean_credit_wo']:.2f} | {row['mean_credit_w']:.2f} | {row['strict_wo']:.0f}% | {row['strict_w']:.0f}% |")
        
        sum_semantic_wo += row['semantic_wo']
        sum_semantic_w += row['semantic_w']
        sum_mean_credit_wo += row['mean_credit_wo']
        sum_mean_credit_w += row['mean_credit_w']
        sum_strict_wo += row['strict_wo']
        sum_strict_w += row['strict_w']

    # Calculate and print the average row
    n = len(results)
    if n > 0:
        print(f"| **AVERAGE** | **{sum_semantic_wo/n:.0f}%** | **{sum_semantic_w/n:.0f}%** | **{sum_mean_credit_wo/n:.2f}** | **{sum_mean_credit_w/n:.2f}** | **{sum_strict_wo/n:.0f}%** | **{sum_strict_w/n:.0f}%** |")

if __name__ == "__main__":
    process_metrics("experiment_metrics.json")
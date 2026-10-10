#!/usr/bin/env bash
set -euo pipefail

mission="${1:-LabSamples}"
if [[ $# -gt 0 ]]; then
    shift
fi

if [[ -f "missions/$mission/goal_model.gm" ]]; then
    mission_dir="missions/$mission"
    goal_model="$mission_dir/goal_model.gm"
    structured_slugs="$mission_dir/model.structuredslugs"
    slugs_input="$mission_dir/model.slugsin"
    controller="$mission_dir/controller.json"
    counter_strategy="$mission_dir/counter_strategy.txt"
else
    goal_model="$mission.gm"
    structured_slugs="$mission.structuredslugs"
    slugs_input="$mission.slugsin"
    controller="controller.json"
    counter_strategy="counter_strategy.txt"
fi

python3 compile_to_gr1.py "$goal_model" "$structured_slugs"
python3 slugs/tools/StructuredSlugsParser/compiler.py "$structured_slugs" > "$slugs_input"

echo "Checking realizability..."

CHECK_REALIZABILITY=$(./slugs/src/slugs "$slugs_input" 2>&1)

# 3. Route the execution based on the result
if [[ "$CHECK_REALIZABILITY" == *"Specification is realizable"* ]]; then
    echo "Result: REALIZABLE! Synthesizing controller..."
    ./slugs/src/slugs --explicitStrategy --jsonOutput "$slugs_input" > "$controller"
    echo "Success: Saved to $controller"
    python3 llm_checker.py \
        --mission "$mission" \
        --goal-model "$goal_model" \
        --structured-slugs "$structured_slugs" \
        --slugs-input "$slugs_input" \
        --counter-strategy "$counter_strategy" \
        --model "${OLLAMA_MODEL:-gemma4:26b}"

elif [[ "$CHECK_REALIZABILITY" == *"Specification is unrealizable"* ]]; then
    echo "Result: UNREALIZABLE! Extracting counter-strategy..."
    ./slugs/src/slugs --counterStrategy "$slugs_input" > "$counter_strategy"
    echo "Failed: Environment winning strategy saved to $counter_strategy"
    python3 llm_checker.py \
        --mission "$mission" \
        --goal-model "$goal_model" \
        --structured-slugs "$structured_slugs" \
        --slugs-input "$slugs_input" \
        --counter-strategy "$counter_strategy" \
        --model "${OLLAMA_MODEL:-gemma4:26b}"

else
    echo "An unexpected error occurred during the SLUGS check:"
    echo "$CHECK_REALIZABILITY"
fi

##!/usr/bin/env bash

python3 compile_to_gr1.py
python3 slugs/tools/StructuredSlugsParser/compiler.py LabSamples.structuredslugs > LabSamples.slugsin

echo "Checking realizability..."

CHECK_REALIZABILITY=$(./slugs/src/slugs LabSamples.slugsin 2>&1)

# 3. Route the execution based on the result
if [[ "$CHECK_REALIZABILITY" == *"Specification is realizable"* ]]; then
    echo "Result: REALIZABLE! Synthesizing controller..."
    ./slugs/src/slugs --explicitStrategy --jsonOutput LabSamples.slugsin > controller.json
    echo "Success: Saved to controller.json"

elif [[ "$CHECK_REALIZABILITY" == *"Specification is unrealizable"* ]]; then
    echo "Result: UNREALIZABLE! Extracting counter-strategy..."
    ./slugs/src/slugs --counterStrategy LabSamples.slugsin > counter_strategy.txt
    echo "Failed: Environment winning strategy saved to counter_strategy.txt"

else
    echo "An unexpected error occurred during the SLUGS check:"
    echo "$CHECK_REALIZABILITY"
fi

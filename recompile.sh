##!/usr/bin/env bash

# 1. Compile the specification
python3 compile_to_gr1.py
python3 slugs/tools/StructuredSlugsParser/compiler.py LabSamples.structuredslugs > LabSamples.slugsin

echo "Checking realizability..."

# 2. Run a dry-pass to check realizability and capture BOTH stdout and stderr (2>&1)
CHECK_RESULT=$(./slugs/src/slugs LabSamples.slugsin 2>&1)

# 3. Route the execution based on the result
if [[ "$CHECK_RESULT" == *"Specification is realizable"* ]]; then
    echo "Result: REALIZABLE! Synthesizing controller..."
    ./slugs/src/slugs --explicitStrategy --jsonOutput LabSamples.slugsin > controller.json
    echo "Success: Saved to controller.json"

elif [[ "$CHECK_RESULT" == *"Specification is unrealizable"* ]]; then
    echo "Result: UNREALIZABLE! Extracting counter-strategy..."
    ./slugs/src/slugs --counterStrategy LabSamples.slugsin > counter_strategy.txt
    echo "Failed: Environment winning playbook saved to counter_strategy.txt"

else
    echo "An unexpected error occurred during the SLUGS check:"
    echo "$CHECK_RESULT"
fi

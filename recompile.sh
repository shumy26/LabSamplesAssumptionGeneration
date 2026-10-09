#!/usr/bin/env bash

python3 compile_to_spectra.py LabSamples.gm LabSamples.spectra

echo "Checking realizability..."

cd spectra
CHECK_REALIZABILITY=$(java -jar spectra-cli.jar -i ../LabSamples.spectra --counter-strategy 2>&1)
cd ..

if [[ "$CHECK_REALIZABILITY" == *"Specification is realizable"* ]]; then
    echo "Result: REALIZABLE!"
    echo "$CHECK_REALIZABILITY" > counter_strategy.txt
    python3 llm_checker.py \
        --model "${OLLAMA_MODEL:-gemma4:26b}"

elif [[ "$CHECK_REALIZABILITY" == *"Specification is unrealizable"* ]]; then
    echo "Result: UNREALIZABLE! Extracting counter-strategy..."
    echo "$CHECK_REALIZABILITY" > counter_strategy.txt
    echo "Failed: Environment winning strategy saved to counter_strategy.txt"
    python3 llm_checker.py \
        --model "${OLLAMA_MODEL:-gemma4:26b}"

else
    echo "An unexpected error occurred during the Spectra check:"
    echo "$CHECK_REALIZABILITY"
fi

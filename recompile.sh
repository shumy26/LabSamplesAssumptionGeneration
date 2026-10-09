#!/usr/bin/env bash

python3 compile_to_strix.py LabSamples.gm LabSamples_strix

FORMULA=$(cat LabSamples_strix_formula.txt)
INS=$(cat LabSamples_strix_ins.txt)
OUTS=$(cat LabSamples_strix_outs.txt)

echo "Checking realizability with Strix..."

# Try to run strix. If not installed, it will error out.
CHECK_REALIZABILITY=$(strix --realizability -f "$FORMULA" --ins="$INS" --outs="$OUTS" 2>&1)

if [[ "$CHECK_REALIZABILITY" == *"REALIZABLE"* && "$CHECK_REALIZABILITY" != *"UNREALIZABLE"* ]]; then
    echo "Result: REALIZABLE! Synthesizing controller..."
    strix -o hoa -f "$FORMULA" --ins="$INS" --outs="$OUTS" > controller.hoa
    echo "Success: Saved to controller.hoa"
    python3 llm_checker.py \
        --model "${OLLAMA_MODEL:-gemma4:26b}"

elif [[ "$CHECK_REALIZABILITY" == *"UNREALIZABLE"* ]]; then
    echo "Result: UNREALIZABLE! Extracting counter-strategy..."
    
    # Run the Dual Game for the counter strategy
    # Negate the formula and swap inputs and outputs
    strix -o hoa -f "!($FORMULA)" --ins="$OUTS" --outs="$INS" > counter_strategy.hoa
    
    echo "Failed: Environment winning strategy saved to counter_strategy.hoa"
    python3 llm_checker.py \
        --model "${OLLAMA_MODEL:-gemma4:26b}"

else
    echo "An unexpected error occurred during the Strix check (is Strix installed?):"
    echo "$CHECK_REALIZABILITY"
fi

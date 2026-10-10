#!/usr/bin/env bash
set -euo pipefail

model="${OLLAMA_MODEL:-gemma4:26b}"

if ! command -v ollama >/dev/null 2>&1; then
	echo "Ollama CLI is not installed or is not on PATH." >&2
	exit 1
fi

if ! ollama show "$model" >/dev/null 2>&1; then
	echo "Ollama model $model is missing; pulling it now..." >&2
	ollama pull "$model"
fi

has_trials=0
for argument in "$@"; do
	if [[ "$argument" == "--trials" || "$argument" == --trials=* ]]; then
		has_trials=1
	fi
done

if [[ "$has_trials" -eq 1 ]]; then
	exec python3 run_experiment.py --model "$model" "$@" --max-assumptions-removed 1
fi

exec python3 run_experiment.py --model "$model" "$@" --trials 20 --max-assumptions-removed 1
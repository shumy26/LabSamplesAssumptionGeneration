# Mission packages

Select a mission by name with `--mission NAME`.

Each packaged mission lives in `missions/NAME/` and contains:

- `goal_model.gm`
- `mission.txt`
- `simulation.json`

Generated files are written beside the mission as `model.structuredslugs`,
`model.slugsin`, `controller.json`, and `counter_strategy.txt`. The simulation
expects the same variable vocabulary as the current controller simulator.

Run the Deliver Goods controller test with:

```bash
python3 deliver_goods_controller_test.py
```

Run separate LLM experiments and dashboards with:

```bash
python3 run_experiment.py --mission LabSamples
python3 information_extraction.py --mission LabSamples
python3 run_experiment.py --mission KeepingClean
python3 information_extraction.py --mission KeepingClean
```

Each mission writes `experiment_metrics.json`, `experiment_results.html`, and
`experiment_runs/` inside its own mission directory.

Legacy root-level missions are still supported using the naming scheme
`NAME.gm`, `NAMENL.txt`, `NAME.structuredslugs`, and `NAME.slugsin`, but new
missions should use the packaged layout above.
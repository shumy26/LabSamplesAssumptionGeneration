from pathlib import Path


ROOT = Path(__file__).resolve().parent


def mission_paths(mission):
    """Return the files used by a mission, supporting packaged and legacy missions."""
    mission_path = Path(mission)
    package = mission_path if mission_path.is_dir() else ROOT / "missions" / mission
    if package.is_dir() and (package / "goal_model.gm").exists():
        name = package.name
        return {
            "name": name,
            "directory": package,
            "goal_model": package / "goal_model.gm",
            "mission_text": package / "mission.txt",
            "structured_slugs": package / "model.structuredslugs",
            "slugs_input": package / "model.slugsin",
            "controller": package / "controller.json",
            "counter_strategy": package / "counter_strategy.txt",
            "simulation": package / "simulation.json",
        }

    name = mission_path.stem
    packaged_simulation = ROOT / "missions" / name / "simulation.json"
    return {
        "name": name,
        "directory": ROOT,
        "goal_model": ROOT / f"{name}.gm",
        "mission_text": ROOT / f"{name}NL.txt",
        "structured_slugs": ROOT / f"{name}.structuredslugs",
        "slugs_input": ROOT / f"{name}.slugsin",
        "controller": ROOT / "controller.json",
        "counter_strategy": ROOT / "counter_strategy.txt",
        "simulation": packaged_simulation if packaged_simulation.exists() else ROOT / f"{name}.simulation.json",
    }


def load_simulation_config(paths):
    import json

    if not paths["simulation"].exists():
        return {}
    return json.loads(paths["simulation"].read_text(encoding="utf-8"))
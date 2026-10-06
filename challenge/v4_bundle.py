#!/usr/bin/env python3
"""Build one v4 task-card bundle (the layout read by ``challenge.v4_workflow``).

A card is described by a small spec; the generator configs start from the author's
reference configs in ``challenge/reference/v4`` and are cross-validated with hashed
seeds before anything is generated. Output layout::

    <root>/config/{v4_scenario.json, v4_fiber_config.json, v4_score_config.json}
    <root>/public/{targets.csv, footprint.csv, v4_night_calendar.csv, v4_bulletins.jsonl, v4_forecasts.jsonl}
    <root>/truth/{v4_slots.csv, v4_weather_truth.csv, v4_events.csv, v4_earthquake_effects.csv,
                  v4_observation_requests.jsonl[, v4_stress_events.csv]}

No seed is written anywhere in the bundle. Pure standard library.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Mapping

from . import v4_catalog_generator, v4_observation_requests, v4_weather_simulator
from .v4_config_check import cross_validate_generator_configs

REFERENCE = Path(__file__).resolve().parent / "reference" / "v4"
PUBLIC = ("targets.csv", "footprint.csv", "v4_night_calendar.csv", "v4_bulletins.jsonl", "v4_forecasts.jsonl")
TRUTH = ("v4_slots.csv", "v4_weather_truth.csv", "v4_events.csv", "v4_earthquake_effects.csv",
         "v4_observation_requests.jsonl")
SCENARIO_CONTRACT = "v4-score-v1"  # public.scenarios.contract of every v4 card


def _load(name: str) -> dict:
    return json.loads((REFERENCE / name).read_text(encoding="utf-8"))


def build_card_bundle(root: Path, spec: Mapping) -> Path:
    """Generate a card bundle into ``root`` (must not exist). Returns ``root``.

    spec keys: name, card_id, scenario_slug, phase, seed (int, secret for real cards),
    start_date, end_date, targets, area_deg2, stress (bool), wallclock_seconds (optional),
    event_counts (optional {condition|rocket_launch|earthquake|instrument_fault: n}) and
    observation_requests (optional generator settings).
    """
    root = Path(root)
    if root.exists():
        raise ValueError("bundle directory already exists")
    seed = int(spec["seed"])
    stress = bool(spec.get("stress", False))
    catalog = _load("v4_catalog_config.json")
    catalog.update(seed=seed, seed_derivation="sha256-v1")
    area = float(spec.get("area_deg2", 900.0))
    if area < 3000.0:
        catalog["footprint"].update(total_area_deg2=area, n_components=2, component_area_weights=[0.6, 0.4],
                                    vertices_per_component=24)
    else:
        catalog["footprint"]["total_area_deg2"] = area
    catalog["targets"]["total_count"] = int(spec["targets"])
    catalog["observability"].update(start_date=spec["start_date"], end_date=spec["end_date"])
    weather = _load("v4_weather_stress_config.json" if stress else "v4_weather_config.json")
    # Same card secret, different stream names: catalogue and weather stay independent.
    weather.update(seed=seed, seed_derivation="sha256-v1")
    weather["survey"].update(start_date=spec["start_date"], end_date=spec["end_date"])
    weather["stress_tests"]["enabled"] = stress
    for key, count in dict(spec.get("event_counts") or {}).items():
        if key in weather["weather_events"]["conditions"]:
            weather["weather_events"]["conditions"][key]["count"] = int(count)
        elif key in ("rocket_launch", "earthquake", "instrument_fault"):
            weather[key]["count"] = int(count)
        else:
            raise ValueError(f"unknown event family {key!r}")
    scenario = _scenario(spec, stress, site=None)
    fiber = _load("v4_fiber_config.json")
    fiber.pop("demo", None)
    cross_validate_generator_configs(catalog, weather, scenario, fiber, require_hashed_seeds=True)
    return _generate(root, seed, catalog, weather, scenario, fiber, REFERENCE / "v4_score_config.json",
                     spec.get("observation_requests"), stress)


SPEC_CONFIGS = ("v4_catalog_config.json", "v4_weather_config.json", "v4_fiber_config.json", "v4_score_config.json")


def build_spec_bundle(root: Path, spec_dir: Path) -> Path:
    """Generate a card bundle into ``root`` (must not exist) from a self-contained spec folder.

    ``spec_dir`` holds ``card.json`` (name, card_id, scenario_slug, phase, stress, wallclock_seconds,
    observation_requests) and the four generator configs (catalog, weather, fiber, score), used as
    they are: their own site, season, seeds and scoring. The scenario comes from the reference
    template with the configs' site. Returns ``root``.
    """
    root, spec_dir = Path(root), Path(spec_dir)
    if root.exists():
        raise ValueError("bundle directory already exists")
    card = json.loads((spec_dir / "card.json").read_text(encoding="utf-8"))
    catalog, weather, fiber, _score = (json.loads((spec_dir / name).read_text(encoding="utf-8")) for name in SPEC_CONFIGS)
    stress = bool(card.get("stress", False))
    weather["stress_tests"]["enabled"] = stress  # card.json decides, as the spec keys of build_card_bundle do
    scenario = _scenario(card, stress, site=catalog["site"])
    scenario["minimum_altitude_deg"] = float(catalog["observability"]["minimum_altitude_deg"])
    fiber.pop("demo", None)
    cross_validate_generator_configs(catalog, weather, scenario, fiber)
    return _generate(root, int(catalog["seed"]), catalog, weather, scenario, fiber, spec_dir / "v4_score_config.json",
                     card.get("observation_requests"), stress)


def _scenario(spec: Mapping, stress: bool, *, site: Mapping | None) -> dict:
    scenario = _load("v4_scenario_stress.json" if stress else "v4_scenario_default.json")
    scenario["name"] = str(spec["name"])
    if site is not None:
        scenario["site"] = dict(site)
    scenario["task_card"] = {"card_id": str(spec["card_id"]), "scenario_slug": str(spec["scenario_slug"]),
                             "phase": str(spec["phase"])}
    scenario["fiber_config"] = "v4_fiber_config.json"
    scenario["score_config"] = "v4_score_config.json"
    scenario["products"] = {
        "targets_csv": "../public/targets.csv", "footprint_csv": "../public/footprint.csv",
        "night_calendar_csv": "../public/v4_night_calendar.csv", "bulletins_jsonl": "../public/v4_bulletins.jsonl",
        "forecasts_jsonl": "../public/v4_forecasts.jsonl", "slots_csv": "../truth/v4_slots.csv",
        "weather_truth_csv": "../truth/v4_weather_truth.csv", "events_csv": "../truth/v4_events.csv",
        "earthquake_effects_csv": "../truth/v4_earthquake_effects.csv",
        "observation_requests_jsonl": "../truth/v4_observation_requests.jsonl",
    }
    scenario.pop("agent_params", None)
    if stress:
        scenario["stress"]["stress_events_csv"] = "../truth/v4_stress_events.csv"
    if spec.get("wallclock_seconds") is not None:
        scenario["limits"] = {"global_wallclock_seconds": int(spec["wallclock_seconds"])}
    return scenario


def _generate(root: Path, seed: int, catalog: dict, weather: dict, scenario: dict, fiber: dict, score_path: Path,
              observation_requests: Mapping | None, stress: bool) -> Path:
    score = json.loads(Path(score_path).read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="v4-card-") as temporary:
        work = Path(temporary)
        (work / "catalog.json").write_text(json.dumps(catalog))
        (work / "weather.json").write_text(json.dumps(weather))
        v4_catalog_generator.generate_catalog(work / "catalog.json", work / "out")
        v4_weather_simulator.generate(work / "weather.json", work / "out")
        request_settings = dict(observation_requests or {})
        request_settings.setdefault(
            "completion_factor_threshold", score["observation_requests"]["completion_factor_threshold"]
        )
        request_settings.setdefault(
            "minimum_feature_flux",
            float(request_settings["completion_factor_threshold"])
            * float(score["flux_zero_point"])
            * float(score["exposure_zero_point_seconds"])
            / float(fiber["exposure"]["max_duration_seconds"]),
        )
        v4_observation_requests.generate_requests(
            seed=seed,
            targets_csv=work / "out" / "targets.csv",
            night_calendar_csv=work / "out" / "v4_night_calendar.csv",
            slots_csv=work / "out" / "v4_slots.csv",
            site=scenario["site"],
            minimum_altitude_deg=float(scenario["minimum_altitude_deg"]),
            output_path=work / "out" / "v4_observation_requests.jsonl",
            settings=request_settings,
            flux_zero_point=float(score["flux_zero_point"]),
            exposure_zero_point_seconds=float(score["exposure_zero_point_seconds"]),
            min_duration_seconds=int(fiber["exposure"]["min_duration_seconds"]),
            max_duration_seconds=int(fiber["exposure"]["max_duration_seconds"]),
        )
        for name in ("config", "public", "truth"):
            (root / name).mkdir(parents=True)
        (root / "config" / "v4_scenario.json").write_text(json.dumps(scenario, indent=2, sort_keys=True) + "\n")
        (root / "config" / "v4_fiber_config.json").write_text(json.dumps(fiber, indent=2, sort_keys=True) + "\n")
        shutil.copyfile(score_path, root / "config" / "v4_score_config.json")
        for name in PUBLIC:
            shutil.copyfile(work / "out" / name, root / "public" / name)
        for name in TRUTH + (("v4_stress_events.csv",) if stress else ()):
            shutil.copyfile(work / "out" / name, root / "truth" / name)
    return root

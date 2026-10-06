#!/usr/bin/env python3
"""Build runnable, self-generated local bundles for the released formal cards A-D.

The released card directories only contain public inputs.  This tool copies those
inputs without changing them, then adds deterministic local weather, events,
bulletins, forecasts, and observation requests in a separate output directory.
Nothing it creates is an official hidden input.

    python3 scripts/build-formal-self-generated-evaluations.py

The output can be passed directly to the repository's local runner, for example:

    python3 examples/_local/runner/run_local.py \
      --card evaluation_assets/formal-v4-self-generated-v2/cards/a \
      --agent "python3 agent.py" --agent-cwd /path/to/your-agent
"""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import os
import shutil
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEFAULT_OUT = ROOT / "evaluation_assets" / "formal-v4-self-generated-v2"
CARDS = ("a", "b", "c", "d")
PUBLIC_SOURCE_FILES = (
    "config/v4_fiber_config.json",
    "config/v4_score_config.json",
    "public/footprint.csv",
    "public/targets.csv",
    "public/taskcard.en.md",
    "public/taskcard.zh.md",
    "public/v4_night_calendar.csv",
)
WEATHER_CONDITIONS = ("rainy", "cloudy", "smoggy", "cold_wave", "tornado")
MINIMUM_ALTITUDE_DEG = 30.0
SUN_ALTITUDE_LIMIT_DEG = -18.0


# These labels describe the purpose of the local test data.  They do not claim
# anything about the organizers' hidden event schedule or severity.
PROFILES: dict[str, dict[str, Any]] = {
    "a": {
        "title_zh": "基础天气与公告",
        "difficulty_focus_zh": "先检验是否会根据公开的天气公告调整常规观测。",
        "seed": 6041001,
        "stress": False,
        "event_counts": {
            "rainy": 1,
            "cloudy": 2,
            "smoggy": 0,
            "cold_wave": 0,
            "tornado": 0,
            "rocket_launch": 0,
            "earthquake": 0,
            "instrument_fault": 3,
        },
        "condition_overrides": {
            "rainy": {"duration_slots": [8, 20], "scope_weights": {"ALL": 1.0}},
            "cloudy": {"duration_slots": [8, 24], "scope_weights": {"ALL": 1.0}},
        },
        "terrain": {"sector_count": [1, 1], "width_deg_range": [30.0, 45.0], "max_altitude_deg_range": [30.0, 42.0]},
        "background_closure": {"start_probability_per_open_slot": 0.003, "reopen_probability_per_closed_slot": 0.35, "seasonal_probability_amplitude": 0.002},
        "requests": {"count": 6, "targets_per_request": 8, "minimum_completion_fraction": 0.75, "window_nights": 3, "completion_reward": 100.0, "include_required_targets": False, "reason": "time-critical follow-up"},
    },
    "b": {
        "title_zh": "方向性天气与避让",
        "difficulty_focus_zh": "检验是否会结合方向公告、永久遮挡和很小的光纤方格选择指向。",
        "seed": 6041002,
        "stress": False,
        "event_counts": {
            "rainy": 0,
            "cloudy": 2,
            "smoggy": 2,
            "cold_wave": 0,
            "tornado": 0,
            "rocket_launch": 2,
            "earthquake": 0,
            "instrument_fault": 3,
        },
        "condition_overrides": {
            "cloudy": {"duration_slots": [8, 28], "scope_weights": {"HORIZON_SECTOR": 1.0}},
            "smoggy": {"duration_slots": [24, 72], "scope_weights": {"HORIZON_SECTOR": 1.0}},
        },
        "terrain": {"sector_count": [2, 2], "width_deg_range": [45.0, 75.0], "max_altitude_deg_range": [35.0, 55.0]},
        "background_closure": {"start_probability_per_open_slot": 0.005, "reopen_probability_per_closed_slot": 0.32, "seasonal_probability_amplitude": 0.004},
        "requests": {"count": 6, "targets_per_request": 10, "minimum_completion_fraction": 0.8, "window_nights": 3, "completion_reward": 100.0, "include_required_targets": False, "reason": "time-critical follow-up"},
    },
    "c": {
        "title_zh": "诊断：天气、地震与隐藏故障",
        "difficulty_focus_zh": "检验是否能区分会公开的天气或地震影响，与只能从结果推断的仪器故障。",
        "seed": 6041003,
        "stress": False,
        "event_counts": {
            "rainy": 1,
            "cloudy": 0,
            "smoggy": 0,
            "cold_wave": 1,
            "tornado": 1,
            "rocket_launch": 0,
            "earthquake": 1,
            "instrument_fault": 3,
        },
        "condition_overrides": {
            "rainy": {"duration_slots": [8, 24], "scope_weights": {"ALL": 1.0}},
            "cold_wave": {"duration_slots": [48, 120], "scope_weights": {"ALL": 1.0}},
            "tornado": {"duration_slots": [4, 10], "scope_weights": {"ALL": 1.0}},
        },
        "terrain": {"sector_count": [0, 0], "width_deg_range": [30.0, 45.0], "max_altitude_deg_range": [30.0, 42.0]},
        "instrument_efficiency_multiplier_range": [0.45, 0.60],
        "earthquake": {"magnitude_range": [5.5, 6.5], "decay_nights": 3.0},
        "background_closure": {"start_probability_per_open_slot": 0.006, "reopen_probability_per_closed_slot": 0.30, "seasonal_probability_amplitude": 0.005},
        "requests": {"count": 6, "targets_per_request": 8, "minimum_completion_fraction": 0.75, "window_nights": 3, "completion_reward": 100.0, "include_required_targets": True, "reason": "time-critical follow-up"},
    },
    "d": {
        "title_zh": "Hard mode：恢复与长期稳定性",
        "difficulty_focus_zh": "检验在长期巡天中处理多类天气、隐藏故障、数据丢失状态更正和固定指向偏差的能力。",
        "seed": 6041004,
        "stress": True,
        "event_counts": {
            "rainy": 1,
            "cloudy": 2,
            "smoggy": 1,
            "cold_wave": 0,
            "tornado": 0,
            "rocket_launch": 1,
            "earthquake": 1,
            "instrument_fault": 3,
        },
        "condition_overrides": {
            "rainy": {"duration_slots": [12, 32], "scope_weights": {"ALL": 1.0}},
            "cloudy": {"duration_slots": [12, 32], "scope_weights": {"ALL": 0.25, "HORIZON_SECTOR": 0.75}},
            "smoggy": {"duration_slots": [36, 96], "scope_weights": {"ALL": 0.25, "HORIZON_SECTOR": 0.75}},
        },
        "terrain": {"sector_count": [1, 1], "width_deg_range": [45.0, 70.0], "max_altitude_deg_range": [35.0, 55.0]},
        "instrument_efficiency_multiplier_range": [0.30, 0.50],
        "earthquake": {"magnitude_range": [5.5, 6.8], "decay_nights": 4.0},
        "background_closure": {"start_probability_per_open_slot": 0.008, "reopen_probability_per_closed_slot": 0.30, "seasonal_probability_amplitude": 0.006},
        "requests": {"count": 6, "targets_per_request": 12, "minimum_completion_fraction": 0.75, "window_nights": 4, "completion_reward": 100.0, "include_required_targets": True, "reason": "time-critical follow-up"},
    },
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _survey_dates(calendar_path: Path) -> tuple[str, str]:
    nights = _read_csv(calendar_path)
    if not nights:
        raise ValueError(f"{calendar_path} has no nights")
    first = date.fromisoformat(nights[0]["night_date"])
    last = date.fromisoformat(nights[-1]["night_date"])
    return first.isoformat(), (last + timedelta(days=1)).isoformat()


def _copy_source_inputs(source: Path, bundle: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for relative in PUBLIC_SOURCE_FILES:
        origin = source / relative
        destination = bundle / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(origin, destination)
        original_hash = _sha256(origin)
        if _sha256(destination) != original_hash:
            raise ValueError(f"copy hash mismatch for {origin}")
        hashes[relative] = original_hash
    return hashes


def _weather_config(card: str, source: Path, profile: Mapping[str, Any]) -> dict[str, Any]:
    config = _load_json(ROOT / "challenge" / "reference" / "v4" / "v4_weather_config.json")
    fiber = _load_json(source / "config" / "v4_fiber_config.json")
    start_date, end_date = _survey_dates(source / "public" / "v4_night_calendar.csv")
    config["seed"] = int(profile["seed"])
    config["seed_derivation"] = "sha256-v1"
    config["site"] = copy.deepcopy(fiber["site"])
    config["survey"] = {
        "start_date": start_date,
        "end_date": end_date,
        "slot_seconds": 900,
        "sun_altitude_limit_deg": SUN_ALTITUDE_LIMIT_DEG,
    }
    config["background_closure"] = copy.deepcopy(profile["background_closure"])
    counts = profile["event_counts"]
    for condition in WEATHER_CONDITIONS:
        definition = config["weather_events"]["conditions"][condition]
        definition["count"] = int(counts[condition])
        definition.update(copy.deepcopy(profile.get("condition_overrides", {}).get(condition, {})))
    for family in ("rocket_launch", "earthquake", "instrument_fault"):
        config[family]["count"] = int(counts[family])
    config["terrain_obstruction"] = copy.deepcopy(profile["terrain"])
    config["instrument_fault"]["instrument_efficiency_multiplier_range"] = list(
        profile.get("instrument_efficiency_multiplier_range", config["instrument_fault"]["instrument_efficiency_multiplier_range"])
    )
    config["earthquake"].update(copy.deepcopy(profile.get("earthquake", {})))
    config["stress_tests"]["enabled"] = bool(profile["stress"])
    # Optional settings for independently sourced local suites. The original v2
    # profiles remain unchanged, including callers that import this builder.
    def merge(target: dict, changes: Mapping) -> None:
        for key, value in changes.items():
            if isinstance(value, Mapping) and isinstance(target.get(key), dict):
                merge(target[key], value)
            else:
                target[key] = copy.deepcopy(value)

    merge(config, profile.get("weather_overrides", {}))
    config["output"] = {"directory": "generated"}
    return config


def _verify_calendar(source: Path, generated: Path) -> None:
    expected = _read_csv(source / "public" / "v4_night_calendar.csv")
    actual = _read_csv(generated / "v4_night_calendar.csv")
    if expected == actual:
        return
    if len(expected) != len(actual):
        raise ValueError(f"night calendar length differs: official={len(expected)}, generated={len(actual)}")
    for index, (left, right) in enumerate(zip(expected, actual), start=1):
        if left != right:
            differences = sorted(key for key in set(left) | set(right) if left.get(key) != right.get(key))
            raise ValueError(f"night calendar differs at row {index}: {differences}")
    raise ValueError("night calendar differs")


def _scenario_config(card: str, fiber: Mapping[str, Any], stress: bool) -> dict[str, Any]:
    products = {
        "targets_csv": "../public/targets.csv",
        "footprint_csv": "../public/footprint.csv",
        "night_calendar_csv": "../public/v4_night_calendar.csv",
        "bulletins_jsonl": "../public/v4_bulletins.jsonl",
        "forecasts_jsonl": "../public/v4_forecasts.jsonl",
        "slots_csv": "../truth/v4_slots.csv",
        "weather_truth_csv": "../truth/v4_weather_truth.csv",
        "events_csv": "../truth/v4_events.csv",
        "earthquake_effects_csv": "../truth/v4_earthquake_effects.csv",
        "observation_requests_jsonl": "../truth/v4_observation_requests.jsonl",
    }
    config: dict[str, Any] = {
        "schema_version": "v4-scenario-v1",
        "name": f"formal-{card}-self-generated-local-v2",
        "task_card": {
            "card_id": f"{card.upper()}-SELF-GENERATED",
            "scenario_slug": f"formal-{card}-self-generated-local-v2",
            "phase": "local-self-generated",
        },
        "site": {**copy.deepcopy(fiber["site"]), "sun_altitude_limit_deg": SUN_ALTITUDE_LIMIT_DEG},
        "minimum_altitude_deg": MINIMUM_ALTITUDE_DEG,
        "limits": {"global_wallclock_seconds": 900},
        "fiber_config": "v4_fiber_config.json",
        "score_config": "v4_score_config.json",
        "products": products,
        "stress": {"enabled": stress},
    }
    if stress:
        config["stress"]["stress_events_csv"] = "../truth/v4_stress_events.csv"
    return config


def _request_settings(profile: Mapping[str, Any], score: Mapping[str, Any], fiber: Mapping[str, Any]) -> dict[str, Any]:
    settings = copy.deepcopy(profile["requests"])
    threshold = float(score["observation_requests"]["completion_factor_threshold"])
    settings["completion_factor_threshold"] = threshold
    settings["minimum_feature_flux"] = (
        threshold
        * float(score["flux_zero_point"])
        * float(score["exposure_zero_point_seconds"])
        / float(fiber["exposure"]["max_duration_seconds"])
    )
    return settings


def _copy_generated_products(generated: Path, bundle: Path, stress: bool) -> None:
    (bundle / "truth").mkdir(exist_ok=True)
    for name in ("v4_bulletins.jsonl", "v4_forecasts.jsonl"):
        shutil.copyfile(generated / name, bundle / "public" / name)
    for name in (
        "v4_slots.csv",
        "v4_weather_truth.csv",
        "v4_events.csv",
        "v4_earthquake_effects.csv",
    ):
        shutil.copyfile(generated / name, bundle / "truth" / name)
    if stress:
        for name in ("v4_stress_events.csv", "v4_state_resync_schema.json"):
            shutil.copyfile(generated / name, bundle / "truth" / name)


def _event_counts(events_path: Path, stress_path: Path | None) -> dict[str, int]:
    result: dict[str, int] = {}
    for row in _read_csv(events_path):
        result[row["event_type"]] = result.get(row["event_type"], 0) + 1
    if stress_path is not None:
        for row in _read_csv(stress_path):
            result[row["event_type"]] = result.get(row["event_type"], 0) + 1
    return dict(sorted(result.items()))


def _write_truth_readme(path: Path, card: str, profile: Mapping[str, Any]) -> None:
    path.write_text(
        "# Self-generated local scoring data\n\n"
        f"This is self-generated local evaluation data for formal card {card.upper()}. "
        "It is not an official hidden input and does not predict the competition's hidden weather, events, "
        "or observation requests.\n\n"
        f"Local profile: {profile['title_zh']} — {profile['difficulty_focus_zh']}\n\n"
        "Your agent must not read these truth files. The local runner uses them only to score decisions and "
        "to emit the same incremental public messages that the platform would emit.\n",
        encoding="utf-8",
    )


def _build_one(output: Path, card: str, *, profile: Mapping[str, Any] | None = None,
               suite_name: str = "v2") -> dict[str, Any]:
    from challenge import v4_observation_requests, v4_weather_simulator
    from challenge.v4_config_check import validate_fiber_config, validate_score_config
    from challenge.v4_workflow import V4Workflow

    source = ROOT / "cards" / card
    profile = PROFILES[card] if profile is None else profile
    bundle = output / "cards" / card
    bundle.mkdir(parents=True)
    source_hashes = _copy_source_inputs(source, bundle)
    fiber = _load_json(source / "config" / "v4_fiber_config.json")
    score = _load_json(source / "config" / "v4_score_config.json")
    validate_fiber_config(fiber)
    validate_score_config(score)

    weather = _weather_config(card, source, profile)
    generator_dir = bundle / "generator"
    generator_dir.mkdir()
    _write_json(generator_dir / "weather_config.json", weather)
    generated = generator_dir / "generated"
    v4_weather_simulator.generate(generator_dir / "weather_config.json", generated)
    _verify_calendar(source, generated)
    _copy_generated_products(generated, bundle, bool(profile["stress"]))

    request_settings = _request_settings(profile, score, fiber)
    _write_json(generator_dir / "observation_request_settings.json", request_settings)
    v4_observation_requests.generate_requests(
        seed=int(profile["seed"]),
        targets_csv=bundle / "public" / "targets.csv",
        night_calendar_csv=bundle / "public" / "v4_night_calendar.csv",
        slots_csv=bundle / "truth" / "v4_slots.csv",
        site=fiber["site"],
        minimum_altitude_deg=MINIMUM_ALTITUDE_DEG,
        output_path=bundle / "truth" / "v4_observation_requests.jsonl",
        settings=request_settings,
        flux_zero_point=float(score["flux_zero_point"]),
        exposure_zero_point_seconds=float(score["exposure_zero_point_seconds"]),
        min_duration_seconds=int(fiber["exposure"]["min_duration_seconds"]),
        max_duration_seconds=int(fiber["exposure"]["max_duration_seconds"]),
    )
    shutil.rmtree(generated)

    scenario = _scenario_config(card, fiber, bool(profile["stress"]))
    if suite_name != "v2":
        scenario["name"] = f"formal-{card}-self-generated-local-{suite_name}"
        scenario["task_card"]["scenario_slug"] = scenario["name"]
    _write_json(bundle / "config" / "v4_scenario.json", scenario)
    _write_truth_readme(bundle / "truth" / "README.md", card, profile)
    stress_path = bundle / "truth" / "v4_stress_events.csv" if profile["stress"] else None
    event_counts = _event_counts(bundle / "truth" / "v4_events.csv", stress_path)
    requests = _read_csv(bundle / "public" / "v4_night_calendar.csv")
    request_records = [line for line in (bundle / "truth" / "v4_observation_requests.jsonl").read_text(encoding="utf-8").splitlines() if line]
    metadata = {
        "schema_version": "self-generated-formal-local-card-v1",
        "classification": "self-generated local evaluation data; not official hidden competition data",
        "formal_card": card.upper(),
        "profile": {
            "title_zh": profile["title_zh"],
            "difficulty_focus_zh": profile["difficulty_focus_zh"],
            "hard_mode": bool(profile["stress"]),
            "seed": int(profile["seed"]),
        },
        "source_public_inputs": {
            "directory": f"cards/{card}",
            "sha256": source_hashes,
        },
        "generated": {
            "weather_config": "generator/weather_config.json",
            "request_settings": "generator/observation_request_settings.json",
            "event_counts": event_counts,
            "observation_request_count": len(request_records),
            "night_count": len(requests),
        },
    }
    if "design_evidence" in profile:
        metadata["design_evidence"] = copy.deepcopy(profile["design_evidence"])
    _write_json(bundle / "self_generated_metadata.json", metadata)
    # This enforces the same containment and schema checks the local runner applies.
    V4Workflow(bundle)
    return metadata


def _root_readme(output_name: str) -> str:
    rows = []
    for card in CARDS:
        profile = PROFILES[card]
        counts = profile["event_counts"]
        types = [key for key, count in counts.items() if count]
        if profile["terrain"]["sector_count"][1] > 0:
            types.append("terrain_obstruction")
        if profile["stress"]:
            types.extend(["data_loss", "pointing_offset"])
        rows.append(f"| {card.upper()} | {profile['title_zh']} | {profile['difficulty_focus_zh']} | {', '.join(types)} |")
    table = "\n".join(rows)
    return f"""# 正式 A–D 的自建本地评测卡\n\n这些目录只供本地开发和跑分使用。每一张卡都复用对应正式卡下载包中的公开目标、天区、日历、台址、光纤和计分参数；天气、事件、预报、简报与限时请求均为**自行生成**，不代表也不能推断正式比赛或隐藏卡的真值。\n\n| 卡 | 本地侧重点 | 用来检查什么 | 覆盖的自建事件 |\n|---|---|---|---|\n{table}\n\nD 启用了 Hard mode，因而额外包含 `data_loss` 和 `pointing_offset`。A–C 没有这两类事件。所有常规事件类型至少在一张卡中出现：雨、云、霾、寒潮、风暴、火箭发射、地震、地形遮挡和仪器故障。当前对齐基线为每张卡 6 条限时请求（每条 100 分）和 3 个仪器故障；其余事件仍按各卡的难度重点设置。\n\n## 运行\n\n从仓库根目录运行，替换 agent 命令和工作目录：\n\n```bash\npython3 examples/_local/runner/run_local.py \\\n  --card evaluation_assets/{output_name}/cards/a \\\n  --agent \"python3 agent.py\" \\\n  --agent-cwd /path/to/your-agent\n```\n\n把 `a` 改为 `b`、`c` 或 `d` 可运行其它卡。`truth/` 中的文件只给本地裁判使用；不要让智能体读取它们。每个卡目录的 `self_generated_metadata.json` 和 `generator/` 记录了来源、固定种子、事件配置和请求配置，因而可复建。\n\n## 重新生成\n\n```bash\npython3 scripts/build-formal-self-generated-evaluations.py \\\n  --out evaluation_assets/{output_name}\n```\n\n默认输出目录已存在时，脚本会停止，避免覆盖已有本地结果。使用另一个 `--out` 路径可以生成一份新的确定性副本。\n"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="new output directory (must not already exist)")
    args = parser.parse_args()
    output = args.out.resolve()
    if output.exists():
        raise SystemExit(f"refusing to overwrite existing output: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{output.name}-", dir=output.parent) as temporary:
        staged = Path(temporary) / output.name
        staged.mkdir()
        cards = {card.upper(): _build_one(staged, card) for card in CARDS}
        (staged / "README.md").write_text(_root_readme(output.name), encoding="utf-8")
        root_metadata = {
            "schema_version": "self-generated-formal-local-evaluations-v1",
            "classification": "self-generated local evaluation data; not official hidden competition data",
            "source_formal_manifest": "cards/formal_package_manifest.json",
            "cards": cards,
        }
        _write_json(staged / "manifest.json", root_metadata)
        os.replace(staged, output)
    print(json.dumps({"output": str(output), "cards": list(cards)}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Build a hold-out evaluation set: the formal cards' public inputs + NEW self-generated weather.

    python3.12 tools/make_holdout_set.py            # -> eval_sets/holdout-reference-rates-s1/

Why: every knob of this agent was chosen on the project's `formal-v4-self-generated-v2` set,
i.e. on one weather realisation per card with comparatively few events. This tool makes a
second, independent set that nothing was tuned on, to check that the result carries over:

* different seeds, so different weather, closures, event times, faults and requests;
* event counts taken from the engine's own reference weather configuration
  (challenge/reference/v4/v4_weather_config.json: 6 rain, 10 overcast, 5 haze, 4 cold snaps,
  1 storm, 4 rocket launches and 2 earthquakes per 123 nights), scaled to each card's length,
  with the reference durations, scopes, terrain and closure rates;
* 3 instrument faults and 6 requests per card, as in the v2 set; card D keeps Hard mode.

It reuses the project's own builder (scripts/build-formal-self-generated-evaluations.py)
unchanged, only with different profiles passed in memory, so the files have exactly the layout
the local judge expects. Like the v2 set this is SELF-GENERATED data: it is not, and says
nothing about, the hidden weather of the real competition. The output is not committed (it is
tens of megabytes and fully reproducible from this script).
"""
from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PROJECT = Path(os.environ.get("OBSERVER_PROJECT_ROOT", REPO.parent.parent)).resolve()
BUILDER = PROJECT / "scripts" / "build-formal-self-generated-evaluations.py"

REFERENCE_PER_123_NIGHTS = {"rainy": 6, "cloudy": 10, "smoggy": 5, "cold_wave": 4, "tornado": 1,
                            "rocket_launch": 4, "earthquake": 2}
NIGHTS = {"a": 123, "b": 183, "c": 121, "d": 365}


def profiles(base: dict, seed0: int) -> dict:
    out = {}
    for index, card in enumerate("abcd"):
        profile = copy.deepcopy(base[card])
        scale = NIGHTS[card] / 123.0
        counts = {kind: max(1, round(count * scale)) for kind, count in REFERENCE_PER_123_NIGHTS.items()}
        counts["instrument_fault"] = 3
        profile["seed"] = seed0 + index
        profile["event_counts"] = counts
        profile["condition_overrides"] = {}          # reference durations and scopes
        profile["terrain"] = {"sector_count": [1, 3], "width_deg_range": [30.0, 70.0],
                              "max_altitude_deg_range": [35.0, 50.0]}
        profile["background_closure"] = {"start_probability_per_open_slot": 0.008,
                                         "reopen_probability_per_closed_slot": 0.32,
                                         "seasonal_probability_amplitude": 0.006}
        profile.pop("earthquake", None)              # reference magnitudes and decay
        if not profile["stress"]:
            profile.pop("instrument_efficiency_multiplier_range", None)   # reference 0.4-0.7
        profile["title_zh"] = "留出集：参考事件频率 + 新种子"
        profile["difficulty_focus_zh"] = "检验在未参与调参的天气与事件下成绩是否保持。"
        out[card] = profile
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--name", default="holdout-reference-rates-s1")
    parser.add_argument("--seed", type=int, default=7150001)
    args = parser.parse_args()
    output = REPO / "eval_sets" / args.name
    if output.exists():
        raise SystemExit(f"already exists: {output}")
    sys.path.insert(0, str(PROJECT))
    spec = importlib.util.spec_from_file_location("formal_builder", BUILDER)
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    builder.PROFILES = profiles(builder.PROFILES, args.seed)
    output.mkdir(parents=True)
    cards = {}
    for card in "abcd":
        cards[card.upper()] = builder._build_one(output, card)   # noqa: SLF001 - reuse, do not copy
        print(card, json.dumps(cards[card.upper()]["generated"]["event_counts"], sort_keys=True), flush=True)
    manifest = {"classification": "self-generated local evaluation data; not official hidden competition data",
                "purpose": "hold-out set for agents/claude-baseline-v1: reference event rates, new seeds",
                "seed": args.seed, "builder": str(BUILDER.relative_to(PROJECT)), "cards": cards}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output / "README.md").write_text(
        f"# {args.name}\n\n留出评测集：正式卡 A–D 的公开输入原样复制，天气、事件、公告、预报和限时请求由 "
        "`tools/make_holdout_set.py` 用引擎自带生成器**自行生成**（新种子，事件数量取引擎参考配置并按夜数缩放，"
        "每卡 3 个故障、6 条请求，D 卡启用 Hard mode）。它不是正式比赛的隐藏数据，也不代表隐藏数据。"
        "智能体的所有参数都没有在这一套数据上调过。\n", encoding="utf-8")
    print("wrote", output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Generate NEW local events informed by official contestant feedback.

Uses only aggregate published episode counts/request shapes, never official event
times or request target IDs. Unobserved numeric quantities remain assumptions.
Keeps the v1/v2 suites and formal public inputs untouched.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VARIANTS = ("nominal", "lighter", "heavier")
KINDS = {"rain": "rainy", "overcast": "cloudy", "haze": "smoggy", "cold_snap": "cold_wave",
         "storm": "tornado", "rocket_launch": "rocket_launch", "earthquake": "earthquake"}


def load_builder():
    spec = importlib.util.spec_from_file_location("formal_builder", ROOT/"scripts/build-formal-self-generated-evaluations.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def profile_for(card: str, variant: str, seed: int, feedback: dict, builder) -> dict:
    entry = feedback["cards"][card]
    summary = entry["public_event_summary"]
    shapes = {(q["targets"], q["minimum_completed"], q["reward"], q["factor_threshold"], q["window_nights"])
              for run in entry["runs"] for q in run["requests"]}
    if shapes != {(8, 6, 100.0, 0.5, 2)} or entry["unique_requests"] != 6:
        raise ValueError(f"Request shape changed for {card}: inspect new feedback before building")
    profile = copy.deepcopy(builder.PROFILES[card])
    profile["seed"] = seed
    profile["title_zh"] = {"a": "寒潮、短时避让与地震", "b": "频繁雨云与方向变化",
                           "c": "频繁降雨与小容量排程", "d": "全年多类事件与数据恢复"}[card]
    profile["difficulty_focus_zh"] = "按线上公开反馈设计的新事件；强度与边界仍属自建假设。"
    profile["event_counts"] = {internal: summary.get(public, {}).get("episodes", 0)
                               for public, internal in KINDS.items()}
    lower_bound = entry["fault_count_lower_bound"]
    profile["event_counts"]["instrument_fault"] = lower_bound + {"lighter": 0, "nominal": 0, "heavier": 2}[variant]
    profile["condition_overrides"] = {}  # Public duration ranges agree with engine reference ranges.
    profile.pop("earthquake", None)
    profile.pop("instrument_efficiency_multiplier_range", None)
    # No terrain announcement in any initial bulletin; do not impose old v2 terrain.
    profile["terrain"] = {"sector_count": [0, 0], "width_deg_range": [30.0, 70.0],
                          "max_altitude_deg_range": [35.0, 50.0]}
    profile["requests"] = {"count": 6, "targets_per_request": 8, "minimum_completion_fraction": .75,
                           "window_nights": 2, "completion_reward": 100.0,
                           "include_required_targets": False, "reason": "time-critical follow-up"}
    profile["background_closure"] = {
        "start_probability_per_open_slot": {"lighter": .003, "nominal": .008, "heavier": .015}[variant],
        "reopen_probability_per_closed_slot": .32, "seasonal_probability_amplitude": .002}
    overrides = {"stress_tests": {"data_loss": {"window_max_fraction": .15},
                                  "pointing_offset": {"max_abs_alt_rad": {"lighter": .0002, "nominal": .0005, "heavier": .002}[variant],
                                                      "max_abs_az_rad": {"lighter": .0002, "nominal": .0005, "heavier": .002}[variant]}}}
    # Global vs compass-direction notices is observed; exact sector angles are not.
    for public, internal in KINDS.items():
        if internal not in builder.WEATHER_CONDITIONS or public not in summary:
            continue
        s = summary[public]
        profile["condition_overrides"][internal] = {
            "scope_weights": {"ALL": s["global_episodes"]/s["episodes"],
                              "HORIZON_SECTOR": s["directional_episodes"]/s["episodes"]}}
    if variant == "heavier":
        # Overlapping equal-kind/direction events can be merged by public notices.
        for kind in ("rainy", "cloudy", "smoggy"):
            count = profile["event_counts"][kind]
            profile["event_counts"][kind] = (count * 5 + 3)//4
        profile["terrain"]["sector_count"] = [1, 2]  # transfer scenario, not an inference about this release
        overrides.update({"instrument_fault": {"instrument_efficiency_multiplier_range": [.25, .5]},
                          "earthquake": {"magnitude_range": [5.5, 7.0], "decay_nights": 5.0},
                          "weather_events": {"sector_width_deg_range": [70.0, 140.0],
                                             "sector_altitude_limit_deg_range": [50.0, 85.0]}})
    elif variant == "lighter":
        overrides.update({"instrument_fault": {"instrument_efficiency_multiplier_range": [.55, .8]},
                          "earthquake": {"magnitude_range": [4.5, 6.2], "decay_nights": 3.0},
                          "weather_events": {"sector_width_deg_range": [30.0, 80.0],
                                             "sector_altitude_limit_deg_range": [35.0, 65.0]}})
    profile["weather_overrides"] = overrides
    profile["design_evidence"] = {
        "variant": variant, "public_episode_counts": {k:v["episodes"] for k,v in summary.items()},
        "fault_count_lower_bound": lower_bound,
        "confirmed": ["6 requests, 8 targets each, minimum 6, 100 points, 0.5 threshold, two-night window",
                      "D state_resync window_max_fraction 0.15", "published event families and global/compass scopes"],
        "assumed": ["Episode counts used as nominal generated event counts; overlaps may conceal additional events",
                    "Reference numeric weather model/durations and seeded event placement",
                    "Fault count uses repair lower bound; onset, efficiency and undetected faults are unknown",
                    "Closure probability, quake strength/decay and exact sector boundaries are hypotheses",
                    "D offset scale is synthetic; agent estimates are not ground truth",
                    "Heavier terrain tests transfer only; no terrain was announced in downloaded runs"],
        "not_copied": ["official event timestamps", "official request target IDs", "official data-loss fractions"]}
    return profile


def readme(name: str, variants: list[str]) -> str:
    return f"""# 正式卡 A–D：根据线上反馈重新设计的本地事件（v3）

正式公开目标、天区、仪器、计分和日历原样复制。天气、公告、事件、请求全部**自行生成**，不是官方隐藏数据。
设计依据是同一版本三次正式运行的参赛者结果，三个运行共享同一套公开事件，不能当作三套独立天气。
完整分析见仓库 `docs/formal-feedback-event-design.zh.md`。

| 卡 | 主评测事件数量（由公开连续公告段设计，不宣称是官方事件总数） |
|---|---|
| A | 寒潮 4、火箭 4、地震 2；故障 1（已修复数量下限） |
| B | 雨 40、云 48、寒潮 4、火箭 4、地震 2；故障 1 |
| C | 雨 35、寒潮 4、火箭 4、地震 2；故障 1 |
| D | 雨 18、云 30、霾 15、寒潮 12、风暴 3、火箭 12、地震 2；故障 3 |

全部卡每条请求为 8 选 6、门槛 0.5、奖励 100 分、两夜期限，共 6 条。D 数据丢失窗口上限为 15%。
主评测不加永久地形遮挡，因为全部初始公告均未出现；较重场景另外加入，用来检查迁移能力。
天气强度、关闭概率、精确方向范围、地震强度、漏检故障和指向偏差没有唯一答案，保留明确假设。

已生成：{', '.join(variants)}。`nominal` 是主设计，`lighter` 和 `heavier` 使用独立种子检查不确定情况；
它们是敏感性检查，不是有统计保证的上下界。仍有未覆盖的未知情况，例如晚季才出现或聚集发生的隐藏故障。
所有配置都在 `profiles.json`，每卡 `generator/`、`self_generated_metadata.json` 和总 `manifest.json` 可追溯来源与哈希。

```bash
python3.12 examples/_local/runner/run_local.py \\
  --card evaluation_assets/{name}/nominal/cards/a \\
  --agent "python3.12 agent.py" --agent-cwd agents/claude-baseline-v1/agent \\
  --out artifacts/formal-feedback-2026-10-05/local-v3/a --quiet
```

将卡字母与场景目录替换可运行其余卡。`truth/` 仅由本地裁判使用，智能体通过标准输入接收按时公开的消息。
目前文件夹约定不能阻止带文件工具的智能体偷读答案；本次基线本身不读任何卡片文件。
旧 v1/v2 及留出集不替换，新场景在独立目录运行；本地分数不能作为线上分数预测。

```bash
python3.12 scripts/build-feedback-local-evaluations.py \\
  --feedback artifacts/formal-feedback-2026-10-05/feedback-analysis.json \\
  --out evaluation_assets/{name}-copy
```

输出已存在时停止，避免覆盖。固定种子可复建，官方时刻和请求目标没有复制到新事件。
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--feedback", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=ROOT/"evaluation_assets/formal-v4-feedback-self-generated-v3")
    parser.add_argument("--variants", nargs="+", choices=VARIANTS, default=list(VARIANTS))
    parser.add_argument("--seed", type=int, default=8105001)
    args = parser.parse_args()
    output = args.out.resolve()
    if output.exists():
        raise SystemExit(f"refusing to overwrite existing output: {output}")
    feedback = json.loads(args.feedback.read_text())
    if set(feedback["cards"]) != set("abcd"):
        raise SystemExit("All formal A-D cards required")
    builder = load_builder()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{output.name}-", dir=output.parent) as temporary:
        staged = Path(temporary)/output.name
        staged.mkdir()
        profiles, suites = {}, {}
        for variant in args.variants:
            destination = staged/variant
            destination.mkdir()
            profiles[variant], suites[variant] = {}, {}
            for index, card in enumerate("abcd"):
                seed = args.seed + VARIANTS.index(variant)*100 + index
                p = profile_for(card, variant, seed, feedback, builder)
                profiles[variant][card] = p
                suites[variant][card] = builder._build_one(destination, card, profile=p, suite_name=f"feedback-v3-{variant}")
                print(f"built {variant}/{card}", flush=True)
        manifest = {"schema_version": "feedback-informed-self-generated-v3",
                    "classification": "self-generated local evaluation data; not official hidden competition data",
                    "feedback_sha256": hashlib.sha256(args.feedback.read_bytes()).hexdigest(),
                    "official_feedback_archives": [{"file": a["file"], "sha256": a["sha256"],
                                                     "evaluation_id": a["evaluation"]["evaluation_id"]} for a in feedback["archives"]],
                    "source_formal_manifest": "cards/formal_package_manifest.json", "suites": suites}
        for name, value in [("profiles.json", profiles), ("manifest.json", manifest)]:
            (staged/name).write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)+"\n")
        (staged/"README.md").write_text(readme(output.name, args.variants))
        os.replace(staged, output)
    print(json.dumps({"output":str(output), "variants":args.variants}))


if __name__ == "__main__":
    main()

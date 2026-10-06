#!/usr/bin/env python3
"""Print a Markdown comparison of official runs (results/<set>/<tag>/summary.json).

    python3 tools/compare_runs.py results/evalset-v2/ref-python-pro-rules results/evalset-v2/v1.0
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def load(folder: Path) -> dict:
    return json.loads((folder / "summary.json").read_text())


def main() -> int:
    folders = [Path(arg) for arg in sys.argv[1:]]
    runs = [(folder.name, load(folder)) for folder in folders]
    cards = sorted({card for _, run in runs for card in run["cards"]})
    print("| 卡 | " + " | ".join(name for name, _ in runs) + " | 差值 |")
    print("|---|" + "---:|" * (len(runs) + 1))
    means = [0.0] * len(runs)
    for card in cards:
        totals = [run["cards"].get(card, {}).get("total") for _, run in runs]
        for i, value in enumerate(totals):
            means[i] += (value or 0.0) / len(cards)
        delta = ""
        if len(totals) >= 2 and totals[0] and totals[-1]:
            delta = f"{totals[-1] - totals[0]:+,.0f} ({100 * (totals[-1] / totals[0] - 1):+.1f}%)"
        print(f"| {card.upper()} | " + " | ".join(f"{v:,.1f}" if v is not None else "-" for v in totals) + f" | {delta} |")
    delta = f"{means[-1] - means[0]:+,.0f} ({100 * (means[-1] / means[0] - 1):+.1f}%)" if len(runs) >= 2 else ""
    print("| **四卡平均** | " + " | ".join(f"**{v:,.1f}**" for v in means) + f" | **{delta}** |")
    print()
    for name, run in runs:
        print(f"`{name}`（评测集 `{run.get('eval_set', 'formal-v4-self-generated-v1')}`，真实时间上限 "
              f"{run.get('wall_cap_seconds', 1800):.0f} s，开跑时本机速度系数 {run.get('machine_speed_factor_at_start', float('nan')):.2f}）：")
        print()
        print("| 卡 | 总分 | 目标得分 | 必观测漏 | 均匀度 | 举报 | 请求 | 曝光次数 | CPU 预算 | 真实用时 | 结束方式 |")
        print("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|")
        for card in cards:
            s = run["cards"].get(card)
            if not s or "total" not in s:
                print(f"| {card.upper()} | 失败 | | | | | | | | | |")
                continue
            clock = s.get("fair_clock", {})
            print(f"| {card.upper()} | {s['total']:,.1f} | {s['sum_best_scores']:,.1f} | {s['required_missing']} | "
                  f"{s['uniformity_penalty']:.1f} | {s['report_settlement']:+.0f} | "
                  f"{s['observation_requests_completed']}/{s['observation_requests_issued']} | {s['observe_actions']} | "
                  f"{clock.get('charged_seconds', 0):.0f}/900 | {clock.get('run_wall_seconds', 0):.0f} s | {s['termination_reason']} |")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

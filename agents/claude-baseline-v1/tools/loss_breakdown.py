#!/usr/bin/env python3
"""Where did the points go?  Break one official run down against the theoretical ceiling.

    python3.12 tools/loss_breakdown.py results/evalset-v2/v1.0            # all cards of a run
    python3.12 tools/loss_breakdown.py results/holdout-s1/v1.0 --eval-set holdout-reference-rates-s1

For developer analysis only: it reads the judge's outputs of a finished run and the SELF-GENERATED
truth of the local evaluation card. Nothing here is available to, or used by, the agent.

Ceiling: every target at weight x 1.20 (saturated, in a matched DARK exposure), every request
completed, every fault reported, no penalties. The gap is split into
  never      targets without any scoring exposure
  depth      best exposure not saturated            w * m * (1 - c)
  multiplier best exposure below 1.20               w * (1.20 - m)
and the telescope's fibre time into useful / over-saturated / superseded / empty.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from bisect import bisect_right
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import cards_dir  # noqa: E402

FIBRES_DEFAULT = 16


def utc(text: str) -> float:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()


def rows(path: Path) -> list[dict]:
    with path.open() as handle:
        return list(csv.DictReader(handle))


def breakdown(run: Path, card_dir: Path) -> dict:
    config = json.loads((card_dir / "config" / "v4_score_config.json").read_text())
    fibres = json.loads((card_dir / "config" / "v4_fiber_config.json").read_text())["field"].get("n_fibers", FIBRES_DEFAULT)
    need_unit = float(config["flux_zero_point"]) * float(config["exposure_zero_point_seconds"])
    top = float(config["program"]["multipliers"]["DARK"])
    report = json.loads((run / "score_report.json").read_text())
    targets = {r["target_id"]: r for r in rows(card_dir / "public" / "targets.csv")}
    weight = {t: float(r["science_weight"]) for t, r in targets.items()}
    need = {t: need_unit / float(r["feature_flux"]) for t, r in targets.items()}     # seconds at Q = 1

    decisions = rows(run / "decisions.csv")
    observes = {int(d["observe_index"]): d for d in decisions if d["action"] == "observe"}
    waits = [(utc(d["start_utc"]), utc(d["end_utc"])) for d in decisions if d["action"] == "wait"]
    report_times = sorted(utc(d["start_utc"]) for d in decisions if d["action"] == "report")

    # ---- truth: slots, closures, faults -------------------------------------------------------
    slots = rows(card_dir / "truth" / "v4_weather_truth.csv")
    slot_start = [utc(s["timestamp_utc"]) for s in slots]
    slot_len = [float(s["duration_seconds"]) for s in slots]
    slot_open = [s["is_observable"] == "true" for s in slots]
    q0 = float(config["q0"])
    slot_band_q = [float(s["transparency"]) * float(s["sky_quality"]) / float(s["seeing_arcsec"]) / q0 if is_open else 0.0
                   for s, is_open in zip(slots, slot_open)]
    slot_eff = [float(s["instrument_efficiency"] or 0.0) for s in slots]

    def open_seconds(a: float, b: float) -> float:
        total = 0.0
        i = max(0, bisect_right(slot_start, a) - 1)
        while i < len(slots) and slot_start[i] < b:
            lo, hi = max(a, slot_start[i]), min(b, slot_start[i] + slot_len[i])
            if hi > lo and slot_open[i]:
                total += hi - lo
            i += 1
        return total

    events = rows(card_dir / "truth" / "v4_events.csv")
    faults = []
    for e in events:
        if e["event_type"] != "instrument_fault":
            continue
        start, end = utc(e["actual_start_utc"]), utc(e["actual_end_utc"])
        repair = next((t for t in report_times if start <= t < end), None)
        faults.append({"start": start, "end": end, "repair": repair, "mult": float(e["instrument_efficiency_multiplier"])})

    def fault_at(moment: float):
        for f in faults:
            if f["start"] <= moment < (f["repair"] if f["repair"] is not None else f["end"]):
                return f
        return None

    # ---- hits ---------------------------------------------------------------------------------
    hits = defaultdict(list)          # target -> [(score, raw completeness, multiplier, duration, quality, index)]
    per_exposure_hits = defaultdict(int)
    for o in rows(run / "observations.csv"):
        if o["valid"] != "true":
            continue
        index = int(o["observe_index"])
        duration = float(observes[index]["duration_seconds"])
        quality = float(o["quality"])
        raw = duration * quality / need[o["target_id"]]
        hits[o["target_id"]].append((float(o["score"]), raw, float(o["prog_mult"]), duration, quality, index))
        per_exposure_hits[index] += 1

    ceiling = sum(weight.values()) * top
    gap = {"never": 0.0, "depth": 0.0, "multiplier": 0.0}
    depth_unreachable = 0.0           # part of the gap an hour at Q = 1 could not have closed
    mult_split = defaultdict(float)
    by_class = defaultdict(lambda: defaultdict(float))
    fibre = defaultdict(float)
    fault_loss = 0.0
    for t, w in weight.items():
        cls = targets[t]["target_class"] if "target_class" in targets[t] else targets[t].get("class", "?")
        by_class[cls]["ceiling"] += w * top
        by_class[cls]["n"] += 1
        hour_cap = min(1.0, 3600.0 / need[t])
        entries = hits.get(t, [])
        best = max(entries, default=None)
        if best is None or best[0] <= 0.0:
            gap["never"] += w * top
            by_class[cls]["never"] += w * top
            depth_unreachable += w * top * (1.0 - hour_cap)
            for e in entries:
                fibre["zero"] += e[3]
            continue
        score, raw, mult, duration, quality, index = best
        c = min(1.0, raw)
        gap["depth"] += w * mult * (1.0 - c)
        gap["multiplier"] += w * (top - mult)
        mult_split[f"{mult:.2f}"] += w * (top - mult)
        by_class[cls]["score"] += score
        by_class[cls]["depth"] += w * mult * (1.0 - c)
        by_class[cls]["multiplier"] += w * (top - mult)
        by_class[cls]["saturated"] += c >= 1.0
        depth_unreachable += w * mult * max(0.0, 1.0 - max(c, hour_cap))
        fibre["useful"] += duration / max(raw, 1.0)
        fibre["oversaturated"] += duration - duration / max(raw, 1.0)
        for e in entries:
            if e is not best:
                fibre["superseded"] += e[3]
        fault = fault_at(utc(observes[index]["start_utc"]))
        if fault is not None and c < 1.0:
            fault_loss += w * mult * (min(1.0, c / fault["mult"]) - c)

    exposure_seconds = sum(float(d["duration_seconds"]) for d in observes.values())
    fibre["empty"] = sum(float(d["duration_seconds"]) * (fibres - per_exposure_hits.get(i, 0)) for i, d in observes.items())
    fibre["capacity"] = exposure_seconds * fibres

    # ---- time ---------------------------------------------------------------------------------
    night = sum(slot_len)
    closed = sum(length for length, is_open in zip(slot_len, slot_open) if not is_open)
    idle_open = sum(open_seconds(a, b) for a, b in waits)
    exposure_closed = sum(float(d["duration_seconds"]) - open_seconds(utc(d["start_utc"]), utc(d["end_utc"])) for d in observes.values())
    blind = sum(float(d["duration_seconds"]) for i, d in observes.items() if per_exposure_hits.get(i, 0) == 0)
    open_len = [length for length, is_open in zip(slot_len, slot_open) if is_open]
    band_share = {name: sum(length for length, is_open, q in zip(slot_len, slot_open, slot_band_q) if is_open and lo <= q < hi) / max(1.0, sum(open_len))
                  for name, lo, hi in (("DARK", 0.65, 9e9), ("BRIGHT", 0.40, 0.65), ("BACKUP", -1.0, 0.40))}
    unrepaired = 0.0
    fault_rows = []
    for f in faults:
        until = f["repair"] if f["repair"] is not None else f["end"]
        seconds = open_seconds(f["start"], until)
        unrepaired += seconds
        fault_rows.append({"mult": round(f["mult"], 2), "open_hours_before_report": round(seconds / 3600.0, 1), "reported": f["repair"] is not None})
    mean_eff_open = sum(e * length for e, length, is_open in zip(slot_eff, slot_len, slot_open) if is_open) / max(1.0, sum(open_len))

    components = report["components"]
    counts = report["counts"]
    extras_ceiling = 100.0 * counts["observation_requests_issued"] + 100.0 * len(faults)
    extras = (components["observation_request_reward"] + components["report_settlement"]
              + components["required_penalty"] + components["uniformity_penalty"])
    return {
        "card": run.name.upper(), "total": report["total"], "ceiling": ceiling + extras_ceiling,
        "target_ceiling": ceiling, "target_score": components["sum_best_scores"], "gap": gap,
        "depth_gap_beyond_one_hour_at_q1": depth_unreachable, "multiplier_gap_by_best_multiplier": dict(mult_split),
        "extras_ceiling": extras_ceiling, "extras": extras,
        "extras_detail": {"requests": f"{counts['observation_requests_completed']}/{counts['observation_requests_issued']}",
                          "reports": components["report_settlement"], "required_missing": counts["required_missing"],
                          "uniformity": components["uniformity_penalty"]},
        "hours": {"night": night / 3600, "closed": closed / 3600, "exposure": exposure_seconds / 3600,
                  "idle_while_open": idle_open / 3600, "exposure_inside_closures": exposure_closed / 3600,
                  "exposures_without_any_hit": blind / 3600, "unrepaired_fault_open": unrepaired / 3600},
        "open_time_band_share_zenith_no_moon": band_share, "mean_instrument_efficiency_open_before_repairs": mean_eff_open,
        "fibre_share": {k: fibre[k] / fibre["capacity"] for k in ("useful", "oversaturated", "superseded", "zero", "empty")},
        "faults": fault_rows, "fault_depth_loss_on_best_exposures": fault_loss,
        "by_class": {k: dict(v) for k, v in by_class.items()},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run", type=Path, help="results/<set>/<tag> or one card folder inside it")
    parser.add_argument("--eval-set", default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    cards = cards_dir(args.eval_set) if args.eval_set else cards_dir()
    folders = [args.run] if (args.run / "score_report.json").exists() else sorted(p for p in args.run.iterdir() if (p / "score_report.json").exists())
    results = [breakdown(folder, cards / folder.name) for folder in folders]
    if args.json:
        print(json.dumps(results, indent=1))
        return 0

    print("| 卡 | 上限 | 实得 | 缺口 | 没观测到 | 曝光不够 | 其中一小时也曝不满 | 档位系数 | 请求/举报/必观测/均匀 |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for r in results:
        g = r["gap"]
        print(f"| {r['card']} | {r['ceiling']:,.0f} | {r['total']:,.0f} | {r['ceiling'] - r['total']:,.0f} | {g['never']:,.0f} | "
              f"{g['depth']:,.0f} | {r['depth_gap_beyond_one_hour_at_q1']:,.0f} | {g['multiplier']:,.0f} | {r['extras_ceiling'] - r['extras']:,.0f} |")
    print()
    print("| 卡 | 夜间 h | 关闭 h | 曝光 h | 开放却空等 h | 曝光落在关闭里 h | 全空曝光 h | 故障未修复 h | 开放时段可达 DARK/BRIGHT/BACKUP |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|---|")
    for r in results:
        h, b = r["hours"], r["open_time_band_share_zenith_no_moon"]
        print(f"| {r['card']} | {h['night']:.0f} | {h['closed']:.0f} | {h['exposure']:.0f} | {h['idle_while_open']:.0f} | "
              f"{h['exposure_inside_closures']:.0f} | {h['exposures_without_any_hit']:.0f} | {h['unrepaired_fault_open']:.1f} | "
              f"{b['DARK']:.0%} / {b['BRIGHT']:.0%} / {b['BACKUP']:.0%} |")
    print()
    print("| 卡 | 光纤时间：有效 | 超过满分所需 | 被更好的曝光取代 | 零分 | 空光纤 | 档位缺口按最佳曝光的系数 |")
    print("|---|---:|---:|---:|---:|---:|---|")
    for r in results:
        f = r["fibre_share"]
        split = ", ".join(f"{k}: {v:,.0f}" for k, v in sorted(r["multiplier_gap_by_best_multiplier"].items()) if v > 0.5)
        print(f"| {r['card']} | {f['useful']:.0%} | {f['oversaturated']:.0%} | {f['superseded']:.0%} | {f['zero']:.0%} | {f['empty']:.0%} | {split} |")
    print()
    for r in results:
        print(f"{r['card']} 各类目标（上限 / 实得 / 没观测到 / 曝光不够 / 档位）：")
        for cls, v in sorted(r["by_class"].items()):
            print(f"  {cls:5s} n={v['n']:.0f} 满分 {v.get('saturated', 0):.0f}  {v['ceiling']:,.0f} / {v.get('score', 0):,.0f} / "
                  f"{v.get('never', 0):,.0f} / {v.get('depth', 0):,.0f} / {v.get('multiplier', 0):,.0f}")
        print(f"  故障：{r['faults']}；故障期间最佳曝光少拿的深度分约 {r['fault_depth_loss_on_best_exposures']:,.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

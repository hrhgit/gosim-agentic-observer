#!/usr/bin/env python3
"""Build a local evaluation set whose assumed hidden conditions agree with the handover notes.

Source: evaluation_assets/formal-v5-new-four-reconstructed-v1 (formal public inputs, the received
bulletins / forecasts / requests of the new cards A1-D1, and assumed hidden conditions). That set places
each instrument fault at the start of the night in which an earlier agent happened to report it, models a
directional `cold_snap` as a mild whole-sky-like degradation and has no maintenance stops on A1/B1. The
handover notes of the same cards state otherwise: when each fault began and how deep it was, that a
directional cold-snap notice is a sector closed for wind, and when the telescope was stopped for work.
An agent that acts on the notes would be scored against conditions that contradict them.

This script changes exactly three things, all taken from the notes as read by the note reader
(results/extract-<card>-*.jsonl) and cross-checked against the downloaded formal run where it can be:

  1. instrument faults: onset = the announced / reported time (15-minute grid), depth = the stated level
     (0.55 when none is given); faults stay back to back as in the public generator. A fault is taken from
     the notes only when somebody states outright that the instrument dropped, or an announced job is
     followed by somebody noticing the drop;
  2. directional cold_wave events become closed sectors (force_close), closed at every altitude;
  3. whole-sky stops that no bulletin shows (maintenance, a power cut, a bare "we are stopped") become
     closed slots, but only where the formal run's own exposures in that window scored zero (so a
     misread note cannot invent a stop).

Version 2 (the default output) differs from v1 in points 2 and 3, both because of what the formal runs'
exposure results show (tools/check_notes_against_formal_runs.py): inside a cold-snap sector every fibre
scored zero up to the zenith (v1 kept the source set's 60 degree top, which let an agent reach the
"unreachable" urgent requests that nobody completed on the platform); and stops of any stated length,
confidence or cause were real (v1 took only firm maintenance / power stops of at most 8 hours).
`--v1` rebuilds the first version.

Everything else is copied unchanged. The result is still an assumption, not the official hidden data: it
tells how an agent behaves if the notes are true. Do not read local scores on it as platform predictions.

    python3.12 tools/build_note_aligned_set.py
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
PROJECT = HERE.parent.parent
CAPTURED = {"a": "8-a1-v5", "b": "7-b1-v5", "c": "6-c1-v5", "d": "5-d1-v5"}
DEFAULT_LEVEL = 0.55
SLOT = 900


def parse(text: str) -> dt.datetime:
    return dt.datetime.fromisoformat(text.replace("Z", "+00:00"))


def fmt(moment: dt.datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def grid(moment: dt.datetime) -> dt.datetime:
    seconds = round(moment.timestamp() / SLOT) * SLOT
    return dt.datetime.fromtimestamp(seconds, dt.timezone.utc)


def real_run(card: str, captured_root: Path) -> tuple[list[tuple[dt.datetime, bool]], list[tuple[dt.datetime, int, int]]]:
    """(report time, correct) and (exposure end, hits, zero-score hits) of the downloaded formal run."""
    reports, exposures = [], []
    path = captured_root / CAPTURED[card] / "captured.jsonl"
    if not path.exists():
        return reports, exposures
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            message = json.loads(line).get("message", {})
            if message.get("message_type") != "decision_request":
                continue
            payload = message["payload"]
            last = payload.get("last_result") or {}
            now = parse(payload["now_utc"])
            if last.get("action") == "report":
                reports.append((now, bool(last.get("correct"))))
            elif last.get("action") == "observe":
                scores = [hit.get("score", 0) for hit in last.get("hits", [])]
                exposures.append((now, len(scores), sum(1 for score in scores if score <= 0)))
    return reports, exposures


def fault_clusters(notes: list[dict]) -> list[dict]:
    """Group fault statements about the same event; a fault needs somebody to have seen the drop."""
    items = []
    for note in notes:
        for fault in note["utc"]["faults"]:
            if fault["onset"] and fault["kind"] in ("scheduled", "instrument", "unclear"):
                items.append({**fault, "onset": parse(fault["onset"]), "note": note["id"]})
    items.sort(key=lambda item: item["onset"])
    clusters: list[list[dict]] = []
    for item in items:
        if clusters and (item["onset"] - clusters[-1][-1]["onset"]).total_seconds() <= 18 * 3600:
            clusters[-1].append(item)
        else:
            clusters.append([item])
    out = []
    for group in clusters:
        seen = [item for item in group if item["kind"] in ("instrument", "unclear")]
        announced = [item for item in group if item["kind"] == "scheduled"]
        # A fault needs somebody saying outright that the instrument dropped, or an announced job followed
        # by somebody noticing the drop. "Readings a bit low, thin cloud maybe" on its own is not one.
        if not any(item["kind"] == "instrument" for item in seen) and not (announced and seen):
            continue
        onset = grid(announced[0]["onset"] if announced else min(item["onset"] for item in seen))
        levels = [item["level"] for item in group if item["level"]]
        out.append({"onset": onset, "level": min(levels) if levels else DEFAULT_LEVEL, "stated_level": bool(levels),
                    "announced": bool(announced), "notes": sorted({item["note"] for item in group})})
    return out


def build_card(card: str, source: Path, out: Path, notes: list[dict], captured_root: Path, v1: bool = False) -> dict:
    shutil.copytree(source, out)
    reports, exposures = real_run(card, captured_root)
    events_path = out / "truth" / "v4_events.csv"
    with events_path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames
        events = list(reader)
    old_faults = [row for row in events if row["event_type"] == "instrument_fault"]
    survey_end = max(parse(row["actual_end_utc"]) for row in events)
    survey_start = min(parse(row["actual_start_utc"]) for row in events)

    # 1. faults
    clusters = [c for c in fault_clusters(notes) if survey_start <= c["onset"] < survey_end]
    for index, cluster in enumerate(clusters):
        until = clusters[index + 1]["onset"] if index + 1 < len(clusters) else survey_end
        cluster["end"] = until
        cluster["formal_run_correct_report"] = next(
            (fmt(when) for when, correct in reports if correct and cluster["onset"] <= when < until), None)
    template = dict(old_faults[0])
    new_faults = []
    for index, cluster in enumerate(clusters):
        row = dict(template)
        row.update(event_id=f"NAF{index + 1:04d}", actual_start_utc=fmt(cluster["onset"]), actual_end_utc=fmt(cluster["end"]),
                   instrument_efficiency_multiplier=f"{cluster['level']:.6f}")
        new_faults.append(row)

    def multiplier(rows: list[dict], moment: dt.datetime) -> float:
        for row in rows:
            if parse(row["actual_start_utc"]) <= moment < parse(row["actual_end_utc"]):
                return float(row["instrument_efficiency_multiplier"])
        return 1.0

    # 2. directional cold wave = sector closed for wind
    wind = 0
    for row in events:
        if row["event_type"] == "cold_wave" and row["scope_type"] != "ALL" and row["force_close"] != "true":
            row["force_close"] = "true"
            if not v1:
                row["max_altitude_deg"] = "90.000000"
            wind += 1
    events = [row for row in events if row["event_type"] != "instrument_fault"] + new_faults
    events.sort(key=lambda row: (row["actual_start_utc"], row["event_id"]))
    with events_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(events)

    # 3. stops that appear in no bulletin, kept only where the formal run saw nothing but zeros
    stops = []
    for note in notes:
        for window in note["utc"]["windows"]:
            if not (window["all"] and window["effect"] == "closed"):
                continue
            if v1 and not (window["conf"] == "firm" and window["kind"] in ("maintenance", "power")):
                continue
            if not v1 and window["kind"] not in ("maintenance", "power", "other"):
                continue
            start, end = parse(window["t0"]), parse(window["t1"])
            if v1 and (end - start).total_seconds() > 8 * 3600:
                continue
            inside = [(hits, zero) for when, hits, zero in exposures if start + dt.timedelta(seconds=600) <= when <= end and hits]
            zero_share = sum(1 for hits, zero in inside if zero == hits) / len(inside) if inside else None
            stops.append({"start": fmt(start), "end": fmt(end), "note": note["id"], "kind": window["kind"], "confidence": window["conf"],
                          "formal_run_exposures": len(inside), "formal_run_all_zero_share": zero_share,
                          "applied": bool(inside) and len(inside) >= 2 and zero_share >= 0.7})
    unique = {(stop["start"], stop["end"]): stop for stop in stops}
    stops = sorted(unique.values(), key=lambda stop: stop["start"])
    applied = [(parse(stop["start"]), parse(stop["end"])) for stop in stops if stop["applied"]]

    weather_path = out / "truth" / "v4_weather_truth.csv"
    with weather_path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        weather_fields = reader.fieldnames
        slots = list(reader)
    closed_slots = rebaked = 0
    for slot in slots:
        moment = parse(slot["timestamp_utc"])
        old, new = multiplier(old_faults, moment), multiplier(new_faults, moment)
        if old != new and slot["instrument_efficiency"]:
            value = float(slot["instrument_efficiency"]) / old * new
            slot["instrument_efficiency"] = f"{min(1.0, max(0.05, value)):.6f}"
            rebaked += 1
        if slot["is_observable"] == "true" and any(start <= moment < end for start, end in applied):
            slot["is_observable"] = "false"
            closed_slots += 1
    with weather_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=weather_fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(slots)

    record = {
        "card": card,
        "faults": [{"onset": fmt(c["onset"]), "end": fmt(c["end"]), "level": c["level"], "level_stated": c["stated_level"],
                    "announced_in_advance": c["announced"], "notes": c["notes"],
                    "formal_run_correct_report": c["formal_run_correct_report"]} for c in clusters],
        "faults_in_source_set": len(old_faults),
        "wind_sectors_closed": wind,
        "stops": stops,
        "slots_closed_for_stops": closed_slots,
        "slots_with_changed_efficiency": rebaked,
    }
    (out / "generator" / "note-alignment.json").write_text(json.dumps(record, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return record


README = """# 新四卡 A1–D1 本地评测集：与交接记录一致的版本

来源：`evaluation_assets/{source}`（正式公开输入、已收到的公告/预报/请求原样保留，隐藏条件为自行推测）。
本目录只改三处，使自行推测的隐藏条件不再与交接记录的陈述相矛盾：

1. 仪器故障：起点取交接记录里预告或事后说明的时刻（对齐到 15 分钟），效率倍数取记录里说的“几成”，没说时保留 0.55；
   故障仍首尾相接（与公开生成程序一致）。原版把每个故障放在此前某次运行恰好报对的那一夜开头。
2. 带方向的 `cold_snap` 公告对应的事件改为该扇区关闭（记录里的“那个方向风大要关”），{sector_text}。
3. 公告里没有的停机（{stop_text}）：只在正式运行的曝光记录显示该时段全部零分时才写入关闭时段。
{version_text}
这些改动依据的是交接记录的文字，由读记录的程序提取，并尽量用下载到的正式运行记录交叉核对；每张卡的明细见
`cards/<卡>/generator/note-alignment.json`。**它们仍是假设，不是官方隐藏数据。** 在这套数据上，
“相信交接记录”的智能体自然占优；本地分数只用于检查行为是否符合预期和比较方案，不代表线上成绩。

构建命令（在 `agents/llm-observer-v1` 下）：`python3.12 tools/build_note_aligned_set.py`
"""


VERSION_2 = """
与 v1 的区别只有第 2、3 两点，依据都是下载到的正式运行里每次曝光的得分（`agents/llm-observer-v1/tools/check_notes_against_formal_runs.py`）：

- 只有带方向 `cold_snap` 公告生效时拍的 154 次曝光里，离公告方向 30° 以内的光纤从 30° 到 80° 高度几乎全部零分；
  v1 沿用的 60° 上限让本地智能体能拍到记录里说“风大关着、拍不到”的紧急请求，而平台上这 6 个请求没有一个完成
  （基线为它们拍了 62 次曝光，全部零分）。
- 记录里说的停机，不论写了多长、是谁说的、有没有说原因，落在其中的正式运行光纤 95–100% 零分；v1 只收了明确的检修/停电
  且不超过 8 小时的。

仍然没有写进本评测集的：正式运行里另有约 5,600 根零分光纤（全部约 17 万根）落在本评测集认为可观测的时段或方向上，
多数是整次曝光全零、公告和记录都没有解释的时段，以及偏离公告方向 30–60° 的扇区边缘。
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", default="formal-v5-new-four-reconstructed-v1")
    parser.add_argument("--v1", action="store_true", help="rebuild the first version (60 degree sector top, firm maintenance/power stops of at most 8 h)")
    parser.add_argument("--out", default=None, help="default: formal-v5-new-four-note-aligned-v2 (v1 with --v1)")
    parser.add_argument("--extractions", default=str(HERE / "results" / "extract-{card}-off.jsonl"))
    parser.add_argument("--captured", type=Path, default=PROJECT / "artifacts" / "formal-card-refresh-2026-10-05" / "captured-runs")
    parser.add_argument("--cards", nargs="+", default=["a", "b", "c", "d"])
    args = parser.parse_args()
    args.out = args.out or ("formal-v5-new-four-note-aligned-v1" if args.v1 else "formal-v5-new-four-note-aligned-v2")
    source = PROJECT / "evaluation_assets" / args.source
    out = Path(args.out) if Path(args.out).is_absolute() else PROJECT / "evaluation_assets" / args.out
    if out.exists():
        raise SystemExit(f"{out} already exists; remove it or choose another --out")
    (out / "cards").mkdir(parents=True)
    records = []
    for card in args.cards:
        notes = [json.loads(line) for line in Path(args.extractions.format(card=card)).read_text(encoding="utf-8").splitlines() if line.strip()]
        notes = [note for note in notes if note["origin"] == "model"]
        records.append(build_card(card, source / "cards" / card, out / "cards" / card, notes, args.captured, args.v1))
    (out / "README.md").write_text(README.format(
        source=args.source,
        sector_text="沿用来源评测集的高度上限 60°" if args.v1 else "从地平到天顶全部关闭",
        stop_text="只取记录里明确的检修、停电，且不超过 8 小时" if args.v1 else "检修、停电，或只说“今晚停着”的，不限时长",
        version_text="" if args.v1 else VERSION_2), encoding="utf-8")
    (out / "manifest.json").write_text(json.dumps(
        {"name": args.out, "source_set": args.source, "built_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
         "note": "assumed hidden conditions aligned with the handover notes; not official hidden data", "cards": records},
        ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    for record in records:
        confirmed = sum(1 for fault in record["faults"] if fault["formal_run_correct_report"])
        print(f"card {record['card']}: {len(record['faults'])} faults ({confirmed} with a correct report in the formal run; source set had "
              f"{record['faults_in_source_set']}), {record['wind_sectors_closed']} wind sectors closed, "
              f"{sum(1 for stop in record['stops'] if stop['applied'])}/{len(record['stops'])} stops applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Check what the note reader extracted against what really happened on the platform.

    python3 tools/check_notes_against_formal_runs.py            # all four new cards

The downloaded formal runs (artifacts/formal-card-refresh-2026-10-05/captured-runs) contain every
exposure result the platform sent to the baseline agent: which targets were hit and with what score.
For every window a note declared (closed sector, whole-sky stop, thin cloud, ...) this lists the
fibres of the real exposures that fall inside it, in time and in direction, and how many scored zero.
A closed window should show zeros only; a "shoot through it" window should show almost none.
The baseline chose its pointings without the notes, so some windows have no exposure in them.

Three further tables use the same exposure results:
  - wind sectors: fibres of exposures taken while a directional cold-snap notice was the only weather
    notice, by altitude and by azimuth distance from the notice's compass direction;
  - the notes' wind-closed windows split by whether the bulletin showed a cold-snap sector there;
  - the urgent requests whose own text says the sky there is closed: what the baseline's attempts scored.
"""
from __future__ import annotations

import bisect
import collections
import csv
import datetime as dt
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
PROJECT = HERE.parent.parent
RUNS = {"a": "8-a1-v5", "b": "7-b1-v5", "c": "6-c1-v5", "d": "5-d1-v5"}
CAPTURED = PROJECT / "artifacts" / "formal-card-refresh-2026-10-05" / "captured-runs"
CARDS = PROJECT / "evaluation_assets" / "formal-v5-new-four-reconstructed-v1" / "cards"


def parse(text: str) -> dt.datetime:
    return dt.datetime.fromisoformat(text.replace("Z", "+00:00"))


def altaz(ra: float, dec: float, lat: float, lon: float, when: dt.datetime) -> tuple[float, float]:
    days = (when - dt.datetime(2000, 1, 1, 12, tzinfo=dt.timezone.utc)).total_seconds() / 86400.0
    hour_angle = math.radians((280.46061837 + 360.98564736629 * days + lon - ra) % 360)
    d, la = math.radians(dec), math.radians(lat)
    alt = math.asin(max(-1.0, min(1.0, math.sin(d) * math.sin(la) + math.cos(d) * math.cos(la) * math.cos(hour_angle))))
    az = math.atan2(-math.cos(d) * math.sin(hour_angle), math.sin(d) * math.cos(la) - math.cos(d) * math.sin(la) * math.cos(hour_angle))
    return math.degrees(alt), math.degrees(az) % 360


def arc_distance(az: float, lo: float, span: float) -> float:
    rel = (az - lo) % 360
    return 0.0 if rel <= span else min(rel - span, 360 - rel)


def exposures(card: str) -> list[tuple[dt.datetime, list[tuple[float, float, float]]]]:
    """(end time, [(alt, az, score) per fibre]) for every exposure of the formal run."""
    base = CARDS / card
    site = json.loads((base / "config" / "v4_scenario.json").read_text())["site"]
    with (base / "public" / "targets.csv").open(encoding="utf-8") as handle:
        targets = {row["target_id"]: (float(row["ra_deg"]), float(row["dec_deg"])) for row in csv.DictReader(handle)}
    out = []
    with (CAPTURED / RUNS[card] / "captured.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            try:
                payload = (json.loads(line).get("message") or {}).get("payload") or {}
            except json.JSONDecodeError:
                continue
            result = payload.get("last_result")
            if not isinstance(result, dict) or result.get("action") != "observe" or not result.get("hits"):
                continue
            end = parse(payload["now_utc"])
            mid = end - dt.timedelta(minutes=4)
            out.append((end, [(*altaz(*targets[hit["target_id"]], site["latitude_deg"], site["longitude_deg"], mid), float(hit["score"]))
                              for hit in result["hits"] if hit["target_id"] in targets]))
    return out


COMPASS = {"N": 0, "NE": 45, "E": 90, "SE": 135, "S": 180, "SW": 225, "W": 270, "NW": 315}


def messages(card: str):
    """(bulletins [(time, [(kind, direction)])], requests {id: message}) of the formal run."""
    bulletins, requests = [], {}
    with (CAPTURED / RUNS[card] / "captured.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            try:
                payload = (json.loads(line).get("message") or {}).get("payload") or {}
            except json.JSONDecodeError:
                continue
            for message in payload.get("new_messages") or []:
                if message.get("record_type") == "bulletin":
                    bulletins.append((parse(message["issued_at_utc"]), [(n["event_kind"], n.get("direction")) for n in message.get("notices", [])
                                                                        if n["event_kind"] != "terrain_obstruction"]))
                elif message.get("record_type") == "observation_request":
                    requests[message["request_id"]] = message
    bulletins.sort(key=lambda item: item[0])
    return bulletins, requests


def sector_tables(cards: list[str]) -> None:
    grid: dict[tuple[int, int], list[int]] = collections.defaultdict(lambda: [0, 0])
    split: dict[tuple[str, str], list[int]] = collections.defaultdict(lambda: [0, 0])
    traps = []
    used = 0
    for card in cards:
        bulletins, requests = messages(card)
        times = [item[0] for item in bulletins]
        shots = exposures(card)
        ends = [shot[0] for shot in shots]

        def notices_at(moment: dt.datetime) -> list[tuple[str, str]]:
            index = bisect.bisect_right(times, moment) - 1
            return bulletins[index][1] if index >= 0 else []

        for end, fibres in shots:
            notices = notices_at(end - dt.timedelta(minutes=4))
            centres = [COMPASS[d] for kind, d in notices if kind == "cold_snap" and d in COMPASS]
            if not centres or any(kind != "cold_snap" for kind, _ in notices):
                continue
            used += 1
            for alt, az, score in fibres:
                away = min(abs((az - centre + 180) % 360 - 180) for centre in centres)
                cell = grid[(min(int(alt // 10) * 10, 80), min(int(away // 15) * 15, 90))]
                cell[0] += score <= 0
                cell[1] += 1
        notes = [json.loads(line) for line in (HERE / "results" / f"extract-{card}-off.jsonl").read_text(encoding="utf-8").splitlines()]
        latest = {}
        for note in notes:
            for window in note["utc"]["windows"]:
                if not window["all"] and window["kind"] == "wind" and window["effect"] == "closed":
                    latest[(window["t0"], window["t1"], window["azLo"], window["azSpan"])] = window
        for window in latest.values():
            t0, t1 = parse(window["t0"]), parse(window["t1"])
            for end, fibres in shots[bisect.bisect_left(ends, t0 + dt.timedelta(minutes=5)):bisect.bisect_right(ends, t1)]:
                centres = [COMPASS[d] for kind, d in notices_at(end - dt.timedelta(minutes=4)) if kind == "cold_snap" and d in COMPASS]
                for alt, az, score in fibres:
                    off_arc = arc_distance(az, window["azLo"], window["azSpan"])
                    if off_arc > 30:
                        continue
                    away = min((abs((az - centre + 180) % 360 - 180) for centre in centres), default=None)
                    where = "inside the stretch the note names" if off_arc == 0 else "up to 30 deg outside that stretch"
                    state = ("no cold-snap sector in the bulletin" if away is None else "notice direction within 30 deg" if away <= 30
                             else "notice direction 30-45 deg away" if away <= 45 else "notice direction farther away")
                    cell = split[(where, state)]
                    cell[0] += score <= 0
                    cell[1] += 1
        for rid, message in sorted(requests.items()):
            if float(message.get("completion_reward") or 0) <= 0 or not any(word in (message.get("reason") or "") for word in ("拍不到", "关着")):
                continue
            wanted = set(message["target_ids"])
            base = CARDS / card
            with (base / "public" / "targets.csv").open(encoding="utf-8") as handle:
                ids = {row["target_id"] for row in csv.DictReader(handle)}
            tried = zero = 0
            with (CAPTURED / RUNS[card] / "captured.jsonl").open(encoding="utf-8") as handle:
                for line in handle:
                    try:
                        payload = (json.loads(line).get("message") or {}).get("payload") or {}
                    except json.JSONDecodeError:
                        continue
                    result = payload.get("last_result")
                    if not isinstance(result, dict) or result.get("action") != "observe":
                        continue
                    if not message["issued_at_utc"] <= payload["now_utc"] <= message["deadline_utc"]:
                        continue
                    hits = result.get("hits") or []
                    if any(hit["target_id"] in wanted for hit in hits):
                        tried += 1
                        zero += all(float(hit["score"]) <= 0 for hit in hits)
            traps.append((card.upper() + "1", rid, float(message["completion_reward"]), tried, zero))
    print(f"\nwind sectors: {used} exposures taken while a directional cold-snap notice was the only weather notice")
    print("zero-score share of fibres, by altitude (rows) and azimuth distance from the notice's direction (columns):")
    columns = [0, 15, 30, 45, 60, 75, 90]
    print("  altitude  " + "".join(f"{c:>3d}-{c + 15:<3d}   " if c < 90 else "  90+   " for c in columns))
    for alt in range(30, 90, 10):
        cells = []
        for c in columns:
            zero, total = grid.get((alt, c), [0, 0])
            cells.append(f"{100 * zero / total:4.0f}% {total:<4d}" if total >= 5 else "    -    ")
        print(f"  {alt:>2d}-{alt + 10:<3d}    " + " ".join(cells))
    print("\nthe notes' wind-closed windows: fibres of the exposures taken inside them")
    for (where, state), (zero, total) in sorted(split.items()):
        print(f"  {where:36s} {state:38s} fibres {total:5d}  scored zero {100 * zero / max(total, 1):5.1f}%")
    print("\nurgent requests whose own text says that sky is closed for wind")
    for card, rid, reward, tried, zero in traps:
        print(f"  {card} {rid} reward {reward:.0f}: the baseline pointed at its targets in {tried} exposures, {zero} of them scored nothing at all")


def main() -> int:
    cards = sys.argv[1:] or list(RUNS)
    totals: dict[tuple[str, str, str], list[int]] = collections.defaultdict(lambda: [0, 0, 0, 0])
    for card in cards:
        notes = [json.loads(line) for line in (HERE / "results" / f"extract-{card}-off.jsonl").read_text(encoding="utf-8").splitlines()]
        shots = exposures(card)
        ends = [shot[0] for shot in shots]
        # The newest statement about a window wins: keep, per (start, end, arc), the last note's version.
        latest: dict[tuple, dict] = {}
        for note in notes:
            for window in note["utc"]["windows"]:
                latest[(window["t0"], window["t1"], window["all"], window.get("azLo"), window.get("azSpan"))] = window
        for window in latest.values():
            t0, t1 = parse(window["t0"]), parse(window["t1"])
            inside = shots[bisect.bisect_left(ends, t0 + dt.timedelta(minutes=5)):bisect.bisect_right(ends, t1)]
            zero = total = 0
            for _, fibres in inside:
                for alt, az, score in fibres:
                    if not window["all"] and arc_distance(az, window["azLo"], window["azSpan"]) > 0:
                        continue
                    total += 1
                    zero += score <= 0
            key = (window["kind"], window["effect"], window["conf"])
            row = totals[key]
            row[0] += 1
            row[1] += total > 0
            row[2] += zero
            row[3] += total
    print(f"cards {' '.join(cards)}: note windows against the formal runs' exposure results")
    print(f"{'kind':14s} {'effect':9s} {'conf':5s} {'windows':>8s} {'with exposures':>15s} {'fibres':>8s} {'scored zero':>12s}")
    for (kind, effect, conf), (count, seen, zero, total) in sorted(totals.items(), key=lambda item: (item[0][1], item[0][0], item[0][2])):
        share = f"{100 * zero / total:5.1f}%" if total else "     -"
        print(f"{kind:14s} {effect:9s} {conf:5s} {count:8d} {seen:15d} {total:8d} {share:>12s}")
    sector_tables(cards)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

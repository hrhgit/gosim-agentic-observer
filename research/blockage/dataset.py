"""Build strictly causal features from received formal feedback and public inputs.

python -m research.blockage.dataset --materials gosim-training-bundle --out artifacts/blockage-v1
"""
from __future__ import annotations

import argparse
import bisect
import csv
import datetime as dt
import importlib.util
import json
import math
import re
from pathlib import Path

import numpy as np
import pandas as pd

from .common import EVENTS, NOTES, ROOT, SEED, code_hashes, digest, freeze, write_json

_spec = importlib.util.spec_from_file_location("formal_notes_check", ROOT / "agents/llm-observer-v1/tools/check_notes_against_formal_runs.py")
_helper = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_helper)
COMPASS, altaz, arc_distance = _helper.COMPASS, _helper.altaz, _helper.arc_distance


def ts(text):
    return dt.datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()


def records(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def payload(record):
    return record.get("message", record).get("payload", record)


def angle(a, b):
    return abs((a - b + 180) % 360 - 180)


def mean_direction(coords):
    """Spherical mean: averaging RA numerically is wrong across 0/360 degrees."""
    vectors = [(math.cos(math.radians(d)) * math.cos(math.radians(r)),
                math.cos(math.radians(d)) * math.sin(math.radians(r)), math.sin(math.radians(d))) for r, d in coords]
    x, y, z = np.mean(vectors, axis=0)
    return math.degrees(math.atan2(y, x)) % 360, math.degrees(math.atan2(z, math.hypot(x, y)))


class PublicState:
    def __init__(self):
        self.bulletin = None
        self.terrain = set()
        self.forecasts = []
        self.received = {}
        self.since = {}

    def ingest(self, messages, received_at):
        for message in messages:
            issued = ts(message["issued_at_utc"])
            if issued > received_at:
                continue
            kind = message.get("record_type")
            if kind == "bulletin":
                pairs = {(n["event_kind"], n.get("direction", "ALL")) for n in message.get("notices", [])}
                self.terrain.update(d for k, d in pairs if k == "terrain_obstruction")
                active = {(k, d) for k, d in pairs if k in EVENTS}
                for pair in active:
                    self.since[pair] = self.since.get(pair, received_at) if self.bulletin and self.bulletin.get("night_id") == message.get("night_id") else received_at
                self.since = {p: v for p, v in self.since.items() if p in active}
                self.bulletin = message
            elif kind == "forecast":
                self.forecasts.append(message)
            elif kind == "observation_request":
                self.received.setdefault(message["request_id"], received_at)

    def features(self, t, az, night_id):
        f = {}
        notices = self.bulletin.get("notices", []) if self.bulletin and self.bulletin.get("night_id") == night_id else []
        forecasts = [m for m in self.forecasts if ts(m["coverage_start_utc"]) <= t < ts(m["coverage_end_utc"])]
        forecast = max(forecasts, key=lambda m: ts(m["issued_at_utc"]), default=None)
        for kind in EVENTS:
            selected = [n.get("direction", "ALL") for n in notices if n["event_kind"] == kind]
            all_sky = "ALL" in selected
            directions = [COMPASS[d] for d in selected if d in COMPASS]
            f.update({f"current_{kind}_active": int(bool(selected)), f"current_{kind}_all": int(all_sky),
                      f"current_{kind}_angle": 0 if all_sky else min((angle(az, d) for d in directions), default=180),
                      f"current_{kind}_directions": len(set(directions)),
                      f"current_{kind}_hours": max(((t - self.since.get((kind, d), t)) / 3600 for d in selected if d == "ALL" or (d in COMPASS and angle(az, COMPASS[d]) <= 30)), default=0)})
            listed = [n.get("direction", "ALL") for n in (forecast or {}).get("notices", [])
                      if n["event_kind"] == kind and (not n.get("nights") or night_id.removeprefix("N") in [s.replace("-", "") for s in n["nights"]])]
            f[f"forecast_{kind}_active"] = int(bool(listed))
            f[f"forecast_{kind}_angle"] = 0 if "ALL" in listed else min((angle(az, COMPASS[d]) for d in listed if d in COMPASS), default=180)
        f["terrain_angle"] = min((angle(az, COMPASS[d]) for d in self.terrain if d in COMPASS), default=180)
        f["no_current_bulletin"] = int(not any(n["event_kind"] in EVENTS for n in notices))
        f["no_forecast"] = int(forecast is None)
        return f


def note_features(notes, state, t, alt, az):
    f = {f"note_{kind}_{suffix}": 0 for kind in NOTES for suffix in ("active", "all", "direction", "closed", "degraded", "ok")}
    f.update(note_available=0, note_ridge_known=0, note_ridge_alt=0, note_ridge_margin=0, note_closed_angle=180, note_uncertain=0)
    windows, ridges = {}, {}
    for note in notes:
        issued = ts(note["utc"]["issued"])
        # A publication date alone does not prove this run had received the message.
        if issued >= t or state.received.get(note["id"], float("inf")) >= t:
            continue
        f["note_available"] = 1
        for window in note["utc"].get("windows", []):
            key = (window["t0"], window["t1"], window["all"], window.get("azLo"), window.get("azSpan"))
            windows[key] = window
        for terrain in note.get("terrain", []):
            match = re.fullmatch(r"([\d.]+)\+([\d.]+) ridge=([\d.]+|null) ok>=([\d.]+|null)", terrain)
            if match:
                lo, span, ridge, _ = match.groups()
                if ridge != "null":
                    ridges[(float(lo), float(span))] = float(ridge)
    for w in windows.values():
        if not ts(w["t0"]) <= t < ts(w["t1"]):
            continue
        kind = "thin_cloud" if w["kind"] == "thin_haze" else w["kind"]
        if kind not in NOTES:
            continue
        distance = 0 if w["all"] else arc_distance(az, w["azLo"], w["azSpan"])
        f[f"note_{kind}_active"] = 1
        f[f"note_{kind}_all"] |= int(w["all"])
        f[f"note_{kind}_direction"] |= int(distance == 0)
        if distance == 0:
            f[f"note_{kind}_{w['effect']}"] = 1
            f["note_uncertain"] |= int(w.get("conf") != "firm")
        if w["effect"] == "closed":
            f["note_closed_angle"] = min(f["note_closed_angle"], distance)
    covered = [ridge for (lo, span), ridge in ridges.items() if arc_distance(az, lo, span) == 0]
    if covered:
        f.update(note_ridge_known=1, note_ridge_alt=max(covered), note_ridge_margin=alt - max(covered))
    return f


def history_features(history, t, alt, az):
    f = dict(history_insufficient=int(len(history) < 16), previous_known=int(bool(history)),
             previous_zero=history[-1]["zero"] if history else 0,
             previous_minutes=(t - history[-1]["end"]) / 60 if history else 0)
    for hours in (1, 3):
        recent = [row for row in reversed(history) if t - hours * 3600 <= row["end"] <= t]
        for area in ("all", "near"):
            subset = recent if area == "all" else [r for r in recent if angle(az, r["az"]) <= 30 and abs(alt - r["alt"]) <= 10]
            for zero in (0, 1):
                f[f"history_{hours}h_{area}_{'zero' if zero else 'score'}"] = sum(r["zero"] == zero for r in subset)
    sector = int((az + 22.5) % 360 // 45)
    same = [r for r in history if int((r["az"] + 22.5) % 360 // 45) == sector]
    for zero, name, reducer in ((1, "zero_max_alt", max), (0, "score_min_alt", min)):
        heights = [r["alt"] for r in same if r["zero"] == zero]
        f[f"history_{name}_known"] = int(bool(heights))
        f[f"history_{name}"] = reducer(heights) if heights else 0
    return f


def extract_run(run, targets, notes, limit=0):
    raw = records(run / "captured.jsonl")
    init = next(payload(r) for r in raw if r.get("message", r).get("message_type") == "initialize")
    card = init["task_card"]["card_id"].lower()
    site, survey = init["site"], init["survey"]
    nights = [(ts(n["observing_start_utc"]), ts(n["observing_end_utc"]), n["night_id"]) for n in survey["nights"]]
    night_starts = [n[0] for n in nights]
    season_start, season_end = ts(survey["start_utc"]), ts(survey["end_utc"])
    actions = records(run / "actions.jsonl") if (run / "actions.jsonl").exists() else None
    decisions = [payload(r) for r in raw if r.get("message", r).get("message_type") == "decision_request"]
    if actions is not None and len(actions) != len(decisions):
        raise ValueError(f"actions/requests misaligned in {run}: {len(actions)} != {len(decisions)}")
    state, history, out = PublicState(), [], []
    for i, decision in enumerate(decisions):
        end = ts(decision["now_utc"])
        result = decision.get("last_result") or {}
        if i:
            start = ts(decisions[i - 1]["now_utc"])
            hits = result.get("hits") or []
            duration = end - start
            if result.get("action") == "observe" and hits and 0 < duration <= 4000:
                coords = [targets[h["target_id"]] for h in hits if h["target_id"] in targets]
                if not coords:
                    raise ValueError(f"unknown hit targets in {run}")
                if actions is not None:
                    action = actions[i - 1]
                    if action.get("action") != "observe":
                        raise ValueError(f"preceding action is not observe: {run}/{i}")
                    alt, az = action["pointing"]["alt_deg"], action["pointing"]["az_deg"]
                else:
                    ra, dec = mean_direction(coords)
                    alt, az = altaz(ra, dec, site["latitude_deg"], site["longitude_deg"], dt.datetime.fromtimestamp((start + end) / 2, dt.timezone.utc))
                ni = bisect.bisect_right(night_starts, start) - 1
                if ni < 0:
                    raise ValueError("exposure before first night")
                n0, n1, night_id = nights[ni]
                f = dict(altitude=alt, az_sin=math.sin(math.radians(az)), az_cos=math.cos(math.radians(az)),
                         duration=duration, night_elapsed_hours=(start - n0) / 3600,
                         night_remaining_hours=(n1 - start) / 3600,
                         season_progress=(start - season_start) / (season_end - season_start))
                f.update(state.features(start, az, night_id))
                f.update(history_features(history, start, alt, az))
                f.update(note_features(notes if card.endswith("1") else [], state, start, alt, az))
                zero = int(all(float(h["score"]) == 0 for h in hits))
                out.append(dict(row_id=f"{run.parent.name}/{run.name}/{i}", run=f"{run.parent.name}/{run.name}",
                                source="probe" if actions is not None else "baseline", card=card,
                                start=start, end=end, alt=alt, az=az, zero=zero,
                                first_zero=int(zero and (not history or not history[-1]["zero"])),
                                direction_inferred=int(actions is None), partial_zero=int(0 < sum(float(h["score"]) == 0 for h in hits) < len(hits)),
                                **{f"f_{k}": v for k, v in f.items()}))
                history.append(dict(start=start, end=end, alt=alt, az=az, zero=zero))
                if limit and len(out) >= limit:
                    break
        # Ingest the result-bearing request only AFTER extracting the preceding exposure.
        state.ingest(decision.get("new_messages") or [], end)
    scored_ends = [r["end"] for r in out if not r["zero"]]
    for row in out:
        j = bisect.bisect_left(scored_ends, row["end"] - 3600)
        row["avoidable_hint"] = int(row["zero"] and j < len(scored_ends) and scored_ends[j] <= row["end"] + 3600)
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--materials", type=Path, default=ROOT / "gosim-training-bundle")
    p.add_argument("--out", type=Path, default=ROOT / "artifacts/blockage-v1")
    p.add_argument("--limit-per-run", type=int, default=0, help="smoke only; never use truncated rows for formal gates")
    args = p.parse_args()
    materials = args.materials.resolve()
    captures = materials / "artifacts/formal-card-refresh-2026-10-05"
    runs = sorted((captures / "captured-runs").iterdir()) + sorted((captures / "probe-new-four").iterdir())
    runs = [r for r in runs if (r / "captured.jsonl").exists()]
    public = materials / "evaluation_assets/formal-v5-new-four-reconstructed-v1/cards"
    files = [r / "captured.jsonl" for r in runs] + [r / "initialize.json" for r in runs]
    files += [r / "actions.jsonl" for r in runs if (r / "actions.jsonl").exists()]
    files += [public / c / "public/targets.csv" for c in "abcd"]
    files += [materials / f"agents/llm-observer-v1/results/extract-{c}-off.jsonl" for c in "abcd"]
    protocol = dict(stage="dataset", seed=SEED, sources={str(f.relative_to(materials)): digest(f) for f in files},
                    materials=str(materials), code=code_hashes(), limit_per_run=args.limit_per_run,
                    forbidden_inputs=["hidden_weather", "future_messages", "current_outcome", "avoidable_hint"],
                    historical_feedback="same run only, available at start; strict note publication and delivery before start",
                    direction="probe: previous recorded action; baseline: spherical hit centroid at midpoint, approximate",
                    sample="observe with hits and 0 < preceding request gap <= 4000 s", timestamps="previous request / result request",
                    selection_bias="hit-bearing exposures only; baseline chosen pointings; no causal rescue claim")
    freeze(args.out, protocol)
    try:
        rows = []
        for run in runs:
            init = payload(next(r for r in records(run / "captured.jsonl") if r.get("message", r).get("message_type") == "initialize"))
            family = init["task_card"]["card_id"].lower()[0]
            with (public / family / "public/targets.csv").open(encoding="utf-8") as handle:
                targets = {r["target_id"]: (float(r["ra_deg"]), float(r["dec_deg"])) for r in csv.DictReader(handle)}
            notes = records(materials / f"agents/llm-observer-v1/results/extract-{family}-off.jsonl")
            extracted = extract_run(run, targets, notes, args.limit_per_run)
            rows.extend(extracted)
            print(f"{run.name}: {len(extracted)} exposures, {sum(r['zero'] for r in extracted)} zero", flush=True)
        frame = pd.DataFrame(rows)
        frame.to_parquet(args.out / "exposures.parquet", index=False)
        write_json(args.out / "dataset-summary.json", dict(rows=len(frame), zero=int(frame.zero.sum()),
                   features=[c for c in frame if c.startswith("f_")], counts=frame.groupby(["card", "source"]).agg(exposures=("zero", "size"), zero=("zero", "sum"), partial=("partial_zero", "sum")).reset_index().to_dict("records"),
                   dataset_sha256=digest(args.out / "exposures.parquet")))
    except Exception as e:
        write_json(args.out / "failure.json", dict(error=repr(e)))
        raise


if __name__ == "__main__":
    main()

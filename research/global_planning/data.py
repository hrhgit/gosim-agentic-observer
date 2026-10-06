"""Explicitly self-generated micro curricula; never edit a formal card."""
from __future__ import annotations

import csv
import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .common import ENGINE, ROOT, save, digest

sys.path.insert(0, str(ENGINE))
from challenge.v4_fiber_map import altaz_to_radec

MICRO_KINDS = ("deadline", "required", "coverage", "repeat", "changing_weather", "request_group", "delayed_request", "mixed")


def write_csv(path, fields, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def utc(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def micro_entries():
    return [dict(id=f"micro-{split}-{i:03}", split=split, seed=8200000 + si * 1000 + i,
                 kind=MICRO_KINDS[i % len(MICRO_KINDS)], source="SELF-GENERATED", card="micro")
            for si, (split, n) in enumerate((("train", 32), ("validation", 8), ("test", 16))) for i in range(n)]


def formal_entries():
    return [dict(id=f"formal-{split}-{card}-{i}", split=split, card=card,
                 seed=8300000 + si * 10000 + ci * 100 + i, source="SELF-GENERATED hidden conditions")
            for si, (split, n) in enumerate((("train", 4), ("validation", 2), ("test", 4), ("stress", 2)))
            for ci, card in enumerate("abcd") for i in range(n)]


def build_micro(entry, root):
    root = Path(root)
    metadata = root / "source.json"
    if metadata.exists():
        if json.loads(metadata.read_text())["entry"] != entry:
            raise ValueError("Micro source changed; use a new experiment directory")
        return root
    rng = random.Random(entry["seed"])
    start = datetime(2026, 6, 1, 2, tzinfo=timezone.utc) + timedelta(days=rng.randrange(90))
    steps = entry.get("steps", 4)
    end = start + timedelta(seconds=60 * steps)
    site = dict(name="SELF-GENERATED micro site", latitude_deg=-24.6157, longitude_deg=-70.3976, utc_offset_hours=-4.)
    fiber = dict(schema_version="v4-fiber-map-v1", site=site,
                 field=dict(n_fibers=1, fiber_area_deg2=.4, gap_deg=0.),
                 exposure=dict(min_duration_seconds=60, max_duration_seconds=60))
    score = json.loads((ROOT / "cards/a/config/v4_score_config.json").read_text())
    score["uniformity"]["weight"] = 12. if entry["kind"] in ("coverage", "mixed") else 0.
    score["required"]["penalty_per_missing"] = 12.
    rows = []
    for j, az in enumerate((70, 110, 150, 200, 250, 290)):
        ra, dec = altaz_to_radec(55 + rng.uniform(-5, 5), az + rng.uniform(-4, 4), start, site["latitude_deg"], site["longitude_deg"])
        rows.append(dict(target_id=f"T{j}", ra_deg=ra, dec_deg=dec, target_class="LRG",
                         feature_flux=rng.uniform(5, 10), science_weight=rng.uniform(6, 20),
                         required="true" if j == 0 and entry["kind"] in ("required", "mixed") else "false"))
    write_csv(root / "public/targets.csv", list(rows[0]), rows)
    write_csv(root / "public/footprint.csv", ["component_id", "vertex_index", "ra_deg", "dec_deg"], [])
    write_csv(root / "public/v4_night_calendar.csv",
              ["night_id", "night_date", "observing_start_utc", "observing_end_utc", "slot_count"],
              [dict(night_id="N0001", night_date=start.date().isoformat(), observing_start_utc=utc(start), observing_end_utc=utc(end), slot_count=steps)])
    slots, weather = [], []
    for i in range(steps):
        sid = f"N0001-S{i:04}"
        slots.append(dict(slot_id=sid, timestamp_utc=utc(start + timedelta(seconds=60*i)), duration_seconds=60))
        poor = entry["kind"] == "changing_weather" and i < 2
        weather.append(dict(slot_id=sid, is_observable="true", seeing_arcsec=4 if poor else 1.,
                            transparency=.35 if poor else .9, sky_quality=.3 if poor else .9, instrument_efficiency=1.))
    write_csv(root / "truth/v4_slots.csv", list(slots[0]), slots)
    write_csv(root / "truth/v4_weather_truth.csv", list(weather[0]), weather)
    event_fields = ["event_id", "event_type", "actual_start_utc", "actual_end_utc", "scope_type", "azimuth_start_deg", "azimuth_end_deg", "min_altitude_deg", "max_altitude_deg", "seeing_multiplier", "transparency_multiplier", "sky_quality_multiplier", "instrument_efficiency_multiplier", "force_close"]
    write_csv(root / "truth/v4_events.csv", event_fields, [])
    write_csv(root / "truth/v4_earthquake_effects.csv", ["event_id"], [])
    for name in ("v4_bulletins.jsonl", "v4_forecasts.jsonl"):
        (root / "public" / name).write_text("")
    requests = []
    if entry["kind"] in ("deadline", "request_group", "delayed_request", "mixed"):
        group = entry["kind"] == "request_group"
        requests.append(dict(schema_version="v4-observation-request-v1", record_type="observation_request",
            request_id="R0", issued_at_utc=utc(start + timedelta(seconds=120 if entry["kind"] == "delayed_request" else 0)),
            deadline_utc=utc(end if group or entry["kind"] == "delayed_request" else start + timedelta(seconds=120)),
            target_ids=["T0", "T1"] if group else ["T0"], minimum_completed=2 if group else 1,
            completion_factor_threshold=.5, completion_reward=rng.uniform(20, 40), reason="SELF-GENERATED task"))
    (root / "truth/v4_observation_requests.jsonl").write_text("".join(json.dumps(r) + "\n" for r in requests))
    save(root / "config/v4_fiber_config.json", fiber)
    save(root / "config/v4_score_config.json", score)
    products = {name: "../" + value for name, value in {
        "targets_csv": "public/targets.csv", "footprint_csv": "public/footprint.csv",
        "night_calendar_csv": "public/v4_night_calendar.csv", "bulletins_jsonl": "public/v4_bulletins.jsonl",
        "forecasts_jsonl": "public/v4_forecasts.jsonl", "slots_csv": "truth/v4_slots.csv",
        "weather_truth_csv": "truth/v4_weather_truth.csv", "events_csv": "truth/v4_events.csv",
        "earthquake_effects_csv": "truth/v4_earthquake_effects.csv", "observation_requests_jsonl": "truth/v4_observation_requests.jsonl"}.items()}
    save(root / "config/v4_scenario.json", dict(schema_version="v4-scenario-v1", name="SELF-GENERATED micro",
        site=site, minimum_altitude_deg=30., fiber_config="v4_fiber_config.json", score_config="v4_score_config.json",
        products=products, stress=dict(enabled=False), limits=dict(global_wallclock_seconds=900),
        task_card=dict(card_id="SELF-GENERATED-MICRO")))
    save(metadata, dict(entry=entry, official_hidden_data=False, generator="global-micro-v1",
                        description="Fully self-generated catalogue/calendar/weather/requests and scaled penalties; not a formal card",
                        hashes={str(p.relative_to(root)): digest(p) for p in root.rglob('*') if p.is_file()}))
    return root

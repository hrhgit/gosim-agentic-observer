"""Observation-request generation, automatic attribution, and ledger replay."""
from __future__ import annotations

import json
import math
from dataclasses import replace
from datetime import timedelta

import pytest

from challenge import v4_runner as vr
from challenge import v4_scorer as vs
from challenge.v4_fiber_map import FiberGrid, radec_to_altaz


def _request(target_id, start, end, *, threshold=0.5, reward=100.0, request_id="V4RQ0001",
             minimum_completed=1):
    target_ids = list(target_id) if isinstance(target_id, (list, tuple)) else [target_id]
    return {
        "schema_version": "v4-observation-request-v1",
        "record_type": "observation_request",
        "request_id": request_id,
        "issued_at_utc": start,
        "deadline_utc": end,
        "target_ids": target_ids,
        "minimum_completed": minimum_completed,
        "completion_factor_threshold": threshold,
        "completion_reward": reward,
        "reason": "test follow-up",
    }


def test_request_progress_uses_only_valid_exposures_inside_the_window():
    from datetime import datetime, timezone

    start = datetime(2026, 10, 2, tzinfo=timezone.utc)
    end = start + timedelta(hours=2)
    request = _request("T1", start, end)
    ledger = vs.BestLedger()
    ledger.record(0, "T1", 0.9, 0.9, start - timedelta(seconds=1), start + timedelta(minutes=10))
    ledger.record(1, "T1", 0.6, 0.6, start + timedelta(minutes=10), start + timedelta(minutes=25))
    status = vs.observation_request_status(request, ledger)
    assert status["completed"] is True and status["reward"] == 100.0
    ledger.invalidate_window(1, 2, "LOSS")
    status = vs.observation_request_status(request, ledger)
    assert status["completed"] is False and status["reward"] == 0.0


def test_runner_publishes_request_tracks_progress_and_awards_it(v4_reference_dir, tmp_path, monkeypatch):
    scenario_path = v4_reference_dir / "v4_scenario_default.json"
    scenario = vr.load_scenario(scenario_path)
    slots = scenario.slots[:2]
    start, deadline = slots[0].start_utc, slots[0].end_utc
    lat, lon = float(scenario.site["latitude_deg"]), float(scenario.site["longitude_deg"])
    target = next(
        item
        for item in scenario.targets
        if float(item["feature_flux"]) > 0.2
        and 50.0 < radec_to_altaz(item["ra_deg"], item["dec_deg"], start, lat, lon)[0] < 70.0
    )
    request = _request(target["target_id"], start, deadline, threshold=0.001, reward=42.0)
    short = replace(
        scenario,
        slots=slots,
        survey_start=start,
        survey_end=slots[-1].end_utc,
        bulletins=[],
        forecasts=[],
        observation_requests=[request],
    )
    monkeypatch.setattr(vr, "load_scenario", lambda _: short)
    grid = FiberGrid.from_config(scenario.fiber_config)
    alt, az = radec_to_altaz(target["ra_deg"], target["dec_deg"], start, lat, lon)
    d_alt, d_az = grid.fiber_center_offset(5)
    cmd_alt = alt - d_alt
    observe = {
        "action": "observe",
        "pointing": {"alt_deg": cmd_alt, "az_deg": (az - d_az / math.cos(math.radians(cmd_alt))) % 360.0},
        "assignments": {"5": target["target_id"]},
        "duration_seconds": 900,
        "program": "BACKUP",
    }
    snapshots = []
    actions = iter([observe, None])

    def factory(_context):
        def agent(snapshot):
            snapshots.append(snapshot)
            return next(actions)
        return agent

    report = vr.run_scenario(scenario_path, factory, tmp_path / "run")
    assert snapshots[0]["active_requests"][0]["remaining_count"] == 1
    assert snapshots[1]["active_requests"] == []
    result = next(
        item for item in snapshots[1]["new_messages"]
        if item["record_type"] == "observation_request_result"
    )
    assert result["status"] == "completed" and result["score_delta"] == 42.0
    assert report["components"]["observation_request_reward"] == 42.0
    assert report["counts"]["observation_requests_completed"] == 1
    written = [json.loads(line) for line in (tmp_path / "run" / "messages.jsonl").read_text().splitlines()]
    assert [row["record_type"] for row in written] == ["observation_request", "observation_request_result"]


# --- deadline boundary and overlapping requests (scorer level) -------------------------


def test_exposure_ending_exactly_at_the_deadline_counts():
    from datetime import datetime, timezone

    start = datetime(2026, 10, 2, tzinfo=timezone.utc)
    deadline = start + timedelta(hours=2)
    request = _request("T1", start, deadline)
    on_time = vs.BestLedger()
    on_time.record(0, "T1", 0.9, 0.9, deadline - timedelta(seconds=900), deadline)
    assert vs.observation_request_status(request, on_time)["completed"] is True
    late = vs.BestLedger()
    late.record(0, "T1", 0.9, 0.9, deadline - timedelta(seconds=900), deadline + timedelta(seconds=1))
    assert vs.observation_request_status(request, late)["completed"] is False


def test_overlapping_requests_on_one_target_each_award():
    from datetime import datetime, timezone

    start = datetime(2026, 10, 2, tzinfo=timezone.utc)
    deadline = start + timedelta(hours=2)
    first = _request("T1", start, deadline, reward=100.0, request_id="V4RQ0001")
    second = _request("T1", start, deadline, reward=200.0, request_id="V4RQ0002")
    ledger = vs.BestLedger()
    ledger.record(0, "T1", 0.9, 0.9, start + timedelta(minutes=10), start + timedelta(minutes=25))
    statuses, total = vs.settle_observation_requests([first, second], ledger)
    assert [item["completed"] for item in statuses] == [True, True]
    assert total == 300.0  # each request settles independently


# --- request revision after data loss: differential score_delta (runner level) ---------


def _observe_for(scenario, target_id, at, duration=900):
    grid = FiberGrid.from_config(scenario.fiber_config)
    lat, lon = float(scenario.site["latitude_deg"]), float(scenario.site["longitude_deg"])
    target = next(item for item in scenario.targets if item["target_id"] == target_id)
    alt, az = radec_to_altaz(target["ra_deg"], target["dec_deg"], at, lat, lon)
    d_alt, d_az = grid.fiber_center_offset(5)
    cmd_alt = alt - d_alt
    return {
        "action": "observe",
        "pointing": {"alt_deg": cmd_alt, "az_deg": (az - d_az / math.cos(math.radians(cmd_alt))) % 360.0},
        "assignments": {"5": target_id},
        "duration_seconds": duration,
        "program": "BACKUP",
    }


def _target_up(scenario, at, exclude=()):
    lat, lon = float(scenario.site["latitude_deg"]), float(scenario.site["longitude_deg"])
    return next(
        item
        for item in scenario.targets
        if item["target_id"] not in exclude
        and float(item["feature_flux"]) > 0.2
        and 50.0 < radec_to_altaz(item["ra_deg"], item["dec_deg"], at, lat, lon)[0] < 70.0
    )


def _loss_row(slot, start_fraction, end_fraction):
    return {
        "event_id": "V4ST0001",
        "event_type": "data_loss",
        "trigger_mode": "fixed_slot",
        "trigger_ref": slot.slot_id,
        "window_start_fraction": str(start_fraction),
        "window_end_fraction": str(end_fraction),
        "window_max_fraction": "1.0",
        "alt_offset_rad": "",
        "az_offset_rad": "",
    }


def _run_with_loss(v4_reference_dir, tmp_path, monkeypatch, request, actions, loss_row, n_slots=4):
    scenario_path = v4_reference_dir / "v4_scenario_default.json"
    scenario = vr.load_scenario(scenario_path)
    slots = scenario.slots[:n_slots]
    short = replace(
        scenario,
        config={**scenario.config, "stress": {"enabled": True}},
        slots=slots,
        survey_start=slots[0].start_utc,
        survey_end=slots[-1].end_utc,
        bulletins=[],
        forecasts=[],
        observation_requests=[request],
        stress_rows=[loss_row],
    )
    monkeypatch.setattr(vr, "load_scenario", lambda _: short)
    stream = iter(actions)

    def factory(_context):
        return lambda _snapshot: next(stream, None)

    report = vr.run_scenario(scenario_path, factory, tmp_path / "run")
    written = [json.loads(line) for line in (tmp_path / "run" / "messages.jsonl").read_text().splitlines()]
    results = [row for row in written if row["record_type"] == "observation_request_result"]
    return report, results


def test_data_loss_revision_withdraws_the_announced_reward(v4_reference_dir, tmp_path, monkeypatch):
    scenario = vr.load_scenario(v4_reference_dir / "v4_scenario_default.json")
    slots = scenario.slots[:4]
    start, deadline = slots[0].start_utc, slots[1].end_utc
    target = _target_up(scenario, start)
    request = _request(target["target_id"], start, deadline, threshold=0.001, reward=100.0)
    actions = [
        _observe_for(scenario, target["target_id"], start),
        {"action": "wait", "duration_seconds": 900},
        {"action": "wait", "duration_seconds": 900},
    ]
    report, results = _run_with_loss(
        v4_reference_dir, tmp_path, monkeypatch, request, actions, _loss_row(slots[3], 0.0, 1.0)
    )
    assert [row["status"] for row in results] == ["completed", "expired"]
    assert results[0]["score_delta"] == 100.0 and results[0]["revised"] is False
    assert results[1]["score_delta"] == -100.0 and results[1]["revised"] is True
    assert report["components"]["observation_request_reward"] == 0.0


def test_data_loss_revision_with_an_unchanged_outcome_costs_nothing(v4_reference_dir, tmp_path, monkeypatch):
    scenario = vr.load_scenario(v4_reference_dir / "v4_scenario_default.json")
    slots = scenario.slots[:4]
    start, deadline = slots[0].start_utc, slots[1].end_utc
    first = _target_up(scenario, start)
    second = _target_up(scenario, slots[1].start_utc, exclude={first["target_id"]})
    request = _request(
        [first["target_id"], second["target_id"]], start, deadline,
        threshold=0.001, reward=100.0, minimum_completed=1,
    )
    actions = [
        _observe_for(scenario, first["target_id"], start),
        _observe_for(scenario, second["target_id"], slots[1].start_utc),
        {"action": "wait", "duration_seconds": 900},
        {"action": "wait", "duration_seconds": 900},
    ]
    # Invalidate only the first of the two exposures: the request stays completed.
    report, results = _run_with_loss(
        v4_reference_dir, tmp_path, monkeypatch, request, actions, _loss_row(slots[3], 0.0, 0.5)
    )
    assert [row["status"] for row in results] == ["completed", "completed"]
    assert results[0]["score_delta"] == 100.0 and results[0]["revised"] is False
    assert results[1]["score_delta"] == 0.0 and results[1]["revised"] is True
    assert results[1]["completed_target_ids"] == [second["target_id"]]
    assert report["components"]["observation_request_reward"] == 100.0


# --- generator: targets need enough continuous observable time ---------------------------


def test_generator_rejects_targets_without_a_long_enough_continuous_window():
    from datetime import datetime, timezone

    from challenge import v4_observation_requests as vreq
    from challenge.tile_geometry_simulator import _local_sidereal_deg

    site = {"latitude_deg": -24.6157, "longitude_deg": -70.3976}
    lat, lon = site["latitude_deg"], site["longitude_deg"]
    nights, slots = [], []
    for day in (2, 3, 4, 5):  # four short 30-minute nights
        date = f"2026-10-{day:02d}"
        night_id = f"N202610{day - 1:02d}"
        nights.append({
            "night_id": night_id,
            "night_date": f"2026-10-{day - 1:02d}",
            "observing_start_utc": f"{date}T00:00:00Z",
            "observing_end_utc": f"{date}T00:30:00Z",
        })
        for index in (1, 2):
            start = datetime(2026, 10, day, 0, 0, tzinfo=timezone.utc) + timedelta(seconds=(index - 1) * 900)
            slots.append({
                "slot_id": f"{night_id}-S{index:03d}",
                "night_id": night_id,
                "timestamp_utc": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "duration_seconds": "900",
            })
    # Transit at the middle of the request window's first night: above the limit all night.
    mid = datetime(2026, 10, 3, 0, 15, tzinfo=timezone.utc)
    ra = _local_sidereal_deg(mid, lon)
    targets = [
        {"target_id": "BRIGHT", "ra_deg": str(ra), "dec_deg": str(lat), "required": "false",
         "feature_flux": "1.0"},   # need = 0.5*0.5*900/1.0 = 225 s -> fits a 30-minute night
        {"target_id": "FAINT", "ra_deg": str(ra), "dec_deg": str(lat), "required": "false",
         "feature_flux": "0.08"},  # need = 2812.5 s -> no continuous run long enough
    ]
    records = vreq.build_requests(
        seed=7,
        targets=targets,
        nights=nights,
        slots=slots,
        site=site,
        minimum_altitude_deg=30.0,
        settings={"count": 1, "targets_per_request": 1, "window_nights": 2,
                  "minimum_feature_flux": 0.0},
        flux_zero_point=0.5,
        exposure_zero_point_seconds=900,
        min_duration_seconds=60,
        max_duration_seconds=3600,
    )
    assert len(records) == 1
    assert records[0]["target_ids"] == ["BRIGHT"]

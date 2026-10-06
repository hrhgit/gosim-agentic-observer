#!/usr/bin/env python3
"""Generate deterministic v4 time-limited observation requests.

Requests refer only to targets already present in the public catalogue.  The future
request stream is written to the truth side of a card; the runner publishes each row
when ``issued_at_utc`` is reached.  Selection checks geometric observability inside the
request window and does not inspect future weather.
"""

from __future__ import annotations

import csv
import json
import math
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping, Sequence

from .contracts import derive_stream_seed, write_text_lf
from .v4_fiber_map import radec_to_altaz

SCHEMA_VERSION = "v4-observation-request-v1"
STREAM_NAME = "v4.observation_requests"
DEFAULTS = {
    "count": None,
    "targets_per_request": 8,
    "minimum_completion_fraction": 0.75,
    "window_nights": 2,
    "completion_reward": 100.0,
    "completion_factor_threshold": 0.5,
    "minimum_feature_flux": 0.0,
    "include_required_targets": False,
    "reason": "time-critical follow-up",
}


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _format_utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_csv(path: Path) -> list[dict[str, str]]:
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def validate_settings(settings: Mapping) -> dict:
    """Return normalized settings, rejecting ambiguous or impossible values."""
    unknown = set(settings) - set(DEFAULTS)
    if unknown:
        raise ValueError(f"unknown observation request settings: {sorted(unknown)}")
    result = {**DEFAULTS, **dict(settings)}
    if result["count"] is not None and (
        isinstance(result["count"], bool) or not isinstance(result["count"], int) or result["count"] < 0
    ):
        raise ValueError("observation request count must be a non-negative integer or null")
    for key in ("targets_per_request", "window_nights"):
        if isinstance(result[key], bool) or not isinstance(result[key], int) or result[key] < 1:
            raise ValueError(f"observation request {key} must be a positive integer")
    fraction = float(result["minimum_completion_fraction"])
    if not 0.0 < fraction <= 1.0:
        raise ValueError("minimum_completion_fraction must lie in (0, 1]")
    threshold = float(result["completion_factor_threshold"])
    if not 0.0 < threshold <= 1.0:
        raise ValueError("completion_factor_threshold must lie in (0, 1]")
    reward = float(result["completion_reward"])
    if not math.isfinite(reward) or reward < 0.0:
        raise ValueError("completion_reward must be finite and non-negative")
    minimum_flux = float(result["minimum_feature_flux"])
    if not math.isfinite(minimum_flux) or minimum_flux < 0.0:
        raise ValueError("minimum_feature_flux must be finite and non-negative")
    if not isinstance(result["include_required_targets"], bool):
        raise ValueError("include_required_targets must be a boolean")
    if not isinstance(result["reason"], str) or not result["reason"].strip():
        raise ValueError("observation request reason must be a non-empty string")
    return result


def _issue_indices(n_nights: int, count: int, window_nights: int) -> list[int]:
    # Keep the first night request-free and leave one full night after the last
    # deadline, so every deadline result can appear in a later decision snapshot.
    latest = n_nights - window_nights - 1
    if count == 0:
        return []
    if latest < 0:
        raise ValueError("survey has fewer nights than the request window")
    candidates = list(range(1, latest + 1))
    if count > len(candidates):
        raise ValueError("too many requests for distinct issue nights")
    if count == 1:
        return [candidates[len(candidates) // 2]]
    return [candidates[round(i * (len(candidates) - 1) / (count - 1))] for i in range(count)]


def _required_exposure_seconds(
    feature_flux: float,
    threshold: float,
    flux_zero_point: float,
    exposure_zero_point_seconds: float,
    min_duration_seconds: int,
    max_duration_seconds: int,
) -> float | None:
    """Ideal-conditions exposure to reach the completion threshold; None if unreachable.

    g = f_i·T·Q/(f0·t0) >= threshold needs T >= threshold·f0·t0/f_i (at Q = 1). A target
    whose need exceeds the longest allowed exposure can never complete the request, and one
    whose need is below the shortest exposure still has to stay observable for that minimum.
    """
    if feature_flux <= 0.0:
        return None
    need = threshold * flux_zero_point * exposure_zero_point_seconds / feature_flux
    if need > max_duration_seconds:
        return None
    return max(need, float(min_duration_seconds))


def _geometrically_observable(
    target: Mapping[str, str], slots: Sequence[tuple[datetime, datetime]], site: Mapping,
    minimum_altitude_deg: float, need_seconds: float,
) -> bool:
    ra = float(target["ra_deg"])
    dec = float(target["dec_deg"])
    lat = float(site["latitude_deg"])
    lon = float(site["longitude_deg"])
    need = timedelta(seconds=need_seconds)
    # Contiguous runs of slots (one run per night inside the request window): the
    # continuous observable interval must not span a daytime gap.
    runs: list[list[tuple[datetime, datetime]]] = []
    for start, end in slots:
        if runs and runs[-1][-1][1] == start:
            runs[-1].append((start, end))
        else:
            runs.append([(start, end)])
    for run in runs:
        run_start, run_end = run[0][0], run[-1][1]
        if run_end - run_start < need:
            continue
        for start, _end in run:
            finish = start + need
            if finish > run_end:
                continue
            middle = start + need / 2
            if min(
                radec_to_altaz(ra, dec, moment, lat, lon)[0]
                for moment in (start, middle, finish)
            ) >= minimum_altitude_deg:
                return True
    return False


def build_requests(
    *,
    seed: int,
    targets: Sequence[Mapping[str, str]],
    nights: Sequence[Mapping[str, str]],
    slots: Sequence[Mapping[str, str]],
    site: Mapping,
    minimum_altitude_deg: float,
    settings: Mapping | None = None,
    flux_zero_point: float,
    exposure_zero_point_seconds: float,
    min_duration_seconds: int,
    max_duration_seconds: int,
) -> list[dict]:
    """Build the hidden request stream for one card."""
    cfg = validate_settings(settings or {})
    n_nights = len(nights)
    default_count = min(6, max(1, n_nights // 14)) if n_nights >= cfg["window_nights"] + 2 else 0
    count = default_count if cfg["count"] is None else int(cfg["count"])
    issue_indices = _issue_indices(n_nights, count, int(cfg["window_nights"]))
    slots_by_night: dict[str, list[tuple[datetime, datetime]]] = {}
    for row in slots:
        start = _parse_utc(row["timestamp_utc"])
        duration = int(row["duration_seconds"])
        slots_by_night.setdefault(row["night_id"], []).append((start, start + timedelta(seconds=duration)))

    pool = [
        row
        for row in targets
        if (cfg["include_required_targets"] or row["required"].lower() != "true")
        and float(row["feature_flux"]) >= float(cfg["minimum_feature_flux"])
    ]
    rng = random.Random(derive_stream_seed(int(seed), STREAM_NAME))
    rng.shuffle(pool)
    threshold = float(cfg["completion_factor_threshold"])
    need_seconds: dict[str, float] = {}
    for row in pool:
        need = _required_exposure_seconds(
            float(row["feature_flux"]), threshold, float(flux_zero_point),
            float(exposure_zero_point_seconds), int(min_duration_seconds), int(max_duration_seconds),
        )
        if need is not None:
            need_seconds[row["target_id"]] = need
    pool = [row for row in pool if row["target_id"] in need_seconds]
    used: set[str] = set()
    records: list[dict] = []
    wanted = int(cfg["targets_per_request"])
    for sequence, issue_index in enumerate(issue_indices, 1):
        deadline_index = issue_index + int(cfg["window_nights"]) - 1
        issue = _parse_utc(nights[issue_index]["observing_start_utc"])
        deadline = _parse_utc(nights[deadline_index]["observing_end_utc"])
        window_slots = [
            item
            for night in nights[issue_index : deadline_index + 1]
            for item in slots_by_night.get(night["night_id"], [])
        ]
        selected: list[str] = []
        for target in pool:
            target_id = target["target_id"]
            if target_id in used:
                continue
            if _geometrically_observable(
                target, window_slots, site, minimum_altitude_deg, need_seconds[target_id]
            ):
                selected.append(target_id)
                used.add(target_id)
                if len(selected) == wanted:
                    break
        if len(selected) < wanted:
            raise RuntimeError(
                f"could not find {wanted} distinct geometrically observable targets for request {sequence}"
            )
        records.append(
            {
                "schema_version": SCHEMA_VERSION,
                "record_type": "observation_request",
                "request_id": f"V4RQ{sequence:04d}",
                "issued_at_utc": _format_utc(issue),
                "deadline_utc": _format_utc(deadline),
                "target_ids": selected,
                "minimum_completed": max(1, math.ceil(len(selected) * float(cfg["minimum_completion_fraction"]))),
                "completion_factor_threshold": float(cfg["completion_factor_threshold"]),
                "completion_reward": float(cfg["completion_reward"]),
                "reason": cfg["reason"],
            }
        )
    return records


def generate_requests(
    *,
    seed: int,
    targets_csv: Path,
    night_calendar_csv: Path,
    slots_csv: Path,
    site: Mapping,
    minimum_altitude_deg: float,
    output_path: Path,
    settings: Mapping | None = None,
    flux_zero_point: float,
    exposure_zero_point_seconds: float,
    min_duration_seconds: int,
    max_duration_seconds: int,
) -> list[dict]:
    records = build_requests(
        seed=seed,
        targets=_read_csv(targets_csv),
        nights=_read_csv(night_calendar_csv),
        slots=_read_csv(slots_csv),
        site=site,
        minimum_altitude_deg=minimum_altitude_deg,
        settings=settings,
        flux_zero_point=flux_zero_point,
        exposure_zero_point_seconds=exposure_zero_point_seconds,
        min_duration_seconds=min_duration_seconds,
        max_duration_seconds=max_duration_seconds,
    )
    write_text_lf(Path(output_path), "".join(json.dumps(row, sort_keys=True) + "\n" for row in records))
    return records

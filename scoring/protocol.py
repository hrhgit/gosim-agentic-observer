"""Observer step protocol (observer-v1): shared by the platform runner and the starter-kit local runner.

Depends only on scorer.py. Messages are newline-delimited JSON.

  platform -> agent : {"type": "init", ...}   once, before the first step
  platform -> agent : {"type": "step", ...}   one per decision point
  agent -> platform : {"action": "observe", "tile_id": "...", "program": "DARK", "reason": "..."}
                      or {"action": "wait", "reason": "..."}
  platform -> agent : {"type": "end", ...}    once, after the weather horizon
"""
from __future__ import annotations

import json
import math
from datetime import timedelta
from typing import Optional

try:  # package import (platform)
    from . import scorer
except ImportError:  # flat import (starter kit)
    import scorer  # type: ignore

PROTOCOL = "observer-v1"
FORECAST_SLOTS = 4
DECISION_FIELDS = ("decision_id", "slot_id", "action", "tile_id", "program", "reason")


class ProtocolError(RuntimeError):
    """The agent violated the protocol (bad JSON, unknown action, ...)."""


def _slot_json(slot: scorer.WeatherSlot) -> dict:
    return {
        "slot_id": slot.slot_id, "night_id": slot.night_id,
        "timestamp_utc": slot.timestamp_utc.isoformat().replace("+00:00", "Z"),
        "duration_seconds": slot.duration_seconds, "seeing_arcsec": slot.seeing_arcsec,
        "transparency": slot.transparency, "sky_brightness": slot.sky_brightness, "is_observable": slot.is_observable,
    }


def _tile_json(tile: scorer.Tile) -> dict:
    return {
        "tile_id": tile.tile_id, "ra_deg": tile.ra_deg, "dec_deg": tile.dec_deg, "program": tile.program,
        "region": tile.region, "priority": tile.priority, "nominal_exptime_seconds": tile.nominal_exptime_seconds,
        "n_lrg": tile.targets["LRG"], "n_elg": tile.targets["ELG"], "n_qso": tile.targets["QSO"], "n_bgs": tile.targets["BGS"],
    }


def build_init(config: scorer.ScoreConfig, raw_config: dict, tiles: dict, scenario_meta: dict, limits: dict) -> dict:
    return {
        "type": "init", "protocol": PROTOCOL, "scenario": scenario_meta, "config": raw_config,
        "site": {"latitude_deg": config.latitude_deg, "longitude_deg": config.longitude_deg, "minimum_altitude_deg": config.minimum_altitude_deg},
        "slot_seconds": config.slot_seconds, "n_tiles": len(tiles),
        "tiles": [_tile_json(t) for t in tiles.values()], "limits": limits,
    }


def build_step(engine: scorer.ScoreEngine, step: int, last_action: Optional[dict]) -> Optional[dict]:
    engine._normalize_cursor()
    idx = engine.cursor.slot_index
    if idx >= len(engine.weather):
        return None
    config = engine.config
    slot = engine.weather[idx]
    now = slot.timestamp_utc + timedelta(seconds=engine.cursor.offset_seconds)
    night_remaining = engine._night_seconds_remaining()
    slots_left = 0
    for s in engine.weather[idx:]:
        if s.night_id != slot.night_id:
            break
        slots_left += 1
    forecast = []
    for s in engine.weather[idx + 1: idx + 1 + FORECAST_SLOTS]:
        forecast.append(_slot_json(s))
    zenith_quality = slot.transparency / (slot.seeing_arcsec * slot.sky_brightness)
    available = []
    for tile in engine.tiles.values():
        if tile.tile_id in engine.completed_tiles:
            continue
        if tile.nominal_exptime_seconds > night_remaining + 1e-9:
            continue
        # Evaluate visibility where the scorer will: the midpoint of the first exposure segment.
        first_segment = min(float(tile.nominal_exptime_seconds), slot.duration_seconds - engine.cursor.offset_seconds)
        altitude, airmass = scorer.altitude_airmass(tile, now + timedelta(seconds=first_segment / 2.0), config)
        if altitude < config.minimum_altitude_deg or not math.isfinite(airmass):
            continue
        quality = slot.transparency / (slot.seeing_arcsec * slot.sky_brightness * airmass)
        cond = scorer.condition_program(quality, config)
        tv = scorer.target_value(tile, config)
        pf = 0.5 + 0.5 * tile.priority / 10.0
        bonus = config.program_bonus[tile.program] if tile.program == cond else 0.0
        expected = quality * tv * pf * (1.0 + bonus) if slot.is_observable else 0.0
        item = _tile_json(tile)
        item.update({
            "altitude_deg": round(altitude, 4), "airmass": round(airmass, 5), "quality": round(quality, 6),
            "condition_program": cond, "program_match": tile.program == cond, "target_value": round(tv, 4),
            "expected_gain": round(expected, 4), "expected_gain_per_second": round(expected / tile.nominal_exptime_seconds, 6),
        })
        available.append(item)
    available.sort(key=lambda t: t["expected_gain"], reverse=True)
    region_total = {}
    region_done = {}
    for t in engine.tiles.values():
        region_total[t.region] = region_total.get(t.region, 0) + 1
        if t.tile_id in engine.completed_tiles:
            region_done[t.region] = region_done.get(t.region, 0) + 1
    region_completion = {str(r): round(region_done.get(r, 0) / n, 6) for r, n in sorted(region_total.items())}
    return {
        "type": "step", "step": step,
        "now": {
            "slot_id": slot.slot_id, "night_id": slot.night_id, "slot_index": idx,
            "timestamp_utc": now.isoformat().replace("+00:00", "Z"),
            "slot_elapsed_seconds": round(engine.cursor.offset_seconds, 3),
            "slot_remaining_seconds": round(slot.duration_seconds - engine.cursor.offset_seconds, 3),
            "night_remaining_seconds": round(night_remaining, 3), "slots_remaining_in_night": slots_left,
            "slots_remaining_total": len(engine.weather) - idx,
        },
        "weather": {
            "seeing_arcsec": slot.seeing_arcsec, "transparency": slot.transparency, "sky_brightness": slot.sky_brightness,
            "is_observable": slot.is_observable, "zenith_quality": round(zenith_quality, 6),
            "zenith_program": scorer.condition_program(zenith_quality, config),
        },
        "forecast": forecast,
        "progress": {
            "completed_tiles": len(engine.completed_tiles), "total_tiles": len(engine.tiles),
            "science_score": round(engine.science_score, 6),
            "waste_seconds": round(engine.idle_seconds + engine.invalid_seconds + engine.unproductive_exposure_seconds, 3),
            "idle_seconds": round(engine.idle_seconds, 3), "invalid_seconds": round(engine.invalid_seconds, 3),
            "unproductive_exposure_seconds": round(engine.unproductive_exposure_seconds, 3),
            "invalid_actions": engine.invalid_actions, "region_completion": region_completion,
            "completed_tile_ids": sorted(engine.completed_tiles),
        },
        "available_tiles": available,
        "last_action": last_action,
    }


def parse_answer(line: str) -> dict:
    line = line.strip()
    if not line:
        raise ProtocolError("agent returned an empty line instead of a JSON action")
    try:
        data = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"agent returned invalid JSON: {line[:200]!r}") from exc
    if not isinstance(data, dict):
        raise ProtocolError("agent answer must be a JSON object")
    action = str(data.get("action", "")).strip().lower()
    if action not in ("observe", "wait"):
        raise ProtocolError(f"agent action must be 'observe' or 'wait', got {data.get('action')!r}")
    tile_id = str(data.get("tile_id", "") or "").strip()
    program = str(data.get("program") or data.get("plan") or "").strip().upper()
    reason = str(data.get("reason", "") or "")[:500]
    if action == "observe" and not tile_id:
        raise ProtocolError("observe requires tile_id")
    if action == "wait":
        tile_id, program = "", ""
    return {"action": action, "tile_id": tile_id, "program": program, "reason": reason}




def normalise_decision(answer: dict, step: int, slot_id: str, tiles: dict) -> tuple[scorer.Decision, Optional[str]]:
    """Turn a parsed answer into a scorer.Decision. Unknown programs are kept loadable but will be
    marked invalid by the engine (program mismatch). Returns (decision, warning)."""
    action, tile_id, program, reason = answer["action"], answer["tile_id"], answer["program"], answer["reason"]
    warning = None
    if action == "observe" and program not in scorer.PROGRAMS:
        tile = tiles.get(tile_id)
        if tile is not None and not program:
            program = tile.program
        else:
            warning = f"step {step}: unknown program {answer['program']!r}; treated as a mismatch"
            program = "BACKUP" if (tile is None or tile.program != "BACKUP") else "DARK"
    return scorer.Decision(decision_id=step, slot_id=slot_id, action=action, tile_id=tile_id, program=program, reason=reason), warning


def apply_and_record(engine: scorer.ScoreEngine, decision: scorer.Decision) -> dict:
    """Apply a decision to the engine and return a compact summary of what happened."""
    engine.apply(decision)
    detail = engine.action_details[-1] if engine.action_details else {}
    return {
        "decision_id": decision.decision_id, "action": decision.action, "tile_id": decision.tile_id,
        "program": decision.program, "valid": detail.get("valid"), "message": detail.get("message"),
        "science_score": detail.get("science_score"), "elapsed_seconds": detail.get("elapsed_seconds"),
    }


def decision_row(decision: scorer.Decision) -> dict:
    return {"decision_id": decision.decision_id, "slot_id": decision.slot_id, "action": decision.action,
            "tile_id": decision.tile_id, "program": decision.program, "reason": decision.reason}

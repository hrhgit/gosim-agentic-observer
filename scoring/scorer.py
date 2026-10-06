#!/usr/bin/env python3
"""Standalone stage-one survey decision scorer.

The implementation uses only the Python standard library so that it can be
embedded in a hackathon evaluation backend without installing astronomy
packages.  See README.md for the frozen CSV and timing contract.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


PROGRAMS = {"DARK", "BRIGHT", "BACKUP"}
TARGET_CLASSES = ("LRG", "ELG", "QSO", "BGS")


class ScoringError(ValueError):
    """Raised when an input file violates the frozen submission schema."""


@dataclass(frozen=True)
class ScoreConfig:
    schema_version: str
    slot_seconds: int
    latitude_deg: float
    longitude_deg: float
    minimum_altitude_deg: float
    dark_threshold: float
    bright_threshold: float
    program_bonus: Dict[str, float]
    target_weights: Dict[str, float]
    characteristic_flux: Dict[str, float]
    reference_flux: float
    low_flux_threshold: float
    low_flux_bonus: float
    idle_penalty_per_second: float


@dataclass(frozen=True)
class WeatherSlot:
    slot_id: str
    night_id: str
    timestamp_utc: datetime
    duration_seconds: int
    seeing_arcsec: float
    transparency: float
    sky_brightness: float
    is_observable: bool

    @property
    def end_utc(self) -> datetime:
        return self.timestamp_utc + timedelta(seconds=self.duration_seconds)


@dataclass(frozen=True)
class Tile:
    tile_id: str
    ra_deg: float
    dec_deg: float
    program: str
    region: int
    priority: float
    nominal_exptime_seconds: int
    targets: Dict[str, int]


@dataclass(frozen=True)
class Decision:
    decision_id: int
    slot_id: str
    action: str
    tile_id: str
    program: str
    reason: str


@dataclass
class Cursor:
    slot_index: int = 0
    offset_seconds: float = 0.0


def _require_mapping_keys(payload: dict, keys: Iterable[str], context: str) -> None:
    missing = sorted(set(keys) - set(payload))
    if missing:
        raise ScoringError(f"{context}: missing keys: {', '.join(missing)}")


def load_config(path: Path) -> ScoreConfig:
    try:
        with path.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ScoringError(f"cannot read score config {path}: {exc}") from exc

    _require_mapping_keys(
        raw,
        {
            "schema_version",
            "slot_seconds",
            "site",
            "quality_thresholds",
            "program_bonus",
            "target_weights",
            "characteristic_flux",
            "reference_flux",
            "low_flux_threshold",
            "low_flux_bonus",
            "idle_penalty_per_second",
        },
        "score config",
    )
    _require_mapping_keys(raw["site"], {"latitude_deg", "longitude_deg", "minimum_altitude_deg"}, "site")
    _require_mapping_keys(raw["quality_thresholds"], {"dark", "bright"}, "quality_thresholds")
    for name in ("program_bonus", "target_weights", "characteristic_flux"):
        required = PROGRAMS if name == "program_bonus" else TARGET_CLASSES
        _require_mapping_keys(raw[name], required, name)

    config = ScoreConfig(
        schema_version=str(raw["schema_version"]),
        slot_seconds=int(raw["slot_seconds"]),
        latitude_deg=float(raw["site"]["latitude_deg"]),
        longitude_deg=float(raw["site"]["longitude_deg"]),
        minimum_altitude_deg=float(raw["site"]["minimum_altitude_deg"]),
        dark_threshold=float(raw["quality_thresholds"]["dark"]),
        bright_threshold=float(raw["quality_thresholds"]["bright"]),
        program_bonus={name: float(raw["program_bonus"][name]) for name in PROGRAMS},
        target_weights={name: float(raw["target_weights"][name]) for name in TARGET_CLASSES},
        characteristic_flux={name: float(raw["characteristic_flux"][name]) for name in TARGET_CLASSES},
        reference_flux=float(raw["reference_flux"]),
        low_flux_threshold=float(raw["low_flux_threshold"]),
        low_flux_bonus=float(raw["low_flux_bonus"]),
        idle_penalty_per_second=float(raw["idle_penalty_per_second"]),
    )
    if config.slot_seconds <= 0:
        raise ScoringError("score config: slot_seconds must be positive")
    if not (-90.0 <= config.latitude_deg <= 90.0):
        raise ScoringError("score config: latitude_deg must be in [-90, 90]")
    if not (-180.0 <= config.longitude_deg <= 180.0):
        raise ScoringError("score config: longitude_deg must be in [-180, 180]")
    if not (0.0 <= config.minimum_altitude_deg < 90.0):
        raise ScoringError("score config: minimum_altitude_deg must be in [0, 90)")
    if config.dark_threshold <= config.bright_threshold or config.bright_threshold < 0:
        raise ScoringError("score config: thresholds must satisfy dark > bright >= 0")
    if config.reference_flux <= 0:
        raise ScoringError("score config: reference_flux must be positive")
    if not (0.0 <= config.low_flux_threshold <= 1.0):
        raise ScoringError("score config: low_flux_threshold must be in [0, 1]")
    if config.low_flux_bonus < 0 or config.idle_penalty_per_second < 0:
        raise ScoringError("score config: bonuses and penalties must be non-negative")
    return config


def _read_csv(path: Path, required: Sequence[str]) -> List[dict]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                raise ScoringError(f"{path}: missing CSV header")
            missing = [name for name in required if name not in reader.fieldnames]
            if missing:
                raise ScoringError(f"{path}: missing columns: {', '.join(missing)}")
            rows = list(reader)
    except OSError as exc:
        raise ScoringError(f"cannot read {path}: {exc}") from exc
    if not rows:
        raise ScoringError(f"{path}: must contain at least one data row")
    return rows


def _field(row: dict, name: str, context: str) -> str:
    value = row.get(name, "")
    if value is None or not str(value).strip():
        raise ScoringError(f"{context}: {name} must not be empty")
    return str(value).strip()


def _float(row: dict, name: str, context: str) -> float:
    text = _field(row, name, context)
    try:
        value = float(text)
    except ValueError as exc:
        raise ScoringError(f"{context}: {name} must be numeric, got {text!r}") from exc
    if not math.isfinite(value):
        raise ScoringError(f"{context}: {name} must be finite")
    return value


def _int(row: dict, name: str, context: str) -> int:
    text = _field(row, name, context)
    try:
        value = int(text)
    except ValueError as exc:
        raise ScoringError(f"{context}: {name} must be an integer, got {text!r}") from exc
    return value


def _bool(row: dict, name: str, context: str) -> bool:
    text = _field(row, name, context).lower()
    if text in {"true", "1"}:
        return True
    if text in {"false", "0"}:
        return False
    raise ScoringError(f"{context}: {name} must be true/false or 1/0")


def _timestamp(row: dict, name: str, context: str) -> datetime:
    text = _field(row, name, context)
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        value = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ScoringError(f"{context}: invalid ISO 8601 timestamp {text!r}") from exc
    if value.tzinfo is None or value.utcoffset() is None:
        raise ScoringError(f"{context}: timestamp_utc must include a timezone")
    if value.utcoffset() != timedelta(0):
        raise ScoringError(f"{context}: timestamp_utc must use UTC (Z or +00:00)")
    return value.astimezone(timezone.utc)


def load_weather(path: Path, config: ScoreConfig) -> List[WeatherSlot]:
    required = (
        "slot_id",
        "night_id",
        "timestamp_utc",
        "duration_seconds",
        "seeing_arcsec",
        "transparency",
        "sky_brightness",
        "is_observable",
    )
    rows = _read_csv(path, required)
    result: List[WeatherSlot] = []
    seen_slots = set()
    closed_nights = set()
    current_night: Optional[str] = None

    for line, row in enumerate(rows, start=2):
        context = f"{path}: row {line}"
        slot_id = _field(row, "slot_id", context)
        night_id = _field(row, "night_id", context)
        if slot_id in seen_slots:
            raise ScoringError(f"{context}: duplicate slot_id {slot_id!r}")
        seen_slots.add(slot_id)
        duration = _int(row, "duration_seconds", context)
        seeing = _float(row, "seeing_arcsec", context)
        transparency = _float(row, "transparency", context)
        sky = _float(row, "sky_brightness", context)
        if duration != config.slot_seconds:
            raise ScoringError(
                f"{context}: duration_seconds must equal configured slot_seconds={config.slot_seconds}"
            )
        if seeing <= 0 or sky <= 0:
            raise ScoringError(f"{context}: seeing_arcsec and sky_brightness must be positive")
        if not (0.0 <= transparency <= 1.0):
            raise ScoringError(f"{context}: transparency must be in [0, 1]")

        item = WeatherSlot(
            slot_id=slot_id,
            night_id=night_id,
            timestamp_utc=_timestamp(row, "timestamp_utc", context),
            duration_seconds=duration,
            seeing_arcsec=seeing,
            transparency=transparency,
            sky_brightness=sky,
            is_observable=_bool(row, "is_observable", context),
        )
        if result:
            previous = result[-1]
            if item.timestamp_utc <= previous.timestamp_utc:
                raise ScoringError(f"{context}: weather timestamps must be strictly increasing")
            if item.night_id == previous.night_id:
                if item.timestamp_utc != previous.end_utc:
                    raise ScoringError(f"{context}: slots within a night must be contiguous")
            elif item.timestamp_utc < previous.end_utc:
                raise ScoringError(f"{context}: weather slots must not overlap")

        if night_id != current_night:
            if current_night is not None:
                closed_nights.add(current_night)
            if night_id in closed_nights:
                raise ScoringError(f"{context}: each night_id must occupy one contiguous block")
            current_night = night_id
        result.append(item)
    return result


def load_tiles(path: Path) -> Dict[str, Tile]:
    required = (
        "tile_id",
        "ra_deg",
        "dec_deg",
        "program",
        "region",
        "priority",
        "nominal_exptime_seconds",
        "n_lrg",
        "n_elg",
        "n_qso",
        "n_bgs",
    )
    rows = _read_csv(path, required)
    result: Dict[str, Tile] = {}
    for line, row in enumerate(rows, start=2):
        context = f"{path}: row {line}"
        tile_id = _field(row, "tile_id", context)
        if tile_id in result:
            raise ScoringError(f"{context}: duplicate tile_id {tile_id!r}")
        ra = _float(row, "ra_deg", context)
        dec = _float(row, "dec_deg", context)
        program = _field(row, "program", context).upper()
        region = _int(row, "region", context)
        priority = _float(row, "priority", context)
        exptime = _int(row, "nominal_exptime_seconds", context)
        if not (0.0 <= ra < 360.0):
            raise ScoringError(f"{context}: ra_deg must be in [0, 360)")
        if not (-90.0 <= dec <= 90.0):
            raise ScoringError(f"{context}: dec_deg must be in [-90, 90]")
        if program not in PROGRAMS:
            raise ScoringError(f"{context}: program must be DARK, BRIGHT, or BACKUP")
        if not (0 <= region <= 7):
            raise ScoringError(f"{context}: region must be an integer in [0, 7]")
        if not (0.0 <= priority <= 10.0):
            raise ScoringError(f"{context}: priority must be in [0, 10]")
        if exptime <= 0:
            raise ScoringError(f"{context}: nominal_exptime_seconds must be positive")
        targets = {}
        for target_class in TARGET_CLASSES:
            count = _int(row, f"n_{target_class.lower()}", context)
            if count < 0:
                raise ScoringError(f"{context}: target counts must be non-negative")
            targets[target_class] = count
        result[tile_id] = Tile(
            tile_id=tile_id,
            ra_deg=ra,
            dec_deg=dec,
            program=program,
            region=region,
            priority=priority,
            nominal_exptime_seconds=exptime,
            targets=targets,
        )
    return result


def load_decisions(path: Path, slot_indices: Dict[str, int]) -> List[Decision]:
    required = ("decision_id", "slot_id", "action", "tile_id", "program", "reason")
    rows = _read_csv(path, required)
    result = []
    seen_ids = set()
    previous_id = -1
    previous_slot_index = -1
    for line, row in enumerate(rows, start=2):
        context = f"{path}: row {line}"
        decision_id = _int(row, "decision_id", context)
        slot_id = _field(row, "slot_id", context)
        action = _field(row, "action", context).lower()
        if decision_id < 0 or decision_id in seen_ids or decision_id <= previous_id:
            raise ScoringError(f"{context}: decision_id must be unique and strictly increasing")
        if slot_id not in slot_indices:
            raise ScoringError(f"{context}: unknown slot_id {slot_id!r}")
        if slot_indices[slot_id] < previous_slot_index:
            raise ScoringError(f"{context}: decision slot_id values must be chronological")
        if action not in {"observe", "wait"}:
            raise ScoringError(f"{context}: action must be observe or wait")
        tile_id = str(row.get("tile_id", "") or "").strip()
        program = str(row.get("program", "") or "").strip().upper()
        if action == "observe":
            if not tile_id or not program:
                raise ScoringError(f"{context}: observe requires tile_id and program")
            if program not in PROGRAMS:
                raise ScoringError(f"{context}: invalid program {program!r}")
        elif tile_id or program:
            raise ScoringError(f"{context}: wait requires empty tile_id and program")
        seen_ids.add(decision_id)
        previous_id = decision_id
        previous_slot_index = slot_indices[slot_id]
        result.append(
            Decision(
                decision_id=decision_id,
                slot_id=slot_id,
                action=action,
                tile_id=tile_id,
                program=program,
                reason=str(row.get("reason", "") or "").strip()[:500],
            )
        )
    return result


def _julian_date(moment: datetime) -> float:
    return 2440587.5 + moment.timestamp() / 86400.0


def local_sidereal_time_deg(moment: datetime, longitude_deg: float) -> float:
    days_since_j2000 = _julian_date(moment) - 2451545.0
    gmst = 280.46061837 + 360.98564736629 * days_since_j2000
    return (gmst + longitude_deg) % 360.0


def altitude_airmass(tile: Tile, moment: datetime, config: ScoreConfig) -> Tuple[float, float]:
    latitude = math.radians(config.latitude_deg)
    declination = math.radians(tile.dec_deg)
    hour_angle_deg = (local_sidereal_time_deg(moment, config.longitude_deg) - tile.ra_deg + 180.0) % 360.0 - 180.0
    hour_angle = math.radians(hour_angle_deg)
    sin_altitude = (
        math.sin(latitude) * math.sin(declination)
        + math.cos(latitude) * math.cos(declination) * math.cos(hour_angle)
    )
    sin_altitude = min(1.0, max(-1.0, sin_altitude))
    altitude = math.degrees(math.asin(sin_altitude))
    airmass = 1.0 / sin_altitude if sin_altitude > 0 else math.inf
    return altitude, airmass


def target_value(tile: Tile, config: ScoreConfig) -> float:
    value = 0.0
    for target_class in TARGET_CLASSES:
        flux_factor = min(
            max(config.characteristic_flux[target_class] / config.reference_flux, 0.0),
            1.0,
        )
        if flux_factor <= config.low_flux_threshold:
            flux_factor *= config.low_flux_bonus
        value += tile.targets[target_class] * config.target_weights[target_class] * flux_factor
    return value


def condition_program(quality: float, config: ScoreConfig) -> str:
    if quality >= config.dark_threshold:
        return "DARK"
    if quality >= config.bright_threshold:
        return "BRIGHT"
    return "BACKUP"


class ScoreEngine:
    def __init__(self, config: ScoreConfig, weather: List[WeatherSlot], tiles: Dict[str, Tile]):
        self.config = config
        self.weather = weather
        self.tiles = tiles
        self.slot_indices = {slot.slot_id: index for index, slot in enumerate(weather)}
        self.cursor = Cursor()
        self.completed_tiles = set()
        self.science_score = 0.0
        self.idle_seconds = 0.0
        self.invalid_seconds = 0.0
        self.unproductive_exposure_seconds = 0.0
        self.unavailable_seconds = 0.0
        self.invalid_actions = 0
        self.action_details: List[dict] = []

    def _normalize_cursor(self) -> None:
        while self.cursor.slot_index < len(self.weather):
            duration = self.weather[self.cursor.slot_index].duration_seconds
            if self.cursor.offset_seconds < duration - 1e-9:
                break
            self.cursor.slot_index += 1
            self.cursor.offset_seconds = 0.0

    def _cursor_timestamp(self) -> Optional[datetime]:
        self._normalize_cursor()
        if self.cursor.slot_index >= len(self.weather):
            return None
        return self.weather[self.cursor.slot_index].timestamp_utc + timedelta(seconds=self.cursor.offset_seconds)

    def _consume_gap_until(self, target_index: int) -> None:
        self._normalize_cursor()
        if target_index < self.cursor.slot_index:
            raise ScoringError(
                "decision schedule overlaps an earlier exposure; use the slot in which the previous action finishes"
            )
        while self.cursor.slot_index < target_index:
            slot = self.weather[self.cursor.slot_index]
            remaining = slot.duration_seconds - self.cursor.offset_seconds
            if slot.is_observable:
                self.idle_seconds += remaining
            else:
                self.unavailable_seconds += remaining
            self.cursor.slot_index += 1
            self.cursor.offset_seconds = 0.0

    def _night_seconds_remaining(self) -> float:
        self._normalize_cursor()
        if self.cursor.slot_index >= len(self.weather):
            return 0.0
        night_id = self.weather[self.cursor.slot_index].night_id
        total = self.weather[self.cursor.slot_index].duration_seconds - self.cursor.offset_seconds
        for slot in self.weather[self.cursor.slot_index + 1 :]:
            if slot.night_id != night_id:
                break
            total += slot.duration_seconds
        return total

    def _consume_duration(self, seconds: float, category: str) -> float:
        """Consume up to `seconds`, stopping at the current night boundary."""
        consumed = 0.0
        self._normalize_cursor()
        if self.cursor.slot_index >= len(self.weather):
            return consumed
        night_id = self.weather[self.cursor.slot_index].night_id
        while seconds > 1e-9 and self.cursor.slot_index < len(self.weather):
            slot = self.weather[self.cursor.slot_index]
            if slot.night_id != night_id:
                break
            amount = min(seconds, slot.duration_seconds - self.cursor.offset_seconds)
            if category == "invalid":
                self.invalid_seconds += amount
            elif category == "idle":
                if slot.is_observable:
                    self.idle_seconds += amount
                else:
                    self.unavailable_seconds += amount
            else:
                raise RuntimeError(f"unknown duration category: {category}")
            consumed += amount
            seconds -= amount
            self.cursor.offset_seconds += amount
            self._normalize_cursor()
        return consumed

    def _record_invalid(self, decision: Decision, message: str, requested_seconds: Optional[float]) -> None:
        start = self._cursor_timestamp()
        if requested_seconds is None:
            if self.cursor.slot_index < len(self.weather):
                requested_seconds = self.weather[self.cursor.slot_index].duration_seconds - self.cursor.offset_seconds
            else:
                requested_seconds = 0.0
        elapsed = self._consume_duration(min(requested_seconds, self._night_seconds_remaining()), "invalid")
        self.invalid_actions += 1
        self.action_details.append(
            {
                "decision_id": decision.decision_id,
                "slot_id": decision.slot_id,
                "action": decision.action,
                "tile_id": decision.tile_id,
                "valid": False,
                "message": message,
                "start_timestamp_utc": start.isoformat().replace("+00:00", "Z") if start else None,
                "elapsed_seconds": round(elapsed, 6),
                "science_score": 0.0,
                "reason": decision.reason,
            }
        )

    def _score_observation(self, decision: Decision, tile: Tile) -> None:
        start = self._cursor_timestamp()
        remaining = float(tile.nominal_exptime_seconds)
        action_score = 0.0
        unproductive = 0.0
        segment_count = 0
        tile_value = target_value(tile, self.config)
        priority_factor = 0.5 + 0.5 * tile.priority / 10.0

        while remaining > 1e-9:
            self._normalize_cursor()
            slot = self.weather[self.cursor.slot_index]
            amount = min(remaining, slot.duration_seconds - self.cursor.offset_seconds)
            midpoint = slot.timestamp_utc + timedelta(seconds=self.cursor.offset_seconds + amount / 2.0)
            altitude, airmass = altitude_airmass(tile, midpoint, self.config)
            segment_score = 0.0
            if slot.is_observable and altitude >= self.config.minimum_altitude_deg and math.isfinite(airmass):
                quality = slot.transparency / (slot.seeing_arcsec * slot.sky_brightness * airmass)
                matching_program = condition_program(quality, self.config)
                bonus = self.config.program_bonus[decision.program] if decision.program == matching_program else 0.0
                segment_score = (
                    quality
                    * (amount / tile.nominal_exptime_seconds)
                    * tile_value
                    * priority_factor
                    * (1.0 + bonus)
                )
            else:
                self.unproductive_exposure_seconds += amount
                unproductive += amount
            action_score += segment_score
            remaining -= amount
            self.cursor.offset_seconds += amount
            segment_count += 1

        self._normalize_cursor()
        self.completed_tiles.add(tile.tile_id)
        self.science_score += action_score
        self.action_details.append(
            {
                "decision_id": decision.decision_id,
                "slot_id": decision.slot_id,
                "action": "observe",
                "tile_id": tile.tile_id,
                "valid": True,
                "message": "observed",
                "start_timestamp_utc": start.isoformat().replace("+00:00", "Z") if start else None,
                "elapsed_seconds": tile.nominal_exptime_seconds,
                "segments": segment_count,
                "unproductive_seconds": round(unproductive, 6),
                "science_score": round(action_score, 6),
                "reason": decision.reason,
            }
        )

    def apply(self, decision: Decision) -> None:
        target_index = self.slot_indices[decision.slot_id]
        self._consume_gap_until(target_index)
        self._normalize_cursor()
        if self.cursor.slot_index >= len(self.weather):
            raise ScoringError("decision starts after the weather horizon")
        if decision.action == "wait":
            start = self._cursor_timestamp()
            elapsed = self._consume_duration(
                self.weather[self.cursor.slot_index].duration_seconds - self.cursor.offset_seconds,
                "idle",
            )
            self.action_details.append(
                {
                    "decision_id": decision.decision_id,
                    "slot_id": decision.slot_id,
                    "action": "wait",
                    "tile_id": "",
                    "valid": True,
                    "message": "wait",
                    "start_timestamp_utc": start.isoformat().replace("+00:00", "Z") if start else None,
                    "elapsed_seconds": round(elapsed, 6),
                    "science_score": 0.0,
                    "reason": decision.reason,
                }
            )
            return

        tile = self.tiles.get(decision.tile_id)
        if tile is None:
            self._record_invalid(decision, "unknown tile_id", None)
            return
        if decision.program != tile.program:
            self._record_invalid(decision, "decision program does not match tile program", tile.nominal_exptime_seconds)
            return
        if tile.tile_id in self.completed_tiles:
            self._record_invalid(decision, "tile has already been completed", tile.nominal_exptime_seconds)
            return
        if tile.nominal_exptime_seconds > self._night_seconds_remaining() + 1e-9:
            self._record_invalid(decision, "exposure cannot finish before the end of the night", self._night_seconds_remaining())
            return
        self._score_observation(decision, tile)

    def finish(self) -> None:
        self._normalize_cursor()
        while self.cursor.slot_index < len(self.weather):
            slot = self.weather[self.cursor.slot_index]
            remaining = slot.duration_seconds - self.cursor.offset_seconds
            if slot.is_observable:
                self.idle_seconds += remaining
            else:
                self.unavailable_seconds += remaining
            self.cursor.slot_index += 1
            self.cursor.offset_seconds = 0.0

    def report(self) -> dict:
        total_waste = self.idle_seconds + self.invalid_seconds + self.unproductive_exposure_seconds
        waste_penalty = self.config.idle_penalty_per_second * total_waste
        region_total = {region: 0 for region in range(8)}
        region_completed = {region: 0 for region in range(8)}
        for tile in self.tiles.values():
            region_total[tile.region] += 1
            if tile.tile_id in self.completed_tiles:
                region_completed[tile.region] += 1
        region_completion = {
            str(region): round(region_completed[region] / region_total[region], 6) if region_total[region] else 0.0
            for region in range(8)
        }
        return {
            "schema_version": self.config.schema_version,
            "status": "ok",
            "score": round(self.science_score - waste_penalty, 6),
            "science_score": round(self.science_score, 6),
            "waste_penalty": round(waste_penalty, 6),
            "total_waste_seconds": round(total_waste, 6),
            "waste_breakdown_seconds": {
                "idle": round(self.idle_seconds, 6),
                "invalid_actions": round(self.invalid_seconds, 6),
                "unproductive_exposure": round(self.unproductive_exposure_seconds, 6),
            },
            "unavailable_unpenalized_seconds": round(self.unavailable_seconds, 6),
            "completed_tiles": len(self.completed_tiles),
            "invalid_actions": self.invalid_actions,
            "region_completion": region_completion,
            "actions": self.action_details,
        }


def score_files(weather_path: Path, tiles_path: Path, decisions_path: Path, config_path: Path) -> dict:
    config = load_config(config_path)
    weather = load_weather(weather_path, config)
    tiles = load_tiles(tiles_path)
    slot_indices = {slot.slot_id: index for index, slot in enumerate(weather)}
    decisions = load_decisions(decisions_path, slot_indices)
    engine = ScoreEngine(config, weather, tiles)
    for decision in decisions:
        engine.apply(decision)
    engine.finish()
    return engine.report()


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Score a survey-agent decision CSV.")
    parser.add_argument("--weather", type=Path, required=True)
    parser.add_argument("--tiles", type=Path, required=True)
    parser.add_argument("--decisions", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, help="Optional JSON report path.")
    args = parser.parse_args(argv)

    try:
        report = score_files(args.weather, args.tiles, args.decisions, args.config)
    except ScoringError as exc:
        report = {"status": "invalid_submission", "error": str(exc)}
        if args.output:
            _write_json(args.output, report)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 2

    if args.output:
        _write_json(args.output, report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())

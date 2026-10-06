"""Scenario storage: each scenario is a directory holding weather.csv, tiles.csv, score_config.json."""
from __future__ import annotations

import csv
import hashlib
import json
import random
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from scoring import scorer

from ..config import get_settings
from ..models import Scenario

WEATHER_FIELDS = ("slot_id", "night_id", "timestamp_utc", "duration_seconds", "seeing_arcsec", "transparency", "sky_brightness", "is_observable")
TILE_FIELDS = ("tile_id", "ra_deg", "dec_deg", "program", "region", "priority", "nominal_exptime_seconds", "n_lrg", "n_elg", "n_qso", "n_bgs")
DECISION_FIELDS = ("decision_id", "slot_id", "action", "tile_id", "program", "reason")

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "scoring" / "score_config.json"


class ScenarioError(ValueError):
    pass


def scenario_dir(scn: Scenario) -> Path:
    return get_settings().scenarios_dir / scn.dir_name


def scenario_paths(scn: Scenario) -> dict[str, Path]:
    d = scenario_dir(scn)
    return {"weather": d / "weather.csv", "tiles": d / "tiles.csv", "config": d / "score_config.json"}


def _sha256_files(paths: list[Path]) -> str:
    h = hashlib.sha256()
    for p in paths:
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()


def _safe_slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")
    if not s:
        raise ScenarioError("slug must contain letters or digits")
    return s[:60]


def validate_scenario_files(weather: Path, tiles: Path, config: Path) -> dict:
    """Run the frozen scorer's loaders; raise ScenarioError with the message if invalid."""
    try:
        cfg = scorer.load_config(config)
        w = scorer.load_weather(weather, cfg)
        t = scorer.load_tiles(tiles)
    except scorer.ScoringError as exc:
        raise ScenarioError(str(exc)) from exc
    nights = []
    for slot in w:
        if not nights or nights[-1] != slot.night_id:
            nights.append(slot.night_id)
    return {"n_slots": len(w), "n_nights": len(nights), "n_tiles": len(t), "schema_version": cfg.schema_version}


def register_scenario(db: Session, *, slug: str, name: str, description: str, weather_src: Path, tiles_src: Path,
                      config_src: Optional[Path] = None, weather_public: bool = True, tiles_public: bool = True,
                      seed: Optional[int] = None) -> Scenario:
    slug = _safe_slug(slug)
    if db.query(Scenario).filter_by(slug=slug).first():
        raise ScenarioError(f"scenario slug '{slug}' already exists")
    config_src = config_src or DEFAULT_CONFIG_PATH
    stats = validate_scenario_files(weather_src, tiles_src, config_src)
    dir_name = slug
    dest = get_settings().scenarios_dir / dir_name
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    shutil.copyfile(weather_src, dest / "weather.csv")
    shutil.copyfile(tiles_src, dest / "tiles.csv")
    shutil.copyfile(config_src, dest / "score_config.json")
    scn = Scenario(
        slug=slug, name=name, description=description, dir_name=dir_name,
        weather_public=weather_public, tiles_public=tiles_public,
        n_slots=stats["n_slots"], n_nights=stats["n_nights"], n_tiles=stats["n_tiles"], seed=seed,
        checksum=_sha256_files([dest / "weather.csv", dest / "tiles.csv", dest / "score_config.json"]),
    )
    db.add(scn)
    db.flush()
    return scn


# ---------------------------------------------------------------------------
# Deterministic generator (ported from the hand-off package, without decisions)
# ---------------------------------------------------------------------------

def generate_weather_rows(seed: int, n_nights: int, slots_per_night: int, slot_seconds: int,
                          first_night: Optional[datetime] = None, closure_probability: float = 0.04) -> list[dict]:
    rng = random.Random(seed + 1000)
    first_night = first_night or datetime(2026, 10, 2, 2, 0, 0, tzinfo=timezone.utc)
    if first_night.tzinfo is None:
        first_night = first_night.replace(tzinfo=timezone.utc)
    rows = []
    for night in range(n_nights):
        night_start = first_night + timedelta(days=night)
        # a weather "front" may close a contiguous block of slots
        closed: set[int] = set()
        if night == 0:
            closed.add(slots_per_night // 2)
        elif rng.random() < closure_probability * slots_per_night / 4:
            start = rng.randrange(0, slots_per_night)
            for k in range(rng.randint(1, 3)):
                closed.add(min(slots_per_night - 1, start + k))
        for slot in range(slots_per_night):
            phase = slot / max(slots_per_night - 1, 1)
            if phase < 0.45:
                sky_base = 1.05
            elif phase < 0.78:
                sky_base = 2.65
            else:
                sky_base = 5.15
            seeing = max(0.65, rng.gauss(1.10, 0.16))
            transparency = min(0.99, max(0.35, rng.gauss(0.86, 0.08)))
            sky = max(0.75, sky_base + rng.uniform(-0.22, 0.28))
            timestamp = night_start + timedelta(seconds=slot * slot_seconds)
            rows.append({
                "slot_id": f"N{night + 1:02d}-S{slot + 1:03d}",
                "night_id": f"N{night + 1:02d}",
                "timestamp_utc": timestamp.isoformat().replace("+00:00", "Z"),
                "duration_seconds": slot_seconds,
                "seeing_arcsec": f"{seeing:.4f}",
                "transparency": f"{transparency:.4f}",
                "sky_brightness": f"{sky:.4f}",
                "is_observable": "false" if slot in closed else "true",
            })
    return rows


def generate_tile_rows(seed: int, n_tiles: int) -> list[dict]:
    rng = random.Random(seed)
    rows = []
    exposure_choices = (450, 600, 900, 1200, 1350)
    programs = ("DARK", "BRIGHT", "BACKUP")
    for index in range(n_tiles):
        ra = rng.uniform(0.0, 360.0)
        dec = rng.uniform(-5.0, 65.0)
        program = programs[index % len(programs)]
        if program == "DARK":
            targets = {"LRG": rng.randint(650, 1500), "ELG": rng.randint(1000, 2600), "QSO": rng.randint(120, 520), "BGS": 0}
        elif program == "BRIGHT":
            targets = {"LRG": rng.randint(0, 220), "ELG": rng.randint(0, 180), "QSO": rng.randint(0, 60), "BGS": rng.randint(1700, 4200)}
        else:
            targets = {"LRG": 0, "ELG": 0, "QSO": rng.randint(0, 35), "BGS": rng.randint(600, 1500)}
        density_proxy = targets["LRG"] + targets["ELG"] + 1.7 * targets["QSO"] + 0.45 * targets["BGS"]
        priority = min(10.0, max(0.0, 1.0 + 9.0 * density_proxy / 5000.0 + rng.uniform(-0.5, 0.5)))
        rows.append({
            "tile_id": str(200000 + index),
            "ra_deg": f"{ra:.6f}", "dec_deg": f"{dec:.6f}", "program": program,
            "region": int(ra // 45.0), "priority": f"{priority:.4f}",
            "nominal_exptime_seconds": rng.choice(exposure_choices),
            "n_lrg": targets["LRG"], "n_elg": targets["ELG"], "n_qso": targets["QSO"], "n_bgs": targets["BGS"],
        })
    return rows


def write_csv(path: Path, fields, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(fields))
        writer.writeheader()
        writer.writerows(rows)


def generate_scenario_files(out_dir: Path, *, seed: int, n_nights: int, slots_per_night: int, n_tiles: int,
                            config_path: Optional[Path] = None, first_night: Optional[datetime] = None) -> dict[str, Path]:
    config_path = config_path or DEFAULT_CONFIG_PATH
    cfg = scorer.load_config(config_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(out_dir / "weather.csv", WEATHER_FIELDS, generate_weather_rows(seed, n_nights, slots_per_night, cfg.slot_seconds, first_night))
    write_csv(out_dir / "tiles.csv", TILE_FIELDS, generate_tile_rows(seed, n_tiles))
    shutil.copyfile(config_path, out_dir / "score_config.json")
    return {"weather": out_dir / "weather.csv", "tiles": out_dir / "tiles.csv", "config": out_dir / "score_config.json"}


def generate_and_register(db: Session, *, slug: str, name: str, description: str, seed: int, n_nights: int,
                          slots_per_night: int, n_tiles: int, weather_public: bool, tiles_public: bool,
                          first_night: Optional[datetime] = None) -> Scenario:
    tmp = get_settings().scenarios_dir / f"_tmp_{_safe_slug(slug)}"
    if tmp.exists():
        shutil.rmtree(tmp)
    paths = generate_scenario_files(tmp, seed=seed, n_nights=n_nights, slots_per_night=slots_per_night, n_tiles=n_tiles, first_night=first_night)
    try:
        return register_scenario(db, slug=slug, name=name, description=description, weather_src=paths["weather"],
                                 tiles_src=paths["tiles"], config_src=paths["config"], weather_public=weather_public,
                                 tiles_public=tiles_public, seed=seed)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def load_scenario_json(scn: Scenario) -> dict:
    """Lightweight JSON view of a scenario (for docs/preview): config + counts."""
    paths = scenario_paths(scn)
    with paths["config"].open("r", encoding="utf-8") as fh:
        config = json.load(fh)
    return {"slug": scn.slug, "name": scn.name, "config": config, "n_slots": scn.n_slots, "n_nights": scn.n_nights, "n_tiles": scn.n_tiles}

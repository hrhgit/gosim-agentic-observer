from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SEED = 26005
EVENTS = ("overcast", "haze", "rain", "cold_snap", "rocket_launch", "earthquake")
NOTES = ("power", "maintenance", "wind", "rain", "thick_cloud", "thin_cloud", "other")
PARAMS = dict(num_leaves=15, max_depth=5, min_child_samples=40,
              learning_rate=0.05, colsample_bytree=0.8, reg_lambda=5,
              n_estimators=400, random_state=SEED, n_jobs=1, verbosity=-1)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def freeze(out: Path, value: dict) -> None:
    """Never silently overwrite an experiment's preregistration."""
    path = out / "protocol.json"
    if path.exists() and json.loads(path.read_text(encoding="utf-8")) != value:
        raise ValueError(f"protocol differs; choose a fresh output directory: {out}")
    write_json(path, value)


def code_hashes() -> dict:
    return {str(p.relative_to(ROOT)): digest(p) for p in sorted(Path(__file__).parent.glob("*.py"))}

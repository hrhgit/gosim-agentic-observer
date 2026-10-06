from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENGINE = ROOT / "examples/_local/runner"
AGENT = ROOT / "agents/global-planner-v1"
DEFAULT = ROOT / "artifacts/global-planning-v1"


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    os.replace(temp, path)


def digest(value):
    data = value.read_bytes() if isinstance(value, Path) else json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(data).hexdigest()


def verify_engine():
    manifest = json.loads((ENGINE / "ENGINE_MANIFEST.json").read_text())
    for item in manifest["files"]:
        if digest(ENGINE / item["runner_path"]) != item["sha256"]:
            raise ValueError(f"Official engine changed: {item['runner_path']}")
    return digest(ENGINE / "ENGINE_MANIFEST.json")


def source_hashes():
    paths = list(Path(__file__).parent.glob("*.py")) + list((AGENT / "src").rglob("*.ts"))
    return {str(p.relative_to(ROOT)): digest(p) for p in sorted(paths)}

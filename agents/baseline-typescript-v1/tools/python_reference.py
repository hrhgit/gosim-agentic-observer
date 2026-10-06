"""Resolve the exact Python source recorded at migration time, without editing it."""
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT.parents[1]

def source_directory():
    manifest = json.loads((ROOT / "SOURCE_MANIFEST.json").read_text())
    original = PROJECT / "agents" / "claude-baseline-v1"
    snapshot = ROOT / "results" / "python-source-snapshot"
    for name, spec in manifest["files"].items():
        target = snapshot / Path(name).relative_to("agent")
        if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == spec["sha256"]:
            continue
        content = subprocess.check_output(["git", "-C", str(original), "show", f"{manifest['current_source_commit']}:{name}"])
        if hashlib.sha256(content).hexdigest() != spec["sha256"]:
            raise RuntimeError(f"reference source differs from migration manifest: {name}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return snapshot

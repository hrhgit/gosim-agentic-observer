#!/usr/bin/env python3
"""Export reviewed trusted code, never participant projects or hidden scenarios.

The destination is a fresh directory. Publishing it is a separate organizer
operation; the backend must approve the resulting exact Git commit before jobs
can be dispatched. This script never writes credentials or changes any remote.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def export_control(destination: Path, *, source: Path = ROOT) -> dict:
    if destination.exists():
        raise ValueError("Use a new export directory; existing files will not be overwritten.")
    files = []
    for package in ("project_platform", "challenge"):
        files.extend((p, p.relative_to(source)) for p in (source / package).glob("*.py"))
    files.extend((p, Path(".github/workflows") / p.name)
                 for p in (source / "ops/control-workflows").glob("observer-*.yml"))
    # Self-contained contract/transport and real Git tests accompany the runtime.
    # They generate disposable fixtures and contain no participant data.
    files.extend((source / "tests" / name, Path("tests") / name)
                 for name in ("test_project_platform.py", "test_project_repository.py", "test_project_runtime.py"))
    if not files or not (source / "project_platform/job_runner.py").is_file():
        raise ValueError("Trusted platform sources are missing.")
    destination.mkdir(parents=True, exist_ok=False)
    inventory = {}
    for origin, relative in sorted(files):
        if origin.is_symlink():
            raise ValueError("Trusted source cannot be a symbolic link.")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(origin, target)
        inventory[relative.as_posix()] = hashlib.sha256(target.read_bytes()).hexdigest()
    (destination / "control-inventory.json").write_text(json.dumps(inventory, sort_keys=True, indent=2) + "\n")
    (destination / ".gitignore").write_text("__pycache__/\n*.py[cod]\n.pytest_cache/\n.venv/\n")
    (destination / "README.md").write_text(
        "# Agentic Observer trusted control runtime\n\n"
        "This private repository contains only organizer-reviewed job code.\n"
        "Participant projects never run as host commands or GitHub workflows.\n"
        "No model master key, GitHub App key, administrative database credential,\n"
        "participant source, or hidden scenario is stored in this checkout.\n\n"
        "Set the public repository variable OBSERVER_JOB_URL to the job API.\n"
        "After every update, approve the exact main commit in the backend before\n"
        "dispatching jobs. Jobs authenticate with GitHub OIDC.\n"
    )
    return inventory


def export_public(destination: Path, *, source: Path = ROOT) -> dict:
    """The public runner pool repository (ops/public-runner-pool.md).

    Only the trusted runtime and the public engine and score workflows: no tests
    (no push or pull request workflow may exist in a public repository), no
    other workflow, nothing that is not already public in this repository.
    """
    if destination.exists():
        raise ValueError("Use a new export directory; existing files will not be overwritten.")
    files = []
    for package in ("project_platform", "challenge"):
        files.extend((p, p.relative_to(source)) for p in (source / package).glob("*.py"))
    files.append((source / "ops/public-pool/observer-engine.yml", Path(".github/workflows/observer-engine.yml")))
    files.append((source / "ops/public-pool/observer-score.yml", Path(".github/workflows/observer-score.yml")))
    files.append((source / "ops/public-pool/requirements.txt", Path("public-pool-requirements.txt")))
    if not (source / "project_platform/sealing.py").is_file():
        raise ValueError("Trusted platform sources are missing.")
    destination.mkdir(parents=True, exist_ok=False)
    inventory = {}
    for origin, relative in sorted(files):
        if origin.is_symlink():
            raise ValueError("Trusted source cannot be a symbolic link.")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(origin, target)
        inventory[relative.as_posix()] = hashlib.sha256(target.read_bytes()).hexdigest()
    (destination / "control-inventory.json").write_text(json.dumps(inventory, sort_keys=True, indent=2) + "\n")
    (destination / ".gitignore").write_text("__pycache__/\n*.py[cod]\n")
    (destination / "README.md").write_text(
        "# Agentic Observer public evaluation runtime\n\n"
        "Organizer-reviewed engine code, exported from the public competition repository.\n"
        "Runs are dispatched by the platform only and show a status line; every\n"
        "private input and result is sealed with public-key encryption. This\n"
        "repository holds no credential, participant data or scenario.\n"
    )
    return inventory


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("destination", type=Path)
    parser.add_argument("--public", action="store_true", help="Export the public runner pool repository instead")
    args = parser.parse_args()
    inventory = (export_public if args.public else export_control)(args.destination)
    print(f"Exported {len(inventory)} trusted files to {args.destination}")


if __name__ == "__main__":
    main()

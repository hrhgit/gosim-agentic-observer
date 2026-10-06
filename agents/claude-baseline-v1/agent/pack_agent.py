#!/usr/bin/env python3
"""Zip this folder into a project ZIP for submission (observer.project.json at the root).

    python3 pack_agent.py --out ../claude-baseline-v1.zip
"""
from __future__ import annotations

import argparse
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SKIP_DIRS = {"__pycache__", "run_output", ".git", ".venv"}
SKIP_FILES = {".env", ".DS_Store"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT.parent / "claude-baseline-v1.zip")
    args = parser.parse_args()
    count = 0
    with zipfile.ZipFile(args.out, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(ROOT.rglob("*")):
            relative = path.relative_to(ROOT)
            if path.is_dir() or any(part in SKIP_DIRS for part in relative.parts) or path.name in SKIP_FILES:
                continue
            if path.suffix in (".pyc", ".zip"):
                continue
            archive.write(path, relative.as_posix())
            count += 1
    print(f"wrote {args.out} ({count} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

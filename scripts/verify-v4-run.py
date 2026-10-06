#!/usr/bin/env python3
"""Organizer-only verification of one v4 card run (formal A-D or hidden final E-H).

Replays the run's recorded actions.jsonl against the card bundle with the trusted
v4 runner and checks that the score and decisions.csv are reproduced exactly.
Run it from the trusted control revision recorded for the job. Both inputs may be
directories or the ZIP files from private storage. Only totals, counts and digests
are printed: nothing from the bundle's truth files, and no seed.

  python3 scripts/verify-v4-run.py --bundle card-E.zip --result run-results.zip
"""
import argparse
import hashlib
import json
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from challenge.v4_workflow import ACTIONS_FILE, V4_SCENARIO_PATH, replay  # noqa: E402


def materialize(path: Path, destination: Path) -> Path:
    if path.is_dir():
        return path
    with zipfile.ZipFile(path) as archive:
        for member in archive.infolist():
            target = (destination / member.filename).resolve()
            if destination.resolve() not in target.parents and target != destination.resolve():
                raise SystemExit(f"unsafe path in {path.name}")
        archive.extractall(destination)
    return destination


def find(root: Path, relative: Path) -> Path:
    matches = [p for p in root.rglob(relative.name) if p.as_posix().endswith(relative.as_posix())]
    if len(matches) != 1:
        raise SystemExit(f"expected exactly one {relative} in {root}, found {len(matches)}")
    return matches[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bundle", type=Path, required=True, help="card bundle directory or ZIP")
    parser.add_argument("--result", type=Path, required=True, help="run result directory or ZIP")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="observer-v4-verify-") as temp:
        temp = Path(temp)
        bundle_scenario = find(materialize(args.bundle, temp / "bundle"), V4_SCENARIO_PATH)
        bundle = bundle_scenario.parent.parent
        result = materialize(args.result, temp / "result")
        recorded = json.loads(find(result, Path("score_report.json")).read_text())
        decisions = find(result, Path("decisions.csv")).read_bytes()
        replayed = replay(bundle, find(result, Path(ACTIONS_FILE)), temp / "replay")
        replayed_decisions = (temp / "replay" / "decisions.csv").read_bytes()
    checks = {
        "total": recorded["total"] == replayed["total"],
        "components": recorded["components"] == replayed["components"],
        "counts": recorded["counts"] == replayed["counts"],
        "decisions_csv": decisions == replayed_decisions,
    }
    print(json.dumps({
        "scenario": replayed["scenario"],
        "recorded_total": recorded["total"], "replayed_total": replayed["total"],
        "recorded_termination": recorded.get("termination", {}).get("reason"),
        "replayed_termination": replayed["termination"]["reason"],
        "decisions_sha256": hashlib.sha256(decisions).hexdigest(),
        "checks": checks, "verified": all(checks.values()),
    }, indent=1, sort_keys=True))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())

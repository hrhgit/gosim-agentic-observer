#!/usr/bin/env python3
"""Build the v4 PUBLIC TEST card: the small card every new agent version plays once
before the team can approve it (the public test, capped at 300 s).

It is deliberately public and separate from every competition card: not alpha/beta,
never A-H. Its spec (including its seed) is fixed here so anyone can rebuild the
identical bundle; nothing about it predicts any other card, because every card uses
hashed per-stream seeds from its own secret. Seven nights, 2,000 targets, no stress
events, a few weather events, 300 s wall clock: a trivial agent finishes the season in
seconds and a model-calling agent still commits decisions well inside the cap.

  python3 scripts/build-v4-public-test-card.py OUT_DIR [--zip OUT.zip]

Registration (organizer): scenario slug v4-public-test, contract 'v4-score-v1',
weather/events/forecasts public, global_wallclock_seconds 300; upload the bundle ZIP
as its evaluation bundle; point the preparation preview at it with
scripts/configure-v4-phases.py --preview v4-public-test.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from challenge.v4_bundle import build_card_bundle  # noqa: E402
from challenge.v4_workflow import V4Workflow  # noqa: E402
from project_platform.artifacts import pack_files  # noqa: E402
from project_platform.package import ProjectFile  # noqa: E402

PUBLIC_TEST_SPEC = {
    "name": "v4-public-test", "card_id": "PT", "scenario_slug": "v4-public-test", "phase": "public-test",
    "seed": 20260929, "start_date": "2026-10-01", "end_date": "2026-10-08",
    "targets": 2000, "area_deg2": 1500.0, "stress": False, "wallclock_seconds": 300,
    "event_counts": {"rainy": 1, "cloudy": 1, "smoggy": 1, "cold_wave": 0, "tornado": 0,
                     "rocket_launch": 1, "earthquake": 0, "instrument_fault": 1},
}


def pack(root: Path) -> bytes:
    files = tuple(ProjectFile(p.relative_to(root).as_posix(), p.read_bytes())
                  for p in sorted(root.rglob("*")) if p.is_file())
    return pack_files(files)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("out", type=Path, help="new directory for the bundle")
    parser.add_argument("--zip", type=Path, help="also write the bundle ZIP here")
    args = parser.parse_args()
    root = build_card_bundle(args.out, PUBLIC_TEST_SPEC)
    workflow = V4Workflow(root)
    summary = {"scenario_slug": PUBLIC_TEST_SPEC["scenario_slug"], "contract": "v4-score-v1",
               "global_wallclock_seconds": workflow.wallclock_budget(None),
               "nights": len(workflow.initialize_payload(300)["survey"]["nights"]),
               "targets": len(workflow.scenario.targets), "slots": len(workflow.scenario.slots)}
    if args.zip:
        data = pack(root)
        args.zip.write_bytes(data)
        summary["zip_sha256"] = hashlib.sha256(data).hexdigest()
    print(json.dumps(summary, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

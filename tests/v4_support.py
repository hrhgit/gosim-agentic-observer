"""Build small v4 card bundles (the layout of challenge.v4_workflow) for engine tests."""
from __future__ import annotations

from pathlib import Path

from challenge.v4_bundle import build_card_bundle


def build_bundle(root: Path, *, start="2026-10-01", end="2026-10-06", targets=1500, seed=4242,
                 stress=False, card_id="T", wallclock_seconds=None) -> Path:
    """A hashed-seed test card with a short season; returns the bundle root."""
    return build_card_bundle(root, {
        "name": f"test-card-{card_id.lower()}", "card_id": card_id, "scenario_slug": f"v4-test-{card_id.lower()}",
        "phase": "test", "seed": seed, "start_date": start, "end_date": end, "targets": targets,
        "area_deg2": 900.0, "stress": stress, "wallclock_seconds": wallclock_seconds,
    })

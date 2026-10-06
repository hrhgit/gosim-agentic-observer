"""The organizer sky map keeps every footprint component in one piece when it can.

A component that straddles RA=180 used to be drawn as two pieces on opposite map edges
(trial card v4-d: about ten targets at RA 180-186, dec < -55 looked like a detached
fragment outside the footprint). The map now moves its seam into the widest RA gap.
"""
from __future__ import annotations

import pytest

from challenge.v4_catalog_generator import _offset_radec
from challenge.v4_sky_map import _choose_center_ra, _crosses_seam, _densify_edges, _split_at_seam

pytest.importorskip("numpy")


def disc(ra: float, dec: float, radius: float, vertices: int = 40) -> list[tuple[float, float]]:
    return [_offset_radec(ra, dec, 360.0 * k / vertices, radius) for k in range(vertices)]


def test_a_footprint_clear_of_the_seam_keeps_the_ra0_map():
    components = {"C00": disc(20.0, -35.0, 20.0), "C01": disc(280.0, -30.0, 20.0)}
    assert _choose_center_ra(components) == 0.0


def test_a_component_across_ra180_moves_the_seam_into_the_widest_gap():
    # The v4-d layout: C02 reaches from RA ~115 to ~186 in the deep south.
    components = {"C00": disc(20.0, -35.0, 25.0), "C01": disc(280.0, -30.0, 25.0),
                  "C02": disc(150.0, -50.0, 22.0)}
    straddling = _densify_edges(components["C02"])
    assert _crosses_seam(straddling, 0.0) and len(_split_at_seam(straddling)) == 2
    center = _choose_center_ra(components)
    assert center != 0.0 and center % 30.0 == 0.0
    for vertices in components.values():
        assert not _crosses_seam(_densify_edges(vertices), center)

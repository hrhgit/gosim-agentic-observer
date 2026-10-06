"""The agent's field geometry must agree with the scorer's.

Compares, for random fields and times on every formal card's public configuration, the fibre
cell the agent computes in its equatorial-frame tangent plane with the cell the engine's
FiberGrid.classify_target assigns, and the agent's altitude window with min_altitude_during.
Run from the repository root:  python3.12 -m pytest tests -q
"""
import json
import math
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PROJECT = REPO.parent.parent
sys.path.insert(0, str(REPO / "agent"))
sys.path.insert(0, str(PROJECT / "examples" / "_local" / "runner"))

from challenge.v4_fiber_map import FiberGrid, min_altitude_during, radec_to_altaz  # noqa: E402
from challenge.v4_scorer import lunar_quality_factor  # noqa: E402
from challenge.tile_geometry_simulator import _normalized_airmass  # noqa: E402

from core.astro import DEG, OMEGA, RAD, Site, unit_vector  # noqa: E402
from core.planner import Planner  # noqa: E402

CARDS = PROJECT / "cards"


def _site(card):
    fiber = json.loads((CARDS / card / "config" / "v4_fiber_config.json").read_text())
    score = json.loads((CARDS / card / "config" / "v4_score_config.json").read_text())
    site = Site(fiber["site"]["latitude_deg"], fiber["site"]["longitude_deg"], 30.0,
                score["airmass_exponent"], score["lunar_model"])
    return fiber, score, site


def test_cells_match_engine():
    rng = random.Random(7)
    for card in "abcd":
        fiber, score, site = _site(card)
        grid = FiberGrid.from_config(fiber)
        n, pitch = grid.n_side, grid.pitch_deg
        checked = 0
        for _ in range(300):
            ts = 1.8e9 + rng.uniform(0, 3.0e7)
            moment = datetime.fromtimestamp(int(ts), tz=timezone.utc)
            ts = moment.timestamp()
            _, zen, north, east = site.frame(ts)
            # a field centre well above the horizon
            alt0, az0 = rng.uniform(32, 86), rng.uniform(0, 360)
            ca, sa = math.cos(alt0 * RAD), math.sin(alt0 * RAD)
            centre = tuple(ca * math.cos(az0 * RAD) * north[k] + ca * math.sin(az0 * RAD) * east[k] + sa * zen[k]
                           for k in range(3))
            alt, az = site.altaz(centre, ts)
            assert abs(alt - alt0) < 1e-9 and abs((az - az0 + 180) % 360 - 180) < 1e-9
            frame = Planner._field_frame(centre, zen)
            _, up, right = frame
            for _ in range(40):
                # a target somewhere in or just around the field
                dx, dy = rng.uniform(-0.6, 0.6) * n * pitch, rng.uniform(-0.6, 0.6) * n * pitch
                v = [centre[k] + dx * RAD * right[k] + dy * RAD * up[k] for k in range(3)]
                norm = math.sqrt(sum(c * c for c in v))
                v = [c / norm for c in v]
                dec = math.asin(v[2]) * DEG
                ra = math.atan2(v[1], v[0]) * DEG % 360.0
                u = unit_vector(ra, dec)
                d = sum(u[k] * centre[k] for k in range(3))
                fx = sum(u[k] * right[k] for k in range(3)) / d * DEG / pitch + n / 2.0
                fy = sum(u[k] * up[k] for k in range(3)) / d * DEG / pitch + n / 2.0
                mine = None
                if 0 <= fx < n and 0 <= fy < n:
                    mine = int(fy) * n + int(fx)
                    edge = min(fx - int(fx), 1 - (fx - int(fx)), fy - int(fy), 1 - (fy - int(fy)))
                    if edge * pitch < 1e-5:
                        continue  # the agent never assigns a target this close to a cell edge
                result = grid.classify_target(ra, dec, moment, alt, az, site.lat, site.lon)
                theirs = result.fiber_id if result.region == "glass" else None
                if mine is None and theirs is not None:
                    # only acceptable right on the outer edge
                    assert min(abs(fx), abs(fx - n), abs(fy), abs(fy - n)) * pitch < 1e-5
                    continue
                assert mine == theirs, (card, mine, theirs, fx, fy)
                checked += 1
        assert checked > 5000


def test_altitude_window_and_quality_terms():
    rng = random.Random(11)
    for card in "abcd":
        fiber, score, site = _site(card)
        config = {"site": fiber["site"]}
        for _ in range(400):
            ra, dec = rng.uniform(0, 360), rng.uniform(-85, 85)
            hm = site.hour_angle_limit(dec)
            moment = datetime.fromtimestamp(int(1.8e9 + rng.uniform(0, 3.0e7)), tz=timezone.utc)
            ts = moment.timestamp()
            lst, zen, _, _ = site.frame(ts)
            u = unit_vector(ra, dec)
            alt, _ = radec_to_altaz(ra, dec, moment, site.lat, site.lon)
            sin_alt = sum(u[k] * zen[k] for k in range(3))
            assert abs(math.asin(sin_alt) * DEG - alt) < 1e-8
            if alt > 1:
                assert abs(site.transmission(sin_alt) - _normalized_airmass(alt) ** -site.beta) < 1e-9
                assert abs(site.atab[int(sin_alt * site.TABLE)] - _normalized_airmass(alt) ** -site.beta) < 5e-3
            moon = site.moon(ts)
            assert abs(site.lunar_factor(u[0], u[1], u[2], moon)
                       - lunar_quality_factor(ra, dec, moment, fiber["site"], score)) < 1e-9
            if hm < 0:
                assert alt < 30.0 + 1e-6
                continue
            h = (lst - ra + 180.0) % 360.0 - 180.0
            up_now = -hm <= h <= hm
            assert up_now == (alt >= 30.0) or abs(alt - 30.0) < 1e-6
            if up_now and hm < 180.0:
                tmax = (hm - h) / OMEGA - 8.0
                for duration in (60, 900, 3600):
                    low = min_altitude_during(ra, dec, moment, moment + timedelta(seconds=duration), config)
                    if duration <= tmax:
                        assert low >= 30.0, (card, ra, dec, duration, tmax, low)


if __name__ == "__main__":  # no pytest needed: python3.12 tests/test_geometry.py
    test_cells_match_engine()
    test_altitude_window_and_quality_terms()
    print("geometry tests passed")

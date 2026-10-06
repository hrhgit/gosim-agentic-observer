"""The offset estimator must recover a hidden pointing offset from hit/miss outcomes alone.

Outcomes are produced by the engine's own FiberGrid.classify_target with a known offset, on the
card D instrument (100 cells of 0.245 deg) and the card A instrument (16 cells of 0.632 deg).
    python3.12 tests/test_pointing.py
"""
import json
import math
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PROJECT = REPO.parent.parent
sys.path.insert(0, str(REPO / "agent"))
sys.path.insert(0, str(PROJECT / "examples" / "_local" / "runner"))

from challenge.v4_fiber_map import FiberGrid  # noqa: E402

from core.astro import DEG, RAD, Site, unit_vector  # noqa: E402
from core.planner import Planner  # noqa: E402
from core.pointing import OffsetEstimator  # noqa: E402

CARDS = PROJECT / "cards"


def simulate(card, true_alt, true_az, exposures, per_exposure, seed, corrected=True):
    fiber = json.loads((CARDS / card / "config" / "v4_fiber_config.json").read_text())
    score = json.loads((CARDS / card / "config" / "v4_score_config.json").read_text())
    site = Site(fiber["site"]["latitude_deg"], fiber["site"]["longitude_deg"], 30.0,
                score["airmass_exponent"], score["lunar_model"])
    grid = FiberGrid.from_config(fiber)
    n, pitch = grid.n_side, grid.pitch_deg
    est = OffsetEstimator(n, pitch)
    rng = random.Random(seed)
    hit_rates = []
    for _ in range(exposures):
        moment = datetime.fromtimestamp(int(1.8e9 + rng.uniform(0, 3.0e7)), tz=timezone.utc)
        ts = moment.timestamp()
        _, zen, north, east = site.frame(ts)
        alt0, az0 = rng.uniform(35, 80), rng.uniform(0, 360)
        ca, sa = math.cos(alt0 * RAD), math.sin(alt0 * RAD)
        centre = tuple(ca * math.cos(az0 * RAD) * north[k] + ca * math.sin(az0 * RAD) * east[k] + sa * zen[k]
                       for k in range(3))
        _, up, right = Planner._field_frame(centre, zen)
        # the agent aims the field at `centre`: it commands centre - estimate
        cmd_alt = alt0 - (est.alt if corrected else 0.0)
        cmd_az = (az0 - (est.az if corrected else 0.0)) % 360.0
        items = []
        hits = 0
        unexpected = 0
        margin_alt = (est.margin_alt + 1e-5) / pitch
        margin_az = (est.margin_az * ca + 1e-5) / pitch
        for _ in range(per_exposure):
            fx, fy = rng.uniform(0, n), rng.uniform(0, n)
            dx, dy = (fx - n / 2) * pitch, (fy - n / 2) * pitch
            v = [centre[k] + dx * RAD * right[k] + dy * RAD * up[k] for k in range(3)]
            norm = math.sqrt(sum(c * c for c in v))
            v = [c / norm for c in v]
            dec = math.asin(v[2]) * DEG
            ra = math.atan2(v[1], v[0]) * DEG % 360.0
            u = unit_vector(ra, dec)
            d = sum(u[k] * centre[k] for k in range(3))
            gx = sum(u[k] * right[k] for k in range(3)) / d * DEG / pitch + n / 2
            gy = sum(u[k] * up[k] for k in range(3)) / d * DEG / pitch + n / 2
            if not (0 <= gx < n and 0 <= gy < n):
                continue
            row, col = int(gy), int(gx)
            edge_x = min(gx - col, 1 - (gx - col))
            edge_y = min(gy - row, 1 - (gy - row))
            safe = edge_x >= margin_az and edge_y >= margin_alt
            result = grid.classify_target(ra, dec, moment, cmd_alt, cmd_az, site.lat, site.lon, true_alt, true_az)
            hit = result.region == "glass" and result.fiber_id == row * n + col
            hits += hit
            if not hit and safe:
                unexpected += 1
            items.append((sum(u[k] * north[k] for k in range(3)), sum(u[k] * east[k] for k in range(3)),
                          sum(u[k] * zen[k] for k in range(3)), row, col, hit))
        est.observe(cmd_alt, cmd_az, items, unexpected)
        hit_rates.append(hits / max(1, len(items)))
    return est, hit_rates


def test_recovers_offset_small_cells():
    for seed, (da, dz) in enumerate([(0.08, -0.05), (-0.11, 0.10), (0.02, 0.114), (-0.06, -0.09)]):
        est, rates = simulate("d", da, dz, exposures=14, per_exposure=28, seed=seed)
        assert est.active
        assert abs(est.alt - da) < 0.006 and abs(est.az - dz) < 0.012, (da, dz, est.alt, est.az, est.margin_alt, est.margin_az)
        assert sum(rates[-4:]) / 4 > 0.93, rates
        print(f"D-like  true ({da:+.3f}, {dz:+.3f})  est ({est.alt:+.4f}, {est.az:+.4f}) "
              f"+-({est.margin_alt:.4f}, {est.margin_az:.4f})  solves {est.solves}  hit rate first/last "
              f"{rates[0]:.2f}/{sum(rates[-4:]) / 4:.2f}")


def test_recovers_offset_large_cells():
    est, rates = simulate("a", 0.09, -0.07, exposures=30, per_exposure=14, seed=5)
    assert est.active
    assert abs(est.alt - 0.09) < 0.03 and abs(est.az + 0.07) < 0.04, (est.alt, est.az)
    print(f"A-like  true (+0.090, -0.070)  est ({est.alt:+.4f}, {est.az:+.4f}) +-({est.margin_alt:.4f}, {est.margin_az:.4f}) "
          f"solves {est.solves}  hit rate last {sum(rates[-6:]) / 6:.2f}")


def test_stays_idle_without_offset():
    est, rates = simulate("d", 0.0, 0.0, exposures=40, per_exposure=28, seed=3)
    assert not est.active and est.alt == 0.0 and est.az == 0.0
    assert min(rates) == 1.0
    print("no offset: estimator stayed idle, every assigned target hit")


if __name__ == "__main__":
    import time
    started = time.process_time()
    test_recovers_offset_small_cells()
    test_recovers_offset_large_cells()
    test_stays_idle_without_offset()
    print(f"pointing tests passed ({time.process_time() - started:.1f} s cpu)")

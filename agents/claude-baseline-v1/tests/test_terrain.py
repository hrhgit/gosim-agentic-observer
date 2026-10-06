"""The terrain learner must converge on a hidden obstruction box and never forbid clear sky.

    python3.12 tests/test_terrain.py
"""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agent"))

from core.conditions import Terrain  # noqa: E402


def run(az_lo, az_hi, alt_top, announced, seed, sightings=60):
    def truth(az, alt):
        inside = az_lo <= az <= az_hi if az_lo <= az_hi else (az >= az_lo or az <= az_hi)
        return inside and alt <= alt_top

    rng = random.Random(seed)
    terrain = Terrain(announced)
    wasted = 0
    for _ in range(sightings):
        # the planner prefers directions it believes are clear: sample by (1 - p_block)
        for _ in range(50):
            az = (announced + rng.uniform(-80, 80)) % 360.0
            alt = rng.uniform(30, 66)
            if rng.random() < 1.0 - terrain.blocked_probability(az, alt):
                break
        blocked = truth(az, alt)
        wasted += blocked
        terrain.add(az, alt, blocked, confirmed=True)
    forbidden_clear = 0
    allowed_blocked = 0
    for _ in range(4000):
        az = (announced + rng.uniform(-90, 90)) % 360.0
        alt = rng.uniform(30, 66)
        p = terrain.blocked_probability(az, alt)
        if p >= 1.0 and not truth(az, alt):
            forbidden_clear += 1
        if p <= 0.0 and truth(az, alt):
            allowed_blocked += 1
    return terrain, wasted, forbidden_clear, allowed_blocked


def test_box_converges():
    cases = [(51.3, 97.4, 50.2, 90.0), (298.2, 3.8, 43.5, 315.0), (15.9, 50.1, 33.0, 45.0), (200.0, 232.0, 48.0, 225.0)]
    for seed, (lo, hi, top, announced) in enumerate(cases):
        terrain, wasted, forbidden_clear, allowed_blocked = run(lo, hi, top, announced, seed)
        # hard blocks may overshoot a sector edge by the 2 degrees a sighting speaks for
        assert forbidden_clear < 4000 * 0.02, (forbidden_clear, lo, hi, top, terrain.box)
        # sky still wrongly believed fully clear is a small sliver next to the learned box
        assert allowed_blocked < 4000 * 0.06, (allowed_blocked, terrain.box)
        assert wasted <= 30, wasted
        box = tuple(round(v, 1) for v in terrain.box) if terrain.box else "none yet (the prior kept the planner out)"
        print(f"sector az {lo}-{hi} alt<{top}: seen blocked {box} around {announced}, {wasted} blocked sightings "
              f"spent, {forbidden_clear}/4000 clear points forbidden, {allowed_blocked}/4000 blocked points thought clear")


def test_all_zero_exposures_need_agreement():
    terrain = Terrain(90.0)
    terrain.add(85.0, 40.0, True, confirmed=False)
    assert terrain.box is None                      # one all-zero exposure could have been a closure
    terrain.add(80.0, 42.0, True, confirmed=False)
    assert terrain.box is not None
    assert terrain.blocked_probability(81.0, 35.0) == 1.0
    assert terrain.blocked_probability(81.0, 60.0) == 0.0


def test_two_overlapping_sectors_of_different_height():
    # one "SE" notice standing for az 102-157 below 49 deg and az 118-174 below 35 deg
    def truth(az, alt):
        return (102.1 <= az <= 157.5 and alt <= 49.1) or (118.5 <= az <= 173.7 and alt <= 35.4)

    rng = random.Random(4)
    terrain = Terrain(135.0)
    for _ in range(300):
        az, alt = rng.uniform(90, 185), rng.uniform(30, 60)
        terrain.add(az, alt, truth(az, alt), confirmed=True)
    wrong_block = wrong_clear = 0
    for _ in range(4000):
        az, alt = rng.uniform(90, 185), rng.uniform(30, 60)
        p = terrain.blocked_probability(az, alt)
        if p >= 1.0 and not truth(az, alt):
            wrong_block += 1
        if p <= 0.0 and truth(az, alt):
            wrong_clear += 1
    # the low part (az 160-174) must not inherit the 49-degree top of the high part
    assert terrain.blocked_probability(168.0, 44.0) < 1.0
    assert wrong_block < 4000 * 0.03 and wrong_clear < 4000 * 0.03, (wrong_block, wrong_clear)
    print(f"two sectors under one notice: {wrong_block} clear points forbidden, {wrong_clear} blocked points allowed, of 4000")


if __name__ == "__main__":
    test_box_converges()
    test_all_zero_exposures_need_agreement()
    test_two_overlapping_sectors_of_different_height()
    print("terrain tests passed")

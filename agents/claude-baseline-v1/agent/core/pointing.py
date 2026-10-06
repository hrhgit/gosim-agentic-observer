"""Hidden pointing-offset estimator (Hard mode).

The scorer adds a fixed (delta_alt, delta_az) to every commanded pointing and never says so.
The only evidence is which assigned targets come back as hits. Each exposure is stored as the
commanded pointing plus, per assigned target, its horizon-frame unit vector, the fibre cell it
was assigned to and whether it hit. A hypothesis (da, dz) is scored by how many of those
outcomes it fails to reproduce with the scorer's own gnomonic grid test.

The estimator stays idle (offset exactly zero, no cost) until a target that could not have
missed under zero offset does miss. From then on it refines a grid search around the best
hypothesis and reports the estimate with an uncertainty box; the planner aims at
"desired - estimate" and discounts targets closer to a cell edge than the box.
"""
from __future__ import annotations

import math
from collections import deque

from .astro import DEG, RAD


class OffsetEstimator:
    def __init__(self, n_side: int, pitch_deg: float) -> None:
        self.n = int(n_side)
        self.pitch = float(pitch_deg)
        self.half = self.n * self.pitch / 2.0
        self.alt = 0.0          # estimated offset, degrees
        self.az = 0.0
        self.margin_alt = 0.0   # half-width of the consistent region, degrees
        self.margin_az = 0.0
        self.active = False
        self.records: deque = deque(maxlen=60)   # exposures
        self.solves = 0
        self.since_solve = 0
        self.best_mismatch = 0
        self.evidence = 0       # target outcomes seen since activation

    # -- data ---------------------------------------------------------------------
    def add(self, cmd_alt: float, cmd_az: float, items: list) -> None:
        """items: (north, east, up, row, col, hit) per assigned target."""
        if items:
            self.records.append((cmd_alt, cmd_az, items))

    def _mismatch(self, da: float, dz: float, records) -> int:
        n = self.n
        pitch = self.pitch
        half = self.half
        mid = n / 2.0
        floor = math.floor
        total = 0
        for cmd_alt, cmd_az, items in records:
            alt_deg = cmd_alt + da
            if not 0.0 <= alt_deg <= 90.0:
                for item in items:
                    if item[5]:
                        total += 1
                continue
            alt = alt_deg * RAD
            az = (cmd_az + dz) * RAD
            ca, sa = math.cos(alt), math.sin(alt)
            cz, sz = math.cos(az), math.sin(az)
            cx, cy, cu = ca * cz, ca * sz, sa
            ux, uy, uu = -sa * cz, -sa * sz, ca
            rx, ry = -sz, cz
            for hn, he, hu, row, col, hit in items:
                depth = hn * cx + he * cy + hu * cu
                predicted = False
                if depth > 0.0:
                    d_alt = (hn * ux + he * uy + hu * uu) / depth * DEG
                    d_az = (hn * rx + he * ry) / depth * DEG
                    if -half <= d_alt <= half and -half <= d_az <= half:
                        r = int(floor(d_alt / pitch + mid))
                        c = int(floor(d_az / pitch + mid))
                        if r >= n:
                            r = n - 1
                        if c >= n:
                            c = n - 1
                        predicted = r == row and c == col
                if predicted != hit:
                    total += 1
        return total

    # -- solve --------------------------------------------------------------------
    def solve(self) -> None:
        records = list(self.records)
        if not records:
            return
        self.solves += 1
        self.since_solve = 0
        # the most recent outcomes are enough and keep one solve to a fraction of a second
        recent, count = [], 0
        for record in reversed(records):
            recent.append(record)
            count += len(record[2])
            if count >= 300:
                break
        step = min(0.02, self.pitch / 10.0)
        span = max(0.26, 13 * step)
        centre = (self.alt, self.az) if self.active and self.solves > 1 else (0.0, 0.0)
        best = None
        for _ in range(3):
            best = self._grid(centre, span, span, step, recent)
            # widen when the minimum set touches the border of the searched square
            if (best[1] - centre[0] > -span + 1.5 * step and best[2] - centre[0] < span - 1.5 * step
                    and best[3] - centre[1] > -span + 1.5 * step and best[4] - centre[1] < span - 1.5 * step):
                break
            centre = (0.5 * (best[1] + best[2]), 0.5 * (best[3] + best[4]))
            span *= 1.6
        # refine around the bounding box of the minimum set; a wide box (few outcomes so far)
        # is kept wide, a tight one is resolved down to about a thousandth of a degree
        for _ in range(3):
            if step <= 0.0012:
                break
            mismatch, lo_a, hi_a, lo_z, hi_z = best
            span_a = 0.5 * (hi_a - lo_a) + step
            span_z = 0.5 * (hi_z - lo_z) + step
            fine = max(max(span_a, span_z) / 7.0, step / 6.0, 0.0008)
            if fine >= step:
                break
            best = self._grid((0.5 * (lo_a + hi_a), 0.5 * (lo_z + hi_z)), span_a, span_z, fine, recent)
            step = fine
        mismatch, lo_a, hi_a, lo_z, hi_z = best
        self.alt = 0.5 * (lo_a + hi_a)
        self.az = 0.5 * (lo_z + hi_z)
        self.margin_alt = 0.5 * (hi_a - lo_a) + step
        self.margin_az = 0.5 * (hi_z - lo_z) + step
        self.best_mismatch = mismatch
        self.active = True

    def _grid(self, centre, span_a: float, span_z: float, step: float, records):
        """Minimum mismatch over a grid and the bounding box of the hypotheses reaching it."""
        na = int(math.ceil(span_a / step))
        nz = int(math.ceil(span_z / step))
        best_m = None
        lo_a = hi_a = lo_z = hi_z = 0.0
        for i in range(-na, na + 1):
            da = centre[0] + i * step
            for k in range(-nz, nz + 1):
                dz = centre[1] + k * step
                m = self._mismatch(da, dz, records)
                if best_m is None or m < best_m:
                    best_m = m
                    lo_a = hi_a = da
                    lo_z = hi_z = dz
                elif m == best_m:
                    if da < lo_a:
                        lo_a = da
                    elif da > hi_a:
                        hi_a = da
                    if dz < lo_z:
                        lo_z = dz
                    elif dz > hi_z:
                        hi_z = dz
        return best_m, lo_a, hi_a, lo_z, hi_z

    # -- per-exposure bookkeeping ---------------------------------------------------
    def observe(self, cmd_alt: float, cmd_az: float, items: list, unexpected: int) -> None:
        """Record one exposure; `unexpected` counts misses of targets that were safe under the
        current estimate."""
        self.add(cmd_alt, cmd_az, items)
        self.since_solve += 1
        if not self.active:
            if unexpected > 0:
                self.solve()
            return
        self.evidence += len(items)
        if self.solves >= 40:
            return
        widest = max(self.margin_alt, self.margin_az)
        if ((widest > self.pitch / 25.0 and self.since_solve >= 1)
                or (widest > self.pitch / 120.0 and self.since_solve >= 8)
                or (unexpected > 0 and self.since_solve >= 5) or self.since_solve >= 250):
            self.solve()

    @property
    def uncertain(self) -> bool:
        return self.active and max(self.margin_alt, self.margin_az) > self.pitch / 12.0

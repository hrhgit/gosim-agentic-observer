"""Choose the next exposure: pointing, fibre assignment, duration and program.

One rule decides everything:   maximise   expected gain - price * duration

* gain counts, per fibre cell, the best target's improvement over its current best score,
  plus the penalties it removes (a required target crossing the completion threshold),
  the request rewards it advances and its effect on the uniformity term;
* price is the going rate of telescope time (a fraction of the recently realised science
  rate), so an exposure is extended only while its marginal gain beats what the same
  seconds would earn elsewhere. Time-poor cards end up with short, dense exposures and
  time-rich ones with deep exposures, without card-specific tuning.

Search: rank zones (field-sized sky cells) by a cheap score, then for the best few
1. pick an anchor target, 2. slide the fibre grid over the anchor's neighbourhood to find
the placement that covers the most value (exact for all n^2 placements via a summed-area
table), 3. recompute the field exactly in the scorer's tangent plane, 4. scan durations.
"""
from __future__ import annotations

import heapq
import math

from .astro import DEG, OMEGA, RAD

BASE_DURATIONS = (60, 90, 120, 150, 180, 240, 300, 360, 420, 480, 540, 600, 720, 840, 900, 1080, 1260,
                  1440, 1620, 1800, 2100, 2400, 2700, 3000, 3300, 3600)


class Planner:
    def __init__(self, survey) -> None:
        self.sv = survey
        self.tab = survey.site.TABLE
        self.cos_reach = math.cos((survey.fov * math.sqrt(2.0) + 0.02) * RAD)
        self.sin_scan = math.sin((survey.site.min_alt + 1.0) * RAD)
        # sub-cell shifts tried when refining a placement; small cells gain the most from them
        self.shifts = (-1.0 / 3.0, 0.0, 1.0 / 3.0) if survey.n_side >= 5 else (-0.25, 0.0, 0.25)
        if survey.knobs.get("fine_shifts", 0.0) > 0.0:
            self.shifts = (-0.4, -0.2, 0.0, 0.2, 0.4)
        self.stat_plans = 0
        self.stat_negative = 0
        self.stat_negative_seconds = 0

    # -- zone ranking -------------------------------------------------------------------
    def _zone_bonus_weight(self, z: int, a_clear: float, east, night: int) -> float:
        """Weight of a required target in zone z right now: penalty * urgency * timing^3, where
        timing compares the transmission now with the best still to come this season."""
        sv = self.sv
        cat = sv.cat
        c = cat.zcoarse[z]
        rising = cat.zcx[z] * east[0] + cat.zcy[z] * east[1] + cat.zcz[z] * east[2] > 0.0
        best = cat.season_future[c][night]
        if rising:
            tonight = cat.season_peak[c][night]
            if tonight > best:
                best = tonight
        if a_clear > best:
            best = a_clear
        timing = a_clear / best if best > 0.0 else 1.0
        urgency = sv.urgency[c]
        # an urgent target cannot afford to wait for its best hour
        timing = timing ** (sv.knobs["timing_power"] / urgency)
        return sv.p_req * urgency * timing

    def plan(self, t: float, night: int, k_zones: int, t_floor: int, t_ceiling: int, calibrate: bool):
        sv = self.sv
        cat = sv.cat
        site = sv.site
        sky = sv.sky
        lst, zen, north, east = site.frame(t)
        zx, zy, zz = zen
        night_end = sv.nights[night][1]
        rem = night_end - t
        t_cap = int(min(sv.t_max, rem, t_ceiling))
        if t_cap < sv.t_min:
            t_cap = sv.t_min
        if t_floor > t_cap:
            t_floor = t_cap
        moon = site.moon(t + min(t_cap, 1200) / 2.0)
        rho, w_level = sky.levels()
        price = sv.price
        directional = sky.directional
        moon_on = moon[3] > 0.01
        atab = site.atab
        tab = self.tab
        zcx, zcy, zcz = cat.zcx, cat.zcy, cat.zcz
        zpot, zflag, zreq = sv.zpot, sv.zflag, sv.zreq
        zdeep = sv.zdeep
        deep_weight = sv.knobs["deep_weight"]
        zextra = sv.zextra
        extra = sv.extra
        need_req = sv.need_req
        t_ref = sv.T_REF
        sin_scan = self.sin_scan
        ex, ey, ez = east
        nx, ny, nz_ = north
        asin, atan2 = math.asin, math.atan2
        sci_urgency = sv.sci_urgency
        zcoarse = cat.zcoarse
        scored = []
        for z in range(cat.nz):
            sa = zcx[z] * zx + zcy[z] * zy + zcz[z] * zz
            if sa < sin_scan:
                continue
            pot = zpot[z]
            if pot <= 1e-9:
                pot = deep_weight * zdeep[z]      # only a much longer or better exposure adds here
            flag = zflag[z]
            if pot <= 1e-9 and not flag and not calibrate:
                continue
            a_clear = atab[int(sa * tab)]
            a = a_clear
            if moon_on:
                a *= site.lunar_factor(zcx[z], zcy[z], zcz[z], moon)
            if directional:
                alt = asin(sa) * DEG
                if alt < 82.0:
                    az = atan2(zcx[z] * ex + zcy[z] * ey + zcz[z] * ez,
                               zcx[z] * nx + zcy[z] * ny + zcz[z] * nz_) * DEG % 360.0
                    a *= sky.dir_factor(alt, az)
            q = rho * a
            if q <= 1e-4:
                continue
            s = pot * (q if q < 1.25 else 1.25) * sci_urgency[zcoarse[z]]
            if flag:
                req = zreq[z]
                if req:
                    weight = self._zone_bonus_weight(z, a_clear, east, night)
                    for j in req:
                        need = need_req[j] / q * 1.25
                        if need <= t_cap:
                            s += weight - price * (need - t_ref if need > t_ref else 0.0)
                        elif need_req[j] / q * 0.95 <= t_cap:
                            s += 0.25 * weight
                ext = zextra.get(z)
                if ext:
                    for j in ext:
                        bonus, deadline, threshold = extra[j]
                        need = threshold * sv.f0t0 / cat.flux[j] / q * 1.25
                        if need <= t_cap and t + need <= deadline:
                            s += bonus - price * (need - t_ref if need > t_ref else 0.0)
            if calibrate:
                s += 1e-3 * len(cat.zmembers[z])
            if s > 0.0:
                scored.append((s, z))
        if not scored:
            return None
        top = heapq.nlargest(k_zones, scored) if len(scored) > k_zones else scored
        ctx = (t, lst, zen, north, east, moon, rho, w_level, t_cap, t_floor, price, night, calibrate, rem)
        plans = []
        for _, z in top:
            plan = self._evaluate(z, ctx)
            if plan is not None:
                plans.append(plan)
        if not plans:
            return None
        # The price is a memory of recent good hours. When the sky has just turned worse (cloud,
        # Moon, a poorer part of the footprint) every option can look unprofitable at that price
        # and the "least bad" one would be the shortest exposure. Time cannot be saved for later,
        # so cap the price by what the best field can earn right now.
        if not calibrate:
            lo = min(300, t_cap)
            rate_now = 0.0
            for plan in plans:
                for duration, eff, total, _ in plan["curve"]:
                    if duration >= lo and eff > 0.0 and total / eff > rate_now:
                        rate_now = total / eff
            capped = sv.knobs["price_cap"] * rate_now
            self.stat_plans += 1
            if capped < price:
                price = capped
                self.stat_negative += 1
        # How deep to go is decided on real value against the price. Which field to take also
        # weighs how soon its part of the sky leaves for the season; keeping the two apart lets
        # an urgent field win the slot without being exposed deeper than it is worth.
        best_plan = None
        best_score = None
        for plan in plans:
            top = None
            for duration, eff, total, science in plan["curve"]:
                nv = total - price * eff
                if top is None or nv > top[0] + 1e-9:
                    top = (nv, duration, science)
            if top is None:
                continue
            score = top[0] + (plan["u_s"] - 1.0) * top[2]
            if best_score is None or score > best_score:
                best_score = score
                best_plan = plan
                best_plan["duration"] = top[1]
                best_plan["nv"] = top[0]
        if best_plan is None:
            return None
        picks = self._picks(best_plan, t, rem, calibrate)
        if not picks:
            return None
        best_plan["picks"] = picks
        return best_plan

    # -- one candidate field ------------------------------------------------------------
    def _evaluate(self, z: int, ctx):
        t, lst, zen, north, east, moon, rho, w_level, t_cap, t_floor, price, night, calibrate, rem = ctx
        sv = self.sv
        cat = sv.cat
        site = sv.site
        sky = sv.sky
        ux, uy, uz = cat.ux, cat.uy, cat.uz
        flux, weight, ra, hmax, isreq, band = cat.flux, cat.weight, cat.ra, cat.hmax, cat.required, cat.band
        best, gmax = sv.best, sv.gmax
        zx, zy, zz = zen
        cx, cy, cz = cat.zcx[z], cat.zcy[z], cat.zcz[z]
        sa = cx * zx + cy * zy + cz * zz
        if sa <= 0.0:
            return None
        a_clear = site.atab[int(sa * self.tab)]
        a = a_clear * site.lunar_factor(cx, cy, cz, moon)
        d_factor = 1.0
        if sky.directional:
            alt = math.asin(sa) * DEG
            az = math.atan2(cx * east[0] + cy * east[1] + cz * east[2],
                            cx * north[0] + cy * north[1] + cz * north[2]) * DEG % 360.0
            d_factor = sky.dir_factor(alt, az)
        q = rho * a * d_factor
        if q <= 1e-4:
            return None
        qf = q / sv.f0t0
        mhat = sky.expected_multiplier(w_level * a, t)
        thr = sv.thr_req
        thr_u = sv.thr_unif
        breq = self._zone_bonus_weight(z, a_clear, east, night) if sv.required_left > 0 else 0.0
        extra = sv.extra
        band_bonus = sv.band_bonus if sv.any_band_bonus else None
        t0 = float(min(t_cap, max(t_floor, sv.T_REF)))
        floor_value = 1e-4 if calibrate else 0.0
        u_s = sv.sci_urgency[cat.zcoarse[z]]      # weight of ordinary science in this part of the sky
        delta = sv.knobs["partial_discount"]      # weight of a gain that leaves the target unsaturated

        def bonus_of(j: int):
            """(weight, threshold, deadline) of the threshold-crossing bonuses of target j."""
            w_b, th, dl = 0.0, thr, 1e18
            if isreq[j] and gmax[j] < thr:
                w_b = breq
            if band_bonus is not None and gmax[j] < thr_u:
                bb = band_bonus[band[j]]
                if bb > 0.0:
                    w_b += bb
                    if thr_u > th:
                        th = thr_u
            if extra:
                e = extra.get(j)
                if e is not None:
                    w_b += e[0]
                    dl = e[1]
                    if e[2] > th:
                        th = e[2]
            return w_b, th, dl

        # 1. anchor: the member whose own best case is worth the most
        anchor = -1
        anchor_value = 0.0
        t_plan = t0
        for j in cat.zmembers[z]:
            hm = hmax[j]
            if hm < 180.0:
                h = (lst - ra[j] + 180.0) % 360.0 - 180.0
                if h < -hm or h > hm:
                    continue
                tmax = (hm - h) / OMEGA - 8.0
                if tmax < sv.t_min:
                    continue
            else:
                tmax = 1e9
            tj = t0 if t0 <= tmax else tmax
            rate = flux[j] * qf
            g = rate * tj
            v = weight[j] * mhat * (g if g < 1.0 else 1.0) - best[j]
            if v <= 0.0:
                # nothing to gain at the usual depth; would the longest exposure add anything?
                v = 0.0
                t_long = t_cap if t_cap <= tmax else tmax
                if t_long > tj:
                    g_long = rate * t_long
                    v_long = weight[j] * mhat * (g_long if g_long < 1.0 else 1.0) - best[j]
                    if v_long > 0.0:
                        v = 0.25 * v_long
                        tj = t_long
                        g = g_long
            if g < 1.0:
                v *= delta
            w_b, th, dl = bonus_of(j)
            if w_b > 0.0 and rate > 0.0:
                tb = th * 1.25 / rate
                limit = min(tmax, t_cap, dl - t)
                if tb > limit:
                    tb = limit
                if tb >= sv.t_min:
                    ratio = rate * tb / th
                    conf = 1.0 if ratio >= 1.25 else ((ratio - 0.85) * 2.5 if ratio > 0.85 else 0.0)
                    if conf > 0.0:
                        gb = rate * tb
                        vb = weight[j] * mhat * (gb if gb < 1.0 else 1.0) - best[j]
                        if vb < 0.0:
                            vb = 0.0
                        if gb < 1.0:
                            vb *= delta
                        vb += w_b * conf - price * (tb - t0 if tb > t0 else 0.0)
                        if vb > v:
                            v = vb
                            tj = tb if tb > t_floor else float(t_floor)
            if v + floor_value > anchor_value:
                anchor_value = v + floor_value
                anchor = j
                t_plan = tj
        if anchor < 0:
            return None
        if calibrate:
            t_plan = float(sv.t_min)

        # 2. neighbours projected on the tangent plane at the anchor
        ax, ay, az_ = ux[anchor], uy[anchor], uz[anchor]
        s_a = ax * zx + ay * zy + az_ * zz
        c_a = math.sqrt(max(1e-12, 1.0 - s_a * s_a))
        upx, upy, upz = (zx - s_a * ax) / c_a, (zy - s_a * ay) / c_a, (zz - s_a * az_) / c_a
        rtx, rty, rtz = (ay * zz - az_ * zy) / c_a, (az_ * zx - ax * zz) / c_a, (ax * zy - ay * zx) / c_a
        cos_reach = self.cos_reach
        items = []
        info = {}
        beacons = []
        for j in cat.reach(z):
            d = ux[j] * ax + uy[j] * ay + uz[j] * az_
            if d < cos_reach:
                continue
            hm = hmax[j]
            if hm < 180.0:
                h = (lst - ra[j] + 180.0) % 360.0 - 180.0
                if h < -hm or h > hm:
                    continue
                tmax = (hm - h) / OMEGA - 8.0
                if tmax < sv.t_min:
                    continue
            else:
                tmax = 1e9
            rate = flux[j] * qf
            tj = t_plan if t_plan <= tmax else tmax
            g = rate * tj
            v = weight[j] * mhat * (g if g < 1.0 else 1.0) - best[j]
            if v < 0.0:
                v = 0.0
            if g < 1.0:
                v *= delta
            w_b, th, dl = bonus_of(j)
            if w_b > 0.0:
                if t + tj <= dl:
                    ratio = g / th
                    if ratio >= 1.25:
                        v += w_b
                    elif ratio > 0.85:
                        v += w_b * (ratio - 0.85) * 2.5
                else:
                    w_b = 0.0
            v += floor_value
            if v <= 1e-9:
                # nothing to gain, but a target this bright saturates whatever the instrument
                # does and so reads out the program band: keep the brightest as beacons
                if rate * t_plan >= 2.5 and tmax >= t_plan:
                    beacons.append((rate, j, tmax))
                continue
            x = (ux[j] * rtx + uy[j] * rty + uz[j] * rtz) / d * DEG
            y = (ux[j] * upx + uy[j] * upy + uz[j] * upz) / d * DEG
            items.append((x, y, v, j))
            info[j] = (rate, weight[j] * mhat, best[j], w_b, th, dl, tmax, v)
        weights = (1.0, delta)
        if not items:
            return None

        # 3. coarse placement: with the anchor at the centre of a cell, every other target has a
        #    fixed cell relative to it, and each of the n x n ways to put the anchor's cell in the
        #    grid is a window sum over those relative cells (summed-area table).
        n = sv.n_side
        p = sv.pitch
        side = 2 * n - 1
        stride = side + 1
        mid = (n - 1) / 2.0
        floor = math.floor
        grid = [0.0] * (side * side)
        for x, y, v, _ in items:
            dc = int(floor(x / p + 0.5))
            dr = int(floor(y / p + 0.5))
            if -n < dc < n and -n < dr < n:
                k = (dr + n - 1) * side + dc + n - 1
                if v > grid[k]:
                    grid[k] = v
        table = [0.0] * (stride * stride)
        for r in range(side):
            run = 0.0
            base = (r + 1) * stride
            prev = r * stride
            row = r * side
            for c in range(side):
                run += grid[row + c]
                table[base + c + 1] = table[prev + c + 1] + run
        best_sum = -1.0
        ar_best = ac_best = 0
        for ar in range(n):
            r0 = n - 1 - ar
            top_row = (r0 + n) * stride
            low_row = r0 * stride
            for ac in range(n):
                c0 = n - 1 - ac
                total = table[top_row + c0 + n] - table[low_row + c0 + n] - table[top_row + c0] + table[low_row + c0]
                total -= 1e-7 * (abs(ar - mid) + abs(ac - mid))
                if total > best_sum:
                    best_sum = total
                    ar_best, ac_best = ar, ac
        xa = (ac_best + 0.5 - n / 2.0) * p * RAD
        ya = (ar_best + 0.5 - n / 2.0) * p * RAD
        vx = ax - xa * rtx - ya * upx
        vy = ay - xa * rty - ya * upy
        vz = az_ - xa * rtz - ya * upz
        norm = math.sqrt(vx * vx + vy * vy + vz * vz)
        centre = (vx / norm, vy / norm, vz / norm)

        # 4. refine in the field's own tangent plane (the alt/az axes turn across the field,
        #    so the anchor's frame is only approximate): try sub-cell shifts of the grid
        candidates = [item[3] for item in items]
        if beacons and not calibrate:
            beacons.sort(reverse=True)
            for rate_b, j_b, tmax_b in beacons[:8]:
                info[j_b] = (rate_b, 0.0, 0.0, 0.0, thr, 1e18, tmax_b, 0.0)
        frame = self._field_frame(centre, zen)
        if frame is None:
            return None
        _, up_c, rt_c = frame
        scale = DEG / p
        half = n / 2.0
        projected = []
        for j in candidates:
            d = ux[j] * centre[0] + uy[j] * centre[1] + uz[j] * centre[2]
            if d <= 0.5:
                continue
            gx = (ux[j] * rt_c[0] + uy[j] * rt_c[1] + uz[j] * rt_c[2]) / d * scale + half
            gy = (ux[j] * up_c[0] + uy[j] * up_c[1] + uz[j] * up_c[2]) / d * scale + half
            if -0.6 < gx < n + 0.6 and -0.6 < gy < n + 0.6:
                projected.append((gx, gy, info[j][7]))
        best_shift = (0.0, 0.0)
        best_value = -1.0
        for sx in self.shifts:
            for sy in self.shifts:
                seen: dict[int, float] = {}
                value = 0.0
                for gx, gy, v in projected:
                    hx = gx - sx
                    hy = gy - sy
                    if 0.0 <= hx < n and 0.0 <= hy < n:
                        k = int(hy) * n + int(hx)
                        old = seen.get(k, 0.0)
                        if v > old:
                            value += v - old
                            seen[k] = v
                if sx == 0.0 and sy == 0.0:
                    value += 1e-7
                if value > best_value:
                    best_value = value
                    best_shift = (sx, sy)
        if best_shift != (0.0, 0.0):
            sx, sy = best_shift[0] * p * RAD, best_shift[1] * p * RAD
            vx = centre[0] + sx * rt_c[0] + sy * up_c[0]
            vy = centre[1] + sx * rt_c[1] + sy * up_c[1]
            vz = centre[2] + sx * rt_c[2] + sy * up_c[2]
            norm = math.sqrt(vx * vx + vy * vy + vz * vz)
            centre = (vx / norm, vy / norm, vz / norm)
        margin_alt = (sv.offset.margin_alt + 1e-5) / p
        if beacons and not calibrate:
            candidates = candidates + [b[1] for b in beacons[:8]]
        best_cells = self._exact_cells(centre, zen, candidates, margin_alt, sv.offset.margin_az,
                                       not sv.offset.active)
        best_centre = centre
        if best_cells is None:
            return None

        durations = self._durations(best_cells, info, t_floor, t_cap, calibrate)
        cell_keys = list(best_cells.keys())
        cell_lists = [[(info[j], phit, j) for j, phit in members] for members in best_cells.values()]
        curve = []
        w_full, w_part = weights
        for duration in durations:
            eff = duration if duration <= rem else rem
            total = 0.0
            science = 0.0
            for members in cell_lists:
                top_v = 0.0
                top_sci = 0.0
                for (rate, cap, base, w_b, th, dl, tmax, _), phit, _ in members:
                    if tmax < eff:
                        continue
                    g = rate * eff
                    v = cap * (g if g < 1.0 else 1.0) - base
                    if v < 0.0:
                        v = 0.0
                    v *= w_full if g >= 1.0 else w_part
                    sci = v
                    if w_b > 0.0 and t + eff <= dl:
                        ratio = g / th
                        if ratio >= 1.25:
                            v += w_b
                        elif ratio > 0.85:
                            v += w_b * (ratio - 0.85) * 2.5
                    v *= phit
                    if v > top_v:
                        top_v = v
                        top_sci = sci * phit
                total += top_v
                science += top_sci
            curve.append((duration, eff, total, science))
        if not curve:
            return None
        return {"curve": curve, "centre": best_centre, "zone": z, "q": q, "anchor": anchor,
                "cell_keys": cell_keys, "cell_lists": cell_lists, "weights": weights, "u_s": u_s}

    def _picks(self, plan: dict, t: float, rem: float, calibrate: bool):
        """The target each fibre cell takes at the chosen duration."""
        duration = plan["duration"]
        eff = duration if duration <= rem else rem
        floor_value = 1e-4 if calibrate else 0.0
        w_full, w_part = plan["weights"]
        picks = []
        spare = []
        for cell, members in zip(plan["cell_keys"], plan["cell_lists"]):
            top_v = floor_value * 0.5
            top_j = -1
            top_phit = 0.0
            for (rate, cap, base, w_b, th, dl, tmax, _), phit, j in members:
                if tmax < eff:
                    continue
                g = rate * eff
                v = cap * (g if g < 1.0 else 1.0) - base
                if v < 0.0:
                    v = 0.0
                v *= w_full if g >= 1.0 else w_part
                if w_b > 0.0 and t + eff <= dl:
                    ratio = g / th
                    if ratio >= 1.25:
                        v += w_b
                    elif ratio > 0.85:
                        v += w_b * (ratio - 0.85) * 2.5
                v = (v + floor_value) * phit
                if v > top_v:
                    top_v = v
                    top_j = j
                    top_phit = phit
            if top_j >= 0:
                picks.append((cell, top_j, top_phit))
            elif not calibrate:
                for (rate, cap, base, w_b, th, dl, tmax, _), phit, j in members:
                    if cap == 0.0 and phit >= 0.999 and tmax >= eff and rate * eff >= 2.0:
                        spare.append((rate, cell, j))
        if picks and spare:
            spare.sort(reverse=True)
            used = set()
            for rate, cell, j in spare:
                if cell not in used:          # one fibre, one target
                    used.add(cell)
                    picks.append((cell, j, 1.0))
                    if len(used) == 2:
                        break
        return picks

    @staticmethod
    def _field_frame(centre, zen):
        """(sin altitude, up, right): the scorer's tangent-plane axes at a field centre, as
        equatorial vectors. up points to increasing altitude, right to increasing azimuth."""
        cx, cy, cz = centre
        zx, zy, zz = zen
        sa = cx * zx + cy * zy + cz * zz
        if sa <= 0.02 or sa >= 0.9986:        # below the horizon, or within 3 deg of the zenith
            return None
        ca = math.sqrt(1.0 - sa * sa)
        up = ((zx - sa * cx) / ca, (zy - sa * cy) / ca, (zz - sa * cz) / ca)
        right = ((cy * zz - cz * zy) / ca, (cz * zx - cx * zz) / ca, (cx * zy - cy * zx) / ca)
        return sa, up, right

    def _exact_cells(self, centre, zen, candidates, margin_alt: float, margin_az_deg: float, hard: bool):
        """Fibre cell of every candidate for a field centred on `centre`, computed the way the
        scorer does it (gnomonic plane at the centre, rows along +alt, columns along +az)."""
        sv = self.sv
        cat = sv.cat
        ux, uy, uz = cat.ux, cat.uy, cat.uz
        cx, cy, cz = centre
        frame = self._field_frame(centre, zen)
        if frame is None:
            return None
        sa, (upx, upy, upz), (rtx, rty, rtz) = frame
        ca = math.sqrt(1.0 - sa * sa)
        n = sv.n_side
        scale = DEG / sv.pitch
        half = n / 2.0
        margin_az = (margin_az_deg * ca + 1e-5) / sv.pitch
        cells: dict[int, list] = {}
        for j in candidates:
            d = ux[j] * cx + uy[j] * cy + uz[j] * cz
            if d <= 0.5:
                continue
            fx = (ux[j] * rtx + uy[j] * rty + uz[j] * rtz) / d * scale + half
            if fx < 0.0 or fx >= n:
                continue
            fy = (ux[j] * upx + uy[j] * upy + uz[j] * upz) / d * scale + half
            if fy < 0.0 or fy >= n:
                continue
            col = int(fx)
            row = int(fy)
            ex = fx - col
            ey = fy - row
            mx = ex if ex < 0.5 else 1.0 - ex
            my = ey if ey < 0.5 else 1.0 - ey
            phit = 1.0
            if mx < margin_az:
                if hard:
                    continue
                phit *= 0.5 + 0.5 * mx / margin_az
            if my < margin_alt:
                if hard:
                    continue
                phit *= 0.5 + 0.5 * my / margin_alt
            members = cells.get(row * n + col)
            if members is None:
                cells[row * n + col] = [(j, phit)]
            else:
                members.append((j, phit))
        return cells or None

    def _durations(self, cells, info, t_floor: int, t_cap: int, calibrate: bool):
        sv = self.sv
        if calibrate:
            return [sv.t_min]
        lo = max(sv.t_min, t_floor)
        values = {d for d in BASE_DURATIONS if lo <= d <= t_cap}
        values.add(t_cap)
        values.add(min(lo, t_cap))
        ceil = math.ceil
        for members in cells.values():
            for j, _ in members:
                rate, _, _, w_b, th, _, tmax, _ = info[j]
                if rate <= 0.0:
                    continue
                if w_b > 0.0:
                    tb = int(ceil(th * 1.25 / rate))
                    if lo <= tb <= t_cap:
                        values.add(tb)
                if tmax < t_cap:
                    tm = int(tmax)
                    if lo <= tm:
                        values.add(tm)
                sat = int(ceil(1.02 / rate / 30.0) * 30)
                if lo <= sat <= t_cap:
                    values.add(sat)
        return sorted(values)

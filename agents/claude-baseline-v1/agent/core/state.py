"""Everything the agent tracks across decisions: per-target progress, zone summaries,
request and uniformity bonuses, the price of telescope time, and the exposure ledger
that makes a Hard-mode `state_resync` exactly reproducible."""
from __future__ import annotations

import math

from .astro import Site, parse_utc
from .catalog import Catalog
from .conditions import Sky
from .pointing import OffsetEstimator


def _median(values):
    ordered = sorted(values)
    k = len(ordered) // 2
    return ordered[k] if len(ordered) % 2 else 0.5 * (ordered[k - 1] + ordered[k])


class Survey:
    T_REF = 900.0            # exposure length the zone potentials are quoted at
    MU_REF = 1.2

    def __init__(self, payload: dict, knobs: dict) -> None:
        site_cfg = payload["site"]
        scoring = payload["scoring"]
        inst = payload["instrument"]
        self.knobs = knobs
        self.scoring = scoring
        self.site = Site(site_cfg["latitude_deg"], site_cfg["longitude_deg"],
                         site_cfg.get("minimum_altitude_deg", 30.0), scoring.get("airmass_exponent", 0.6),
                         scoring.get("lunar_model", {}))
        self.n_fibers = int(inst["n_fibers"])
        self.n_side = int(inst.get("grid_side") or math.isqrt(self.n_fibers))
        self.pitch = float(inst.get("pitch_deg") or (math.sqrt(float(inst["fiber_area_deg2"])) + float(inst.get("gap_deg", 0.0))))
        self.fov = self.n_side * self.pitch
        exposure = inst.get("exposure", {})
        self.t_min = int(exposure.get("min_duration_seconds", 60))
        self.t_max = int(exposure.get("max_duration_seconds", 3600))
        self.f0t0 = float(scoring["flux_zero_point"]) * float(scoring["exposure_zero_point_seconds"])
        self.MU_REF = max(float(v) for v in scoring["program"]["multipliers"].values())
        required = scoring.get("required", {})
        self.thr_req = float(required.get("observed_factor_threshold", 0.5))
        self.p_req = float(required.get("penalty_per_missing", 50.0))
        uniformity = scoring.get("uniformity", {})
        self.thr_unif = float(uniformity.get("observed_factor_threshold", 0.5))
        self.u_weight = float(uniformity.get("weight", 0.0))
        self.slot = float(payload["survey"].get("slot_seconds", 900))
        self.nights = [(parse_utc(n["observing_start_utc"]), parse_utc(n["observing_end_utc"]))
                       for n in payload["survey"]["nights"]]
        self.nights.sort()
        self.survey_end = self.nights[-1][1]
        self.total_night_seconds = sum(end - start for start, end in self.nights)

        self.cat = Catalog(payload, self.site, self.fov, float(uniformity.get("ra_band_width_deg", 10.0)))
        self.cat.build_season(self.nights)
        self.sky = Sky(scoring)
        self.offset = OffsetEstimator(self.n_side, self.pitch)

        cat = self.cat
        n = cat.n
        self.best = [0.0] * n            # best score so far (the scorer's best_i)
        self.gmax = [0.0] * n            # largest completion factor we are sure of
        self.need_req = [self.thr_req * self.f0t0 / max(f, 1e-9) for f in cat.flux]
        self.ledger: dict[int, list] = {}

        self.band_done: dict[int, int] = {b: 0 for b in cat.band_total}
        self.band_bonus: dict[int, float] = {b: 0.0 for b in cat.band_total}
        self.any_band_bonus = False

        self.zpot = [0.0] * cat.nz
        self.zdeep = [0.0] * cat.nz      # what a longest exposure under a very good sky could still add
        self.zreq: list[list[int]] = [[] for _ in range(cat.nz)]
        self.zflag = [False] * cat.nz
        for z in range(cat.nz):
            self.refresh_zone(z)
        self.urgency = [1.0] * cat.nc
        self.sci_urgency = [1.0] * cat.nc
        self.urgency_night = -1

        self.extra: dict[int, tuple[float, float, float]] = {}    # request targets: bonus, deadline, threshold
        self.zextra: dict[int, list[int]] = {}

        # price of telescope time: kappa * recent realised science rate (points per second)
        potentials = sorted(self.zpot)
        typical = potentials[int(0.6 * (len(potentials) - 1))] if potentials else 0.0
        self.rate_gain = 0.8 * typical / self.T_REF * 3600.0
        self.rate_time = 3600.0
        self.observes = 0
        self.required_left = sum(1 for flag in cat.required if flag)

    # -- zones ------------------------------------------------------------------------
    def refresh_zone(self, z: int) -> None:
        cat = self.cat
        flux, weight, best, gmax = cat.flux, cat.weight, self.best, self.gmax
        scale = self.T_REF / self.f0t0
        deep_scale = 1.2 * self.t_max / self.f0t0
        mu = self.MU_REF
        thr = self.thr_req
        values = []
        deep = []
        req = []
        for j in cat.zmembers[z]:
            g = flux[j] * scale
            v = weight[j] * mu * (g if g < 1.0 else 1.0) - best[j]
            if v > 0.0:
                values.append(v)
            else:
                g = flux[j] * deep_scale
                v = weight[j] * mu * (g if g < 1.0 else 1.0) - best[j]
                if v > 1e-3:
                    deep.append(v)
            if cat.required[j] and gmax[j] < thr:
                req.append(j)
        if len(values) > self.n_fibers:
            values.sort(reverse=True)
            values = values[:self.n_fibers]
        if len(deep) > self.n_fibers:
            deep.sort(reverse=True)
            deep = deep[:self.n_fibers]
        self.zpot[z] = sum(values)
        self.zdeep[z] = sum(deep)
        self.zreq[z] = req
        self.zflag[z] = bool(req) or z in getattr(self, "zextra", ())

    @property
    def price(self) -> float:
        return self.knobs["kappa"] * self.rate_gain / self.rate_time

    def night_index(self, t: float) -> int:
        """Index of the night containing t, or of the next night if t is in daytime; len(nights) after the last."""
        nights = self.nights
        lo, hi = 0, len(nights)
        while lo < hi:
            mid = (lo + hi) // 2
            if nights[mid][1] <= t:
                lo = mid + 1
            else:
                hi = mid
        return lo

    # -- feedback ---------------------------------------------------------------------
    def apply_hits(self, observe_index: int, rows: list, teff: float, closed: bool) -> float:
        """rows: (j, score, g_estimate, g_lower). Returns the realised science gain."""
        cat = self.cat
        best, gmax = self.best, self.gmax
        thr_u = self.thr_unif
        thr_r = self.thr_req
        gain = 0.0
        touched = set()
        for j, score, g_est, g_low in rows:
            if score > best[j]:
                gain += score - best[j]
                best[j] = score
                touched.add(cat.zone_of[j])
            if g_low > gmax[j]:
                before = gmax[j]
                gmax[j] = g_low
                if before < thr_u <= g_low:
                    self.band_done[cat.band[j]] += 1
                if cat.required[j] and before < thr_r <= g_low:
                    self.required_left -= 1
                touched.add(cat.zone_of[j])
        self.ledger[observe_index] = [(j, score, g_low) for j, score, _, g_low in rows]
        for z in touched:
            self.refresh_zone(z)
        if not closed and teff > 0:
            decay = math.exp(-teff / (self.knobs["rate_hours"] * 3600.0))
            self.rate_gain = self.rate_gain * decay + gain
            self.rate_time = self.rate_time * decay + teff
        self.observes += 1
        return gain

    def resync(self, message: dict) -> int:
        """Hard-mode data loss: drop the invalidated observe actions from the ledger and
        rebuild every per-target quantity from what is left."""
        window = message.get("invalidated_window") or {}
        first = int(window.get("action_index_start", 0))
        last = int(window.get("action_index_end_exclusive", 0))
        dropped = 0
        for index in range(first, last):
            if self.ledger.pop(index, None) is not None:
                dropped += 1
        cat = self.cat
        n = cat.n
        best = [0.0] * n
        gmax = [0.0] * n
        for rows in self.ledger.values():
            for j, score, g_low in rows:
                if score > best[j]:
                    best[j] = score
                if g_low > gmax[j]:
                    gmax[j] = g_low
        # the message's best scores are authoritative for the scorer's ledger
        listed = message.get("best_scores")
        if isinstance(listed, list):
            official = [0.0] * n
            for row in listed:
                j = cat.index.get(row.get("target_id"))
                if j is not None:
                    official[j] = float(row.get("best_score", 0.0))
            for j in range(n):
                if official[j] < best[j] - 1e-6:
                    # we believed in an exposure the scorer no longer counts
                    best[j] = official[j]
                    gmax[j] = min(gmax[j], official[j] / max(cat.weight[j] * self.MU_REF, 1e-9))
                elif official[j] > best[j]:
                    best[j] = official[j]
        self.best[:] = best
        self.gmax[:] = gmax
        self.band_done = {b: 0 for b in cat.band_total}
        left = 0
        for j in range(n):
            if gmax[j] >= self.thr_unif:
                self.band_done[cat.band[j]] += 1
            if cat.required[j] and gmax[j] < self.thr_req:
                left += 1
        self.required_left = left
        for z in range(cat.nz):
            self.refresh_zone(z)
        return dropped

    # -- bonuses ----------------------------------------------------------------------
    def set_requests(self, active: list, now: float) -> None:
        """Turn active time-limited requests into per-target bonuses.

        A request pays only when `minimum_completed` of its targets are done inside the window,
        so each still-needed target is worth reward / (number still needed): the value of the
        last one is the whole reward. A request that can no longer be met is dropped."""
        cat = self.cat
        old_zones = set(self.zextra)
        extra: dict[int, tuple[float, float, float]] = {}
        for request in active or ():
            try:
                need = int(request.get("minimum_completed", 0)) - int(request.get("completed_count", 0))
                if need <= 0:
                    continue
                deadline = parse_utc(request["deadline_utc"])
                done = set(request.get("completed_target_ids") or ())
                pending = [cat.index[t] for t in request.get("target_ids", ()) if t in cat.index and t not in done]
                if len(pending) < need or deadline - now < 60.0:
                    continue
                reward = float(request.get("completion_reward", 0.0))
                threshold = float(request.get("completion_factor_threshold", 0.5))
            except (KeyError, TypeError, ValueError):
                continue
            # A request field competes with fields that hold required targets, which can wait
            # for another night; the request cannot. Its bonus is kept above theirs and grows
            # as the window runs out (up to three times by the deadline).
            share = max(self.knobs["request_gain"] * reward / need, self.knobs["request_floor"] * reward)
            try:
                issued = parse_utc(request["issued_at_utc"])
                used = (now - issued) / max(1.0, deadline - issued)
                share *= 1.0 + 2.0 * max(0.0, min(1.0, used)) ** 2
            except (KeyError, TypeError, ValueError):
                pass
            for j in pending:
                previous = extra.get(j)
                if previous is None:
                    extra[j] = (share, deadline, threshold)
                else:
                    extra[j] = (previous[0] + share, min(previous[1], deadline), max(previous[2], threshold))
        self.extra = extra
        zextra: dict[int, list[int]] = {}
        for j in extra:
            zextra.setdefault(cat.zone_of[j], []).append(j)
        self.zextra = zextra
        for z in old_zones | set(zextra):
            self.zflag[z] = bool(self.zreq[z]) or z in zextra

    def update_band_bonus(self) -> None:
        """Marginal value of completing one more target of each RA band for the Jain-index
        uniformity penalty; only bands with few targets get a noticeable per-target bonus."""
        totals = self.cat.band_total
        if self.u_weight <= 0.0 or len(totals) < 2:
            return
        ratios = {b: self.band_done[b] / totals[b] for b in totals}
        s1 = sum(ratios.values())
        s2 = sum(r * r for r in ratios.values())
        n = len(ratios)
        any_bonus = False
        for b, r in ratios.items():
            if s2 <= 0.0:
                # nothing observed anywhere yet: J jumps from 0 to 1/n with the first band
                value = 0.0
            else:
                d_j = (2.0 * s1 / (n * s2)) * (1.0 - r * s1 / s2)
                value = self.u_weight * d_j / totals[b]
            value = max(0.0, min(value, 10.0))
            if value < 0.05:
                value = 0.0
            self.band_bonus[b] = value
            any_bonus = any_bonus or value > 0.0
        self.any_band_bonus = any_bonus

    def update_urgency(self, night: int) -> None:
        """Required targets in parts of the sky with little visible time left this season are
        worth more now: urgency = sqrt(median remaining visibility / own remaining visibility)."""
        if night == self.urgency_night or night >= len(self.nights):
            return
        self.urgency_night = night
        cat = self.cat
        # the same idea for ordinary targets, weaker: sky that is about to leave for the season
        # cannot be done later, sky that stays can
        power_s = self.knobs["sci_urgency_power"]
        if power_s > 0.0:
            weight: dict[int, float] = {}
            for z in range(cat.nz):
                if self.zpot[z] > 0.0:
                    c = cat.zcoarse[z]
                    weight[c] = weight.get(c, 0.0) + self.zpot[z]
            left = {c: cat.season_vis[c][night] for c in weight if cat.season_vis[c][night] > 0.0}
            sci = [1.0] * cat.nc
            if left:
                # potential-weighted median of the visibility that is left
                ordered = sorted(left.items(), key=lambda kv: kv[1])
                half = 0.5 * sum(weight[c] for c, _ in ordered)
                run = 0.0
                median = ordered[-1][1]
                for c, value in ordered:
                    run += weight[c]
                    if run >= half:
                        median = value
                        break
                cap_s = self.knobs["sci_urgency_cap"]
                for c, value in left.items():
                    sci[c] = max(1.0, min(cap_s, (median / max(value, 1800.0)) ** power_s))
            self.sci_urgency = sci
        cells = set()
        for z in range(cat.nz):
            if self.zreq[z]:
                cells.add(cat.zcoarse[z])
        if not cells:
            return
        remaining = {c: cat.season_vis[c][night] for c in cells}
        positive = [v for v in remaining.values() if v > 0.0]
        if not positive:
            return
        median = _median(positive)
        power = self.knobs["urgency_power"]
        cap = self.knobs["urgency_cap"]
        urgency = [1.0] * cat.nc
        for c, value in remaining.items():
            ratio = median / max(value, 1800.0)
            urgency[c] = max(1.0, min(cap, ratio ** power))
        self.urgency = urgency

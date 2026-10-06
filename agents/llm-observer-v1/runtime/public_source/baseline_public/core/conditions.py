"""What the sky and the instrument are doing, inferred from bulletins and exposure feedback.

Two numbers describe an exposure's throughput in the public scoring formulas:

    W    weather-only level  = transparency * sky_quality / (seeing * q0)   (site-wide)
    e    instrument efficiency (jitter, earthquakes, hidden instrument faults)

A target's completion factor scales with rho = W * e (times its own air-mass / Moon term),
while its program band depends on W alone. Feedback gives both views:

* an unsaturated hit returns score = w * g * m, so g (hence rho) once the multiplier m is known;
* a saturated hit returns score / w = m exactly, i.e. whether the declared program matched the
  band, which bounds W without involving e.

The filter below attributes each change of rho to weather (W moves) or to the instrument
(e moves): slow drifts and changes that coincide with an all-sky weather notice are weather;
a sudden unannounced drop within a night, or a band that says the sky is better than the
throughput, is the instrument. A persistently low e with no earthquake notice is what an
unrepaired instrument fault looks like, and is the trigger for `report`.
"""
from __future__ import annotations

import math
from collections import deque

PROGRAMS = ("DARK", "BRIGHT", "BACKUP")
COMPASS = {"N": 0.0, "NE": 45.0, "E": 90.0, "SE": 135.0, "S": 180.0, "SW": 225.0, "W": 270.0, "NW": 315.0}
# Typical throughput factor of an all-sky weather notice, used only until the first exposure
# under the new conditions measures the real value.
ALL_SKY_PRIOR = {"overcast": 0.42, "haze": 0.58, "cold_snap": 0.90}
CLOSING_KINDS = ("rain", "storm")
# Directional notices: (min half-width, max half-width of the hidden azimuth sector,
# altitude below which it surely applies, altitude above which it never does,
# throughput factor inside). The published direction is the sector centre rounded to 45 deg.
SECTOR_MODEL = {
    "terrain_obstruction": (15.0, 35.0, 35.0, 56.0, 0.0),
    "rocket_launch": (20.0, 40.0, 30.0, 62.0, 0.0),
    "overcast": (20.0, 60.0, 40.0, 82.0, 0.40),
    "haze": (20.0, 60.0, 40.0, 82.0, 0.55),
    "rain": (20.0, 60.0, 40.0, 82.0, 0.0),
    "storm": (20.0, 60.0, 40.0, 82.0, 0.0),
    "cold_snap": (20.0, 60.0, 40.0, 82.0, 0.90),
}


def _median(values):
    ordered = sorted(values)
    k = len(ordered) // 2
    return ordered[k] if len(ordered) % 2 else 0.5 * (ordered[k - 1] + ordered[k])


def _sector_table(lo: float, hi: float) -> list[float]:
    """P(azimuth inside the hidden sector | distance d from the published compass centre)."""
    table = []
    for d in range(181):
        total = 0.0
        for k in range(8):
            half = lo + (hi - lo) * (k + 0.5) / 8.0
            total += max(0.0, min(1.0, (22.5 + half - d) / 45.0))
        table.append(total / 8.0)
    return table


_SECTOR_TABLES = {kind: _sector_table(model[0], model[1]) for kind, model in SECTOR_MODEL.items()}


def _inside_probability(kind: str, alt: float, az: float, centre: float) -> float:
    model = SECTOR_MODEL[kind]
    if alt >= model[3]:
        return 0.0
    d = abs((az - centre + 180.0) % 360.0 - 180.0)
    p_az = _SECTOR_TABLES[kind][int(d)]
    if p_az <= 0.0:
        return 0.0
    p_alt = 1.0 if alt <= model[2] else (model[3] - alt) / (model[3] - model[2])
    return p_az * p_alt


class Terrain:
    """One announced obstruction, learned as an altitude profile over azimuth.

    The opening bulletin gives only a compass direction (and one notice can stand for two
    overlapping sectors of different height). What the instrument then sees is which targets
    scored zero. Terrain blocks from the horizon up, so around every blocked sighting
    everything at that azimuth and below is blocked, and around every clear sighting everything
    above is clear. The profile is kept per 2 degrees of azimuth relative to the announced
    direction; between what is known blocked and known clear only a strip stays uncertain."""

    SPAN = 80.0     # degrees of azimuth either side of the announced direction that are modelled
    NEAR = 2.0      # a blocked sighting is certain for this many degrees of azimuth either side
    REACH = 6.0     # ... and very likely this far, unless a clear sighting says otherwise

    def __init__(self, centre: float) -> None:
        self.centre = centre
        self.blocked: list[tuple[float, float]] = []     # (relative azimuth, altitude) trusted
        self.pending: list[tuple[float, float]] = []     # from exposures that read all zero
        bins = int(2 * self.SPAN / 2.0) + 1
        self.sure = [-1.0] * bins                        # highest blocked altitude at each azimuth
        self.top = [-1.0] * bins                         # ... and within REACH of it
        self.floor = [99.0] * bins                       # lowest clear altitude near each azimuth
        self.box = None                                  # (lo, hi, top) of everything blocked

    def relative(self, az: float) -> float:
        return (az - self.centre + 180.0) % 360.0 - 180.0

    def _bin(self, r: float) -> int:
        return int((r + self.SPAN) / 2.0 + 0.5)

    def add(self, az: float, alt: float, blocked: bool, confirmed: bool) -> None:
        r = self.relative(az)
        if abs(r) > self.SPAN or alt > 66.0:
            return
        if not blocked:
            lo = max(0, self._bin(r - 3.0))
            hi = min(len(self.floor) - 1, self._bin(r + 3.0))
            for b in range(lo, hi + 1):
                if alt < self.floor[b]:
                    self.floor[b] = alt
            return
        if not confirmed:
            # an exposure that read all zero might have been a closure: trust such a sighting
            # only when a second one, or a confirmed one, agrees with it
            partner = None
            for other in self.pending + self.blocked:
                if abs(other[0] - r) <= 10.0 and other[1] >= alt - 6.0:
                    partner = other
                    break
            if partner is None:
                self.pending.append((r, alt))
                return
            if partner in self.pending:
                self.pending.remove(partner)
                self._trust(partner[0], partner[1])
        self._trust(r, alt)

    def _trust(self, r: float, alt: float) -> None:
        self.blocked.append((r, alt))
        lo = max(0, self._bin(r - self.REACH))
        hi = min(len(self.top) - 1, self._bin(r + self.REACH))
        for b in range(lo, hi + 1):
            if alt > self.top[b]:
                self.top[b] = alt
        for b in range(max(0, self._bin(r - self.NEAR)), min(len(self.top) - 1, self._bin(r + self.NEAR)) + 1):
            if alt > self.sure[b]:
                self.sure[b] = alt
        if self.box is None:
            self.box = (r, r, alt)
        else:
            self.box = (min(self.box[0], r), max(self.box[1], r), max(self.box[2], alt))

    def blocked_probability(self, az: float, alt: float) -> float:
        r = self.relative(az)
        if self.box is None:
            return _inside_probability("terrain_obstruction", alt, az, self.centre)
        if abs(r) > self.SPAN:
            return 0.0
        b = self._bin(r)
        if alt <= self.sure[b]:
            return 1.0
        if alt >= self.floor[b]:
            return 0.0
        top = self.top[b]
        if top >= 0.0:
            if alt <= top:
                return 0.85               # a blocked sighting a few degrees away, nothing against it
            # above what is known blocked at this azimuth: fading over 10 degrees
            return 0.5 * (1.0 - (alt - top) / 10.0) if alt < top + 10.0 else 0.0
        # no blocked sighting near this azimuth yet
        lo, hi, top_all = self.box
        if alt <= top_all:
            p_alt = 1.0
        elif alt < top_all + 10.0:
            p_alt = 0.5 * (1.0 - (alt - top_all) / 10.0)
        else:
            return 0.0
        if lo <= r <= hi:
            return 0.6 * p_alt
        # beside everything seen so far: fading with distance; one sector is at most about
        # 70 degrees wide
        room = max(1.0, min(40.0, 75.0 - (hi - lo)))
        distance = lo - r if r < lo else r - hi
        if distance >= room:
            return 0.0
        return 0.6 * (1.0 - distance / room) * p_alt


class Sky:
    def __init__(self, scoring: dict) -> None:
        program = scoring["program"]
        self.b_dark = float(program["bands"]["DARK"])
        self.b_bright = float(program["bands"]["BRIGHT"])
        self.ln_dark = math.log(self.b_dark)
        self.ln_bright = math.log(self.b_bright)
        self.mult = {name: float(program["multipliers"][name]) for name in PROGRAMS}
        self.mis = float(program.get("mismatch_multiplier", 1.0))
        self.mu_max = max(self.mult.values())
        reporting = scoring.get("reporting", {})
        self.allowance = int(reporting.get("false_report_free_allowance", 0))
        self.free_left = self.allowance

        self.W = 1.10
        self.e = 0.95
        self.rho = self.W * self.e
        self.measured = False
        self.m_night = -1
        self.m_all: frozenset = frozenset()
        self.m_quake = False
        self.tonight: deque = deque(maxlen=6)        # (t, rho, W, e) of clean measurements
        self.prev_tail = None                        # (rho, W, night, all-sky weather) at the end of last night
        self.night_levels: deque = deque(maxlen=21)  # per-night median W
        self._night_w: list[float] = []

        self.bulletins: deque = deque(maxlen=96)     # (ts, frozenset of (kind, direction))
        self.now_notices: frozenset = frozenset()
        self.now_all: frozenset = frozenset()
        self.terrain: list[Terrain] = []
        self.dir_now: list[tuple[str, float]] = []
        self.quake_now = False
        self.site_closed_now = False
        self.quake_last_seen = -1e18
        self.quake_since = -1e18

        self.closed_suspect = False
        self.suspect = False                  # a throughput drop is currently attributed to the instrument
        self.e_ref = 0.95                     # efficiency just before that drop
        self.quake_onset = -1e18
        self.quake_night0 = -1
        self.quake_d0 = None                  # efficiency lost on the night of the earthquake
        self.debug = False
        self.probe = None                     # (program, valid until): one-shot band probe
        self.ruled_out: dict = {}             # program -> (valid until, transmission) after a failed declaration
        self.since_dark_probe = 0
        self.dark_probe_every = 8
        self.weather_settled_at = -1e18
        self.low_count = 0
        self.low_seconds = 0.0
        self.cooldown_until = -1e18
        self.reports_correct = 0
        self.reports_false = 0

    # -- bulletins ------------------------------------------------------------------
    def bulletin(self, record: dict, ts: float) -> None:
        pairs = set()
        for notice in record.get("notices") or ():
            kind = notice.get("event_kind")
            direction = notice.get("direction")
            if kind == "terrain_obstruction":
                centre = COMPASS.get(direction)
                if centre is not None and all(t.centre != centre for t in self.terrain):
                    self.terrain.append(Terrain(centre))
                continue
            if isinstance(kind, str) and isinstance(direction, str):
                pairs.add((kind, direction))
        timed = frozenset(pairs)
        self.bulletins.append((ts, timed))
        self.now_notices = timed
        self.now_all = frozenset(k for k, d in timed if d == "ALL" and k in ALL_SKY_PRIOR)
        self.site_closed_now = any(d == "ALL" and k in CLOSING_KINDS for k, d in timed)
        self.quake_now = any(k == "earthquake" for k, _ in timed)
        if self.quake_now:
            if ts - self.quake_last_seen > 36 * 3600.0:
                self.quake_since = ts          # a new earthquake (its notice then stays for nights)
            self.quake_last_seen = ts
        self.dir_now = [(k, COMPASS[d]) for k, d in timed if d in COMPASS and k in SECTOR_MODEL]

    def notices_between(self, start: float, end: float, slot: float = 900.0) -> frozenset:
        merged = set()
        for ts, timed in self.bulletins:
            if start - slot < ts < end:
                merged.update(timed)
        return frozenset(merged)

    # -- planning views ---------------------------------------------------------------
    def levels(self) -> tuple[float, float]:
        """(rho, W) to plan with now: the last measurement, moved by the typical factor of any
        all-sky weather notice that started or ended since that measurement."""
        rho, w = self.rho, self.W
        if self.now_all != self.m_all:
            for kind in self.now_all - self.m_all:
                rho *= ALL_SKY_PRIOR[kind]
                w *= ALL_SKY_PRIOR[kind]
            for kind in self.m_all - self.now_all:
                rho /= ALL_SKY_PRIOR[kind]
                w /= ALL_SKY_PRIOR[kind]
            rho = min(rho, 1.5)
            w = min(w, 1.6)
        return rho, w

    @property
    def directional(self) -> bool:
        return bool(self.terrain or self.dir_now)

    def dir_factor(self, alt: float, az: float) -> float:
        """Expected throughput factor at (alt, az) from terrain and directional notices."""
        factor = 1.0
        if self.terrain and alt < 66.0:
            for terrain in self.terrain:
                p = terrain.blocked_probability(az, alt)
                if p >= 1.0:
                    return 0.0
                if p > 0.0:
                    factor *= 1.0 - p
        for kind, centre in self.dir_now:
            p = _inside_probability(kind, alt, az, centre)
            if p > 0.0:
                factor *= 1.0 - p * (1.0 - SECTOR_MODEL[kind][4])
        return factor

    def band_probabilities(self, level: float) -> tuple[float, float]:
        """(P(band is DARK), P(band is DARK or BRIGHT)) for a predicted band level."""
        if level <= 0.0:
            return 0.0, 0.0
        x = math.log(level)
        za = (x - self.ln_dark) / 0.07
        zb = (x - self.ln_bright) / 0.07
        pd = 1.0 / (1.0 + math.exp(-za)) if za > -30.0 else 0.0
        pb = 1.0 / (1.0 + math.exp(-zb)) if zb > -30.0 else 0.0
        return pd, pb

    def expected_multiplier(self, level: float, t: float) -> float:
        """Expected program multiplier at band level `level` under the best declaration."""
        pd, pb = self.band_probabilities(level)
        mis = self.mis
        best = mis
        for name, p in (("DARK", pd), ("BRIGHT", pb - pd), ("BACKUP", 1.0 - pb)):
            value = mis + (self.mult[name] - mis) * p
            if value > best:
                best = value
        return best

    def choose_program(self, pairs, t: float) -> str:
        """pairs: (weight, band level) per assigned target."""
        if self.probe is not None and t <= self.probe[1]:
            name = self.probe[0]
            self.probe = None
            self.since_dark_probe = 0
            return name
        self.probe = None
        totals = {"DARK": 0.0, "BRIGHT": 0.0, "BACKUP": 0.0}
        levels = []
        for weight, level in pairs:
            pd, pb = self.band_probabilities(level)
            totals["DARK"] += weight * pd * (self.mult["DARK"] - self.mis)
            totals["BRIGHT"] += weight * (pb - pd) * (self.mult["BRIGHT"] - self.mis)
            totals["BACKUP"] += weight * (1.0 - pb) * (self.mult["BACKUP"] - self.mis)
            levels.append(level)
        choice = max(PROGRAMS, key=lambda name: totals[name])
        if choice == "DARK":
            self.since_dark_probe = 0
            return choice
        # The band is the one reading that ignores the instrument. If the throughput says the sky
        # is poor but recent nights were dark-grade, declare DARK now and then: a match would
        # mean the sky is fine and the instrument is not (an unreported fault looks like this).
        self.since_dark_probe += 1
        if self.since_dark_probe >= self.dark_probe_every and len(self.night_levels) >= 2 and levels and self.W > 0.0:
            usual = sorted(self.night_levels)[(3 * len(self.night_levels)) // 4]
            if usual * _median(levels) / self.W >= self.b_dark * 1.08:
                self.since_dark_probe = 0
                return "DARK"
        return choice

    def _band_of(self, level: float) -> str:
        if level >= self.b_dark:
            return "DARK"
        if level >= self.b_bright:
            return "BRIGHT"
        return "BACKUP"

    # -- feedback -----------------------------------------------------------------------
    def update(self, rec: dict, hits: dict, night: int) -> dict:
        """Digest one exposure. `rec["items"]` holds, per assigned target,
        (j, row, col, unit, A, D, alt, az, safe, weight) where `unit` is the completion factor
        the target would reach at rho = 1 and A its exposure-mean transmission * lunar factor.
        Returns {j: (g_estimate, g_lower_bound)} for the hit targets."""
        program = rec["program"]
        mu = self.mult[program]
        mis = self.mis
        t0 = rec["t0"]
        t1 = t0 + rec["teff"]
        factors: dict[int, tuple[float, float]] = {}
        known_match: list[float] = []
        known_miss: list[float] = []
        unknown: list[tuple[tuple, float]] = []
        ratios: list[float] = []
        bounds: list[float] = []
        zero_items = []
        live_items = []
        n_hit = 0
        for item in rec["items"]:
            j = item[0]
            score = hits.get(j)
            if score is None:
                continue
            n_hit += 1
            weight = item[9]
            obs = score / weight
            tol = 1.2e-6 / weight + 1e-9
            unit, a, d = item[3], item[4], item[5]
            if obs <= tol:
                zero_items.append(item)
                factors[j] = (0.0, 0.0)
                continue
            live_items.append(item)
            if obs >= mu - tol:
                known_match.append(a)
                factors[j] = (1.0, 1.0)
                if d >= 0.999 and unit > 0.0:
                    bounds.append(1.0 / unit)
            elif abs(obs - mis) <= tol:
                known_miss.append(a)
                factors[j] = (1.0, 1.0)
                if d >= 0.999 and unit > 0.0:
                    bounds.append(1.0 / unit)
            elif obs > mis + tol:
                known_match.append(a)
                g = obs / mu
                factors[j] = (g, g)
                if d >= 0.999 and unit > 0.0:
                    ratios.append(g / unit)
            else:
                unknown.append((item, obs))

        if n_hit == 0:
            return factors
        if len(zero_items) == n_hit:
            # Every fibre that landed read zero. Where an obstruction or a directional closure
            # was already likely, that is the explanation (and terrain is permanent: remember
            # it). Anywhere else the site itself was shut for the whole exposure.
            risky = sum(1 for item in zero_items if item[5] < 0.9)
            if risky * 2 >= n_hit:
                if self.terrain:
                    # one tentative sighting for the whole exposure, at its highest fibre
                    high = max(zero_items, key=lambda item: item[6])
                    self._mark_horizon(high[6], high[7], True, confirmed=False)
            else:
                self.closed_suspect = True
                self.tonight.clear()
                self._closure_explains()
            return factors
        self.closed_suspect = False

        # which multiplier applied to the hits that do not show it themselves
        if unknown:
            evidence = bool(known_match or known_miss)
            if known_match and not known_miss:
                verdict = [True] * len(unknown)
            elif known_miss and not known_match:
                verdict = [False] * len(unknown)
            elif evidence:
                verdict = []
                for item, _ in unknown:
                    a = item[4]
                    dm = min(abs(math.log(a / x)) for x in known_match)
                    dx = min(abs(math.log(a / x)) for x in known_miss)
                    verdict.append(dm <= dx)
            else:
                usable = [(obs / item[3], item[4]) for item, obs in unknown if item[3] > 0.0 and item[5] >= 0.999]
                if not usable:
                    usable = [(obs / item[3], item[4]) for item, obs in unknown if item[3] > 0.0]
                matched = True
                if usable:
                    raw = _median([u[0] for u in usable])
                    a_mid = _median([u[1] for u in usable])
                    rho1, rho0 = raw / mu, raw / mis
                    c1 = self._band_of(rho1 / self.e * a_mid) == program
                    c0 = self._band_of(rho0 / self.e * a_mid) != program
                    if self.suspect:
                        # e is the unknown here; the weather level kept from before the drop decides
                        matched = self._band_of(self.W * a_mid) == program
                    elif c1 != c0:
                        matched = c1
                    else:
                        ref = max(self.rho, 1e-6)
                        matched = abs(math.log(max(rho1, 1e-9) / ref)) <= abs(math.log(max(rho0, 1e-9) / ref))
                verdict = [matched] * len(unknown)
            for (item, obs), matched in zip(unknown, verdict):
                g = obs / (mu if matched else mis)
                g = min(1.0, g)
                factors[item[0]] = (g, g if evidence else min(g, obs / self.mu_max))
                if item[3] > 0.0 and item[5] >= 0.999:
                    ratios.append(g / item[3])

        # horizon map: a zero-score hit while others scored is behind something
        if self.terrain:
            for item in zero_items:
                self._mark_horizon(item[6], item[7], True)
            for item in live_items:
                if item[6] < 66.0:
                    self._mark_horizon(item[6], item[7], False)

        all_now = frozenset(k for k, d in rec["notices"] if d == "ALL" and k in ALL_SKY_PRIOR)
        quake = any(k == "earthquake" for k, _ in rec["notices"])
        closing = any(d == "ALL" and k in CLOSING_KINDS for k, d in rec["notices"])
        if all_now != self.m_all or closing:
            # an exposure that straddles the start or end of an all-sky event sees part of it:
            # throughput steps around such a change are weather, never the instrument. While an
            # event simply continues its effect is constant, and a step is as telling as ever.
            self.weather_settled_at = t1 + 2.0 * 3600.0
        clean = not zero_items and len(ratios) >= 1
        partial_closure = False
        if ratios:
            rho_new = _median(ratios)
            if bounds:
                # A saturated hit proves the throughput was at least 1 / unit. Unsaturated hits
                # reading below that floor were held back by something of their own (a target
                # crossing an obstruction, say), not by the sky or the instrument.
                floor_rho = max(bounds)
                if rho_new < floor_rho:
                    if rho_new < 0.8 * floor_rho:
                        clean = False
                    rho_new = floor_rho
            new_night = night != self.m_night
            if self.measured and new_night:
                self._close_night()
            reference = None
            if not new_night:
                # compare with the last hour only: within a night the weather drifts by tens of
                # percent over hours but only a few percent between neighbouring slots
                for entry in self.tonight:
                    if t0 - entry[0] <= 3600.0 and (reference is None or entry[1] > reference[1]):
                        reference = entry
            tail = self.prev_tail
            # A drop is "sudden" below this fraction of the last hour's level. The sky's own
            # slot-to-slot scatter grows with its level (the seeing term is 1 / seeing), so an
            # exceptionally good sky has to drop further before the instrument is blamed.
            step = 0.76
            if reference is not None and reference[2] > 1.2:
                step = max(0.55, 0.76 - 0.25 * (reference[2] - 1.2))
            if not self.measured:
                self.W = rho_new / self.e
            elif not new_night and all_now != self.m_all:
                self.W = rho_new / self.e                       # an all-sky event began or ended
                self.tonight.clear()
            elif quake and (not self.m_quake or self.quake_d0 is None):
                # an earthquake just struck: the step is its damage, not a fault
                self.e = max(0.05, min(1.0, rho_new / self.W))
                self.suspect = False
                self.quake_onset = t0
                self.quake_night0 = night
                self.quake_d0 = max(0.0, min(0.95, 1.0 - self.e / 0.95))
                self.tonight.clear()
            elif quake and night == self.quake_night0 and not self.suspect:
                # rest of that night: the first reading may have straddled the shock, so keep
                # measuring the damage against the weather level from before it
                self.e = max(0.05, min(1.0, rho_new / self.W))
                self.quake_d0 = max(0.0, min(0.95, 1.0 - self.e / 0.95))
            elif self.suspect and rho_new < 0.25 * self.e_ref * self.W:
                self.closed_suspect = True                      # mostly shut: says nothing new
                partial_closure = True
                self._closure_explains()
            elif self.suspect:
                # W stays where it was before the drop; the instrument explains the rest
                if rho_new >= 0.86 * self.e_ref * self.W:
                    self.suspect = False                        # throughput is back: it was transient
                    self.e = self.e_ref
                    self.W = rho_new / self.e
                else:
                    self.e = max(0.05, min(1.0, rho_new / self.W))
            elif new_night:
                if (tail is not None and 1 <= night - tail[2] <= 3 and not all_now and not tail[3]
                        and not (quake and self.quake_since >= tail[5] - 3600.0)
                        and t0 >= self.weather_settled_at
                        and rho_new < (0.60 if night == tail[2] + 1 else 0.50) * tail[0]):
                    # far below where last night ended, with nothing announced: night-to-night
                    # weather rarely moves this much and earthquake damage only heals; an
                    # instrument fault does this
                    self.suspect = True
                    self.e_ref = tail[4]
                    self.W = tail[1]
                    self.e = max(0.05, min(1.0, rho_new / tail[1]))
                elif quake and self.quake_d0 is not None:
                    # Earthquake damage heals night by night (the guide says so; the reference
                    # time constant is about four nights). Follow that curve for e and let W
                    # take the rest: a fault on top of the damage then looks like a sky that
                    # went bad without notice, which the band readings contradict.
                    nights_since = max(0, night - self.quake_night0)
                    self.e = 0.95 * (1.0 - self.quake_d0 * math.exp(-nights_since / 4.0))
                    self.W = rho_new / self.e
                else:
                    self.e = 0.95
                    self.quake_d0 = None
                    self.W = rho_new / self.e
            elif reference is not None and rho_new < 0.25 * reference[1]:
                # almost nothing came through: the site was shut for most of the exposure
                # (unannounced closures come in whole slots), not a statement about W or e
                self.closed_suspect = True
                partial_closure = True
                self._closure_explains()
            elif (reference is not None and rho_new < step * reference[1] and known_miss and not known_match
                  and program == "DARK"):
                # The throughput fell AND the band fell with it. An instrument fault leaves the
                # band alone, so this is the sky: typically a slot or two shut inside the exposure.
                self.W = rho_new / self.e
                self.tonight.clear()
            elif reference is not None and rho_new < step * reference[1] and t0 >= self.weather_settled_at:
                # sudden unannounced drop: the weather cannot move this fast
                self.suspect = True
                self.e_ref = reference[3]
                self.W = reference[2]
                self.e = max(0.05, min(1.0, reference[3] * rho_new / reference[1]))
            else:
                self.W = rho_new / self.e
            if partial_closure:
                rho_new = self.rho
            elif self.W > 2.2:
                # no sky is this good: the split between W and e has gone wrong, start it over
                self.suspect = False
                self.e = 0.95
                self.e_ref = 0.95
                self.W = rho_new / self.e
            self.rho = rho_new
            self.measured = True
            self.m_night = night
            self.m_all = all_now
            self.m_quake = quake
        elif bounds:
            # only saturated hits: they bound the throughput from below
            floor_rho = max(bounds)
            if floor_rho > self.rho:
                if self.suspect:
                    if floor_rho >= 0.86 * self.e_ref * self.W:
                        self.suspect = False              # throughput is back: it was transient
                        self.e = self.e_ref
                        self.W = floor_rho / self.e
                    else:
                        self.e = min(self.e_ref, max(self.e, floor_rho / self.W))
                else:
                    self.W = floor_rho / self.e
                self.rho = floor_rho
            if not self.measured or night != self.m_night:
                if self.measured:
                    self._close_night()
                self.m_night = night
                self.m_all = all_now
                self.m_quake = quake
                self.measured = True
            if not zero_items and all_now == self.m_all and not self.suspect:
                # a floor on the throughput is still a reference for "it suddenly dropped"
                self.tonight.append((t0, floor_rho, self.W, self.e))

        # Band evidence bounds W on its own. rho / e already pins W to a few percent while the
        # instrument is healthy, so only a contradiction larger than the efficiency jitter moves
        # W, and the difference lands in e (a band better than the throughput = lost efficiency).
        lower = upper = None
        if known_match and not known_miss:
            self.ruled_out.clear()
            if program == "DARK":
                lower = self.b_dark / min(known_match)
            elif program == "BRIGHT":
                lower = self.b_bright / min(known_match)
                upper = self.b_dark / max(known_match)
            else:
                upper = self.b_bright / max(known_match)
        elif known_miss and not known_match:
            a_ref = _median(known_miss)
            self.ruled_out = {name: v for name, v in self.ruled_out.items() if v[0] >= t0}
            self.ruled_out[program] = (t1 + 2.5 * 3600.0, a_ref)
            out = self.ruled_out
            if program == "DARK":
                upper = self.b_dark / a_ref
            elif program == "BACKUP":
                lower = self.b_bright / a_ref
            if "BRIGHT" in out:
                a_b = out["BRIGHT"][1]
                # BRIGHT failed at transmission a_b: the level there is below the BRIGHT band or
                # above it. A second failed declaration at comparable transmission says which.
                if "BACKUP" in out and out["BACKUP"][1] <= a_b * 1.03:
                    lower = max(lower or 0.0, self.b_dark / a_b)
                elif "DARK" in out and out["DARK"][1] >= a_b * 0.97:
                    upper = min(upper or 1e9, self.b_bright / a_b)
                elif program == "BRIGHT":
                    self.probe = ("DARK", t1 + 2.0 * 3600.0)    # find out on the next exposure
            if len(out) == 3:
                self.ruled_out = {}
        if lower is not None and upper is not None and lower >= upper:
            lower = upper = None                                # contradictory: the sky moved
            self.ruled_out = {}
        if lower is not None and lower > self.W * 1.04:
            before = self.e
            self.W = lower * 1.01
            self.e = max(0.05, min(1.0, self.rho / self.W))
            if quake and self.quake_d0 is not None and night <= self.quake_night0 + 1:
                # The shock night rarely gives a clean reading of the damage (the earthquake
                # strikes mid-exposure, or late). The band now shows the efficiency is lower
                # than the healing curve assumed: correct the curve, do not suspect a fault.
                healed = math.exp(-max(0, night - self.quake_night0) / 4.0)
                self.quake_d0 = max(self.quake_d0, min(0.95, (1.0 - self.e / 0.95) / healed))
            elif not self.suspect and self.e < 0.80 * before:
                # the band says the sky is clearly better than the throughput: lost efficiency
                self.suspect = True
                self.e_ref = before
        if upper is not None and upper < self.W / 1.04:
            self.W = upper * 0.99
            self.e = max(0.05, min(1.0, self.rho / self.W))
            if self.suspect and self.e >= 0.86 * self.e_ref:
                self.suspect = False                            # the sky really is this poor
                self.e = self.e_ref
                self.W = self.rho / self.e

        if self.debug:
            import sys
            from .astro import format_utc
            print(f"sky {format_utc(t0)} T={rec['teff']:.0f} {program} match={len(known_match)} miss={len(known_miss)} "
                  f"unk={len(unknown)} zero={len(zero_items)} nrat={len(ratios)} "
                  f"rho={(_median(ratios) if ratios else float('nan')):.3f} W={self.W:.3f} e={self.e:.3f} "
                  f"low={self.low_count} lower={lower} upper={upper} "
                  f"Amatch={(min(known_match) if known_match else 0):.3f} notices={sorted(rec['notices'])}",
                  file=sys.stderr)
        if ratios and clean and not partial_closure:
            self.tonight.append((t0, self.rho, self.W, self.e))
            self._night_w.append(self.W)
            if rec.get("clear_dirs", True) and t0 >= self.weather_settled_at:
                if self.suspect and self.e < 0.78 * self.e_ref and not partial_closure:
                    self.low_count += 1
                    self.low_seconds += rec["teff"]
        if not self.suspect:
            self.low_count = 0
            self.low_seconds = 0.0
        return factors

    def _mark_horizon(self, alt: float, az: float, blocked: bool, confirmed: bool = True) -> None:
        """Feed one sighting to the obstruction whose announced direction is nearest."""
        if blocked:
            # a directional closure that is being announced right now explains a zero as well
            for kind, centre in self.dir_now:
                if SECTOR_MODEL[kind][4] == 0.0 and abs((az - centre + 180.0) % 360.0 - 180.0) < 70.0:
                    return
        nearest = min(self.terrain, key=lambda t: abs(t.relative(az)))
        nearest.add(az, alt, blocked, confirmed)

    def _closure_explains(self) -> None:
        """A closure right after a fresh drop explains the drop: the exposure before it was
        already partly shut. Forget the suspicion unless it had time to establish itself."""
        if self.suspect and self.low_count <= 2:
            self.suspect = False
            self.e = self.e_ref
            self.low_count = 0
            self.low_seconds = 0.0

    def _close_night(self) -> None:
        if len(self.tonight) >= 2:
            last = list(self.tonight)[-3:]
            self.prev_tail = (_median([entry[1] for entry in last]), _median([entry[2] for entry in last]),
                              self.m_night, bool(self.m_all), _median([entry[3] for entry in last]), last[0][0])
        else:
            self.prev_tail = None
        if self._night_w:
            self.night_levels.append(_median(self._night_w))
        self._night_w = []
        self.tonight.clear()
        self.probe = None
        self.ruled_out = {}
        # evidence of a fault has to be confirmed on the new night before it is acted on
        self.low_count = min(self.low_count, 2)
        self.low_seconds = min(self.low_seconds, 2400.0)

    # -- instrument-fault reporting -------------------------------------------------------
    def want_report(self, t: float, remaining_gain: float = 0.0) -> bool:
        """Report when a low efficiency has persisted and the bet is worth it.

        A report that turns out wrong costs nothing inside the free allowance and 150 after it;
        one that is right earns 100 and, more importantly, gives back the lost efficiency for
        the rest of the survey (`remaining_gain` is the science still expected at full
        efficiency). With a persistent, unannounced drop the odds of a real fault are taken as
        roughly one in three."""
        if t < self.cooldown_until or t - self.quake_onset < 3 * 3600.0 or t - self.quake_since < 3 * 3600.0:
            return False
        # Faults in every configuration seen multiply the efficiency by 0.7 or less; a shortfall
        # smaller than about a fifth is within what the sky does on its own.
        if not self.suspect or self.e >= 0.78 * self.e_ref:
            return False
        ratio = self.e / self.e_ref
        strong = ratio < 0.62          # what most faults look like; the sky alone hardly does this
        need = 3 + min(4, self.reports_false)
        seconds = 2400.0
        if not strong:
            # a shortfall of a fifth to a third: a mild fault, or the sky. Wait for more of it.
            need = max(need, 5)
            seconds = 5400.0
        elif self.quake_now and t - self.quake_since < 60 * 3600.0:
            need += 2
            seconds = 3600.0
        elif self.free_left >= 2 and self.reports_false == 0:
            # the first call is free and a second free one stays in reserve: act a little sooner
            need, seconds = 2, 1800.0
        # low readings spanning well over one slot: a single shut slot can spoil two
        # neighbouring exposures, never more
        if self.low_count < need or self.low_seconds < seconds:
            return False
        if self.free_left > 0:
            return True
        # no free call left: only a strong, well established shortfall is worth the risk
        if not strong or self.low_count < 4 or self.low_seconds < 3600.0:
            return False
        deficit = 1.0 - ratio
        return 0.3 * (100.0 + deficit * remaining_gain) > 0.7 * 150.0

    def report_result(self, correct: bool, t: float) -> None:
        self.low_count = 0
        self.low_seconds = 0.0
        self.suspect = False
        self.tonight.clear()
        if correct:
            self.reports_correct += 1
            self.free_left = self.allowance
            self.e = self.e_ref                 # repaired: back to the efficiency before the drop
            self.rho = self.W * self.e
        else:
            self.reports_false += 1
            self.free_left = max(0, self.free_left - 1)
            self.e = self.e_ref                 # not the instrument: it was the weather after all
            self.W = self.rho / self.e
            self.cooldown_until = t + 8 * 3600.0

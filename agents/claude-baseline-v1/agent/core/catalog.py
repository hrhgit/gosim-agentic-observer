"""Static catalogue structures built once from `initialize`.

* per-target parallel arrays (position, unit vector, flux, weight, required, RA band,
  hour-angle limit);
* zones: a declination-banded grid whose cells are about one field of view wide. Zones
  are the unit the planner ranks before it evaluates any exact pointing, and their 5x5
  neighbourhoods answer "which targets could share a field with this one";
* a coarse seasonal table (5 deg cells x nights): how much visible time is left this
  season and the best transmission still to come, used for urgency and timing.
"""
from __future__ import annotations

import math

from .astro import DEG, OMEGA, RAD, lst_deg, unit_vector


def _wrap180(angle: float) -> float:
    return (angle + 180.0) % 360.0 - 180.0


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    lo = a0 if a0 > b0 else b0
    hi = a1 if a1 < b1 else b1
    return hi - lo if hi > lo else 0.0


class Grid:
    """Declination bands of height `size`, each cut into RA cells of about `size` on the sky."""

    def __init__(self, size: float) -> None:
        self.size = size
        self.nb = int(math.ceil(180.0 / size))
        self.ncell = []
        for b in range(self.nb):
            dec_mid = -90.0 + (b + 0.5) * size
            self.ncell.append(max(1, int(360.0 * math.cos(dec_mid * RAD) / size)))

    def locate(self, ra: float, dec: float) -> tuple[int, int]:
        b = int((dec + 90.0) / self.size)
        if b >= self.nb:
            b = self.nb - 1
        elif b < 0:
            b = 0
        n = self.ncell[b]
        k = int(ra / 360.0 * n)
        if k >= n:
            k = n - 1
        return b, k

    def cells_near(self, b: int, k: int, radius: float) -> list[tuple[int, int]]:
        """Cells that can hold a point within `radius` degrees of any point of cell (b, k)."""
        size = self.size
        reach_bands = int(math.ceil(radius / size))
        width = 360.0 / self.ncell[b]
        ra_lo = k * width
        ra_hi = ra_lo + width
        out = []
        dec_lo = -90.0 + b * size - radius
        dec_hi = -90.0 + (b + 1) * size + radius
        extreme = max(abs(dec_lo), abs(dec_hi))
        if extreme >= 88.0:
            delta = 360.0
        else:
            delta = radius / math.cos(extreme * RAD)
        for bb in range(max(0, b - reach_bands), min(self.nb - 1, b + reach_bands) + 1):
            n = self.ncell[bb]
            w = 360.0 / n
            if ra_hi - ra_lo + 2.0 * delta >= 360.0 - w:
                out.extend((bb, kk) for kk in range(n))
                continue
            first = int(math.floor((ra_lo - delta) / w))
            last = int(math.floor((ra_hi + delta) / w))
            seen = set()
            for kk in range(first, last + 1):
                kk %= n
                if kk not in seen:
                    seen.add(kk)
                    out.append((bb, kk))
        return out


class Catalog:
    def __init__(self, payload: dict, site, fov_side_deg: float, band_width_deg: float) -> None:
        table = payload["targets"]
        col = {name: index for index, name in enumerate(table["columns"])}
        rows = table["rows"]
        n = len(rows)
        self.n = n
        self.site = site
        c_id, c_ra, c_dec = col["target_id"], col["ra_deg"], col["dec_deg"]
        c_f, c_w, c_req = col["feature_flux"], col["science_weight"], col["required"]
        self.ids = [row[c_id] for row in rows]
        self.ra = [float(row[c_ra]) for row in rows]
        self.dec = [float(row[c_dec]) for row in rows]
        self.flux = [float(row[c_f]) for row in rows]
        self.weight = [float(row[c_w]) for row in rows]
        self.required = [row[c_req] is True or str(row[c_req]).strip().lower() == "true" for row in rows]
        self.index = {target_id: i for i, target_id in enumerate(self.ids)}
        ux, uy, uz = [0.0] * n, [0.0] * n, [0.0] * n
        hmax = [0.0] * n
        for i in range(n):
            ux[i], uy[i], uz[i] = unit_vector(self.ra[i], self.dec[i])
            hmax[i] = site.hour_angle_limit(self.dec[i])
        self.ux, self.uy, self.uz, self.hmax = ux, uy, uz, hmax
        self.band_width = float(band_width_deg)
        self.band = [int(ra // self.band_width) for ra in self.ra]
        self.band_total: dict[int, int] = {}
        for b in self.band:
            self.band_total[b] = self.band_total.get(b, 0) + 1

        self.fov = float(fov_side_deg)
        self._build_zones()

    # -- zones ----------------------------------------------------------------------
    def _build_zones(self) -> None:
        grid = Grid(self.fov)
        self.grid = grid
        key_to_zone: dict[tuple[int, int], int] = {}
        zone_of = [0] * self.n
        members: list[list[int]] = []
        keys: list[tuple[int, int]] = []
        for i in range(self.n):
            key = grid.locate(self.ra[i], self.dec[i])
            z = key_to_zone.get(key)
            if z is None:
                z = len(members)
                key_to_zone[key] = z
                members.append([])
                keys.append(key)
            members[z].append(i)
            zone_of[i] = z
        self.zone_of = zone_of
        self.zmembers = members
        self.zkeys = keys
        self.key_to_zone = key_to_zone
        nz = len(members)
        self.nz = nz
        zcx, zcy, zcz = [0.0] * nz, [0.0] * nz, [0.0] * nz
        zra, zdec = [0.0] * nz, [0.0] * nz
        ux, uy, uz = self.ux, self.uy, self.uz
        for z, idx in enumerate(members):
            sx = sum(ux[i] for i in idx)
            sy = sum(uy[i] for i in idx)
            sz = sum(uz[i] for i in idx)
            norm = math.sqrt(sx * sx + sy * sy + sz * sz) or 1.0
            zcx[z], zcy[z], zcz[z] = sx / norm, sy / norm, sz / norm
            zdec[z] = math.asin(max(-1.0, min(1.0, sz / norm))) * DEG
            zra[z] = math.atan2(sy, sx) * DEG % 360.0
        self.zcx, self.zcy, self.zcz, self.zra, self.zdec = zcx, zcy, zcz, zra, zdec
        self.zhmax = [self.site.hour_angle_limit(d) for d in zdec]
        self._reach: dict[int, list[int]] = {}
        self.reach_radius = self.fov * math.sqrt(2.0) + 0.05

    def reach(self, z: int) -> list[int]:
        """Targets that may fall in a field containing any target of zone z."""
        cached = self._reach.get(z)
        if cached is None:
            b, k = self.zkeys[z]
            cached = []
            for key in self.grid.cells_near(b, k, self.reach_radius):
                other = self.key_to_zone.get(key)
                if other is not None:
                    cached.extend(self.zmembers[other])
            self._reach[z] = cached
        return cached

    # -- seasonal tables ------------------------------------------------------------
    def build_season(self, nights: list[tuple[float, float]], cell_deg: float = 5.0) -> None:
        """Per coarse cell and night: visible seconds left this season (suffix sum) and the
        best transmission of any later night (suffix max), plus tonight's own peak."""
        site = self.site
        coarse = Grid(cell_deg)
        key_to_cell: dict[tuple[int, int], int] = {}
        sums: list[list[float]] = []
        zcoarse = [0] * self.nz
        for z in range(self.nz):
            key = coarse.locate(self.zra[z], self.zdec[z])
            c = key_to_cell.get(key)
            if c is None:
                c = len(sums)
                key_to_cell[key] = c
                sums.append([0.0, 0.0, 0.0])
            zcoarse[z] = c
            acc = sums[c]
            acc[0] += self.zcx[z]
            acc[1] += self.zcy[z]
            acc[2] += self.zcz[z]
        self.zcoarse = zcoarse
        nc = len(sums)
        self.nc = nc
        nn = len(nights)
        lon = site.lon
        night_lst = [lst_deg(start, lon) for start, _ in nights]
        night_span = [OMEGA * (end - start) for start, end in nights]
        self.season_vis: list[list[float]] = []
        self.season_peak: list[list[float]] = []
        self.season_future: list[list[float]] = []
        sin_lat, cos_lat = site.sin_lat, site.cos_lat
        for c in range(nc):
            sx, sy, sz = sums[c]
            norm = math.sqrt(sx * sx + sy * sy + sz * sz) or 1.0
            dec = math.asin(max(-1.0, min(1.0, sz / norm))) * DEG
            ra = math.atan2(sy, sx) * DEG % 360.0
            hm = site.hour_angle_limit(dec)
            sd, cd = math.sin(dec * RAD), math.cos(dec * RAD)
            a_culm = site.transmission(math.sin(min(90.0, 90.0 - abs(dec - site.lat)) * RAD))
            vis = [0.0] * (nn + 1)
            peak = [0.0] * nn
            for i in range(nn):
                if hm < 0.0:
                    continue
                h0 = _wrap180(night_lst[i] - ra)
                h1 = h0 + night_span[i]
                if hm >= 180.0:
                    vis[i] = night_span[i] / OMEGA
                else:
                    vis[i] = (_overlap(h0, h1, -hm, hm) + _overlap(h0, h1, 360.0 - hm, 360.0 + hm)) / OMEGA
                if vis[i] <= 0.0:
                    continue
                if h0 <= 0.0 <= h1 or h0 <= 360.0 <= h1:
                    peak[i] = a_culm
                else:
                    best = 0.0
                    for h in (h0, h1):
                        if abs(_wrap180(h)) <= hm:
                            value = site.transmission(sin_lat * sd + cos_lat * cd * math.cos(h * RAD))
                            if value > best:
                                best = value
                    # an arc that crosses the visible window without containing either night
                    # edge peaks at the window edge nearest transit; the edges above cover the
                    # common cases and the culmination case is handled separately.
                    peak[i] = best
            remaining = [0.0] * (nn + 1)
            future = [0.0] * (nn + 1)
            for i in range(nn - 1, -1, -1):
                remaining[i] = remaining[i + 1] + vis[i]
                future[i] = max(future[i + 1], peak[i + 1] if i + 1 < nn else 0.0)
            self.season_vis.append(remaining)
            self.season_peak.append(peak)
            self.season_future.append(future)

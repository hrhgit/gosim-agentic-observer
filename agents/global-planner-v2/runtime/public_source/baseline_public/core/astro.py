"""Public sky geometry, written from the participant guide (sections 2 and 3).

Everything here is a function of the public site parameters and UTC time only. The
formulas (and their order of floating-point operations) follow the guide's equations
(1)-(16), so a field the planner computes lands where the scorer says it lands.

Vectors are equatorial unit vectors (x to RA 0, y to RA 90 deg, z to the north pole).
Working in that frame makes the per-target cost of a field evaluation three dot
products with no trigonometry: only the field centre and the zenith depend on time.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

RAD = math.pi / 180.0
DEG = 180.0 / math.pi
SIDEREAL_DEG_PER_DAY = 360.98564736629
OMEGA = SIDEREAL_DEG_PER_DAY / 86400.0  # hour-angle rate, degrees per second

_ZENITH_RAW = 1.0 / (1.0 + 0.50572 * 96.07995 ** -1.6364)


def parse_utc(text: str) -> float:
    """ISO-8601 UTC string -> unix seconds."""
    return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()


def format_utc(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def days_since_j2000(ts: float) -> float:
    return (ts / 86400.0 + 2440587.5) - 2451545.0


def lst_deg(ts: float, longitude_deg: float) -> float:
    """Local sidereal time in degrees, guide eq. (1)."""
    return (280.46061837 + 360.98564736629 * days_since_j2000(ts) + longitude_deg) % 360.0


def unit_vector(ra_deg: float, dec_deg: float) -> tuple[float, float, float]:
    ra = ra_deg * RAD
    dec = dec_deg * RAD
    c = math.cos(dec)
    return c * math.cos(ra), c * math.sin(ra), math.sin(dec)


def sun_radec(ts: float) -> tuple[float, float]:
    """Guide eqs. (5)-(6)."""
    d = days_since_j2000(ts)
    mean_longitude = (280.460 + 0.9856474 * d) % 360.0
    g = ((357.528 + 0.9856003 * d) % 360.0) * RAD
    lam = ((mean_longitude + 1.915 * math.sin(g) + 0.020 * math.sin(2 * g)) % 360.0) * RAD
    eps = (23.439 - 0.0000004 * d) * RAD
    ra = math.atan2(math.cos(eps) * math.sin(lam), math.cos(lam)) * DEG % 360.0
    dec = math.asin(math.sin(eps) * math.sin(lam)) * DEG
    return ra, dec


def moon_radec(ts: float) -> tuple[float, float]:
    """Guide eqs. (8)-(11): the low-precision Moon the scorer uses."""
    d = days_since_j2000(ts)
    lm = ((218.316 + 13.176396 * d) % 360.0) * RAD
    mm = ((134.963 + 13.064993 * d) % 360.0) * RAD
    fm = ((93.272 + 13.229350 * d) % 360.0) * RAD
    lam = lm + 6.289 * RAD * math.sin(mm)
    beta = 5.128 * RAD * math.sin(fm)
    eps = (23.439 - 0.0000004 * d) * RAD
    x = math.cos(lam) * math.cos(beta)
    y = math.sin(lam) * math.cos(beta) * math.cos(eps) - math.sin(beta) * math.sin(eps)
    z = math.sin(lam) * math.cos(beta) * math.sin(eps) + math.sin(beta) * math.cos(eps)
    return math.atan2(y, x) * DEG % 360.0, math.asin(z) * DEG


def airmass(alt_deg: float) -> float:
    """Zenith-normalised air mass, guide eq. (15)."""
    if alt_deg <= 0.0:
        return float("inf")
    z = 90.0 - alt_deg
    raw = 1.0 / (math.cos(z * RAD) + 0.50572 * (96.07995 - z) ** -1.6364)
    return raw / _ZENITH_RAW


class Site:
    """Site constants plus the time-dependent horizon frame and Moon state."""

    TABLE = 2000  # resolution of the sin(altitude) -> transmission table

    def __init__(self, latitude_deg: float, longitude_deg: float, minimum_altitude_deg: float,
                 airmass_exponent: float, lunar: dict) -> None:
        self.lat = float(latitude_deg)
        self.lon = float(longitude_deg)
        self.sin_lat = math.sin(self.lat * RAD)
        self.cos_lat = math.cos(self.lat * RAD)
        self.min_alt = float(minimum_altitude_deg)
        self.sin_min_alt = math.sin(self.min_alt * RAD)
        self.beta = float(airmass_exponent)
        self.moon_penalty = float(lunar.get("maximum_penalty", 0.0))
        self.moon_alt_exp = float(lunar.get("altitude_exponent", 1.0))
        self.moon_scale = float(lunar.get("angular_decay_scale_deg", 35.0))
        # transmission(alt) = airmass^-beta, tabulated on sin(alt) so a field scan costs
        # one dot product and one list lookup per zone.
        n = self.TABLE
        table = [0.0] * (n + 2)
        for i in range(1, n + 1):
            alt = math.asin(min(1.0, i / n)) * DEG
            table[i] = airmass(alt) ** -self.beta
        table[n + 1] = table[n]
        self.atab = table

    def transmission(self, sin_alt: float) -> float:
        if sin_alt <= 0.0:
            return 0.0
        return airmass(math.asin(min(1.0, sin_alt)) * DEG) ** -self.beta

    def frame(self, ts: float):
        """(lst_deg, zenith, north, east): the horizon triad as equatorial unit vectors."""
        lst = lst_deg(ts, self.lon)
        cl = math.cos(lst * RAD)
        sl = math.sin(lst * RAD)
        zenith = (self.cos_lat * cl, self.cos_lat * sl, self.sin_lat)
        north = (-self.sin_lat * cl, -self.sin_lat * sl, self.cos_lat)
        east = (-sl, cl, 0.0)
        return lst, zenith, north, east

    def moon(self, ts: float):
        """(mx, my, mz, strength): Moon direction and P_M * illumination * sin(alt)^gamma.

        strength is 0 while the Moon is below the horizon; the lunar factor of a target at
        angular distance rho degrees is then 1 - strength * exp(-rho / theta), eq. (14).
        """
        mra, mdec = moon_radec(ts)
        sra, sdec = sun_radec(ts)
        m = unit_vector(mra, mdec)
        s = unit_vector(sra, sdec)
        cos_psi = max(-1.0, min(1.0, m[0] * s[0] + m[1] * s[1] + m[2] * s[2]))
        illumination = (1.0 - cos_psi) / 2.0
        _, zen, _, _ = self.frame(ts)
        sin_alt = m[0] * zen[0] + m[1] * zen[1] + m[2] * zen[2]
        if sin_alt <= 0.0 or self.moon_penalty <= 0.0:
            return m[0], m[1], m[2], 0.0
        return m[0], m[1], m[2], self.moon_penalty * illumination * sin_alt ** self.moon_alt_exp

    def lunar_factor(self, ux: float, uy: float, uz: float, moon) -> float:
        strength = moon[3]
        if strength <= 0.0:
            return 1.0
        cos_sep = max(-1.0, min(1.0, ux * moon[0] + uy * moon[1] + uz * moon[2]))
        value = 1.0 - strength * math.exp(-math.acos(cos_sep) * DEG / self.moon_scale)
        return max(0.0, min(1.0, value))

    def hour_angle_limit(self, dec_deg: float) -> float:
        """Half-width (deg of hour angle) of the arc on which a target is at or above the
        minimum altitude: -1 if it never is, 180 if it always is."""
        dec = dec_deg * RAD
        denom = self.cos_lat * math.cos(dec)
        if abs(denom) < 1e-12:
            return 180.0 if self.sin_lat * math.sin(dec) >= self.sin_min_alt else -1.0
        cos_h = (self.sin_min_alt - self.sin_lat * math.sin(dec)) / denom
        if cos_h > 1.0:
            return -1.0
        if cos_h < -1.0:
            return 180.0
        return math.acos(cos_h) * DEG

    def altaz(self, vec, ts: float) -> tuple[float, float]:
        """Altitude and azimuth (deg, azimuth 0 = north, 90 = east) of a unit vector."""
        _, zen, north, east = self.frame(ts)
        sin_alt = vec[0] * zen[0] + vec[1] * zen[1] + vec[2] * zen[2]
        alt = math.asin(max(-1.0, min(1.0, sin_alt))) * DEG
        az = math.atan2(vec[0] * east[0] + vec[1] * east[1] + vec[2] * east[2],
                        vec[0] * north[0] + vec[1] * north[1] + vec[2] * north[2]) * DEG % 360.0
        return alt, az

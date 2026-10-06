from __future__ import annotations
import math
from datetime import datetime

def _julian_date(moment: datetime) -> float:
    return moment.timestamp() / 86400.0 + 2440587.5

def _local_sidereal_deg(moment: datetime, longitude_deg: float) -> float:
    days = _julian_date(moment) - 2451545.0
    return (280.46061837 + 360.98564736629 * days + longitude_deg) % 360.0

def _sun_equatorial_deg(moment: datetime) -> tuple[float, float]:
    days = _julian_date(moment) - 2451545.0
    mean_longitude = (280.46 + 0.9856474 * days) % 360.0
    mean_anomaly = math.radians((357.528 + 0.9856003 * days) % 360.0)
    longitude = math.radians((mean_longitude + 1.915 * math.sin(mean_anomaly) + 0.02 * math.sin(2 * mean_anomaly)) % 360.0)
    obliquity = math.radians(23.439 - 4e-07 * days)
    return (math.degrees(math.atan2(math.cos(obliquity) * math.sin(longitude), math.cos(longitude))) % 360.0, math.degrees(math.asin(math.sin(obliquity) * math.sin(longitude))))

def _moon_equatorial_deg(moment: datetime) -> tuple[float, float]:
    days = _julian_date(moment) - 2451545.0
    mean_longitude = math.radians((218.316 + 13.176396 * days) % 360.0)
    mean_anomaly = math.radians((134.963 + 13.064993 * days) % 360.0)
    argument_latitude = math.radians((93.272 + 13.22935 * days) % 360.0)
    longitude = mean_longitude + math.radians(6.289) * math.sin(mean_anomaly)
    latitude = math.radians(5.128) * math.sin(argument_latitude)
    obliquity = math.radians(23.439 - 4e-07 * days)
    x = math.cos(longitude) * math.cos(latitude)
    y = math.sin(longitude) * math.cos(latitude) * math.cos(obliquity) - math.sin(latitude) * math.sin(obliquity)
    z = math.sin(longitude) * math.cos(latitude) * math.sin(obliquity) + math.sin(latitude) * math.cos(obliquity)
    return (math.degrees(math.atan2(y, x)) % 360.0, math.degrees(math.asin(z)))

def _angular_separation_deg(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    ra1r, dec1r, ra2r, dec2r = map(math.radians, (ra1, dec1, ra2, dec2))
    cosine = math.sin(dec1r) * math.sin(dec2r) + math.cos(dec1r) * math.cos(dec2r) * math.cos(ra1r - ra2r)
    return math.degrees(math.acos(max(-1.0, min(1.0, cosine))))

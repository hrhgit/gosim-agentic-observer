from __future__ import annotations
import math
from datetime import datetime
from .tile_geometry_simulator import _local_sidereal_deg

def radec_to_altaz(ra_deg: float, dec_deg: float, moment: datetime, latitude_deg: float, longitude_deg: float) -> tuple[float, float]:
    """Equatorial -> horizontal (az 0=north, 90=east), same convention as the v3 geometry."""
    hour_angle = math.radians((_local_sidereal_deg(moment, longitude_deg) - ra_deg + 180.0) % 360.0 - 180.0)
    latitude = math.radians(latitude_deg)
    declination = math.radians(dec_deg)
    sin_altitude = math.sin(latitude) * math.sin(declination) + math.cos(latitude) * math.cos(declination) * math.cos(hour_angle)
    altitude = math.asin(max(-1.0, min(1.0, sin_altitude)))
    cos_altitude = max(1e-12, math.cos(altitude))
    sin_azimuth = -math.sin(hour_angle) * math.cos(declination) / cos_altitude
    cos_azimuth = (math.sin(declination) - math.sin(altitude) * math.sin(latitude)) / (cos_altitude * max(1e-12, math.cos(latitude)))
    return (math.degrees(altitude), math.degrees(math.atan2(sin_azimuth, cos_azimuth)) % 360.0)

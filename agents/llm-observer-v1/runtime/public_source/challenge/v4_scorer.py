from __future__ import annotations
import math
from datetime import datetime
from functools import lru_cache
from .tile_geometry_simulator import _moon_equatorial_deg, _sun_equatorial_deg, _angular_separation_deg
from .v4_fiber_map import radec_to_altaz

@lru_cache(maxsize=65536)
def _lunar_geometry(moment: datetime, latitude_deg: float, longitude_deg: float) -> tuple[float, float, float, float]:
    """Moon RA/Dec, illuminated fraction, and altitude shared across targets."""
    moon_ra, moon_dec = _moon_equatorial_deg(moment)
    sun_ra, sun_dec = _sun_equatorial_deg(moment)
    sun_moon_separation = _angular_separation_deg(sun_ra, sun_dec, moon_ra, moon_dec)
    illumination = (1.0 - math.cos(math.radians(sun_moon_separation))) / 2.0
    moon_altitude, _ = radec_to_altaz(moon_ra, moon_dec, moment, latitude_deg, longitude_deg)
    return (moon_ra, moon_dec, illumination, moon_altitude)

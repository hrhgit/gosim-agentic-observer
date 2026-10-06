"""Public-only features. No weather truth, seeds, identifiers or scores are accepted."""
from __future__ import annotations

import math
import bisect
from datetime import datetime, timedelta, timezone

import numpy as np

from challenge.v4_scorer import _lunar_geometry

KINDS = ('rain', 'overcast', 'haze', 'cold_snap', 'storm', 'rocket_launch', 'earthquake', 'terrain_obstruction')
DIRECTIONS = ('N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW', 'ALL')
HISTORY_LENGTH = 32
HISTORY_DIM = 75
CONTEXT_NAMES = ([f'current:{k}:{d}' for k in KINDS for d in DIRECTIONS]
    + [f'forecast:{k}:{d}' for k in KINDS for d in DIRECTIONS]
    + [f'terrain:{d}' for d in DIRECTIONS]
    + [f'run:{k}:{d}' for k in KINDS for d in DIRECTIONS]
    + ['bulletin_age', 'forecast_age', 'forecast_covers', 'latitude', 'longitude_sin', 'longitude_cos',
       'minimum_altitude', 'q0', 'airmass_exponent', 'dark_threshold', 'bright_threshold',
       'season_sin', 'season_cos', 'lead', 'requested_duration', 'actual_duration', 'night_remaining'])
CANDIDATE_NAMES = [f'{stage}:{field}' for stage in ('start', 'middle', 'end') for field in
    ('altitude', 'azimuth_sin', 'azimuth_cos', 'airmass', 'lunar_factor', 'moon_altitude',
     'moon_illumination', 'moon_separation', 'geometric_quality')] + ['minimum_altitude', 'mean_geometric_quality']
VERSION = 'public-env-features-v1'


def utc(value):
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError('UTC datetime must have a timezone')
        return value.astimezone(timezone.utc)
    result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('timestamp must have a timezone')
    return result.astimezone(timezone.utc)


def notice_matrix(notices):
    result = np.zeros((len(KINDS), len(DIRECTIONS)), dtype=np.float64)
    for notice in notices:
        kind, direction = notice['event_kind'], notice['direction']
        if kind not in KINDS or direction not in DIRECTIONS:
            raise ValueError(f'unsupported public notice: {kind}/{direction}')
        result[KINDS.index(kind), DIRECTIONS.index(direction)] = 1.0
    return result


def age_value(seconds):
    return math.log1p(max(0.0, seconds)) / math.log1p(7 * 86400)


def public_context(now, start, requested, actual, night_date, night_end, site, score, bulletins, forecasts):
    """Only messages issued <= now influence the returned values (including persistent state)."""
    now, start, night_end = utc(now), utc(start), utc(night_end)
    if start < now:
        raise ValueError('candidate starts before decision time')
    past = sorted((b for b in bulletins if utc(b['issued_at_utc']) <= now), key=lambda b: b['issued_at_utc'])
    published = sorted((f for f in forecasts if utc(f['issued_at_utc']) <= now), key=lambda f: f['issued_at_utc'])
    history = np.zeros((HISTORY_LENGTH, HISTORY_DIM), dtype=np.float32)
    for index, bulletin in enumerate(past[-HISTORY_LENGTH:], HISTORY_LENGTH - min(len(past), HISTORY_LENGTH)):
        issued = utc(bulletin['issued_at_utc'])
        absolute_index = len(past) - HISTORY_LENGTH + index
        previous = utc(past[absolute_index-1]['issued_at_utc']) if absolute_index > 0 else issued
        history[index] = np.r_[notice_matrix(bulletin['notices']).ravel(),
                               age_value((now-issued).total_seconds()), age_value((issued-previous).total_seconds()), 1]
    terrain = np.zeros(9)
    starts = {}
    previous_keys = set()
    for bulletin in past:
        issued = utc(bulletin['issued_at_utc'])
        matrix = notice_matrix(bulletin['notices'])
        terrain = np.maximum(terrain, matrix[-1])
        keys = set(zip(*np.nonzero(matrix)))
        starts = {key: starts[key] if key in previous_keys else issued for key in keys}
        previous_keys = keys
    run = np.zeros((8, 9))
    for key, issued in starts.items():
        run[key] = age_value((now-issued).total_seconds())
    current = notice_matrix(past[-1]['notices']) if past else np.zeros((8,9))
    future = np.zeros((8,9))
    forecast = published[-1] if published else None
    covers = bool(forecast and utc(forecast['coverage_start_utc']) <= start < utc(forecast['coverage_end_utc']))
    if covers:
        future = notice_matrix([n for n in forecast['notices'] if str(night_date) in n.get('nights', [])])
    lon = math.radians(site['longitude_deg'])
    season = 2 * math.pi * (start.timetuple().tm_yday - 1) / 365.2425
    extras = [age_value((now-utc(past[-1]['issued_at_utc'])).total_seconds()) if past else -1,
              age_value((now-utc(forecast['issued_at_utc'])).total_seconds()) if forecast else -1, covers,
              site['latitude_deg']/90, math.sin(lon), math.cos(lon), site['minimum_altitude_deg']/90,
              score['q0'], score['airmass_exponent'], score['program']['bands']['DARK'],
              score['program']['bands']['BRIGHT'], math.sin(season), math.cos(season),
              (start-now).total_seconds()/3600, requested/3600, actual/3600,
              (night_end-start).total_seconds()/43200]
    context = np.r_[current.ravel(), future.ravel(), terrain, run.ravel(), extras].astype(np.float32)
    assert len(context) == len(CONTEXT_NAMES)
    return context, history


def altaz(ra, dec, moment, site):
    ra, dec = np.asarray(ra, dtype=np.float64), np.radians(np.asarray(dec, dtype=np.float64))
    d = utc(moment).timestamp()/86400 - 10957.5
    lst = (280.46061837 + 360.98564736629*d + site['longitude_deg']) % 360
    hour = np.radians(lst-ra)
    lat = math.radians(site['latitude_deg'])
    alt = np.arcsin(np.clip(math.sin(lat)*np.sin(dec)+math.cos(lat)*np.cos(dec)*np.cos(hour), -1, 1))
    cosine = np.maximum(1e-12, np.cos(alt))
    az = np.arctan2(-np.sin(hour)*np.cos(dec)/cosine,
                   (np.sin(dec)-np.sin(alt)*math.sin(lat))/(cosine*max(1e-12, math.cos(lat))))
    return np.degrees(alt), np.degrees(az) % 360


def minimum_altitude(ra, dec, start, end, site):
    a, _ = altaz(ra, dec, start, site)
    b, _ = altaz(ra, dec, end, site)
    minimum = np.minimum(a, b)
    d = utc(start).timestamp()/86400 - 10957.5
    hour = ((280.46061837 + 360.98564736629*d + site['longitude_deg']) % 360 - np.asarray(ra)) % 360
    crosses = (180-hour) % 360 <= 360.98564736629*(utc(end)-utc(start)).total_seconds()/86400
    lat, dec = math.radians(site['latitude_deg']), np.radians(dec)
    lower = np.degrees(np.arcsin(np.clip(math.sin(lat)*np.sin(dec)-math.cos(lat)*np.cos(dec), -1, 1)))
    return np.where(crosses, np.minimum(minimum, lower), minimum)


def airmass(alt):
    alt = np.asarray(alt)
    z = 90 - np.maximum(alt,0)
    raw = 1/(np.cos(np.radians(z))+0.50572*(96.07995-z)**-1.6364)
    return np.where(alt>0,raw*(1+0.50572*96.07995**-1.6364),np.inf)


def geometry(ra, dec, moment, site, score):
    alt, az = altaz(ra, dec, moment, site)
    moon_ra, moon_dec, illumination, moon_alt = _lunar_geometry(utc(moment), site['latitude_deg'], site['longitude_deg'])
    r, d, mr, md = np.radians(ra), np.radians(dec), math.radians(moon_ra), math.radians(moon_dec)
    separation = np.degrees(np.arccos(np.clip(np.sin(d)*math.sin(md)+np.cos(d)*math.cos(md)*np.cos(r-mr), -1, 1)))
    lunar = score['lunar_model']
    penalty = lunar['maximum_penalty']*illumination*math.sin(math.radians(max(0, moon_alt)))**lunar['altitude_exponent']
    factor = 1-penalty*np.exp(-separation/lunar['angular_decay_scale_deg'])
    air = airmass(alt)
    geometric = factor/(score['q0']*air**score['airmass_exponent'])
    return np.column_stack([alt/90, np.sin(np.radians(az)), np.cos(np.radians(az)), air,
                            factor, np.full_like(alt, moon_alt/90), np.full_like(alt, illumination),
                            separation/180, geometric]), alt, az, geometric


def candidate_features(ra, dec, start, end, site, score):
    middle = utc(start)+(utc(end)-utc(start))/2
    parts = [geometry(ra, dec, t, site, score)[0] for t in (start, middle, end)]
    minimum = minimum_altitude(ra, dec, start, end, site)
    x = np.column_stack(parts+[minimum/90, np.mean([p[:,-1] for p in parts], axis=0)]).astype(np.float32)
    return x, minimum >= site['minimum_altitude_deg']


class PublicTimeline:
    """Precompute persistent public state once; snapshots never select future messages."""
    def __init__(self, bulletins, forecasts):
        self.bulletins = sorted(bulletins, key=lambda b: b['issued_at_utc'])
        self.forecasts = sorted(forecasts, key=lambda f: f['issued_at_utc'])
        self.times = np.array([utc(b['issued_at_utc']).timestamp() for b in self.bulletins])
        self.forecast_times = [utc(f['issued_at_utc']).timestamp() for f in self.forecasts]
        self.matrices = np.array([notice_matrix(b['notices']).ravel() for b in self.bulletins]).reshape(-1,72)
        self.terrain = np.maximum.accumulate(self.matrices[:,63:72], axis=0)
        starts = np.zeros(72)
        previous = np.zeros(72)
        self.starts = np.empty_like(self.matrices)
        for i, (timestamp, matrix) in enumerate(zip(self.times, self.matrices)):
            starts = np.where(matrix > 0, np.where(previous > 0, starts, timestamp), np.nan)
            self.starts[i] = starts
            previous = matrix

    def snapshot(self, now, start, requested, actual, night_date, night_end, site, score):
        now, start, night_end = utc(now), utc(start), utc(night_end)
        index = bisect.bisect_right(self.times, now.timestamp())-1
        first = max(0, index-HISTORY_LENGTH+1)
        count = max(0, index-first+1)
        history = np.zeros((HISTORY_LENGTH, HISTORY_DIM), dtype=np.float32)
        if count:
            times = self.times[first:index+1]
            gaps = times-np.r_[self.times[first-1] if first else times[0], times[:-1]]
            history[-count:] = np.column_stack([self.matrices[first:index+1],
                np.log1p(now.timestamp()-times)/math.log1p(7*86400),
                np.log1p(gaps)/math.log1p(7*86400), np.ones(count)])
        fi = bisect.bisect_right(self.forecast_times, now.timestamp())-1
        forecast = self.forecasts[fi] if fi >= 0 else None
        covers = bool(forecast and utc(forecast['coverage_start_utc']) <= start < utc(forecast['coverage_end_utc']))
        future = notice_matrix([n for n in forecast['notices'] if str(night_date) in n.get('nights', [])]) if covers else np.zeros((8,9))
        current = self.matrices[index] if index >= 0 else np.zeros(72)
        terrain = self.terrain[index] if index >= 0 else np.zeros(9)
        runs = np.log1p(np.maximum(0, now.timestamp()-self.starts[index]))/math.log1p(7*86400) if index >= 0 else np.zeros(72)
        runs = np.nan_to_num(runs)
        lon = math.radians(site['longitude_deg'])
        season = 2*math.pi*(start.timetuple().tm_yday-1)/365.2425
        extras = [age_value(now.timestamp()-self.times[index]) if index >= 0 else -1,
            age_value(now.timestamp()-self.forecast_times[fi]) if fi >= 0 else -1, covers,
            site['latitude_deg']/90, math.sin(lon), math.cos(lon), site['minimum_altitude_deg']/90,
            score['q0'], score['airmass_exponent'], score['program']['bands']['DARK'],
            score['program']['bands']['BRIGHT'], math.sin(season), math.cos(season),
            (start-now).total_seconds()/3600, requested/3600, actual/3600,
            (night_end-start).total_seconds()/43200]
        return np.r_[current, future.ravel(), terrain, runs, extras].astype(np.float32), history

from __future__ import annotations
import numpy as np
import math
from research.observing_conditions.features import utc,KINDS,DIRECTIONS,notice_matrix
from .core import Evidence,PROGRAMS,age,finite_stats,matrix_stats

VERSION = 'fault-feedback-only-v1'
STATS = ('observed_mean', 'observed_median', 'observed_std', 'response_mean', 'response_median', 'response_std', 'zero_fraction', 'miss_fraction', 'near_cap_fraction', 'flux', 'weight', 'duration', 'altitude', 'az_sin', 'az_cos', 'assigned', 'hits', 'usable_targets', 'program_dark', 'program_bright', 'program_backup')
HISTORY = STATS[:9]
FEATURE_NAMES = list(STATS)
for window in (3, 10, 30):
    FEATURE_NAMES += [f'last{window}:{name}:{stat}' for name in HISTORY for stat in ('mean', 'std')]
    FEATURE_NAMES += [f'last{window}:{name}' for name in ('count', 'response_trend', 'elapsed')]
for nights in (1, 3, 7):
    FEATURE_NAMES += [f'nights{nights}:{name}' for name in ('observed', 'response', 'count')]
FEATURE_NAMES += [f'direction:{d}:{s}' for d in DIRECTIONS[:8] for s in ('response', 'count')]
FEATURE_NAMES += [f'current:{k}:{d}' for k in KINDS for d in DIRECTIONS]
FEATURE_NAMES += ['evidence_exposures', 'evidence_targets', 'direction_count', 'evidence_age', 'quake_age', 'quake_nights', 'repair_age', 'false_report_age']

class FeedbackEvidence(Evidence):

    def __init__(self, score):
        super().__init__(score, None)

    def observation(self, record):
        required = {'observe_index', 'start', 'end', 'night', 'duration', 'program', 'altitude', 'azimuth', 'targets', 'result'}
        if set(record) != required:
            raise ValueError('feedback-only record must contain only public fields')
        start, end = (utc(record['start']), utc(record['end']))
        if record['observe_index'] <= self.latest_observe_index or end <= start:
            raise ValueError('duplicate, out-of-order or invalid exposure')
        if self.latest_observation_end and start < self.latest_observation_end:
            raise ValueError('overlapping exposure history')
        if record['program'] not in PROGRAMS or record['duration'] <= 0:
            raise ValueError('invalid public program or duration')
        if not np.isfinite([record['duration'], record['altitude'], record['azimuth']]).all():
            raise ValueError('nonfinite public exposure')
        targets = {}
        for target in record['targets']:
            if set(target) != {'target_id', 'feature_flux', 'science_weight', 'ra_deg', 'dec_deg'}:
                raise ValueError('target contains an unsupported field')
            if target['target_id'] in targets:
                raise ValueError('duplicate assigned target')
            targets[target['target_id']] = target
        result = record['result']
        if set(result) != {'assigned_count', 'hit_count', 'hits'} or result['assigned_count'] != len(targets):
            raise ValueError('invalid public feedback counts')
        hit_ids = [h['target_id'] for h in result['hits']]
        if len(set(hit_ids)) != len(hit_ids) or len(hit_ids) != result['hit_count'] or (not set(hit_ids) <= targets.keys()):
            raise ValueError('invalid hit target ids')
        if any((set(h) != {'target_id', 'score'} for h in result['hits'])):
            raise ValueError('unsupported feedback fields')
        pairs = [(h, targets[h['target_id']]) for h in result['hits'] if targets[h['target_id']]['science_weight'] > 0 and targets[h['target_id']]['feature_flux'] > 0]
        assigned, hits = (result['assigned_count'], result['hit_count'])
        mult = max(self.score['program']['multipliers'][record['program']], self.score['program']['mismatch_multiplier'])
        if pairs:
            flux = np.array([t['feature_flux'] for _, t in pairs])
            weight = np.array([t['science_weight'] for _, t in pairs])
            observed = np.array([h['score'] for h, _ in pairs]) / weight
            if not np.isfinite(np.r_[flux, weight, observed]).all() or np.any(observed < 0):
                raise ValueError('invalid public target or score')
            scale = flux * record['duration'] / (self.score['flux_zero_point'] * self.score['exposure_zero_point_seconds'])
            response = observed / (scale * mult)
            stats = [float(observed.mean()), float(np.median(observed)), float(observed.std()), float(response.mean()), float(np.median(response)), float(response.std()), float((observed == 0).mean()), 1 - hits / assigned, float((observed >= mult - 1e-06).mean()), float(flux.mean()), float(weight.mean())]
        else:
            stats = [math.nan] * 11
            stats[7] = 1 - hits / assigned if assigned else math.nan
        az = math.radians(record['azimuth'])
        stats += [record['duration'] / 3600, record['altitude'] / 90, math.sin(az), math.cos(az), assigned, hits, len(pairs)]
        stats += [int(record['program'] == p) for p in PROGRAMS]
        stats = dict(zip(STATS, stats))
        self.latest_observe_index, self.latest_observation_end = (record['observe_index'], end)
        if record['night'] not in self.nights:
            self.nights.append(record['night'])
        self.records.append(dict(record, stats=stats, numeric=np.fromiter(stats.values(), float)))

    def info(self, now):
        now = utc(now)
        if self.latest_observation_end and self.latest_observation_end > now:
            raise ValueError('future feedback entered evidence')
        rows = [r for r in self.records[-30:] if r['stats']['usable_targets'] > 0]
        count = len(rows)
        confirmed = self.confirmed_time == now
        return dict(fault_probability=0.0 if confirmed else None, evidence_exposure_count=count, evidence_target_count=sum((r['stats']['usable_targets'] for r in rows)), direction_count=len({int((r['azimuth'] + 22.5) // 45) % 8 for r in rows}), last_evidence_age_seconds=(now - utc(rows[-1]['end'])).total_seconds() if rows else None, status='confirmed_current_state' if confirmed else 'ready' if count >= 3 else 'limited_evidence' if count else 'no_observation_evidence')

    def vector(self, now):
        now = utc(now)
        info = self.info(now)
        vector = list(self.records[-1]['stats'].values()) if self.records else [math.nan] * len(STATS)
        recent = self.records[-30:]
        matrix = np.array([r['numeric'] for r in recent]) if recent else np.empty((0, len(STATS)))
        indices = [STATS.index(name) for name in HISTORY]
        for window in (3, 10, 30):
            rows = recent[-window:]
            vector.extend(matrix_stats(matrix[-window:, indices]))
            usable = [r for r in rows if r['stats']['usable_targets']]
            trend = usable[-1]['stats']['response_median'] - usable[0]['stats']['response_median'] if len(usable) > 1 else math.nan
            vector += [len(usable), trend, age(utc(rows[-1]['end']), utc(rows[0]['end'])) if rows else math.nan]
        for number in (1, 3, 7):
            keep = set(self.nights[-number:])
            rows = []
            for r in reversed(self.records):
                if r['night'] not in keep:
                    break
                rows.append(r)
            vector += [finite_stats([r['stats']['observed_mean'] for r in rows])[0], finite_stats([r['stats']['response_median'] for r in rows])[0], len(rows)]
        for direction in range(8):
            rows = [r for r in recent if int((r['azimuth'] + 22.5) // 45) % 8 == direction and r['stats']['usable_targets']]
            vector += [finite_stats([r['stats']['response_median'] for r in rows])[0], len(rows)]
        vector += notice_matrix(self.current_notices).ravel().tolist()
        vector += [info['evidence_exposure_count'], info['evidence_target_count'], info['direction_count'], age(now, utc(recent[-1]['end'])) if recent else math.nan, age(now, self.quake_time), len(self.nights) - self.quake_night if self.quake_night is not None else math.nan, age(now, self.repair_time), age(now, self.false_time)]
        assert len(vector) == len(FEATURE_NAMES)
        return np.asarray(vector, np.float32)

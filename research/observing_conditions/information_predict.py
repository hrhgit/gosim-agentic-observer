"""Public information -> complete point/region tables; no shortlist or recommendation."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import timedelta
from pathlib import Path
import time

import numpy as np
import torch

from .data import ROOT, save_json
from .features import VERSION as FEATURE_VERSION, utc, candidate_features
from .models import Normalizer
from .predict import Predictor
from .information_model import VERSION, InformationNet, numeric, calibration_apply, LEGACY, NAMES
from .information_regions import representatives, aggregate, mean_intervals, GRID_VERSION


class InformationPredictor(Predictor):
    def __init__(self, directory=ROOT / 'artifacts/observing_conditions-information/models/selected'):
        self.directory = Path(directory)
        self.metadata = json.loads((self.directory / 'metadata.json').read_text())
        if self.metadata['version'] != VERSION or self.metadata['feature_version'] != FEATURE_VERSION:
            raise ValueError('information/feature version mismatch')
        self.normalizer = Normalizer.load(self.directory / 'normalizer.npz')
        self.model = InformationNet(self.metadata['architecture'], self.metadata['context_dim'],
                                    self.metadata['history_dim'], self.metadata['candidate_dim'])
        self.model.load_state_dict(torch.load(self.directory / 'weights.pt', map_location='cpu', weights_only=True))
        self.model.eval()
        digest = hashlib.sha256()
        for filename in ('metadata.json', 'normalizer.npz', 'weights.pt'):
            digest.update((self.directory / filename).read_bytes())
        self.metadata['model_version'] = VERSION + '-' + digest.hexdigest()[:16]
        self._extra = {}

    @torch.inference_mode()
    def numeric(self, normalized):
        x = [torch.as_tensor(normalized[k]) for k in ('context', 'history', 'candidate')]
        self._last = calibration_apply(numeric(self.model(*x), self.metadata['quality_scale']),
                                       self.metadata['calibration'])
        return self._last[..., LEGACY]

    def record_group_predictions(self, indices):
        for index, p in zip(indices, self._last[0]):
            self._extra[index] = {name: float(p[i]) for i, name in enumerate(NAMES) if name.startswith('u0')
                                 or name in ('u50', 'u95')}

    def predict(self, request):
        self._extra = {}
        result = super().predict(request)
        for index, row in enumerate(result['predictions']):
            row.update(self._extra.get(index, {}))
        result.update(role='objective_information_for_agent_reasoning', automatic_actions=False,
                      validation_reliability_scope='Aggregate validation-half statistics only; see subgroup evidence and final_test_passed.',
                      interval_definition='Per-position exposure outcomes; empirical scene-held-out coverage, '
                      'not correctness probabilities for individual rows.',
                      reliability_evidence=self.metadata.get('reliability_evidence'),
                      joint_success_probability=None)
        return result

    def sky(self, request):
        allowed = {'initialize', 'now_utc', 'bulletins', 'forecasts', 'exposures'}
        if set(request) - allowed:
            raise ValueError('unsupported sky request fields')
        begin = time.perf_counter()
        initialize = request['initialize']; site = initialize['site']; score = initialize['scoring']
        all_candidates, group_info = [], []
        for gi, exposure in enumerate(request['exposures']):
            if set(exposure) - {'start_utc', 'duration_seconds'}:
                raise ValueError('unsupported exposure fields')
            regions, coordinates, alts, azs = representatives(site, exposure['start_utc'])
            group_info.append((regions, coordinates, alts, azs, exposure))
            for i, (ra, dec) in enumerate(coordinates):
                all_candidates.append(dict(candidate_id=f'exposure-{gi}:region-{i // 9}:position-{i % 9}',
                    ra_deg=float(ra), dec_deg=float(dec), **exposure))
        public_request = {k: v for k, v in request.items() if k != 'exposures'} | dict(candidates=all_candidates)
        points = self.predict(public_request)
        region_rows, facts = [], []
        for gi, (regions, coordinates, alts, azs, exposure) in enumerate(group_info):
            rows = points['predictions'][gi * 216:(gi + 1) * 216]
            p = np.zeros((1, 216, 12), np.float32)
            ok = np.array([[row['eligible'] for row in rows]])
            for i, row in enumerate(rows):
                if row['eligible']:
                    p[0, i] = [row[name] for name in NAMES]
            summary = aggregate(p, ok)
            intervals = mean_intervals(summary, self.metadata['region_calibration'])
            start = utc(exposure['start_utc'])
            night = next((n for n in initialize['survey']['nights']
                          if utc(n['observing_start_utc']) <= start < utc(n['observing_end_utc'])), None)
            end = min(start + timedelta(seconds=exposure['duration_seconds']), utc(night['observing_end_utc'])) if night else None
            x = candidate_features(coordinates[:, 0], coordinates[:, 1], start, end, site, score)[0] if end else None
            for i, row in enumerate(rows):
                fact = dict(candidate_id=row['candidate_id'], initial_altitude_deg=float(alts[i]),
                            initial_azimuth_deg=float(azs[i]), full_exposure_height_eligible=row['eligible'])
                if x is not None:
                    fact.update(minimum_altitude_deg=float(x[i, -2] * 90),
                                actual_duration_seconds=(end - start).total_seconds(),
                                moon_separation_start_deg=float(x[i, 7] * 180),
                                lunar_factor_start=float(x[i, 4]), geometric_quality_mean_estimate=float(x[i, -1]),
                                geometry_sampling='start/middle/end')
                    fact['geometry_samples'] = [dict(stage=stage, altitude_deg=float(x[i, j] * 90),
                        azimuth_deg=float(np.degrees(np.arctan2(x[i, j + 1], x[i, j + 2])) % 360),
                        airmass=float(x[i, j + 3]), lunar_factor=float(x[i, j + 4]),
                        moon_altitude_deg=float(x[i, j + 5] * 90), moon_illumination=float(x[i, j + 6]),
                        moon_separation_deg=float(x[i, j + 7] * 180), geometric_quality=float(x[i, j + 8]))
                        for stage, j in (('start', 0), ('middle', 9), ('end', 18))]
                facts.append(fact)
            for ri, region in enumerate(regions):
                count = int(summary['representatives_count'][0, ri])
                row = region | dict(exposure_index=gi, **exposure, representatives_total=9,
                    representatives_height_eligible=count,
                    geometry_eligible_area_fraction_estimate=float(summary['eligible_fraction'][0, ri]),
                    representative_candidate_ids=[r['candidate_id'] for r in rows[ri * 9:(ri + 1) * 9]],
                    mean_interval_calibration_scenes=self.metadata['region_calibration']['independent_scenes'])
                if count:
                    row.update(mean_quality=float(summary['quality_mean'][0, ri]),
                        usable_fraction=float(summary['usable_mean'][0, ri]),
                        severe_block_probability=float(summary['blocked_probability'][0, ri]),
                        band_probabilities=dict(zip(('DARK', 'BRIGHT', 'BACKUP'), summary['bands'][0, ri].tolist())),
                        spatial_quality_mean_quantiles=dict(zip(('p10', 'p50', 'p90'),
                            summary['spatial_quality_mean_quantiles'][0, ri].tolist())),
                        spatial_quality_mean_std=float(summary['spatial_quality_mean_std'][0, ri]),
                        quality_mean_prediction_interval=[float(v[0, ri]) for v in intervals['quality']],
                        usable_mean_prediction_interval=[float(v[0, ri]) for v in intervals['usable']])
                else:
                    row.update(mean_quality=None, usable_fraction=None, severe_block_probability=None,
                               quality_mean_prediction_interval=None, usable_mean_prediction_interval=None,
                               information_status='no height-eligible representatives; no weather conclusion')
                region_rows.append(row)
        now = utc(request['now_utc'])
        past = sorted((b for b in request.get('bulletins', []) if utc(b['issued_at_utc']) <= now),
                      key=lambda b: utc(b['issued_at_utc']))
        forecasts = sorted((f for f in request.get('forecasts', []) if utc(f['issued_at_utc']) <= now),
                           key=lambda f: utc(f['issued_at_utc']))
        notices = lambda items: [{k: n[k] for k in ('event_kind', 'direction')} for n in items]
        latest = past[-1] if past else None
        forecast = forecasts[-1] if forecasts else None
        published = dict(latest_bulletin_issued_at_utc=latest['issued_at_utc'] if latest else None,
                         current_notices=notices(latest['notices']) if latest else [],
                         history_bulletins_available=min(32, len(past)),
                         bulletin_missing=latest is None,
                         latest_forecast_issued_at_utc=forecasts[-1]['issued_at_utc'] if forecasts else None,
                         latest_bulletin_age_seconds=(now - utc(latest['issued_at_utc'])).total_seconds() if latest else None,
                         latest_forecast_age_seconds=(now - utc(forecast['issued_at_utc'])).total_seconds() if forecast else None,
                         forecast_missing=not forecasts,
                         persistent_terrain_directions=sorted({n['direction'] for b in past for n in b['notices']
                            if n['event_kind'] == 'terrain_obstruction'}))
        published['bulletin_history'] = [dict(issued_at_utc=b['issued_at_utc'], notices=notices(b['notices']))
                                        for b in past[-32:]]
        published['latest_forecast'] = (dict(issued_at_utc=forecast['issued_at_utc'],
            coverage_start_utc=forecast['coverage_start_utc'], coverage_end_utc=forecast['coverage_end_utc'],
            notices=[dict(event_kind=n['event_kind'], direction=n['direction'], nights=n.get('nights', []))
                     for n in forecast['notices']]) if forecast else None)
        for row in region_rows:
            start = utc(row['start_utc'])
            row['forecast_covers_start'] = bool(forecast and utc(forecast['coverage_start_utc']) <= start
                                               < utc(forecast['coverage_end_utc']))
        return dict(model_version=points['model_version'], architecture=points['architecture'],
                    quality_definition=points['quality_definition'], source=points['training_source'],
                    final_test_passed=points['final_test_passed'],
                    validation_reliability_scope=points['validation_reliability_scope'],
                    grid_version=GRID_VERSION, regions_per_exposure=24,
                    public_facts=published, point_fact_table=facts, point_prediction_table=points['predictions'],
                    region_prediction_table=region_rows, reliability_evidence=points['reliability_evidence'],
                    information_limits=dict(region_statistics='Equal-area sampled sky positions, conditional on full-exposure height eligibility.',
                        probability='Exposure risk at a randomly selected eligible position; not probability of whole-region closure.',
                        spatial_quantiles='Variation of predicted position means; not a future-outcome prediction interval.',
                        mean_intervals=self.metadata['region_calibration']['limitation'],
                        missing_information='No bulletin is not proof of unaffected sky; exact event strength/boundaries remain hidden.',
                        sampling='Nine representatives are an approximation; spatial variation can occur between them.'),
                    automatic_actions=False, joint_success_probability=None,
                    timing=dict(total_seconds=time.perf_counter() - begin, point_prediction=points['timing']))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', type=Path, default=ROOT / 'artifacts/observing_conditions-information/models/selected')
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--result', type=Path, required=True)
    args = parser.parse_args()
    torch.set_num_threads(1)
    predictor = InformationPredictor(args.model)
    request = json.loads(args.input.read_text())
    save_json(args.result, predictor.sky(request) if 'exposures' in request else predictor.predict(request))

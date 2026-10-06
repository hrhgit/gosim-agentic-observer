"""Fixed horizon regions and honest spatial summaries, separate from uncertainty."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from challenge.v4_fiber_map import altaz_to_radec
from .data import read_truth, labels, save_json
from .features import utc, candidate_features, PublicTimeline

DIRECTIONS = ('N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW')
GRID_VERSION = 'local-horizon-8x3-equal-solid-angle-v1'


def representatives(site, start, side=3):
    """Stratified midpoints in azimuth and sin(altitude); nine equal-area cells per region."""
    if side < 1:
        raise ValueError('side must be positive')
    edges = np.linspace(site['minimum_altitude_deg'], 90., 4)
    regions, coordinates, altitudes, azimuths = [], [], [], []
    for direction, name in enumerate(DIRECTIONS):
        for layer in range(3):
            low, high = edges[layer:layer + 2]
            region = dict(region_id=f'{name}-{layer}', direction=name, altitude_layer=layer,
                          altitude_min_deg=float(low), altitude_max_deg=float(high),
                          azimuth_start_deg=float((direction * 45 - 22.5) % 360),
                          azimuth_end_deg=float((direction * 45 + 22.5) % 360))
            regions.append(region)
            sine = np.linspace(np.sin(np.radians(low)), np.sin(np.radians(high)), side + 1)
            for ai in range(side):
                alt = float(np.degrees(np.arcsin((sine[ai] + sine[ai + 1]) / 2)))
                for zi in range(side):
                    az = float((direction * 45 - 22.5 + 45 * (zi + .5) / side) % 360)
                    coordinates.append(altaz_to_radec(alt, az, utc(start), site['latitude_deg'], site['longitude_deg']))
                    altitudes.append(alt); azimuths.append(az)
    return regions, np.asarray(coordinates), np.asarray(altitudes), np.asarray(azimuths)


def aggregate(predictions, eligible, answers=None, side=3):
    p = predictions.reshape(-1, 24, side * side, 12)
    ok = eligible.reshape(-1, 24, side * side)
    count = ok.sum(axis=-1)
    weights = ok / np.maximum(count[..., None], 1)
    result = dict(eligible_fraction=ok.mean(axis=-1), representatives_count=count,
                  quality_mean=(p[..., 0] * weights).sum(axis=-1),
                  usable_mean=(p[..., 4] * weights).sum(axis=-1),
                  blocked_probability=(p[..., 8] * weights).sum(axis=-1),
                  bands=(p[..., 9:12] * weights[..., None]).sum(axis=-2))
    spatial = np.full((*count.shape, 3), np.nan)
    spatial_std = np.full(count.shape, np.nan)
    for g, r in zip(*np.nonzero(count)):
        values = p[g, r, ok[g, r], 0]
        spatial[g, r] = np.quantile(values, [.1, .5, .9])
        spatial_std[g, r] = values.std()
    result['spatial_quality_mean_quantiles'] = spatial
    result['spatial_quality_mean_std'] = spatial_std
    if answers is not None:
        y = answers.reshape(-1, 24, side * side, 4)
        truth_std = np.full(count.shape, np.nan)
        for g, r in zip(*np.nonzero(count)):
            truth_std[g, r] = y[g, r, ok[g, r], 0].std()
        result.update(true_quality=(y[..., 0] * weights).sum(axis=-1),
                      true_usable=(y[..., 1] * weights).sum(axis=-1),
                      true_blocked_fraction=(y[..., 2] * weights).sum(axis=-1),
                      true_bands=(np.eye(3)[y[..., 3].astype(int)] * weights[..., None]).sum(axis=-2),
                      true_spatial_quality_std=truth_std)
    return result


def fit_mean_calibration(summary, scene):
    mask = summary['representatives_count'] > 0
    corrections = {}
    for name in ('quality', 'usable'):
        residual = np.abs(summary[name + '_mean'] - summary['true_' + name])[mask]
        corrections[name] = float(np.quantile(residual, .9, method='higher'))
    return dict(method='empirical region-mean absolute residual calibration', corrections=corrections,
                target=.9, independent_scenes=len(np.unique(scene)), region_exposure_samples=int(mask.sum()),
                limitation='Measured scene-held-out coverage only; no pointwise, joint, or shifted-distribution guarantee. '
                           'Region mean is conditional on sampled positions meeting the full-exposure height requirement.')


def mean_intervals(summary, settings):
    result = {}
    for name in ('quality', 'usable'):
        mean = summary[name + '_mean']
        correction = settings['corrections'][name]
        lo, hi = np.maximum(0, mean - correction), mean + correction
        if name == 'usable':
            hi = np.minimum(1, hi)
        result[name] = (lo, hi)
    return result


def measure_regions(summary, settings):
    mask = summary['representatives_count'] > 0
    ranges = mean_intervals(summary, settings)
    result = dict(region_exposure_samples=int(mask.sum()),
                  geometry_eligible_fraction=float(summary['eligible_fraction'].mean()))
    for name, (lo, hi) in ranges.items():
        truth = summary['true_' + name]
        score = hi - lo + 20 * np.maximum(lo - truth, 0) + 20 * np.maximum(truth - hi, 0)
        result.update({name + '_mean_mae': float(np.abs(summary[name + '_mean'] - truth)[mask].mean()),
                       name + '_mean_coverage': float(((lo <= truth) & (truth <= hi))[mask].mean()),
                       name + '_mean_interval_score': float(score[mask].mean()),
                       name + '_mean_width': float((hi - lo)[mask].mean())})
    result['blocked_fraction_mse'] = float(((summary['blocked_probability'] - summary['true_blocked_fraction']) ** 2)[mask].mean())
    result['band_fraction_mse'] = float(((summary['bands'] - summary['true_bands']) ** 2).sum(axis=-1)[mask].mean())
    result['spatial_quality_std_mae'] = float(np.abs(summary['spatial_quality_mean_std'] - summary['true_spatial_quality_std'])[mask].mean())
    return result


def build_region_data(source, entries, cache, groups=8, side=3):
    """Offline labels only. Runtime prediction never invokes this truth-reading function."""
    cache = Path(cache)
    specification = dict(entries=entries, groups=groups, side=side, grid_version=GRID_VERSION,
                         source=str(Path(source).resolve()))
    if cache.exists():
        if json.loads(cache.with_suffix('.json').read_text()) != specification:
            raise ValueError('region data specification changed')
        with np.load(cache) as a:
            return {k: a[k].copy() for k in a.files}
    fields = {k: [] for k in ('context', 'history', 'candidate', 'y', 'eligible')}
    scenes, cards, leads, durations = [], [], [], []
    for entry in entries:
        directory = Path(source) / 'scenarios' / entry['id']
        metadata = json.loads((directory / 'metadata.json').read_text())
        public = directory / 'truth_and_public'
        weather, events = read_truth(public)
        bulletins = [json.loads(s) for s in (public / 'v4_bulletins.jsonl').read_text().splitlines()]
        forecasts = [json.loads(s) for s in (public / 'v4_forecasts.jsonl').read_text().splitlines()]
        timeline = PublicTimeline(bulletins, forecasts)
        ix = np.linspace(0, len(metadata['groups']) - 1, groups, dtype=int)
        for index in ix:
            info = metadata['groups'][int(index)]
            start, end = utc(info['start']), utc(info['end'])
            _, coordinates, _, _ = representatives(metadata['site'], start, side)
            x, ok = candidate_features(coordinates[:, 0], coordinates[:, 1], start, end,
                                       metadata['site'], metadata['score'])
            context, history = timeline.snapshot(info['now'], start, info['requested'],
                (end - start).total_seconds(), info['night_date'], info['night_end'], metadata['site'], metadata['score'])
            y = labels(coordinates[:, 0], coordinates[:, 1], start, end, weather, events,
                       metadata['site'], metadata['score'])
            for key, value in dict(context=context, history=history, candidate=x, y=y, eligible=ok).items():
                fields[key].append(value)
            scenes.append(entry['id']); cards.append(entry['card']); leads.append(info['lead']); durations.append(info['requested'])
        print(f'regions {cache.stem}: {entry["id"]}', flush=True)
    result = {k: np.array(v) for k, v in fields.items()} | dict(scene=np.array(scenes), card=np.array(cards),
                lead=np.array(leads), duration=np.array(durations))
    cache.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(cache, **result)
    save_json(cache.with_suffix('.json'), specification)
    return result

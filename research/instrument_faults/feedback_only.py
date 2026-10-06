"""An independent public-feedback baseline, runnable before weather training ends.

The simulator creates feedback and answers. FeedbackEvidence receives only public
records. No real weather values or placeholder weather predictions enter features.
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import itertools
import json
import math
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from threadpoolctl import threadpool_limits

from challenge.v4_runner import _data_loss_plan
from challenge.v4_scorer import WeatherTruth
from research.observing_conditions.data import save_json
from research.observing_conditions.features import utc, KINDS, DIRECTIONS, notice_matrix
from .core import Evidence, PROGRAMS, age, finite_stats, matrix_stats
from .data import DEFAULT_OUTPUT
from .models import Classifier, Preprocessor, logit, metrics
from .simulation import exposure_scores, fault_label, load_assets

VERSION = 'fault-feedback-only-v1'
STATS = ('observed_mean', 'observed_median', 'observed_std', 'response_mean',
         'response_median', 'response_std', 'zero_fraction', 'miss_fraction',
         'near_cap_fraction', 'flux', 'weight', 'duration', 'altitude', 'az_sin',
         'az_cos', 'assigned', 'hits', 'usable_targets',
         'program_dark', 'program_bright', 'program_backup')
HISTORY = STATS[:9]
FEATURE_NAMES = list(STATS)
for window in (3, 10, 30):
    FEATURE_NAMES += [f'last{window}:{name}:{stat}' for name in HISTORY for stat in ('mean', 'std')]
    FEATURE_NAMES += [f'last{window}:{name}' for name in ('count', 'response_trend', 'elapsed')]
for nights in (1, 3, 7):
    FEATURE_NAMES += [f'nights{nights}:{name}' for name in ('observed', 'response', 'count')]
FEATURE_NAMES += [f'direction:{d}:{s}' for d in DIRECTIONS[:8] for s in ('response', 'count')]
FEATURE_NAMES += [f'current:{k}:{d}' for k in KINDS for d in DIRECTIONS]
FEATURE_NAMES += ['evidence_exposures', 'evidence_targets', 'direction_count', 'evidence_age',
                  'quake_age', 'quake_nights', 'repair_age', 'false_report_age']


def source_identity():
    paths = [Path(__file__), Path(__file__).with_name('core.py'),
             Path(__file__).with_name('simulation.py'), Path(__file__).with_name('models.py')]
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


class FeedbackEvidence(Evidence):
    def __init__(self, score):
        super().__init__(score, None)

    def observation(self, record):
        required = {'observe_index', 'start', 'end', 'night', 'duration', 'program',
                    'altitude', 'azimuth', 'targets', 'result'}
        if set(record) != required:
            raise ValueError('feedback-only record must contain only public fields')
        start, end = utc(record['start']), utc(record['end'])
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
        if len(set(hit_ids)) != len(hit_ids) or len(hit_ids) != result['hit_count'] or not set(hit_ids) <= targets.keys():
            raise ValueError('invalid hit target ids')
        if any(set(h) != {'target_id', 'score'} for h in result['hits']):
            raise ValueError('unsupported feedback fields')
        pairs = [(h, targets[h['target_id']]) for h in result['hits']
                 if targets[h['target_id']]['science_weight'] > 0 and targets[h['target_id']]['feature_flux'] > 0]
        assigned, hits = result['assigned_count'], result['hit_count']
        mult = max(self.score['program']['multipliers'][record['program']], self.score['program']['mismatch_multiplier'])
        if pairs:
            flux = np.array([t['feature_flux'] for _, t in pairs])
            weight = np.array([t['science_weight'] for _, t in pairs])
            observed = np.array([h['score'] for h, _ in pairs]) / weight
            if not np.isfinite(np.r_[flux, weight, observed]).all() or np.any(observed < 0):
                raise ValueError('invalid public target or score')
            scale = flux * record['duration'] / (self.score['flux_zero_point'] * self.score['exposure_zero_point_seconds'])
            # This is an apparent response using the public maximum program
            # multiplier. Saturation and program mismatch prevent identifying
            # weather or instrument efficiency from this value alone.
            response = observed / (scale * mult)
            stats = [float(observed.mean()), float(np.median(observed)), float(observed.std()),
                     float(response.mean()), float(np.median(response)), float(response.std()),
                     float((observed == 0).mean()), 1-hits/assigned,
                     float((observed >= mult-1e-6).mean()), float(flux.mean()), float(weight.mean())]
        else:
            stats = [math.nan]*11
            stats[7] = 1-hits/assigned if assigned else math.nan
        az = math.radians(record['azimuth'])
        stats += [record['duration']/3600, record['altitude']/90, math.sin(az), math.cos(az), assigned, hits, len(pairs)]
        stats += [int(record['program'] == p) for p in PROGRAMS]
        stats = dict(zip(STATS, stats))
        self.latest_observe_index, self.latest_observation_end = record['observe_index'], end
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
        return dict(fault_probability=0.0 if confirmed else None,
                    evidence_exposure_count=count, evidence_target_count=sum(r['stats']['usable_targets'] for r in rows),
                    direction_count=len({int((r['azimuth']+22.5)//45)%8 for r in rows}),
                    last_evidence_age_seconds=(now-utc(rows[-1]['end'])).total_seconds() if rows else None,
                    status='confirmed_current_state' if confirmed else 'ready' if count >= 3 else 'limited_evidence' if count else 'no_observation_evidence')

    def vector(self, now):
        now = utc(now); info = self.info(now)
        vector = list(self.records[-1]['stats'].values()) if self.records else [math.nan]*len(STATS)
        recent = self.records[-30:]
        matrix = np.array([r['numeric'] for r in recent]) if recent else np.empty((0, len(STATS)))
        indices = [STATS.index(name) for name in HISTORY]
        for window in (3, 10, 30):
            rows = recent[-window:]
            vector.extend(matrix_stats(matrix[-window:, indices]))
            usable = [r for r in rows if r['stats']['usable_targets']]
            trend = usable[-1]['stats']['response_median']-usable[0]['stats']['response_median'] if len(usable) > 1 else math.nan
            vector += [len(usable), trend, age(utc(rows[-1]['end']), utc(rows[0]['end'])) if rows else math.nan]
        for number in (1, 3, 7):
            keep = set(self.nights[-number:]); rows = []
            for r in reversed(self.records):
                if r['night'] not in keep:
                    break
                rows.append(r)
            vector += [finite_stats([r['stats']['observed_mean'] for r in rows])[0],
                       finite_stats([r['stats']['response_median'] for r in rows])[0], len(rows)]
        for direction in range(8):
            rows = [r for r in recent if int((r['azimuth']+22.5)//45)%8 == direction and r['stats']['usable_targets']]
            vector += [finite_stats([r['stats']['response_median'] for r in rows])[0], len(rows)]
        vector += notice_matrix(self.current_notices).ravel().tolist()
        vector += [info['evidence_exposure_count'], info['evidence_target_count'], info['direction_count'],
                   age(now, utc(recent[-1]['end'])) if recent else math.nan, age(now, self.quake_time),
                   len(self.nights)-self.quake_night if self.quake_night is not None else math.nan,
                   age(now, self.repair_time), age(now, self.false_time)]
        assert len(vector) == len(FEATURE_NAMES)
        return np.asarray(vector, np.float32)


def build_trace(directory, track):
    """Organizer-side feedback generation; returns separate public X and private y."""
    scenario, rows, physics = load_assets(directory, track)
    weather = WeatherTruth(scenario.slots, [e for e in scenario.events if e.event_type == 'instrument_fault'])
    state = FeedbackEvidence(scenario.score_config)
    messages = sorted(scenario.bulletins+scenario.forecasts, key=lambda m: utc(m['issued_at_utc']))
    times = [utc(m['issued_at_utc']) for m in messages]; cursor = 0
    loss = _data_loss_plan(scenario); loss_done = False
    report_times = [utc(rows[max(0, math.ceil(len(rows)*f)-1)]['start']) for f in (.25, .5, .75, 1)] if track and rows else []
    report_cursor = 0; xs = []; ys = []; valid = []; answers = []

    def receive(now, observed_count):
        nonlocal cursor, loss_done
        last = bisect.bisect_right(times, now)
        state.messages(messages[cursor:last], now); cursor = last
        if loss and not loss_done and now >= loss.trigger_utc:
            first = math.floor(loss.window_start_fraction*observed_count)
            end = math.floor(loss.window_end_fraction*observed_count)
            state.messages([dict(record_type='state_resync', issued_at_utc=now.isoformat(),
                                 invalidated_window=dict(action_index_start=first, action_index_end_exclusive=end))], now)
            loss_done = True

    for row in rows:
        start, end = utc(row['start']), utc(row['end'])
        receive(start, row['observe_index'])
        while report_cursor < len(report_times) and start >= report_times[report_cursor]:
            correct = fault_label(weather, start)
            delta = weather.report_fault(start, scenario.score_config)
            state.messages([dict(record_type='report_result', issued_at_utc=start.isoformat(),
                                 correct=correct, repaired=correct, score_delta=delta)], start)
            report_cursor += 1
        scores, _, _, hit_mask = exposure_scores(scenario, row, physics, weather)
        targets = [scenario.targets[i] for i in row['target_indices']]
        public = {k: row[k] for k in ('observe_index', 'start', 'end', 'night', 'duration', 'program', 'altitude', 'azimuth')}
        public['targets'] = [{k: t[k] for k in ('target_id', 'feature_flux', 'science_weight', 'ra_deg', 'dec_deg')} for t in targets]
        public['result'] = dict(assigned_count=len(targets), hit_count=int(hit_mask.sum()),
                                hits=[dict(target_id=t['target_id'], score=round(float(s), 6))
                                      for t, s, hit in zip(targets, scores, hit_mask) if hit])
        state.observation(public); receive(end, row['observe_index']+1)
        info = state.info(end)
        xs.append(state.vector(end)); ys.append(int(fault_label(weather, end)))
        valid.append(info['evidence_target_count'] > 0)
        answers.append(dict(observe_index=row['observe_index'], now=end.isoformat(), label=ys[-1], usable=bool(valid[-1])))
    return np.asarray(xs), np.asarray(ys, np.int8), np.asarray(valid, bool), answers


def features(data, output, splits):
    data, output = Path(data), Path(output)
    manifest = json.loads((data/'manifest.json').read_text())
    entries = [e for e in manifest['entries'] if e['split'] in splits]
    if not entries:
        raise ValueError('no matching scenes')
    if any(not (data/'scenarios'/e['id']/'prepared.json').exists() for e in entries):
        raise FileNotFoundError('selected complete-scene collection has not finished preparing')
    identity = dict(manifest_sha256=hashlib.sha256((data/'manifest.json').read_bytes()).hexdigest(),
                    code=source_identity(), feature_version=VERSION, feature_names=FEATURE_NAMES, splits=sorted(splits))
    marker = output/'dataset_identity.json'
    if marker.exists() and json.loads(marker.read_text()) != identity:
        raise ValueError('feedback baseline data or code changed; use a new output directory')
    save_json(marker, identity); save_json(output/'manifest.json', manifest | dict(entries=entries))
    for count, entry in enumerate(entries, 1):
        directory = output/'scenarios'/entry['id']; done = directory/'complete.json'
        if done.exists():
            if json.loads(done.read_text())['sha256'] != hashlib.sha256((directory/'features.npz').read_bytes()).hexdigest():
                raise ValueError('baseline feature checksum changed')
            continue
        xs = []; ys = []; valid = []
        for track in (0, 1):
            x, y, v, answers = build_trace(data/'scenarios'/entry['id'], track)
            xs.append(x); ys.append(y); valid.append(v)
            save_json(directory/f'answers-{track}.json', answers)
        np.savez_compressed(directory/'features.npz', x=np.concatenate(xs), y=np.concatenate(ys), valid=np.concatenate(valid))
        save_json(done, dict(sha256=hashlib.sha256((directory/'features.npz').read_bytes()).hexdigest(), rows=sum(map(len, ys))))
        print(f'feedback-only prepared {count}/{len(entries)} {entry["id"]}', flush=True)
    return entries


def load_data(output, ids):
    xs = []; ys = []; scenes = []
    for scene in ids:
        with np.load(Path(output)/'scenarios'/scene/'features.npz') as arrays:
            keep = arrays['valid']; xs.append(arrays['x'][keep]); ys.append(arrays['y'][keep])
            scenes.extend([scene]*int(keep.sum()))
    scene = np.asarray(scenes)
    unique, counts = np.unique(scene, return_counts=True)
    weight_by_scene = dict(zip(unique, 1/counts))
    weights = np.asarray([weight_by_scene[s] for s in scene]); weights /= weights.mean()
    return dict(x=np.concatenate(xs), y=np.concatenate(ys), scene=scene, weight=weights)


def fit(kind, train, parameters, excluded=()):
    x = train['x'].copy(); x[:, list(excluded)] = np.nan
    with threadpool_limits(limits=1):
        if kind == 'logistic':
            pre = Preprocessor().fit(x, train['weight'])
            base = LogisticRegression(max_iter=2000, **parameters).fit(pre.apply(x), train['y'], sample_weight=train['weight'])
        else:
            pre = None
            # Constant columns carry no information. The installed sklearn
            # binning routine fails when a training column is entirely NaN;
            # make those columns constant while retaining ordinary NaNs.
            x[:, ~np.isfinite(x).any(axis=0)] = 0
            base = HistGradientBoostingClassifier(max_iter=300, min_samples_leaf=50, early_stopping=False,
                                                 random_state=404, **parameters).fit(x, train['y'], sample_weight=train['weight'])
    return Classifier(kind, base, pre, features_mask=list(excluded) or None)


def save_model(directory, model, metadata):
    directory = Path(directory); directory.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, directory/'classifier.joblib')
    save_json(directory/'metadata.json', metadata | dict(feature_version=VERSION, feature_names=FEATURE_NAMES,
                                                       weather_version=None, code=source_identity()))


def audit_labels(data, output, entries):
    rng = np.random.default_rng(481402); checks = []
    # Replay the official event/report state independently of saved feature X.
    for entry in entries:
        for track in (0, 1):
            scenario, rows, _ = load_assets(Path(data)/'scenarios'/entry['id'], track)
            weather = WeatherTruth(scenario.slots, [e for e in scenario.events if e.event_type == 'instrument_fault'])
            reports = [utc(rows[max(0, math.ceil(len(rows)*f)-1)]['start']) for f in (.25, .5, .75, 1)] if track and rows else []
            answers = json.loads((Path(output)/'scenarios'/entry['id']/f'answers-{track}.json').read_text())
            chosen = set(rng.choice(len(rows), 13 if len(checks) < 104 else 12, replace=False).tolist())
            cursor = 0
            for index, row in enumerate(rows):
                while cursor < len(reports) and reports[cursor] <= utc(row['start']):
                    weather.report_fault(reports[cursor], scenario.score_config); cursor += 1
                if index in chosen:
                    event = weather.active_fault(utc(row['end']))
                    expected = int(event is not None and event.event_id not in weather.fault_repairs)
                    if answers[index]['label'] != expected:
                        raise AssertionError('feedback-only answer differs from official fault/repair state')
                    checks.append(dict(scene=entry['id'], track=track, observe_index=index, expected=expected, saved=answers[index]['label']))
    result = dict(samples=len(checks), errors=0, records=checks,
                  source='official current fault and repaired-event state; answers never enter input vectors')
    save_json(Path(output)/'label_audit.json', result)
    return result


def pilot(data, output):
    entries = features(data, output, {'development'})
    label_audit = audit_labels(data, output, entries)
    # The original eight developer scenes couple quake presence with fault
    # presence. Exclude quake information here; even then, these are checks,
    # not a sufficiently varied estimate of field performance.
    excluded = [i for i, name in enumerate(FEATURE_NAMES) if 'earthquake' in name or name in ('quake_age', 'quake_nights')]
    configs = {'logistic': dict(C=1), 'boosting': dict(learning_rate=.05, max_leaf_nodes=15, l2_regularization=1)}
    folds = []
    for card in 'abcd':
        train_ids = [e['id'] for e in entries if e['card'] != card]
        held_ids = [e['id'] for e in entries if e['card'] == card]
        train, held = load_data(output, train_ids), load_data(output, held_ids)
        prior = float(np.average(train['y'], weights=train['weight']))
        row = dict(held_card=card, train_scenes=train_ids, held_scenes=held_ids,
                   constant_training_prior=metrics(held, np.full(len(held['y']), prior)), models={})
        for kind, parameters in configs.items():
            directory = Path(output)/'folds'/card/kind
            if (directory/'metadata.json').exists():
                measured = json.loads((directory/'metadata.json').read_text())['held_metrics']
            else:
                model = fit(kind, train, parameters, excluded)
                with threadpool_limits(limits=1):
                    p = model.raw(held['x'])
                measured = metrics(held, p)
                save_model(directory, model, dict(kind=kind, parameters=parameters, calibrated=False,
                                                  scope='development check; not final evaluation', held_metrics=measured))
                np.savez_compressed(directory/'held_predictions.npz', probability=p, label=held['y'], scene=held['scene'])
            row['models'][kind] = measured
            print(f'feedback-only held card={card} {kind} log_loss={measured["log_loss"]:.6f}', flush=True)
        folds.append(row)
    report = dict(scope='8 developer scenes only; whole-card-held-out checks; not final results',
                  weather_predictions_used=False, calibrated=False, report_policy_evaluated=False,
                  development_fault_quake_confound=True, excluded_features=[FEATURE_NAMES[i] for i in excluded],
                  feature_count=len(FEATURE_NAMES), label_audit=dict(samples=label_audit['samples'], errors=label_audit['errors']), folds=folds)
    save_json(Path(output)/'pilot_report.json', report)
    lines = ['# 无观测质量预测的故障模型：开发检查', '',
             '仅使用 8 个自行生成的开发场景，两条完整过程归入同一场景。每次留下整张卡的两个场景，其他六个场景训练。', '',
             '未使用天气模型输出或真实天气答案。只接收实际反馈、公开目标和曝光信息、已收到的天气公告及申报反馈。', '',
             f'独立抽查 {label_audit["samples"]} 条答案，与官方当前故障及修复状态一致，错误 {label_audit["errors"]} 条。', '',
             '开发场景中地震与故障场景绑定，因此这轮排除地震字段。数量和变化仍不足，不能用本轮确定最终模型、申报门槛或达标情况。概率未经独立校准，未模拟模型申报收益。', '',
             '| 留下的卡 | 训练集故障比例常数：损失 | 逻辑回归：损失 | 提升树：损失 |',
             '|---|---:|---:|---:|']
    for fold in folds:
        lines.append(f'| {fold["held_card"].upper()} | {fold["constant_training_prior"]["log_loss"]:.4f} | {fold["models"]["logistic"]["log_loss"]:.4f} | {fold["models"]["boosting"]["log_loss"]:.4f} |')
    lines += ['', '损失越小越好；每个场景总权重相同。表格是开发检查，不能据此声称提升树或逻辑回归在完整实验中获胜。', '',
              '完整训练入口只读取原定训练、验证和校准集合，沿用整场景划分；最终和外部测试保留给选择冻结后的比较。']
    (Path(output)/'REPORT.md').write_text('\n'.join(lines)+'\n')
    return report


def train(data, output):
    entries = features(data, output, {'train', 'validation', 'calibration'})
    datasets = {split: load_data(output, [e['id'] for e in entries if e['split'] == split])
                for split in ('train', 'validation', 'calibration')}
    configs = {'logistic': [dict(C=c) for c in (.1, 1, 10)],
               'boosting': [dict(learning_rate=lr, max_leaf_nodes=leaves, l2_regularization=l2)
                            for lr, leaves, l2 in itertools.product((.05, .1), (15, 31), (1, 10))]}
    results = {}
    for kind, grid in configs.items():
        candidates = []
        for index, parameters in enumerate(grid):
            directory = Path(output)/'models'/f'{kind}-candidate-{index}'
            if (directory/'metadata.json').exists():
                model = joblib.load(directory/'classifier.joblib')
                metadata = json.loads((directory/'metadata.json').read_text())
            else:
                model = fit(kind, datasets['train'], parameters)
                with threadpool_limits(limits=1):
                    measured = metrics(datasets['validation'], model.raw(datasets['validation']['x']))
                metadata = dict(kind=kind, parameters=parameters, calibrated=False, validation=measured)
                save_model(directory, model, metadata)
            candidates.append((metadata['validation']['log_loss'], directory, metadata))
            print(f'feedback-only trained {kind} {index+1}/{len(grid)}', flush=True)
        _, directory, metadata = min(candidates, key=lambda candidate: candidate[0])
        model = joblib.load(directory/'classifier.joblib')
        cal = datasets['calibration']
        with threadpool_limits(limits=1):
            model.calibrator = LogisticRegression(C=1e6, max_iter=2000).fit(logit(model.raw(cal['x']))[:, None], cal['y'], sample_weight=cal['weight'])
            measured = metrics(datasets['validation'], model.predict_proba(datasets['validation']['x']))
        save_model(Path(output)/'models'/kind, model, metadata | dict(calibrated=True, validation_calibrated=measured))
        results[kind] = metadata | dict(validation_calibrated=measured)
    save_json(Path(output)/'model_comparison.json', dict(weather_predictions_used=False, final_test_used=False, models=results))
    return results


class FeedbackPredictor:
    def __init__(self, directory):
        directory = Path(directory)
        self.metadata = json.loads((directory/'metadata.json').read_text())
        if self.metadata['feature_version'] != VERSION or self.metadata['feature_names'] != FEATURE_NAMES:
            raise ValueError('feedback model input format changed')
        if self.metadata['code'] != source_identity():
            raise ValueError('feedback model code changed')
        self.model = joblib.load(directory/'classifier.joblib')

    def predict(self, state, now):
        info = state.info(now)
        if info['status'] not in ('no_observation_evidence', 'confirmed_current_state'):
            with threadpool_limits(limits=1):
                info['fault_probability'] = float(self.model.predict_proba(state.vector(now)[None])[0])
        return info | dict(versions=dict(fault_model=VERSION, weather_model=None, input=VERSION),
                           calibrated=self.metadata['calibrated'])


def main():
    parser = argparse.ArgumentParser(description='不等待天气预测的独立故障参照；不读取最终测试')
    parser.add_argument('command', choices=['pilot', 'train'])
    parser.add_argument('--data', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    pilot_run = args.command == 'pilot'
    data = args.data or Path(str(DEFAULT_OUTPUT)+'-development' if pilot_run else DEFAULT_OUTPUT)
    output = args.output or Path(str(DEFAULT_OUTPUT)+'-feedback-pilot' if pilot_run else str(DEFAULT_OUTPUT)+'-feedback')
    (pilot if pilot_run else train)(data, output)


if __name__ == '__main__':
    main()

"""A full, independent fault model and a controlled weather-output comparison.

Baseline X contains public feedback plus deterministic public announcement and
geometry inputs. The paired arm appends only saved weather-model-derived fields.
Final scene labels and row identifiers never enter classifier X.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import itertools
import json
import math
import shutil
import time
from pathlib import Path

import joblib
import numpy as np
from sklearn.linear_model import LogisticRegression
from threadpoolctl import threadpool_limits

from research.observing_conditions.data import save_json
from research.observing_conditions.features import CONTEXT_NAMES, CANDIDATE_NAMES, utc
from challenge.v4_scorer import WeatherTruth
from . import feedback_only as feedback
from .ablations import removed_features
from .core import FEATURE_NAMES as WEATHER_FEATURE_NAMES
from .models import logit, metrics
from .evaluation import probability_intervals, measure
from .data import DEFAULT_OUTPUT

VERSION = 'fault-feedback-benchmark-v1'
CONTEXT_INDICES = [i for i, name in enumerate(CONTEXT_NAMES) if not name.startswith('current:')]
BASE_NAMES = (feedback.FEATURE_NAMES + ['public_before:'+CONTEXT_NAMES[i] for i in CONTEXT_INDICES]
              + ['geometry_mean:'+name for name in CANDIDATE_NAMES]
              + ['geometry_quality_std', 'geometry_minimum_altitude'])
WEATHER_INDICES = removed_features('no_weather')
AUGMENTED_NAMES = BASE_NAMES + ['weather_prediction:'+WEATHER_FEATURE_NAMES[i] for i in WEATHER_INDICES]
GRIDS = {'logistic': [dict(C=c) for c in (.1, 1, 10)],
         'boosting': [dict(learning_rate=lr, max_leaf_nodes=leaves, l2_regularization=l2)
                      for lr, leaves, l2 in itertools.product((.05, .1), (15, 31), (1, 10))]}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def sources():
    return feedback.source_identity() | {'benchmark.py': digest(__file__),
        'public_features.py': digest(Path(__file__).parents[1]/'observing_conditions/features.py')}


def protocol(data, output):
    data, output = Path(data), Path(output)
    manifest = json.loads((data/'manifest.json').read_text())
    value = dict(version=VERSION, manifest_sha256=digest(data/'manifest.json'), code=sources(),
                 base_feature_names=BASE_NAMES, added_weather_feature_names=AUGMENTED_NAMES[len(BASE_NAMES):],
                 candidate_grids=GRIDS, probability_decision_threshold=.5,
                 selection='validation scene-weighted log loss, same candidate budgets in both arms',
                 calibration='independent calibration scenes, sigmoid',
                 test='same complete test/external scenes, same fixed reports, feedback and labels',
                 uncertainty='2000 complete-scene resamples; both traces stay together',
                 source=manifest['source'], report_actions_evaluated=False)
    path = output/'comparison_protocol.json'
    if path.exists() and json.loads(path.read_text()) != value:
        raise ValueError('frozen comparison protocol or source changed; use a new output directory')
    save_json(path, value)
    return manifest


def progress(output, phase, detail):
    save_json(Path(output)/'_state.json', dict(phase=phase, detail=detail))
    print(f'feedback benchmark {phase}: {detail}', flush=True)
    write_report(output)


def public_exposure_inputs(context, candidates):
    """Raw public forecasts and analytic geometry, with no trained predictor."""
    if len(candidates):
        geometry = np.r_[candidates.mean(0), candidates[:, -1].std(), candidates[:, -2].min()]
    else:
        geometry = np.full(len(CANDIDATE_NAMES)+2, np.nan)
    return np.r_[np.asarray(context)[CONTEXT_INDICES], geometry].astype(np.float32)


def prepare_scene(data, output, entry):
    directory = Path(output)/'inputs'/entry['id']; marker = directory/'complete.json'
    if marker.exists():
        if json.loads(marker.read_text())['sha256'] != digest(directory/'features.npz'):
            raise ValueError('baseline numerical input checksum changed')
        return
    original = Path(data)/'scenarios'/entry['id']; xs = []; ys = []; valid = []; tracks = []; indices = []
    for track in (0, 1):
        x, y, v, answers = feedback.build_trace(original, track)
        with np.load(original/f'weather-input-{track}.npz') as raw:
            extra = np.asarray([public_exposure_inputs(c, g[:count])
                                for c, g, count in zip(raw['context'], raw['candidate'], raw['counts'])])
        xs.append(np.column_stack([x, extra])); ys.append(y); valid.append(v)
        tracks.extend([track]*len(y)); indices.extend(range(len(y)))
        save_json(directory/f'answers-{track}.json', answers)
    np.savez_compressed(directory/'features.npz', x=np.concatenate(xs).astype(np.float32), y=np.concatenate(ys),
                        valid=np.concatenate(valid), track=np.asarray(tracks, np.int8), observe_index=np.asarray(indices, np.int32))
    save_json(marker, dict(rows=sum(map(len, ys)), sha256=digest(directory/'features.npz')))


def prepare_inputs(data, output, entries, await_data=False):
    remaining = list(entries)
    while remaining:
        pending = []; made = 0
        for entry in remaining:
            if not (Path(data)/'scenarios'/entry['id']/'prepared.json').exists():
                pending.append(entry); continue
            prepare_scene(data, output, entry); made += 1
            if made % 16 == 0:
                print(f'feedback benchmark input {entry["id"]}; pending scenes {len(pending)+len(remaining)-made-len(pending)}', flush=True)
        remaining = pending
        if remaining:
            if not await_data:
                raise FileNotFoundError(f'{len(remaining)} complete scenes have not finished generating')
            progress(output, 'waiting_for_data', f'{len(entries)-len(remaining)}/{len(entries)} 输入已完成；等待剩余完整场景')
            time.sleep(30)


def load_data(output, entries, weather_data=None):
    xs = []; ys = []; scenes = []; cards = []; groups = []; row_ids = []; valid = []
    for entry in entries:
        directory = Path(output)/'inputs'/entry['id']
        if json.loads((directory/'complete.json').read_text())['sha256'] != digest(directory/'features.npz'):
            raise ValueError('baseline data checksum mismatch')
        with np.load(directory/'features.npz') as arrays:
            x = arrays['x']; y = arrays['y']; v = arrays['valid']
            ids = [f'{entry["id"]}/{t}/{i}' for t, i in zip(arrays['track'], arrays['observe_index'])]
        if weather_data is not None:
            source = Path(weather_data)/'scenarios'/entry['id']
            marker = json.loads((source/'features_complete.json').read_text())
            if marker['features_sha256'] != digest(source/'features.npz') or marker['feature_names'] != WEATHER_FEATURE_NAMES:
                raise ValueError('weather-derived input checksum/format mismatch')
            if marker['weather_version'] != json.loads((Path(weather_data)/'weather_dependency.json').read_text())['identity']:
                raise ValueError('weather-derived input version mismatch')
            with np.load(source/'features.npz') as weather:
                if not np.array_equal(weather['y'], y):
                    raise ValueError('comparison arms do not have identical current fault answers')
                extra = weather['x'][:, WEATHER_INDICES]
            x = np.column_stack([x, extra])
        xs.append(x[v]); ys.append(y[v]); scenes.extend([entry['id']]*int(v.sum()))
        cards.extend([entry['card']]*int(v.sum())); groups.extend([entry.get('group', 'main')]*int(v.sum()))
        row_ids.extend(np.asarray(ids)[v]); valid.extend(v)
    scene = np.asarray(scenes); unique, counts = np.unique(scene, return_counts=True)
    by_scene = dict(zip(unique, 1/counts)); weights = np.asarray([by_scene[s] for s in scene]); weights /= weights.mean()
    return dict(x=np.concatenate(xs), y=np.concatenate(ys), scene=scene, weight=weights, card=np.asarray(cards),
                group=np.asarray(groups), row_id=np.asarray(row_ids), coverage=float(np.mean(valid)), total_rows=len(valid))


def classifications(data, p):
    result = metrics(data, p); y = data['y']; w = data['weight']; positive = p >= .5
    tp = float(w[(y == 1) & positive].sum()); fn = float(w[(y == 1) & ~positive].sum())
    fp = float(w[(y == 0) & positive].sum()); tn = float(w[(y == 0) & ~positive].sum())
    ratio = lambda a, b: a/b if b else None
    sensitivity, specificity = ratio(tp, tp+fn), ratio(tn, tn+fp)
    return result | dict(accuracy=ratio(tp+tn, tp+tn+fp+fn), precision=ratio(tp, tp+fp), recall=sensitivity,
                         specificity=specificity, false_positive_rate=ratio(fp, fp+tn),
                         balanced_accuracy=(sensitivity+specificity)/2 if sensitivity is not None and specificity is not None else None,
                         f1=ratio(2*tp, 2*tp+fp+fn), decision_threshold=.5,
                         coverage=data.get('coverage'), total_rows=data.get('total_rows'))


def save_model(path, model, metadata, augmented=False):
    path = Path(path); path.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, path/'classifier.joblib')
    save_json(path/'metadata.json', metadata | dict(feature_version=VERSION,
        feature_names=AUGMENTED_NAMES if augmented else BASE_NAMES, augmented=augmented, code=sources(),
        artifact_sha256=digest(path/'classifier.joblib')))


def model_hashes(output):
    return {kind: {name: digest(Path(output)/'models'/kind/name) for name in ('classifier.joblib', 'metadata.json')}
            for kind in GRIDS}


def train_models(data_output, entries, model_output, weather_data=None):
    model_output = Path(model_output)
    datasets = {split: load_data(data_output, [e for e in entries if e['split'] == split], weather_data)
                for split in ('train', 'validation', 'calibration')}
    identity = digest(Path(data_output)/'comparison_protocol.json')
    if weather_data is not None:
        identity += digest(Path(weather_data)/'weather_dependency.json')
    weather_version = json.loads((Path(weather_data)/'weather_dependency.json').read_text())['identity'] if weather_data is not None else None
    results = {}
    for kind, grid in GRIDS.items():
        candidates = []
        for index, parameters in enumerate(grid):
            path = model_output/'candidates'/f'{kind}-{index}'
            if (path/'metadata.json').exists():
                metadata = json.loads((path/'metadata.json').read_text())
                if metadata['identity'] != identity or metadata['code'] != sources():
                    raise ValueError('resumed fault candidate input/code changed')
                model = joblib.load(path/'classifier.joblib')
            else:
                start = time.perf_counter(); model = feedback.fit(kind, datasets['train'], parameters)
                with threadpool_limits(limits=1):
                    measured = classifications(datasets['validation'], model.raw(datasets['validation']['x']))
                metadata = dict(kind=kind, parameters=parameters, identity=identity, validation=measured,
                                calibrated=False, training_seconds=time.perf_counter()-start, weather_version=weather_version)
                save_model(path, model, metadata, weather_data is not None)
            candidates.append((metadata['validation']['log_loss'], path, metadata))
            print(f'feedback benchmark trained {kind} {index+1}/{len(grid)} validation={metadata["validation"]["log_loss"]:.6f}', flush=True)
        _, chosen, metadata = min(candidates, key=lambda c: c[0])
        model = joblib.load(chosen/'classifier.joblib'); cal = datasets['calibration']
        with threadpool_limits(limits=1):
            model.calibrator = LogisticRegression(C=1e6, max_iter=2000).fit(logit(model.raw(cal['x']))[:, None], cal['y'], sample_weight=cal['weight'])
            measured = classifications(datasets['validation'], model.predict_proba(datasets['validation']['x']))
        save_model(model_output/'models'/kind, model, metadata | dict(calibrated=True, validation_calibrated=measured), weather_data is not None)
        results[kind] = metadata | dict(validation_calibrated=measured, latency=measure(model, datasets['validation']['x']))
    kind = min(results, key=lambda k: results[k]['validation_calibrated']['log_loss'])
    selection = dict(frozen=True, model=kind, identity=identity, model_hashes=model_hashes(model_output),
                     threshold=.5, candidates=results, basis='validation scene-weighted calibrated classification loss only')
    shutil.copytree(model_output/'models'/kind, model_output/'models/selected', dirs_exist_ok=True)
    save_json(model_output/'selection.json', selection)
    return selection


def verify_selection(output):
    selection = json.loads((Path(output)/'selection.json').read_text())
    if not selection['frozen'] or selection['model_hashes'] != model_hashes(output):
        raise ValueError('final tests require unchanged frozen classifiers and calibration')
    return selection


def audit_training_labels(data, output, entries, samples=200):
    path = Path(output)/'label_audit.json'
    if path.exists():
        return json.loads(path.read_text())
    rng = np.random.default_rng(481410); grouped = {}
    for _ in range(samples):
        entry = entries[int(rng.integers(len(entries)))]; track = int(rng.integers(2))
        grouped.setdefault((entry['id'], track), 0); grouped[(entry['id'], track)] += 1
    checks = []
    for (scene_id, track), count in grouped.items():
        scenario, rows, _ = feedback.load_assets(Path(data)/'scenarios'/scene_id, track)
        answers = json.loads((Path(output)/'inputs'/scene_id/f'answers-{track}.json').read_text())
        report_times = [utc(rows[max(0, math.ceil(len(rows)*fraction)-1)]['start']) for fraction in (.25, .5, .75, 1)] if track and rows else []
        for position in rng.integers(len(rows), size=count):
            row = rows[position]; truth = WeatherTruth(scenario.slots, [e for e in scenario.events if e.event_type == 'instrument_fault'])
            for at in report_times:
                if at <= utc(row['start']):
                    truth.report_fault(at, scenario.score_config)
            event = truth.active_fault(utc(row['end']))
            expected = int(event is not None and event.event_id not in truth.fault_repairs)
            if answers[position]['label'] != expected:
                raise AssertionError('independent model answer differs from official current fault/repair state')
            checks.append(dict(scene=scene_id, track=track, observe_index=int(position), expected=expected, saved=answers[position]['label']))
    result = dict(samples=len(checks), errors=0, records=checks, source='official fault and report/repair state; train/validation/calibration only')
    save_json(path, result)
    return result


def organizer_slices(data_directory, entries, data):
    """Scorer-side slice metadata only; never concatenate it into model X."""
    records = {}
    for entry in entries:
        for track in (0, 1):
            scenario, rows, physics = feedback.load_assets(Path(data_directory)/'scenarios'/entry['id'], track)
            truth = WeatherTruth(scenario.slots, [e for e in scenario.events if e.event_type == 'instrument_fault'])
            for row in rows:
                first, last = physics['pointers'][row['observe_index']:row['observe_index']+2]
                quality = float(physics['environment'][first:last].mean()) if last > first else np.nan
                event = truth.active_fault(utc(row['end']))
                records[f'{entry["id"]}/{track}/{row["observe_index"]}'] = (quality, event.instrument_efficiency_multiplier if event else np.nan)
    values = np.asarray([records[key] for key in data['row_id']])
    q, strength = values.T
    strength = np.where(data['y'] == 1, strength, np.nan)
    return dict(weather=np.where(q < .4, 'true Q_env<0.4', np.where(q < .65, 'true Q_env 0.4-0.65', 'true Q_env>=0.65')),
                fault_strength=np.where(np.isnan(strength), 'no unrepaired fault', np.where(strength < .55, 'multiplier<0.55', np.where(strength <= .7, 'multiplier 0.55-0.70', 'weak fault>0.70'))))


def evaluate(data_output, entries, model_output, weather_data=None, data_directory=None):
    selection = verify_selection(model_output); report = {}
    for split in ('test', 'external'):
        data = load_data(data_output, [e for e in entries if e['split'] == split], weather_data); report[split] = {}
        organizer = organizer_slices(data_directory, [e for e in entries if e['split'] == split], data) if data_directory is not None else {}
        for kind in GRIDS:
            model = joblib.load(Path(model_output)/'models'/kind/'classifier.joblib')
            with threadpool_limits(limits=1):
                p = model.predict_proba(data['x'])
            directory = Path(model_output)/'predictions'/split; directory.mkdir(parents=True, exist_ok=True)
            np.savez_compressed(directory/f'{kind}.npz', p=p, y=data['y'], scene=data['scene'], row_id=data['row_id'])
            slices = {}
            quake_columns = [i for i, name in enumerate(BASE_NAMES) if name.startswith('current:earthquake:')]
            dimensions = dict(card=data['card'], earthquake=np.where((data['x'][:, quake_columns]>0).any(1), 'earthquake announced', 'no current earthquake notice'),
                              duration=np.where(data['x'][:, BASE_NAMES.index('duration')] <= 300/3600, '<=300s', '>300s'),
                              density=np.where(data['x'][:, BASE_NAMES.index('nights3:count')] <= 6, '<=6', '>6'),
                              external_group=data['group'])
            if data_directory is not None:
                dimensions.update(organizer)
            for name, values in dimensions.items():
                slices[name] = {}
                for value in np.unique(values):
                    mask = values == value
                    subset = {k: v[mask] for k, v in data.items() if isinstance(v, np.ndarray)}
                    slices[name][str(value)] = classifications(subset, p[mask])
            report[split][kind] = dict(classification=classifications(data, p), intervals=probability_intervals(data, p), slices=slices)
        report[split]['always_healthy'] = classifications(data, np.zeros(len(data['y'])))
    save_json(Path(model_output)/'report.json', dict(completed=True, weather_predictions_used=weather_data is not None,
        selected_model=selection['model'], tests=report, scope='self-generated fixed observation feedback; classification only; no model report actions'))
    return report


def write_report(output):
    output = Path(output); state = json.loads((output/'_state.json').read_text())
    lines = ['# 独立故障模型：不使用观测质量预测', '', f'当前阶段：{state["phase"]}。{state["detail"]}', '',
             '输入只包含实际观测反馈、公开目标及曝光信息、已收到的天气公告和预报、公开公式计算的位置几何信息、申报和撤销反馈。真实天气与故障状态只用于生成反馈及答案。', '',
             '使用原定整场景划分：训练 320、选择 64、校准 64、最终测试 64；另有 64 个外部测试场景。每个场景两条过程归在一起，场景总训练权重相同。', '',
             '候选为逻辑回归和提升树；参数只在验证集合选择，概率使用独立校准集合校准。固定 0.5 判断门槛。未模拟本模型执行申报的收益。', '']
    if (output/'report.json').exists():
        report = json.loads((output/'report.json').read_text()); kind = report['selected_model']
        lines += [f'验证集选定并冻结的模型：{kind}。', '',
                  '| 集合 | 模型 | 准确率 | 故障发现比例 | 误判比例 | 概率平方误差 | 分类损失 |',
                  '|---|---|---:|---:|---:|---:|---:|']
        number = lambda v: '无数据' if v is None else f'{v:.4f}'
        for split, models in report['tests'].items():
            for name, values in models.items():
                m = values['classification'] if 'classification' in values else values
                lines.append('| '+' | '.join([split, name, number(m['accuracy']), number(m['recall']), number(m['false_positive_rate']), number(m['brier']), number(m['log_loss'])])+' |')
        lines += ['', '准确率、发现比例和误判比例针对反馈后的当前未修复故障判断，不是申报事件指标。概率区间按完整场景重抽样 2000 次。无观测证据返回空值，可判断覆盖率及分卡、地震、曝光时长、观测密度和外部场景分项见 report.json。']
    else:
        lines += ['完整训练和独立测试尚未完成；8 个开发场景的试跑不作为这里的成绩。']
    lines += ['', '未来的受控比较保留这些输入、记录和答案，只追加曝光前保存的观测质量预测及其差异。两个版本使用相同候选、参数预算和划分，独立校准；按完整场景计算配对误差区间。比较方案已存入 comparison_protocol.json，测试集合不用于调整。']
    (output/'REPORT.md').write_text('\n'.join(lines)+'\n')


def run(data, output, await_data=False):
    data, output = Path(data), Path(output); output.mkdir(parents=True, exist_ok=True)
    with (output/'.run.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        manifest = protocol(data, output); entries = manifest['entries']
        if (output/'report.json').exists():
            verify_selection(output); return json.loads((output/'report.json').read_text())
        progress(output, 'preparing_training_inputs', '独立整理训练、验证、校准输入；不等待天气模型')
        write_report(output)
        prepare_inputs(data, output, [e for e in entries if e['split'] in ('train', 'validation', 'calibration')], await_data)
        audit_training_labels(data, output, [e for e in entries if e['split'] in ('train', 'validation', 'calibration')])
        progress(output, 'training', '逻辑回归与提升树，完整验证场景选参，独立概率校准')
        if not (output/'selection.json').exists():
            train_models(output, entries, output)
        verify_selection(output)
        progress(output, 'final_test', '模型及 0.5 判断门槛已冻结，开始最终与外部测试')
        prepare_inputs(data, output, [e for e in entries if e['split'] in ('test', 'external')], await_data)
        evaluate(output, entries, output, data_directory=data)
        progress(output, 'complete', '独立无预测模型、校准与最终测试完成；未来比较保留同一场景')
        write_report(output)
        return json.loads((output/'report.json').read_text())


def paired_difference(baseline_path, augmented_path, repetitions=2000):
    with np.load(baseline_path) as a, np.load(augmented_path) as b:
        if not np.array_equal(a['row_id'], b['row_id']) or not np.array_equal(a['y'], b['y']) or not np.array_equal(a['scene'], b['scene']):
            raise ValueError('paired comparison must use exactly the same feedback rows and answers')
        y, scene, old, new = a['y'], a['scene'], a['p'], b['p']
        old_log = -y*np.log(np.clip(old, 1e-7, 1-1e-7))-(1-y)*np.log(np.clip(1-old, 1e-7, 1))
        new_log = -y*np.log(np.clip(new, 1e-7, 1-1e-7))-(1-y)*np.log(np.clip(1-new, 1e-7, 1))
        rows = [np.array([((old[scene == s]-y[scene == s])**2-(new[scene == s]-y[scene == s])**2).mean(),
                         (old_log[scene == s]-new_log[scene == s]).mean(),
                         (((new[scene == s] >= .5) == y[scene == s]).astype(float)-((old[scene == s] >= .5) == y[scene == s])).mean()])
                for s in np.unique(scene)]
    values = np.asarray(rows); rng = np.random.default_rng(511204)
    draws = values[rng.integers(len(values), size=(repetitions, len(values)))].mean(1)
    return {name: dict(improvement=float(values[:, i].mean()), ci95=np.quantile(draws[:, i], [.025, .975]).tolist(),
                       evidence_of_improvement=bool(np.quantile(draws[:, i], .025) > 0), scenes=len(values), repetitions=repetitions)
            for i, name in enumerate(('brier', 'log_loss', 'accuracy'))}


def compare(data, baseline_output, output):
    data, baseline_output, output = Path(data), Path(baseline_output), Path(output)
    manifest = protocol(data, baseline_output); baseline_selection = verify_selection(baseline_output)
    dependency = json.loads((data/'weather_dependency.json').read_text())
    identity = dict(protocol_sha256=digest(baseline_output/'comparison_protocol.json'), weather_identity=dependency['identity'], code=sources())
    if (output/'identity.json').exists() and json.loads((output/'identity.json').read_text()) != identity:
        raise ValueError('controlled weather comparison changed; use a new output directory')
    save_json(output/'identity.json', identity)
    if not (output/'selection.json').exists():
        train_models(baseline_output, manifest['entries'], output, data)
    augmented_selection = verify_selection(output)
    evaluate(baseline_output, manifest['entries'], output, data, data_directory=data)
    comparison = {}
    for split in ('test', 'external'):
        comparison[split] = {kind: paired_difference(baseline_output/'predictions'/split/f'{kind}.npz', output/'predictions'/split/f'{kind}.npz') for kind in GRIDS}
        comparison[split]['validation_selected_models'] = paired_difference(
            baseline_output/'predictions'/split/f'{baseline_selection["model"]}.npz',
            output/'predictions'/split/f'{augmented_selection["model"]}.npz')
    save_json(output/'paired_comparison.json', dict(weather_identity=dependency['identity'], results=comparison,
        definition='positive improvement means lower Brier/log loss or higher accuracy with weather outputs; identical feedback rows'))
    lines = ['# 观测质量预测是否提高故障判断准确性', '',
             '两个版本共享公开反馈、公告/预报、位置几何输入、场景、记录与答案；新增版本仅追加曝光前保存的观测质量预测及其差异。模型候选及选参预算相同；各自仅在验证集合选择，独立校准。', '',
             '| 集合 | 对比 | 概率平方误差改善 | 95% 区间 | 准确率改善 | 95% 区间 |', '|---|---|---:|---|---:|---|']
    for split, models in comparison.items():
        for kind, result in models.items():
            lines.append(f'| {split} | {kind} | {result["brier"]["improvement"]:.5f} | {result["brier"]["ci95"]} | {result["accuracy"]["improvement"]:.5f} | {result["accuracy"]["ci95"]} |')
    lines += ['', '改善值为正才表示加入观测质量预测更好；区间跨过 0 时不能确定有提升。结果仅限自行生成的固定观测反馈，未证明平台运行效果。']
    (output/'REPORT.md').write_text('\n'.join(lines)+'\n')
    return comparison


class Predictor:
    """Independent model, consuming a feedback state and saved raw public inputs."""
    def __init__(self, directory):
        directory = Path(directory); self.metadata = json.loads((directory/'metadata.json').read_text())
        expected = AUGMENTED_NAMES if self.metadata['augmented'] else BASE_NAMES
        if self.metadata['code'] != sources() or self.metadata['feature_names'] != expected:
            raise ValueError('benchmark model input/code changed')
        if self.metadata['artifact_sha256'] != digest(directory/'classifier.joblib'):
            raise ValueError('benchmark model weight checksum changed')
        self.model = joblib.load(directory/'classifier.joblib')

    def predict(self, state, now, saved_public_inputs=None, weather_evidence=None):
        info = state.info(now)
        if info['status'] not in ('no_observation_evidence', 'confirmed_current_state'):
            required = {'issued_at_utc', 'observe_index', 'context', 'candidates'}
            if saved_public_inputs is None or set(saved_public_inputs) != required:
                raise ValueError('saved public input fields mismatch')
            latest = state.records[-1]
            if saved_public_inputs['observe_index'] != latest['observe_index'] or utc(saved_public_inputs['issued_at_utc']) > utc(latest['start']):
                raise ValueError('public input must match the latest exposure and precede its start')
            context = np.asarray(saved_public_inputs['context']); candidates = np.asarray(saved_public_inputs['candidates'])
            if context.shape != (len(CONTEXT_NAMES),) or candidates.ndim != 2 or candidates.shape[1] != len(CANDIDATE_NAMES):
                raise ValueError('saved public input array shape mismatch')
            x = np.r_[state.vector(now), public_exposure_inputs(context, candidates)]
            if self.metadata['augmented']:
                if weather_evidence is None:
                    return info | dict(status='weather_unavailable', fault_probability=None)
                if weather_evidence.weather_version != self.metadata['weather_version']:
                    raise ValueError('comparison weather model version mismatch')
                x = np.r_[x, weather_evidence.vector(now)[WEATHER_INDICES]]
            with threadpool_limits(limits=1):
                info['fault_probability'] = float(self.model.predict_proba(x[None].astype(np.float32))[0])
        return info | dict(versions=dict(fault_model=self.metadata['artifact_sha256'], weather_model=self.metadata['weather_version'], input=VERSION))


def main():
    parser = argparse.ArgumentParser(description='完整无观测质量预测模型与受控增益比较')
    parser.add_argument('command', choices=['run', 'compare'])
    parser.add_argument('--data', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--baseline', type=Path, default=Path(str(DEFAULT_OUTPUT)+'-feedback'))
    parser.add_argument('--await-data', action='store_true')
    args = parser.parse_args()
    if args.command == 'run':
        run(args.data, args.output or args.baseline, args.await_data)
    else:
        compare(args.data, args.baseline, args.output or Path(str(args.data)+'-weather-comparison'))


if __name__ == '__main__':
    main()

"""Public band evidence and cost-sensitive report decisions on fixed schedules.

The score multiplier can reveal a match or mismatch without revealing hidden
weather. Ambiguous targets remain ambiguous. Labels/cost weights are organizer
side; all decision vectors use public feedback and confirmed report results.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import json
import math
import multiprocessing
import os
from pathlib import Path
import time

import joblib
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from threadpoolctl import threadpool_limits

from . import benchmark, baseline_comparison as comparison
from .feedback_only import FeedbackEvidence, FEATURE_NAMES
from .models import Classifier
from .simulation import fault_label, load_assets
from research.observing_conditions.data import save_json
from research.observing_conditions.features import utc

VERSION = 'fault-report-model-v1'
BAND_NAMES = ['match_fraction', 'mismatch_fraction', 'ambiguous_fraction', 'zero_fraction',
              'matched_transmission_min', 'matched_transmission_max', 'mismatched_transmission_median',
              'weather_lower_bound', 'weather_upper_bound', 'band_bounds_conflict',
              'estimated_weather', 'estimated_efficiency', 'previous_efficiency', 'efficiency_ratio',
              'suspect', 'low_count', 'low_seconds', 'closed_suspect', 'band_probe_needed',
              'free_reports_left', 'confirmed_false_reports', 'cooldown_remaining',
              'remaining_science_estimate', 'correct_reward', 'paid_false_cost']
PAIRS = [(0.5, .9), (.7, .95), (.9, .99)]


def band_evidence(rec, hits, sky, score, now, remaining_gain):
    """Infer only identifiable multiplier evidence; never read physical band truth."""
    match, mismatch, ambiguous, zero = [], [], 0, 0
    mu = float(score['program']['multipliers'][rec['program']])
    mis = float(score['program']['mismatch_multiplier'])
    for item in rec['items']:
        if item[0] not in hits or item[9] <= 0:
            continue
        observed = hits[item[0]]/item[9]
        tol = 1.2e-6/item[9]+1e-9
        if observed <= tol:
            zero += 1
        elif observed >= mu-tol or observed > mis+tol:
            match.append(item[4])
        elif abs(observed-mis) <= tol:
            mismatch.append(item[4])
        else:
            ambiguous += 1
    n = len(match)+len(mismatch)+ambiguous+zero
    lower = upper = math.nan
    if match and not mismatch:
        if rec['program'] == 'DARK':
            lower = sky.b_dark/min(match)
        elif rec['program'] == 'BRIGHT':
            lower, upper = sky.b_bright/min(match), sky.b_dark/max(match)
        else:
            upper = sky.b_bright/max(match)
    elif mismatch and not match:
        a = float(np.median(mismatch))
        if rec['program'] == 'DARK':
            upper = sky.b_dark/a
        elif rec['program'] == 'BACKUP':
            lower = sky.b_bright/a
    conflict = bool(np.isfinite([lower, upper]).all() and lower >= upper)
    if conflict:
        lower = upper = math.nan
    return np.asarray([len(match)/n if n else math.nan, len(mismatch)/n if n else math.nan,
        ambiguous/n if n else math.nan, zero/n if n else math.nan,
        min(match) if match else math.nan, max(match) if match else math.nan,
        float(np.median(mismatch)) if mismatch else math.nan, lower, upper, float(conflict),
        sky.W, sky.e, sky.e_ref, sky.e/max(sky.e_ref, 1e-8), float(sky.suspect),
        sky.low_count, sky.low_seconds, float(sky.closed_suspect), float(sky.probe is not None),
        sky.free_left, sky.reports_false, max(0., sky.cooldown_until-now.timestamp()),
        remaining_gain, score['reporting']['correct_reward'], abs(score['reporting']['false_penalty'])], np.float32)


class ReportJudge(comparison.ModelJudge):
    def __init__(self, scenario, rows, shapes, model, thresholds, extra, guarded=True, augmented=True):
        super().__init__(scenario, model, thresholds, extra)
        self.band = comparison.BaselineJudge(scenario, rows, shapes)
        self.score = scenario.score_config
        self.guarded, self.augmented = guarded, augmented
        self.last_vector = None
        self.remaining_gain = 0.0

    def messages(self, batch, now):
        super().messages(batch, now)
        self.band.messages(batch, now)
        if any(m['record_type'] == 'state_resync' for m in batch):
            # Old geometry/sky inferences may depend on withdrawn exposures.
            # Keep known report allowance/cooldown, discard accumulated drop evidence.
            self.band.sky.suspect = False
            self.band.sky.low_count = 0
            self.band.sky.low_seconds = 0.0
            self.band.sky.tonight.clear()

    def plan(self, position):
        self.band.plan(position)

    def feedback(self, position, public, hits, batch, end, free):
        # Update the public-only band estimator even on exposures with no decision.
        self.band.feedback(position, public, hits, batch, end, free)
        self.state.observation(public)
        comparison.ModelJudge.messages(self, batch, end)
        if any(m['record_type'] == 'state_resync' for m in batch):
            self.band.sky.suspect = False
            self.band.sky.low_count = 0
            self.band.sky.low_seconds = 0.0
            self.band.sky.tonight.clear()
        self.remaining_gain = self.band._gain(position+1)
        base = super().vector(position, end)
        extra = band_evidence(self.band.rec, hits, self.band.sky, self.score, end, self.remaining_gain)
        self.last_vector = np.r_[base, extra].astype(np.float32) if self.augmented else base
        records = self.state.records
        if records and records[-1]['observe_index'] == public['observe_index'] and records[-1]['stats']['usable_targets'] > 0:
            self.new.add(public['observe_index'])
        if self.model is None or len(self.new) < 3 or self.state.info(end)['status'] != 'ready':
            return False
        self.new.clear()
        score = float(self.model.raw(self.last_vector[None])[0])
        self.decisions.append((score, free))
        if self.guarded and not self.band.sky.want_report(end.timestamp(), self.remaining_gain):
            return False
        return score >= self.thresholds[0 if free else 1]

    def reported(self, result, now):
        super().reported(result, now)
        self.band.reported(result, now)
        # Both correct and incorrect feedback confirm a healthy instrument now.
        # The old low-response records must not trigger another report.
        self.state.records = [r for r in self.state.records if utc(r['end']) > now]
        self.new.clear()


def trace_inputs(data, inputs, entry, track):
    assets = load_assets(Path(data)/'scenarios'/entry['id'], track)
    scenario, rows, _ = assets
    with np.load(Path(inputs)/'inputs'/entry['id']/'features.npz') as stored:
        mask = stored['track'] == track
        extra = stored['x'][mask, len(FEATURE_NAMES):]
        expected = stored['y'][mask]
    if len(extra) != len(rows):
        raise ValueError('public inputs do not align with exposure schedule')
    shapes = comparison.geometry(scenario, rows)
    return assets, extra, shapes, expected


def prepare_job(args):
    data, inputs, output, entry = args
    if entry['split'] != 'train':
        raise ValueError('report training only accepts training scenes')
    path = Path(output)/'inputs'/entry['id']/'data.npz'
    if path.exists():
        return entry['id']
    xs, ys, costs = [], [], []
    with threadpool_limits(limits=1):
        for track in (0, 1):
            assets, extra, shapes, expected = trace_inputs(data, inputs, entry, track)
            scenario, rows, _ = assets
            engine = comparison.Engine(*assets)
            judge = ReportJudge(scenario, rows, shapes, None, None, extra)
            scheduled = [utc(rows[max(0, math.ceil(len(rows)*f)-1)]['start']) for f in (.25, .5, .75, 1)] if track and rows else []
            cursor = 0
            for position, row in enumerate(rows):
                start, end = utc(row['start']), utc(row['end'])
                judge.messages(engine.deliver(start, row['observe_index']), start)
                while cursor < len(scheduled) and start >= scheduled[cursor]:
                    judge.reported(engine.report(start), start); cursor += 1
                judge.plan(position)
                public, hits, usable = engine.expose(row)
                judge.feedback(position, public, hits, engine.deliver(end, row['observe_index']+1), end, engine.free)
                label = int(fault_label(engine.weather, end))
                if label != expected[position]:
                    raise AssertionError('new training labels differ from official saved states')
                if usable and judge.state.info(end)['status'] == 'ready':
                    xs.append(judge.last_vector); ys.append(label)
                    # Organizer-side cost: expected repair benefit for correct calls,
                    # official paid false cost for wrong calls. Never an input.
                    fault = engine.weather.active_fault(end)
                    benefit = scenario.score_config['reporting']['correct_reward']
                    if label:
                        benefit += (1-fault.instrument_efficiency_multiplier)*judge.remaining_gain
                    costs.append(max(1., benefit if label else abs(scenario.score_config['reporting']['false_penalty'])))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name('data.tmp.npz')
    np.savez_compressed(temporary, x=np.asarray(xs, np.float32), y=np.asarray(ys, np.int8), costs=np.asarray(costs))
    temporary.replace(path)
    return entry['id']


def train(output, entries):
    x, y, weights = [], [], []
    for entry in entries:
        with np.load(Path(output)/'inputs'/entry['id']/'data.npz') as arrays:
            x.append(arrays['x']); y.append(arrays['y'])
            c = arrays['costs']; weights.append(c/c.sum())
    x = np.concatenate(x); y = np.concatenate(y); w = np.concatenate(weights); w /= w.mean()
    started = time.perf_counter()
    with threadpool_limits(limits=2):
        x[:, ~np.isfinite(x).any(axis=0)] = 0
        base = HistGradientBoostingClassifier(max_iter=300, learning_rate=.1, max_leaf_nodes=15,
            min_samples_leaf=50, l2_regularization=1, early_stopping=False, random_state=404).fit(x, y, sample_weight=w)
    model = Classifier('boosting', base)
    joblib.dump(model, Path(output)/'model.joblib')
    save_json(Path(output)/'model.json', dict(training_scenes=len(entries), training_rows=len(y),
        training_seconds=time.perf_counter()-started, features=benchmark.BASE_NAMES+['band:'+n for n in BAND_NAMES],
        cost_weighted=True, score_is_calibrated_fault_probability=False,
        target='report recommendation: cost-weighted fault/repair opportunity, chosen by closed-loop score'))


def simulation_job(args):
    data, inputs, output, old_models, entry, mode, selected = args
    cache = Path(output)/'simulations'/mode/f'{entry["id"]}.json'
    if cache.exists():
        return json.loads(cache.read_text())
    model = joblib.load(Path(output)/'model.joblib')
    old = joblib.load(Path(old_models)/'boosting/classifier.joblib')
    records = []
    with threadpool_limits(limits=1):
        for track in (0, 1):
            assets, extra, shapes, _ = trace_inputs(data, inputs, entry, track)
            scenario, rows, _ = assets
            none = comparison.closed_loop(assets, 'none')
            opportunities = none['event_opportunities']
            policies = [('none', None, None)]
            if mode == 'validation':
                policies += [('baseline', None, comparison.BaselineJudge(scenario, rows, shapes)),
                             ('old_model', (.9, .99), comparison.ModelJudge(scenario, old, (.9, .99), extra)),
                             ('old_model_guarded', (.9, .99), ReportJudge(scenario, rows, shapes, old, (.9, .99), extra, True, False))]
                pair = tuple(selected['thresholds'])
                policies += [('new_model', pair, ReportJudge(scenario, rows, shapes, model, pair, extra)),
                             ('new_model_unguarded', pair, ReportJudge(scenario, rows, shapes, model, pair, extra, False))]
            else:
                policies += [('new_model', pair, ReportJudge(scenario, rows, shapes, model, pair, extra)) for pair in PAIRS]
            for name, pair, judge in policies:
                result = none if name == 'none' else comparison.closed_loop(assets, name, judge, opportunities)
                result = dict(result, scene=entry['id'], track=track, model=name, thresholds=list(pair) if pair else [])
                records.append(result)
    save_json(cache, records)
    return records


def map_jobs(function, jobs, workers, label):
    os.environ['OMP_NUM_THREADS'] = '1'
    results = []
    with ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context('spawn')) as pool:
        for i, result in enumerate(pool.map(function, jobs), 1):
            results.append(result)
            if i % 8 == 0 or i == len(jobs):
                print(f'{label} {i}/{len(jobs)}', flush=True)
    return results


def select_action_policy(runs):
    candidates = []
    for pair in PAIRS:
        rows = [r for r in runs if r['model'] == 'new_model' and r['thresholds'] == list(pair)]
        if not rows:
            raise ValueError('missing report policy simulation')
        summary = comparison.summarize(rows)
        candidates.append(dict(thresholds=list(pair), simulation=summary,
            meets_false_report_targets=summary['false_per_scene'] <= .5 and summary['paid_false_per_scene'] <= .1))
    feasible = [c for c in candidates if c['meets_false_report_targets']]
    chosen = max(feasible, key=lambda c: c['simulation']['mean_score']) if feasible else min(
        candidates, key=lambda c: (c['simulation']['paid_false_per_scene'], c['simulation']['false_per_scene'], -c['simulation']['mean_score']))
    return chosen, candidates


def run(data, inputs, small, output, workers):
    data, inputs, small, output = map(Path, (data, inputs, small, output))
    output.mkdir(parents=True, exist_ok=True)
    entries = json.loads((data/'manifest.json').read_text())['entries']
    original = json.loads((small/'protocol.json').read_text())
    ids = set(original['training_ids']['40'])
    training = [e for e in entries if e['id'] in ids]
    tuning = comparison.tuning_scenes(entries, ids)
    validation = [e for e in entries if e['split'] == 'validation']
    protocol = dict(version=VERSION, source=benchmark.sources() | {
        'report_model.py': benchmark.digest(__file__), 'baseline_comparison.py': benchmark.digest(comparison.__file__),
        'baseline_conditions.py': benchmark.digest(comparison.AGENT/'core/conditions.py'),
        'baseline_astro.py': benchmark.digest(comparison.AGENT/'core/astro.py')},
        training_ids=[e['id'] for e in training], tuning_ids=[e['id'] for e in tuning],
        validation_ids=[e['id'] for e in validation], feature_names=benchmark.BASE_NAMES+['band:'+n for n in BAND_NAMES],
        threshold_pairs=PAIRS, workers=workers, final_test_accessed=False,
        manifest_sha256=benchmark.digest(data/'manifest.json'),
        old_model_sha256=benchmark.digest(small/'scenes-040/boosting/classifier.joblib'))
    marker = output/'protocol.json'
    if marker.exists() and json.loads(marker.read_text()) != json.loads(json.dumps(protocol)):
        raise ValueError('report model configuration changed; use a new directory')
    save_json(marker, protocol)
    def state(phase):
        save_json(output/'_state.json', dict(phase=phase, final_test_accessed=False))
    state('preparing_band_inputs')
    map_jobs(prepare_job, [(str(data), str(inputs), str(output), e) for e in training], workers, 'band inputs')
    if not (output/'model.joblib').exists():
        state('training_cost_sensitive_model'); train(output, training)
    old_models = small/'scenes-040'
    if not (output/'selection.json').exists():
        state('selecting_report_policy')
        runs = sum(map_jobs(simulation_job, [(str(data), str(inputs), str(output), str(old_models), e, 'tuning', None)
                    for e in tuning], workers, 'report selection'), [])
        selected, candidates = select_action_policy(runs)
        save_json(output/'selection.json', dict(selected=selected, candidates=candidates, frozen=True,
            model_sha256=benchmark.digest(output/'model.joblib'),
            basis='closed-loop score including repair gains and official false penalties, false-report targets first'))
    selection = json.loads((output/'selection.json').read_text())
    if selection['model_sha256'] != benchmark.digest(output/'model.joblib'):
        raise ValueError('frozen report model changed')
    state('validating_actual_reports')
    runs = sum(map_jobs(simulation_job, [(str(data), str(inputs), str(output), str(old_models), e, 'validation', selection['selected'])
                for e in validation], workers, 'report validation'), [])
    grouped = {name: [r for r in runs if r['model'] == name] for name in sorted({r['model'] for r in runs})}
    result = dict(selection=selection, results={name: comparison.summarize(rows) for name, rows in grouped.items()},
        gains_vs_none={name: comparison.paired(rows, grouped['none']) for name, rows in grouped.items()},
        gains_vs_baseline={name: comparison.paired(rows, grouped['baseline']) for name, rows in grouped.items()},
        worst_false_reports={name: max(r['false'] for r in rows) for name, rows in grouped.items()},
        worst_paid_false_reports={name: max(r['paid_false'] for r in rows) for name, rows in grouped.items()},
        final_test_accessed=False, validation_is_reused=True)
    save_json(output/'results.json', result)
    lines = ['# 档位证据与申报收益优化', '',
        '40 个原训练场景；64 个未参与模型训练的训练场景选申报门槛；原 64 个验证场景作改进检查。',
        '这些验证场景已用于前期分析，所以不是最终独立成绩。最终和外部测试仍未读取。',
        '新增档位证据从公开得分和几何量推断，含明确匹配、明确不匹配、无法确定和天气上下界，不读真实档位。',
        '训练使用官方误报扣分和估计修复收益的代价权重；输出是申报倾向，不是校准故障概率。',
        '误报后重新整理反馈历史；使用基线的天气/仪器分离、持续低效率和误报后冷却判断，避免旧证据反复申报。',
        '申报门槛按实际修复后得分选择；每次修复重新计算后续反馈。', '',
        '|方法|相对不申报多得分|误报/场景|扣分误报/场景|单条过程最多误报|申报正确率|故障事件发现率|',
        '|---|---:|---:|---:|---:|---:|---:|']
    for name, r in result['results'].items():
        precision = f'{r["report_precision"]:.1%}' if r['report_precision'] is not None else '无申报'
        recall = f'{r["eligible_event_recall"]:.1%}' if r['eligible_event_recall'] is not None else '无事件'
        lines.append(f'|{name}|{result["gains_vs_none"][name]["mean"]:+.1f}|{r["false_per_scene"]:.2f}|'
                     f'{r["paid_false_per_scene"]:.2f}|{result["worst_false_reports"][name]}|{precision}|{recall}|')
    passed = selection['selected']['meets_false_report_targets']
    lines += ['', f'选门槛阶段是否满足原误报目标：{passed}。实际未满足时如实保留，不宣称达标。',
              'old_model_guarded：旧模型只换申报处理；new_model_unguarded：新档位模型不加申报保护，帮助区分输入与策略作用。',
              '完整场景配对差异及 95% 区间、最差场景、实际报告记录见 results.json 和 simulations/。',
              '仍为自行生成天气与固定观测安排；没有接入正式参赛智能体。']
    (output/'REPORT.md').write_text('\n'.join(lines)+'\n')
    state('complete')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', default='artifacts/instrument_faults')
    parser.add_argument('--inputs', default='artifacts/instrument_faults-feedback')
    parser.add_argument('--small', default='artifacts/instrument_faults-smallscale-4cpu')
    parser.add_argument('--output', default='artifacts/instrument_faults-report-model')
    parser.add_argument('--workers', type=int, default=4)
    args = parser.parse_args()
    run(args.data, args.inputs, args.small, args.output, args.workers)

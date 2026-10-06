"""Independent calibration, frozen new tests, regional checks, and a numeric report."""
from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, as_completed
import json
from pathlib import Path
import shutil
import time

import numpy as np
import torch

from .data import ROOT, save_json, generate_one, audit_dataset, source_hashes, read_truth, pieces
from .features import utc
from .models import Normalizer
from .information_model import (InformationBaseline, VERSION, NAMES, infer, calibration_fit,
                                calibration_apply, measure, reliability_gate, selection_score, probability_tables)
from .information_train import load_public_partitions, load_model, digest, EVALUATION_GROUPS, subset
from .information_regions import (build_region_data, aggregate, fit_mean_calibration, measure_regions,
                                  representatives, GRID_VERSION)


def predict_raw(name, data, output, normalizer):
    if name in ('constant', 'lookup'):
        return InformationBaseline.from_dict(json.loads((output / f'{name}.json').read_text())).predict(data)
    model = load_model(output / 'models' / name)
    return infer(model, normalizer.apply(data), float(normalizer.values['quality_scale']))


def sliced_metrics(data, p):
    result = {}
    for category, values in (('card', data['card']), ('lead_seconds', np.rint(data['context'][:, -4] * 3600).astype(int)),
                             ('requested_seconds', np.rint(data['context'][:, -3] * 3600).astype(int))):
        result[category] = {str(v): measure(data['y'][values == v], p[values == v]) |
                            dict(independent_scenes=len(np.unique(data['scene'][values == v]))) for v in np.unique(values)}
    from .features import KINDS
    result['events'] = {}
    for i, event in enumerate(KINDS):
        mask = data['context'][:, i * 9:(i + 1) * 9].any(axis=1)
        if mask.any():
            result['events'][event] = measure(data['y'][mask], p[mask]) | dict(independent_scenes=len(np.unique(data['scene'][mask])))
    result['altitude'] = {}
    altitude = data['candidate'][..., 0] * 90
    for lo, hi in ((30, 50), (50, 70), (70, 91)):
        mask = (altitude >= lo) & (altitude < hi)
        if mask.any():
            result['altitude'][f'{lo}-{min(hi, 90)}'] = measure(data['y'][mask], p[mask]) | dict(
                independent_scenes=len(np.unique(data['scene'][mask.any(axis=1)])))
    return result


def subgroup_review(slices):
    """Report failures and evidence scarcity instead of promoting pooled calibration to all cases."""
    rows = []
    for category, values in slices.items():
        for name, metrics in values.items():
            checks = dict(probability_calibration=max(metrics['blocked_ece'], metrics['band_ece']) <= .05,
                          quality_range=.85 <= metrics['quality_coverage'] <= .95,
                          usable_range=.85 <= metrics['usable_coverage'] <= .95)
            enough = metrics['independent_scenes'] >= 8
            rows.append(dict(category=category, value=name, independent_scenes=metrics['independent_scenes'],
                samples=metrics['samples'], checks=checks,
                status=('insufficient_scene_evidence' if not enough else 'meets_targets' if all(checks.values()) else 'fails_targets'),
                blocked_ece=metrics['blocked_ece'], band_ece=metrics['band_ece'],
                quality_coverage=metrics['quality_coverage'], usable_coverage=metrics['usable_coverage']))
    return dict(rows=rows, minimum_scene_evidence=8,
                sufficiently_supported_groups_pass=all(r['status'] != 'fails_targets' for r in rows),
                limited_evidence_groups=sum(r['status'] == 'insufficient_scene_evidence' for r in rows),
                failed_groups=sum(r['status'] == 'fails_targets' for r in rows),
                limitation='Empirical subgroup checks; eight scenes is an evidence flag, not a coverage guarantee.')


def scene_difference_ci(data, selected, reference, repetitions=500):
    names = np.unique(data['scene'])
    keys = ('quality_pinball', 'usable_pinball', 'blocked_brier', 'band_brier',
            'quality_coverage', 'usable_coverage', 'quality_interval_score', 'usable_interval_score')
    differences = []
    for name in names:
        mask = data['scene'] == name
        a, b = measure(data['y'][mask], selected[mask]), measure(data['y'][mask], reference[mask])
        differences.append([a[k] - b[k] for k in keys])
    values = np.array(differences)
    draws = np.random.default_rng(20261004).integers(len(names), size=(repetitions, len(names)))
    means = values[draws].mean(axis=1)
    return {key: dict(difference=float(values[:, i].mean()), ci95=np.quantile(means[:, i], [.025, .975]).tolist(),
                     independent_scenes=len(names), repetitions=repetitions,
                     interpretation='Selected minus reference; equal-sized complete scenes resampled. Negative scores favor selected.')
            for i, key in enumerate(keys)}


def reliability_with_ci(data, p):
    result = probability_tables(data['y'], p, data['scene'])
    scene_names = np.unique(data['scene'])
    y = data['y'].reshape(-1, 4); flat = p.reshape(-1, 12)
    scene = np.repeat(data['scene'], data['y'].shape[1])
    draws = np.random.default_rng(20261005).integers(len(scene_names), size=(500, len(scene_names)))
    for name, probability, truth in [('blocked', flat[:, 8], y[:, 2])] + [
            (name, flat[:, 9 + i], (y[:, 3] == i).astype(float)) for i, name in enumerate(('DARK', 'BRIGHT', 'BACKUP'))]:
        for index, row in enumerate(result[name]):
            mask = (probability >= index / 10) & ((probability < (index + 1) / 10) if index < 9 else (probability <= 1))
            statistics = []
            for s in scene_names:
                active = mask & (scene == s)
                statistics.append((active.sum(), probability[active].sum(), truth[active].sum()))
            totals = np.array(statistics)[draws].sum(axis=1)
            nonempty = totals[:, 0] > 0
            if nonempty.any():
                totals = totals[nonempty]
                row['actual_frequency_ci95'] = np.quantile(totals[:, 2] / totals[:, 0], [.025, .975]).tolist()
                row['probability_gap_ci95'] = np.quantile((totals[:, 1] - totals[:, 2]) / totals[:, 0], [.025, .975]).tolist()
            else:
                row['actual_frequency_ci95'] = row['probability_gap_ci95'] = None
            row['evidence_status'] = 'limited' if row['independent_scenes'] < 8 else 'scene-bootstrap estimate'
    return result


def region_slices(data, summary, settings):
    result = {}
    for category, values in (('card', data['card']), ('lead_seconds', data['lead']), ('requested_seconds', data['duration'])):
        result[category] = {str(v): measure_regions({k: a[values == v] for k, a in summary.items()}, settings)
                            for v in np.unique(values)}
    result['altitude_layer'] = {str(layer): measure_regions({k: a[:, layer::3] for k, a in summary.items()}, settings)
                                for layer in range(3)}
    return result


def audit_region_labels(data, output, entries):
    from challenge.v4_fiber_map import radec_to_altaz
    from challenge.v4_scorer import (score_target_exposure, program_band, lunar_quality_factor,
                                     ZERO_SCORE_TYPE, FORCE_CLOSE_TYPE)
    max_q = max_u = 0.
    for entry in entries:
        g = int(np.flatnonzero(data['scene'] == entry['id'])[0])
        index = int(np.flatnonzero(data['eligible'][g])[0])
        directory = output / 'scenarios' / entry['id']
        meta = json.loads((directory / 'metadata.json').read_text())
        info = meta['groups'][0]
        start, end = utc(info['start']), utc(info['end'])
        _, coords, _, _ = representatives(meta['site'], start)
        ra, dec = coords[index]
        weather, events = read_truth(directory / 'truth_and_public')
        target = dict(target_id='region-audit', ra_deg=float(ra), dec_deg=float(dec), feature_flux=1., science_weight=1.)
        func = lambda t: radec_to_altaz(float(ra), float(dec), t, meta['site']['latitude_deg'], meta['site']['longitude_deg'])
        truth = score_target_exposure(target, list(weather.segments(start, end)), [e for e in events if e.overlaps(start, end)],
            weather, int((end - start).total_seconds()), 1., meta['score'], start, func, meta['site'])
        max_q = max(max_q, abs(truth.quality - float(data['y'][g, index, 0])))
        band = program_band(weather.band_quality(start, end, meta['score'], lambda t: func(t)[0],
            lambda t: lunar_quality_factor(float(ra), float(dec), t, meta['site'], meta['score'])), meta['score'])
        if int(data['y'][g, index, 3]) != ('DARK', 'BRIGHT', 'BACKUP').index(band):
            raise ValueError('region official band mismatch')
        opened = 0.
        for moment, seconds, slot in pieces(weather, events, start, end, True):
            alt, az = func(moment)
            closed = any(e.actual_start_utc <= moment < e.actual_end_utc and e.covers_altaz(alt, az)
                         and (e.force_close or e.event_type in (ZERO_SCORE_TYPE, FORCE_CLOSE_TYPE)) for e in events)
            if slot and slot.is_observable and not closed:
                opened += seconds
        max_u = max(max_u, abs(opened / (end - start).total_seconds() - float(data['y'][g, index, 1])))
    if max_q > 2e-6 or max_u > 1e-6:
        raise ValueError('region labels differ from official scorer')
    return dict(checked=len(entries), maximum_quality_error=max_q, maximum_usable_error=max_u,
                source='Direct official scorer + separately integrated openness; one point per independent final scene', passed=True)


def acceptance(m, reference, regional, subgroup=None):
    return dict(probability_calibration=max(m['blocked_ece'], m['band_ece']) <= .05,
                quality_range=.85 <= m['quality_coverage'] <= .95,
                usable_range=.85 <= m['usable_coverage'] <= .95,
                informative_proper_score=selection_score(m, reference) < 1.,
                region_quality_range=.85 <= regional['quality_mean_coverage'] <= .95,
                region_usable_range=.85 <= regional['usable_mean_coverage'] <= .95,
                subgroup_reliability=subgroup['sufficiently_supported_groups_pass'] if subgroup else False)


def report_markdown(report):
    lines = ['# 观测条件信息模型：单次训练比较', '',
             '来源：正式 A–D 公开输入＋官方生成器自行生成天气。结果不代表正式比赛隐藏数据或比赛成绩。', '',
             '## 训练与选型', '',
             f'- GRU、MLP 各一个初始化、各 20 轮，训练 {report["protocol"]["training_records"]:,} 条记录。',
             f'- 验证集选定：**{report["selected"]}**；最终测试前冻结参数。',
             '- 概率与分位数为主，均值误差辅助；无排序损失、无候选截断、无自动观测动作。',
             '- 旧训练/验证/校准场景复用；本轮最终测试种子全新。', '',
             '## 同一新测试集上的位置预测', '',
             '范围目标为 90%；区间评分与 Brier 越低越好，覆盖率需要与宽度和分项结果一起看。', '',
             '| 集合 | 模型 | Q区间评分 | Q覆盖率 | U区间评分 | U覆盖率 | 受阻Brier | 档位Brier | 受阻/档位校准误差 |',
             '|---|---|---:|---:|---:|---:|---:|---:|---|']
    for split, section in report['tests'].items():
        for name, values in section['models'].items():
            m = values['metrics']
            lines.append(f'| {split} | {name} | {m["quality_interval_score"]:.4f} | {m["quality_coverage"]:.1%} | '
                         f'{m["usable_interval_score"]:.4f} | {m["usable_coverage"]:.1%} | {m["blocked_brier"]:.4f} | '
                         f'{m["band_brier"]:.4f} | {m["blocked_ece"]:.3f}/{m["band_ece"]:.3f} |')
    lines += ['', '## 区域概览', '',
              '24 个方向×高度区域，每区 9 个等面积代表位置。区域均值区间单独校准；空间差异不冒充未来不确定性。', '',
              '| 集合 | 模型 | 区域Q均值区间覆盖 | 区域Q区间评分 | 区域U均值区间覆盖 | 区域U区间评分 |',
              '|---|---|---:|---:|---:|---:|']
    for split, section in report['tests'].items():
        for name, values in section['models'].items():
            r = values['regional']
            lines.append(f'| {split} | {name} | {r["quality_mean_coverage"]:.1%} | {r["quality_mean_interval_score"]:.4f} | '
                         f'{r["usable_mean_coverage"]:.1%} | {r["usable_mean_interval_score"]:.4f} |')
    lines += ['', '## 可信度与限制', '',
              '- 正常、变化更大的天气、新站点分开报告；每类事件、高度、曝光时长、预测距离的细分在 report.json。',
              '- 概率分成 10 组并报告实际频率、独立场景数量和场景重复抽样的误差范围。样本不足标为 limited。',
              '- U 在 0/1 有大量样本；校准前的连续分位数端点可能无法包含精确 0/1，校准后覆盖率仍有离散跳变。',
              '- 区域统计是采样近似；概率指随机选一个符合高度要求的位置，不是整片区域同时关闭的概率。',
              '- 全部预测仍可输出，不能将未通过检查的分项标成可靠；不据最终测试反复改参数。',
              f'- 本轮完整验收通过：**{report["final_test_passed"]}**。总体达标不等于分项可信；本轮部分分项失败、部分独立场景不足。',
              '- 权重、预处理与校准：models/selected；公开预测示例：example_prediction.json、sky_prediction.json。', '',
              '## 分项薄弱与证据不足', '',
              '少于8个独立场景标为证据不足；其数值仍保留。达到8个场景也不是单条预测可靠性的保证。', '',
              '| 集合 | 分组 | 独立场景 | Q范围覆盖 | U范围覆盖 | 受阻/档位概率偏差 | 状态 |',
              '|---|---|---:|---:|---:|---|---|']
    labels = {'insufficient_scene_evidence': '证据不足', 'fails_targets': '未达标'}
    for split, section in report['tests'].items():
        for row in section['subgroup_review']['rows']:
            if row['status'] == 'meets_targets':
                continue
            lines.append(f'| {split} | {row["category"]}:{row["value"]} | {row["independent_scenes"]} | '
                         f'{row["quality_coverage"]:.1%} | {row["usable_coverage"]:.1%} | '
                         f'{row["blocked_ece"]:.1%}/{row["band_ece"]:.1%} | {labels[row["status"]]} |')
    lines += ['', '## 可重跑命令', '', '```sh',
              'research/observing_conditions/.venv/bin/python -m research.observing_conditions.information_train',
              'research/observing_conditions/.venv/bin/python -m research.observing_conditions.information_predict --input artifacts/observing_conditions-information/sky_request.json --result artifacts/observing_conditions-information/sky_prediction.json',
              '```', '', '已完成训练会复用权重；恢复中断只继续未完成轮次。']
    return '\n'.join(lines) + '\n'


def finalize(base, output):
    torch.set_num_threads(1)
    start = time.perf_counter()
    if (output / 'report.json').exists():
        print('Final report already complete; reusing frozen results.', flush=True)
        return
    protocol = json.loads((output / 'protocol.json').read_text())
    selection = json.loads((output / 'selection.json').read_text())
    selected = selection['selected']; names = ('gru', 'mlp', 'lookup', 'constant')
    normalizer = Normalizer.load(output / 'normalizer.npz')
    base_manifest = json.loads((base / 'manifest.json').read_text())
    cal_entries = [e for e in base_manifest['entries'] if e['split'] == 'calibration']
    calibration = load_public_partitions(base, ('calibration',), EVALUATION_GROUPS)
    region_calibration = build_region_data(base, cal_entries, output / 'region_calibration.npz')
    settings, region_settings = {}, {}
    for name in names:
        settings[name] = calibration_fit(calibration['y'], predict_raw(name, calibration, output, normalizer))
        p = predict_raw(name, region_calibration, output, normalizer)
        summary = aggregate(p, region_calibration['eligible'], region_calibration['y'])
        region_settings[name] = fit_mean_calibration(summary, region_calibration['scene'])
    directory = output / 'models/selected'; directory.mkdir(parents=True, exist_ok=True)
    for filename in ('weights.pt', 'normalizer.npz'):
        shutil.copyfile(output / 'models' / selected / filename, directory / filename)
    metadata = json.loads((output / 'models' / selected / 'metadata.json').read_text()) | dict(
        feature_version=protocol['feature_version'], calibration=settings[selected], region_calibration=region_settings[selected],
        source=protocol['source'], quality_definition=protocol['quality_definition'],
        validation_reliable=selection['comparisons'][selected]['reliable'], final_test_passed=None, acceptance=None,
        reliability_evidence=dict(status='final test pending', calibration_scenes=32, region_calibration_scenes=32,
                                 limitations=protocol['development_limitation']))
    save_json(directory / 'metadata.json', metadata)
    frozen = dict(selected=selected, protocol_sha256=digest(output / 'protocol.json'),
                  point_calibration=settings, region_calibration=region_settings,
                  weights={name: digest(output / 'models' / name / 'weights.pt') for name in ('gru', 'mlp')},
                  normalizer_sha256=digest(output / 'normalizer.npz'),
                  selected_metadata_before_test=metadata)
    if (output / 'frozen.json').exists() and json.loads((output / 'frozen.json').read_text()) != frozen:
        raise ValueError('model selection or calibration changed after final freeze')
    save_json(output / 'frozen.json', frozen)
    calibration_seconds = time.perf_counter() - start
    print('ALL PARAMETERS FROZEN; generating genuinely new final scenes', flush=True)
    entries = protocol['final_entries']; begin = time.perf_counter()
    with ProcessPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(generate_one, (e, str(output), EVALUATION_GROUPS)) for e in entries]
        for i, future in enumerate(as_completed(jobs), 1):
            completed = future.result()
            print(f'new final scenes {i}/{len(entries)} {completed["id"]}', flush=True)
    generation_seconds = time.perf_counter() - begin
    begin = time.perf_counter()
    all_region_data = build_region_data(output, entries, output / 'region_final.npz')
    regional_generation_seconds = time.perf_counter() - begin
    tests = {}
    saved = {}
    for split in ('test', 'weather_test', 'site_test'):
        data = load_public_partitions(output, (split,), EVALUATION_GROUPS)
        entry_ids = [e['id'] for e in entries if e['split'] == split]
        region = subset(all_region_data, np.isin(all_region_data['scene'], entry_ids))
        predictions = {}; models = {}
        for name in names:
            raw = predict_raw(name, data, output, normalizer)
            p = calibration_apply(raw, settings[name]); predictions[name] = p
            rp_raw = predict_raw(name, region, output, normalizer)
            rp = calibration_apply(rp_raw, settings[name])
            summary = aggregate(rp, region['eligible'], region['y'])
            rmetrics = measure_regions(summary, region_settings[name])
            models[name] = dict(metrics=measure(data['y'], p), uncalibrated=measure(data['y'], raw),
                                slices=sliced_metrics(data, p), reliability_bins=reliability_with_ci(data, p),
                                regional=rmetrics, regional_slices=region_slices(region, summary, region_settings[name]),
                                regional_position_metrics=measure(region['y'][region['eligible']], rp[region['eligible']]))
            np.savez_compressed(output / f'{split}-{name}-predictions.npz', point_predictions=p,
                                region_point_predictions=rp, region_point_predictions_uncalibrated=rp_raw)
        reference = models['lookup']['metrics']
        comparisons = {name: scene_difference_ci(data, predictions[selected], predictions[name])
                       for name in ('lookup', 'mlp') if name != selected}
        subgroup = subgroup_review(models[selected]['slices'])
        checks = acceptance(models[selected]['metrics'], reference, models[selected]['regional'], subgroup)
        tests[split] = dict(independent_scenes=len(np.unique(data['scene'])), models=models,
                            selected_checks=checks, selected_passed=all(checks.values()),
                            selected_difference_ci95=comparisons, subgroup_review=subgroup)
        saved[split] = dict(data=data, region=region)
        print(f'{split}: selected={selected}; checks={checks}', flush=True)
    # Sampling sensitivity is descriptive final evaluation, never used to tune grid/model.
    convergence_entries = [e for e in entries if e['id'].endswith('-0')]
    fine = build_region_data(output, convergence_entries, output / 'region_finer.npz', groups=1, side=6)
    fp = predict_raw(selected, fine, output, normalizer)
    fs = aggregate(fp, fine['eligible'], fine['y'], side=6)
    coarse_indices = [int(np.flatnonzero(all_region_data['scene'] == e['id'])[0]) for e in convergence_entries]
    coarse = subset(all_region_data, coarse_indices)
    cp = predict_raw(selected, coarse, output, normalizer)
    cs = aggregate(cp, coarse['eligible'], coarse['y'])
    mask = (cs['representatives_count'] > 0) & (fs['representatives_count'] > 0)
    convergence = dict(independent_scenes=len(convergence_entries), representative_counts=[9, 36],
        predicted_region_quality_mean_absolute_change=float(np.abs(cs['quality_mean'] - fs['quality_mean'])[mask].mean()),
        true_region_quality_mean_absolute_change=float(np.abs(cs['true_quality'] - fs['true_quality'])[mask].mean()),
        true_blocked_fraction_absolute_change=float(np.abs(cs['true_blocked_fraction'] - fs['true_blocked_fraction'])[mask].mean()),
        geometry_eligible_fraction_absolute_change=float(np.abs(cs['eligible_fraction'] - fs['eligible_fraction']).mean()),
        limitation='One exposure per chosen independent scene; 36-point estimate is also approximate. No parameter changed based on this check.')
    save_json(output / 'sampling_sensitivity.json', convergence)
    # Reuse already audited official vector integration, add fresh direct oracle checks.
    point_audit = audit_dataset(output, count=32, sample_splits=('test', 'weather_test', 'site_test'))
    region_audit = audit_region_labels(all_region_data, output, entries)
    report = dict(version=VERSION, selected=selected, protocol=protocol, selection=selection, tests=tests,
                  sampling_sensitivity=convergence, point_label_audit=point_audit, region_label_audit=region_audit,
                  final_test_passed=all(s['selected_passed'] for s in tests.values()),
                  timing=dict(training_seconds={name: json.loads((output / 'models' / name / 'complete.json').read_text())['elapsed_seconds']
                                                for name in ('gru', 'mlp')},
                              calibration_seconds=calibration_seconds, final_point_generation_seconds=generation_seconds,
                              final_region_generation_seconds=regional_generation_seconds,
                              finalization_seconds=time.perf_counter() - start))
    metadata['final_test_passed'] = report['final_test_passed']
    metadata['acceptance'] = {split: section['selected_checks'] for split, section in tests.items()}
    metadata['reliability_evidence'] = dict(status='independent self-generated final test completed',
        calibration_scenes=32, region_calibration_scenes=32,
        test_scenes={split: s['independent_scenes'] for split, s in tests.items()},
        point_metrics={split: s['models'][selected]['metrics'] for split, s in tests.items()},
        region_metrics={split: s['models'][selected]['regional'] for split, s in tests.items()},
        subgroup_review={split: s['subgroup_review'] for split, s in tests.items()},
        sampling_sensitivity=convergence, all_checks_passed=report['final_test_passed'],
        limitation='Only self-generated scenarios evaluated; no individual, joint, real competition, or arbitrary new-distribution guarantee.')
    save_json(directory / 'metadata.json', metadata)
    # Public-only request examples, then actual inference through the shipped entry point.
    from .information_predict import InformationPredictor
    request = json.loads((base / 'example_request.json').read_text())
    save_json(output / 'example_request.json', request)
    predictor = InformationPredictor(directory)
    save_json(output / 'example_prediction.json', predictor.predict(request))
    sky_request = {k: v for k, v in request.items() if k != 'candidates'} | dict(exposures=[
        {k: request['candidates'][0][k] for k in ('start_utc', 'duration_seconds')}])
    save_json(output / 'sky_request.json', sky_request)
    sky = predictor.sky(sky_request)
    if len(sky['region_prediction_table']) != 24 or len(sky['point_prediction_table']) != 216:
        raise ValueError('public sky entry point did not preserve complete region/point tables')
    if any('suggest_priority' in row or 'suggest_exclude' in row for row in sky['point_prediction_table']):
        raise ValueError('decision fields leaked into information output')
    save_json(output / 'sky_prediction.json', sky)
    # End-to-end batching timing, including public feature preparation and region summaries.
    timings = []
    for i in range(12):
        begin = time.perf_counter(); predictor.sky(sky_request)
        if i >= 2:
            timings.append(1000 * (time.perf_counter() - begin))
    report['runtime'] = dict(regions=24, representative_positions=216, cpu_threads=1,
        repetitions=10, total_sky_request_p50_ms=float(np.median(timings)),
        total_sky_request_p95_ms=float(np.quantile(timings, .95)),
        boundary='Already-loaded model: public geometry, feature processing, NN, calibration, regional summaries; excludes startup/file loading.')
    if protocol['official_sources'] != source_hashes():
        raise ValueError('official input changed during this experiment')
    if frozen['weights'] != {name: digest(output / 'models' / name / 'weights.pt') for name in ('gru', 'mlp')}:
        raise ValueError('weights changed after final freeze')
    save_json(output / 'report.json', report)
    (output / 'report.md').write_text(report_markdown(report))
    save_json(output / 'completion_audit.json', dict(training_fits=2, initializations_each=1, ranking_loss=False,
        official_inputs_unchanged=True, parameters_frozen_before_final_test=True, labels_audited=True,
        regional_output_count=24, position_output_count=216, automatic_actions=False,
        implementation_sources={str(p.relative_to(ROOT)): digest(p) for p in (ROOT / 'research/observing_conditions').glob('information_*.py')}))
    save_json(output / 'state.json', dict(stage='complete', selected=selected, training_complete=True,
              calibration_complete=True, final_test_complete=True, final_test_passed=report['final_test_passed']))
    print(f'INFORMATION RUN COMPLETE; selected={selected}; all gates={report["final_test_passed"]}', flush=True)

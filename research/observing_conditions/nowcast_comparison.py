"""Quality models vs the rule baseline's own estimate, on the same exposures and targets.

Before each exposure of the fault experiment's fixed schedules both sides predict the
same things for the same targets: Q_env (instrument efficiency 1) and the program band.
The models see public bulletins, forecasts and geometry; the baseline
(`agents/claude-baseline-v1`) sees bulletins and the feedback of earlier exposures.
Truth is the cached official physics. The baseline side is written by
`research.instrument_faults.baseline_comparison`.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import torch

from .data import ROOT, save_json
from .predict import Predictor
from .information_predict import InformationPredictor

VERSION = 'conditions-nowcast-comparison-v1'
MODELS = {'mlp_round1': (Predictor, 'artifacts/observing_conditions/models/selected'),
          'gru_information': (InformationPredictor, 'artifacts/observing_conditions-information/models/selected')}
BAND_SCALE = 0.07   # the baseline's own logistic width on log(level)


def model_predictions(predictor, path):
    with np.load(path) as arrays:
        normalized = predictor.normalizer.apply(arrays)
        counts = arrays['counts']
    parts = [predictor.numeric({k: normalized[k][first:first+64] for k in ('context', 'history', 'candidate')})
             for first in range(0, len(counts), 64)]
    return np.concatenate(parts), counts


def load(data, nowcast, entries, predictors):
    columns = {k: [] for k in ('scene', 'exposure', 'first', 'duration', 'fault', 'truth', 'band', 'baseline', 'level')}
    outputs = {name: [] for name in predictors}
    for number, entry in enumerate(entries):
        for track in (0, 1):
            directory = Path(data)/'scenarios'/entry['id']
            rows = json.loads((directory/f'schedule-{track}.json').read_text())
            saved = np.load(Path(nowcast)/f'{entry["id"]}-{track}.npy')
            position = saved[:, 0].astype(int)
            first = np.array([i == 0 or rows[i]['night'] != rows[i-1]['night'] for i in range(len(rows))])
            columns['scene'].append(np.full(len(saved), number)); columns['exposure'].append(position+len(rows)*track)
            columns['first'].append(first[position]); columns['duration'].append(np.array([r['duration'] for r in rows])[position])
            columns['fault'].append(saved[:, 5] > 0); columns['truth'].append(saved[:, 3]); columns['band'].append(saved[:, 4])
            columns['baseline'].append(saved[:, 1]); columns['level'].append(saved[:, 2])
            for name, predictor in predictors.items():
                p, counts = model_predictions(predictor, directory/f'weather-input-{track}.npz')
                flat = np.concatenate([p[i, :n] for i, n in enumerate(counts) if n])
                if len(flat) != len(saved):
                    raise ValueError('model and baseline predictions cover different targets')
                outputs[name].append(flat)
    return {k: np.concatenate(v) for k, v in columns.items()}, {k: np.concatenate(v) for k, v in outputs.items()}


def band_class(level, bands):
    return np.where(level >= bands['DARK'], 0, np.where(level >= bands['BRIGHT'], 1, 2))


def baseline_bands(level, bands):
    """The baseline's band probabilities for a predicted band level (conditions.Sky.band_probabilities)."""
    x = np.log(np.maximum(level, 1e-12))
    sigmoid = lambda z: 1/(1+np.exp(-np.clip(z, -30, 30)))
    dark = sigmoid((x-np.log(bands['DARK']))/BAND_SCALE); bright = sigmoid((x-np.log(bands['BRIGHT']))/BAND_SCALE)
    return np.where((level > 0)[:, None], np.column_stack([dark, bright-dark, 1-bright]), [0.0, 0.0, 1.0])


def scene_sums(d, q, bands_p, mask, score):
    """Per-scene sums; ratios of their totals weigh scenes as resampling units."""
    multipliers = np.array([score['program']['multipliers'][k] for k in ('DARK', 'BRIGHT', 'BACKUP')])
    mismatch = score['program']['mismatch_multiplier']
    actual = band_class(d['band'], score['program']['bands'])
    onehot = np.eye(3)[actual]
    error = np.abs(q-d['truth'])
    rows = []
    for scene in np.unique(d['scene']):
        keep = mask & (d['scene'] == scene)
        if not keep.any():
            rows.append(np.zeros(8)); continue
        # One program per exposure, chosen from the predicted band probabilities of its targets.
        exposure = d['exposure'][keep]; order = np.argsort(exposure, kind='stable')
        _, starts = np.unique(exposure[order], return_index=True)
        expected = np.add.reduceat(bands_p[keep][order]*(multipliers-mismatch), starts)
        chosen = np.repeat(expected.argmax(1), np.diff(np.r_[starts, keep.sum()]))
        got = np.where(actual[keep][order] == chosen, multipliers[chosen], mismatch)
        # The best single program per exposure, knowing the true bands.
        ideal = keep.sum()*mismatch+np.add.reduceat(onehot[keep][order]*(multipliers-mismatch), starts).max(1).sum()
        rows.append([keep.sum(), error[keep].sum(), d['truth'][keep].sum(), (q-d['truth'])[keep].sum(),
                     ((bands_p[keep]-onehot[keep])**2).sum(), (bands_p[keep].argmax(1) == actual[keep]).sum(),
                     got.sum(), ideal])
    return np.asarray(rows, float)


def measures(sums):
    n = sums[:, 0].sum()
    total = sums.sum(0)
    return dict(rows=int(n), scenes=int((sums[:, 0] > 0).sum()), quality_mae=float(total[1]/n),
                quality_relative_error=float(total[1]/total[2]), quality_bias=float(total[3]/n),
                band_brier=float(total[4]/n), band_accuracy=float(total[5]/n),
                realised_multiplier=float(total[6]/n), ideal_multiplier=float(total[7]/n))


def compare(d, predictions, mask, score, repetitions=2000):
    sums = {name: scene_sums(d, q, bands, mask, score) for name, (q, bands) in predictions.items()}
    result = {name: measures(s) for name, s in sums.items()}
    used = np.flatnonzero(sums['baseline'][:, 0] > 0)
    draws = used[np.random.default_rng(20261005).integers(len(used), size=(repetitions, len(used)))]
    for name in result:
        if name == 'baseline':
            continue
        diff = {}
        for key in ('quality_mae', 'band_brier', 'realised_multiplier'):
            values = [measures(sums[name][i])[key]-measures(sums['baseline'][i])[key] for i in draws]
            diff[key] = dict(mean=result[name][key]-result['baseline'][key], ci95=np.quantile(values, [.025, .975]).tolist())
        result[name]['minus_baseline'] = diff
    return result


def run(data, nowcast, output):
    data, nowcast, output = Path(data), Path(nowcast), Path(output)
    output.mkdir(parents=True, exist_ok=True)
    entries = [e for e in json.loads((data/'manifest.json').read_text())['entries'] if e['split'] == 'validation']
    torch.set_num_threads(1)
    predictors = {name: cls(ROOT/path) for name, (cls, path) in MODELS.items()}
    d, outputs = load(data, nowcast/'nowcast', entries, predictors)
    scores = {e['card']: json.loads((ROOT/f'cards/{e["card"]}/config/v4_score_config.json').read_text()) for e in entries}
    if len({json.dumps(s['program'], sort_keys=True) for s in scores.values()}) != 1:
        raise ValueError('cards use different program bands; compare per card')
    score = next(iter(scores.values()))
    predictions = {'baseline': (d['baseline'], baseline_bands(d['level'], score['program']['bands']))}
    for name, p in outputs.items():
        predictions[name] = (p[:, 0], p[:, 6:9])
    clean = np.isin(d['scene'], [i for i, e in enumerate(entries) if e['fault_count'] == 0])
    subsets = {'no_fault_scenes': clean, 'no_fault_scenes_first_of_night': clean & d['first'],
               'no_fault_scenes_later_in_night': clean & ~d['first'],
               'no_fault_scenes_short_exposure': clean & (d['duration'] <= 300),
               'no_fault_scenes_long_exposure': clean & (d['duration'] > 300),
               'all_scenes_no_active_fault': ~d['fault'], 'all_scenes_active_fault': d['fault'], 'all_scenes': np.ones(len(clean), bool)}
    for card in 'abcd':
        subsets[f'no_fault_scenes_card_{card}'] = clean & np.isin(d['scene'], [i for i, e in enumerate(entries) if e['card'] == card])
    result = dict(version=VERSION, scenes=len(entries), target_exposures=len(clean),
                  comparisons={name: compare(d, predictions, mask, score) for name, mask in subsets.items()},
                  interval_coverage={name: {subset: float(((d['truth'] >= p[:, 1]) & (d['truth'] <= p[:, 3]))[mask].mean())
                                            for subset, mask in subsets.items()} for name, p in outputs.items()},
                  source='formal public A-D inputs + self-generated weather, events and fixed schedules; validation scenes of the fault experiment',
                  models={name: predictor.metadata.get('model_version') for name, predictor in predictors.items()})
    save_json(output/'results.json', result)
    write_report(output, result)
    return result


def write_report(output, result):
    label = {'baseline': '基线自己的估计', 'mlp_round1': '首轮 MLP（三个初始化平均）', 'gru_information': '信息提炼版 GRU'}
    subset = {'no_fault_scenes': '无故障场景（主要比较）', 'no_fault_scenes_first_of_night': '无故障场景：每夜第一次曝光',
              'no_fault_scenes_later_in_night': '无故障场景：同夜后续曝光', 'no_fault_scenes_short_exposure': '无故障场景：曝光 ≤300 秒',
              'no_fault_scenes_long_exposure': '无故障场景：曝光 >300 秒', 'all_scenes_no_active_fault': '全部场景：当时没有未修复故障',
              'all_scenes_active_fault': '全部场景：当时有未修复故障', 'all_scenes': '全部场景'}
    lines = ['# 观测质量：模型与基线估计的同口径比较', '',
             '来源：故障实验的 64 个验证场景（正式 A–D 公开输入＋自行生成的天气、事件和固定观测安排）；不是正式比赛隐藏数据。',
             f'比较单位：每次曝光里的每个目标，共 {result["target_exposures"]} 条。两边都在曝光开始前给出预测，真实值来自官方评分公式。', '',
             '- 模型只看公开公告、预报和位置几何，不看任何观测反馈。',
             '- 基线只看公告和此前曝光的反馈，不看预报；预测值是它规划时使用的天气水平 × 该目标的大气和月光因子 × 方向性折扣。',
             '- 质量指不含仪器效率的 Q_env；档位按官方门槛由真实档位水平划分。', '']
    for key, title in subset.items():
        block = result['comparisons'][key]
        lines += [f'## {title}', '', f'{block["baseline"]["scenes"]} 个场景，{block["baseline"]["rows"]} 条。', '',
                  '|方法|质量平均绝对误差|总体相对误差|平均偏差|档位概率误差（Brier）|档位判对比例|按预测选程序的平均倍率|',
                  '|---|---:|---:|---:|---:|---:|---:|']
        for name in ('baseline', 'mlp_round1', 'gru_information'):
            m = block[name]
            lines.append(f'|{label[name]}|{m["quality_mae"]:.4f}|{m["quality_relative_error"]:.1%}|{m["quality_bias"]:+.4f}|'
                         f'{m["band_brier"]:.4f}|{m["band_accuracy"]:.1%}|{m["realised_multiplier"]:.4f}|')
        lines.append('')
        for name in ('mlp_round1', 'gru_information'):
            diff = block[name]['minus_baseline']
            lines.append(f'{label[name]}减基线：质量误差 {diff["quality_mae"]["mean"]:+.4f}（{diff["quality_mae"]["ci95"][0]:+.4f} 至 {diff["quality_mae"]["ci95"][1]:+.4f}），'
                         f'档位概率误差 {diff["band_brier"]["mean"]:+.4f}（{diff["band_brier"]["ci95"][0]:+.4f} 至 {diff["band_brier"]["ci95"][1]:+.4f}），'
                         f'选程序倍率 {diff["realised_multiplier"]["mean"]:+.4f}（{diff["realised_multiplier"]["ci95"][0]:+.4f} 至 {diff["realised_multiplier"]["ci95"][1]:+.4f}）。')
        lines.append('')
    lines += ['## 分卡（无故障场景，质量平均绝对误差 / 档位概率误差）', '', '|卡|基线|首轮 MLP|信息提炼版 GRU|', '|---|---:|---:|---:|']
    for card in 'abcd':
        block = result['comparisons'][f'no_fault_scenes_card_{card}']
        lines.append(f'|{card.upper()}|'+'|'.join(f'{block[n]["quality_mae"]:.4f} / {block[n]["band_brier"]:.4f}'
                                                  for n in ('baseline', 'mlp_round1', 'gru_information'))+'|')
    coverage = result['interval_coverage']
    lines += ['', '模型的 90% 质量范围在无故障场景的实际覆盖率：'
              +'；'.join(f'{label[n]} {coverage[n]["no_fault_scenes"]:.1%}' for n in coverage)+'。基线不给范围。', '',
              '## 限制', '',
              '- 固定安排里相邻曝光间隔 60–80 分钟，基线的估计来自一小时以前甚至前一夜；它在自己的比赛过程里连续观测，估计只落后几分钟。',
              '- 有未修复故障时，基线会把一部分仪器损失记成天气变差，所以主要比较只用无故障场景；这类场景只有 16 个（每卡 4 个）。',
              '- 区间按完整场景重复抽样 2000 次；负的误差差异表示模型更好，正的倍率差异表示模型更好。',
              '- 选程序倍率只衡量“按预测的档位概率选一个程序”这一步，不含曝光时长和选址的影响，也不等于比赛得分。',
              '- 两个模型的验收状态不变：都没有通过各自的完整验收。这里没有重新训练或调整任何一方。']
    (Path(output)/'REPORT.md').write_text('\n'.join(lines)+'\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='质量模型与基线估计在同一批曝光上的比较')
    parser.add_argument('--data', type=Path, default=Path('artifacts/instrument_faults'))
    parser.add_argument('--nowcast', type=Path, default=Path('artifacts/instrument_faults-baseline-comparison'))
    parser.add_argument('--output', type=Path, default=Path('artifacts/observing_conditions-baseline-comparison'))
    args = parser.parse_args()
    run(args.data, args.nowcast, args.output)

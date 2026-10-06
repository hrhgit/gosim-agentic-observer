"""Public-feedback-only fixed rules on the same held-out validation scenes."""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import json
import multiprocessing
from pathlib import Path
import time

import numpy as np

from . import benchmark
from research.observing_conditions.data import save_json

VERSION = 'fault-public-rules-v1'
SPECS = ([dict(name=f'low3_{t:g}', family='low3', threshold=t) for t in (.3, .5, .7)]
         + [dict(name=f'drop_{t:g}', family='drop', threshold=t) for t in (.5, .7, .85)]
         + [dict(name=f'guarded_low3_{t:g}', family='guarded_low3', threshold=t) for t in (.3, .5, .7)])
INDEX = {name: i for i, name in enumerate(benchmark.BASE_NAMES)}
WEATHER_COLUMNS = [i for name, i in INDEX.items() if name.startswith('current:')
                   and name.split(':')[1] in ('rain', 'overcast', 'haze', 'cold_snap', 'storm')]


def decisions(x, spec):
    """Only public numeric inputs. No answers, scene IDs, or true weather."""
    col = lambda name: x[:, INDEX[name]]
    recent = col('last3:response_median:mean')
    count = col('last3:count')
    enough = (count >= 3) & np.isfinite(recent) & (col('usable_targets') > 0)
    if spec['family'] == 'drop':
        reference = col('last30:response_median:mean')
        enough &= (col('last30:count') >= 10) & np.isfinite(reference) & (reference > 1e-8)
        score = np.divide(recent, reference, out=np.full(len(x), np.nan), where=reference > 1e-8)
    else:
        score = recent
    positive = enough & (score < spec['threshold'])
    if spec['family'] == 'guarded_low3':
        quake_nights = col('quake_nights')
        quake_clear = np.isnan(quake_nights) | (quake_nights >= 4)
        weather_clear = ~np.any(x[:, WEATHER_COLUMNS] > 0, axis=1)
        positive &= quake_clear & weather_clear
    return positive, enough


def rates(y, predicted, weight):
    tp = float(weight[(y == 1) & predicted].sum())
    fn = float(weight[(y == 1) & ~predicted].sum())
    fp = float(weight[(y == 0) & predicted].sum())
    tn = float(weight[(y == 0) & ~predicted].sum())
    ratio = lambda a, b: a/b if b else None
    return dict(accuracy=ratio(tp+tn, tp+tn+fp+fn), precision=ratio(tp, tp+fp),
                recall=ratio(tp, tp+fn), false_positive_rate=ratio(fp, fp+tn),
                tp=tp, fn=fn, fp=fp, tn=tn)


def scene_job(inputs, entry):
    if entry['split'] != 'validation':
        raise ValueError('rule screen only reads validation scenes')
    start = time.perf_counter()
    data = benchmark.load_data(inputs, [entry])
    x, y = data['x'], data['y']
    results = {}
    for spec in SPECS:
        before = time.perf_counter()
        predicted, enough = decisions(x, spec)
        elapsed = time.perf_counter()-before
        result = rates(y, predicted, np.ones(len(y))/len(y))
        result.update(evidence_coverage=float(enough.mean()), decision_seconds=elapsed,
                      positive_rows=int(predicted.sum()), missed_rows=int(((y == 1) & ~predicted).sum()),
                      false_positive_rows=int(((y == 0) & predicted).sum()))
        results[spec['name']] = result
    always = rates(y, np.zeros(len(y), bool), np.ones(len(y))/len(y))
    return dict(scene=entry['id'], card=entry['card'], rows=len(y), rules=results,
                always_no_fault=always, seconds=time.perf_counter()-start)


def aggregate(rows, name):
    records = [r['always_no_fault'] if name == 'always_no_fault' else r['rules'][name] for r in rows]
    sums = {k: sum(r[k] for r in records) for k in ('tp', 'fn', 'fp', 'tn')}
    tp, fn, fp, tn = (sums[k] for k in ('tp', 'fn', 'fp', 'tn'))
    ratio = lambda a, b: a/b if b else None
    result = dict(accuracy=ratio(tp+tn, tp+tn+fp+fn), precision=ratio(tp, tp+fp),
                  recall=ratio(tp, tp+fn), false_positive_rate=ratio(fp, fp+tn))
    if name != 'always_no_fault':
        result.update(evidence_coverage=float(np.mean([r['evidence_coverage'] for r in records])),
                      decision_seconds=sum(r['decision_seconds'] for r in records),
                      missed_rows=sum(r['missed_rows'] for r in records),
                      false_positive_rows=sum(r['false_positive_rows'] for r in records))
    # Bootstrap whole scenes, never individual correlated exposures.
    rng = np.random.default_rng(510051)
    counts = np.asarray([[r[k] for k in ('tp', 'fn', 'fp', 'tn')] for r in records])
    sampled = counts[rng.integers(len(rows), size=(2000, len(rows)))].sum(axis=1)
    a, b, c, d = sampled.T
    for metric, numerator, denominator in [('accuracy', a+d, a+b+c+d), ('precision', a, a+c),
                                            ('recall', a, a+b), ('false_positive_rate', c, c+d)]:
        values = np.divide(numerator, denominator, out=np.full(2000, np.nan), where=denominator > 0)
        result[metric+'_ci95'] = np.nanquantile(values, [.025, .975]).tolist() if np.isfinite(values).any() else None
    return result


def run(data, inputs, output, workers, model_results):
    output = Path(output); output.mkdir(parents=True, exist_ok=True)
    entries = [e for e in json.loads((Path(data)/'manifest.json').read_text())['entries'] if e['split'] == 'validation']
    comparison = Path(model_results)
    small_protocol = json.loads((comparison/'protocol.json').read_text())
    if small_protocol['validation_ids'] != [e['id'] for e in entries] or any(
            small_protocol['code'].get(k) != v for k, v in benchmark.sources().items()):
        raise ValueError('small models and fixed rules must use identical public input definitions and validation scenes')
    protocol = dict(version=VERSION, specifications=SPECS, workers=workers,
        code=benchmark.sources() | {'rules.py': benchmark.digest(__file__)},
        manifest_sha256=benchmark.digest(Path(data)/'manifest.json'),
        input_protocol_sha256=benchmark.digest(Path(inputs)/'comparison_protocol.json'),
        validation_ids=[e['id'] for e in entries], final_test_accessed=False,
        report_actions_evaluated=False, probability_output=False,
        insufficient_evidence='no positive flag; still included in overall misses',
        source='saved public feedback, public announcements; no predicted or true weather',
        thresholds='fixed in code before evaluating; all candidates reported, not final-test selected')
    marker = output/'protocol.json'
    if marker.exists() and json.loads(marker.read_text()) != protocol:
        raise ValueError('rule protocol changed; use another output directory')
    save_json(marker, protocol)
    start = time.perf_counter()
    cached = []; pending = []
    for entry in entries:
        path = output/'scenes'/f'{entry["id"]}.json'
        if path.exists():
            cached.append(json.loads(path.read_text()))
        else:
            pending.append(entry)
    save_json(output/'_state.json', dict(phase='running', complete_scenes=len(cached), total_scenes=len(entries)))
    if pending:
        from functools import partial
        with ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context('spawn')) as pool:
            for record in pool.map(partial(scene_job, str(inputs)), pending):
                save_json(output/'scenes'/f'{record["scene"]}.json', record)
                cached.append(record)
                save_json(output/'_state.json', dict(phase='running', complete_scenes=len(cached), total_scenes=len(entries)))
    summary = {name: aggregate(cached, name) for name in ['always_no_fault']+[s['name'] for s in SPECS]}
    cards = {card: {name: aggregate([r for r in cached if r['card'] == card], name)
                   for name in summary} for card in sorted({e['card'] for e in entries})}
    result = dict(scenes=len(cached), rows=sum(r['rows'] for r in cached), rules=summary, by_card=cards,
                  wall_seconds=time.perf_counter()-start, validation_only=True)
    save_json(output/'results.json', result)
    percent = lambda value: '无正例判断' if value is None else f'{value:.2%}'
    lines = ['# 纯规则故障判断：验证集比较', '',
        '只使用实际反馈和公开信息，无模型训练，无天气预测。统一原定 64 个验证场景、201284 条可比较反馈，每个场景等权。',
        '响应值为目标得分按亮度、权重、时长和公开程序最大倍率归一化的数值；不是真实仪器效率。',
        'low3：最近三次反馈的响应中间值取平均，低于门槛则标记故障。',
        'drop：最近三次平均响应 / 最近三十次平均响应低于门槛，且至少有十次可比较反馈。',
        'guarded_low3：low3 加上当前无天气干扰公告、最近四个观测夜无地震公告。',
        '证据不足时不发出故障标记；计算漏报时仍包含这些故障状态。修复、撤销的历史整理复用已有公开输入。', '',
        '|方法|判断正确比例|故障状态发现比例|判为故障中正确比例|正常状态误报比例|',
        '|---|---:|---:|---:|---:|']
    for name, r in summary.items():
        lines.append('|'+name+'|'+'|'.join(percent(r[k]) for k in ('accuracy', 'recall', 'precision', 'false_positive_rate'))+'|')
    for r in json.loads((comparison/'results.json').read_text()):
        m = r['validation']
        lines.append('|'+f'{r["training_scenes"]} 场景 {r["kind"]}'+'|'+'|'.join(percent(m[k]) for k in ('accuracy', 'recall', 'precision', 'false_positive_rate'))+'|')
    lines += ['', f'实际总耗时：{result["wall_seconds"]:.1f} 秒（含读取与核验输入、4 进程启动、完整场景抽样统计，不是单次推理耗时）。',
              '规则阈值预先固定，全部结果公开；本轮不冻结最优规则，也不使用最终/外部测试调参。',
              'results.json 包含按完整场景抽样 2000 次的区间与按卡结果。',
              '这是逐曝光当前故障状态判断，不是事件发现率、真实申报正确率或修复收益；没有执行申报改变后续反馈。',
              '几条规则表现差不能证明所有规则都差；尤其是得分上限、程序不匹配、天气和故障重叠会影响这几种简单规则。']
    (output/'REPORT.md').write_text('\n'.join(lines)+'\n')
    save_json(output/'_state.json', dict(phase='complete', complete_scenes=len(cached), final_test_accessed=False))
    print(json.dumps({k: result[k] for k in ('scenes', 'rows', 'wall_seconds')}, ensure_ascii=False))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', type=Path, default=Path('artifacts/instrument_faults'))
    parser.add_argument('--inputs', type=Path, default=Path('artifacts/instrument_faults-feedback'))
    parser.add_argument('--output', type=Path, default=Path('artifacts/instrument_faults-rules'))
    parser.add_argument('--model-results', type=Path, default=Path('artifacts/instrument_faults-smallscale-4cpu'))
    parser.add_argument('--workers', type=int, default=4)
    args = parser.parse_args()
    if args.workers < 1:
        parser.error('workers must be positive')
    run(args.data, args.inputs, args.output, args.workers, args.model_results)

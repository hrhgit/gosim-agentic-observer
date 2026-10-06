"""Small, nested training cohorts; fixed validation scenes, no final test access."""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
import json
import multiprocessing
import os
from pathlib import Path
import time

import joblib
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from threadpoolctl import threadpool_limits

from . import benchmark
from .models import Classifier, Preprocessor
from research.observing_conditions.data import save_json

PARAMETERS = {'logistic': {'C': 1}, 'boosting': {
    'learning_rate': .1, 'max_leaf_nodes': 15, 'l2_regularization': 1}}
VERSION = 'fault-smallscale-v1'


def nested_cohorts(entries, sizes, seed=51005):
    """Shuffle within each card/fault-count stratum, then interleave strata."""
    sizes = sorted(set(sizes))
    if not sizes or any(n < 4 or n % 4 for n in sizes):
        raise ValueError('sizes must be positive multiples of four')
    rng = np.random.default_rng(seed)
    cards = sorted({e['card'] for e in entries if e['split'] == 'train'})
    if len(cards) != 4:
        raise ValueError('four training cards required')
    orders = {}
    for card in cards:
        groups = {k: [e for e in entries if e['split'] == 'train'
                     and e['card'] == card and e['fault_count'] == k] for k in (0, 1, 3)}
        for group in groups.values():
            rng.shuffle(group)
        order = []
        while any(groups.values()):
            for count in (1, 0, 1, 3):
                if groups[count]:
                    order.append(groups[count].pop())
        if len(order) < max(sizes)//4:
            raise ValueError('not enough complete training scenes')
        orders[card] = order
    return {n: [e for card in cards for e in orders[card][:n//4]] for n in sizes}


def fit_parallel(kind, data, threads):
    x = data['x'].copy()
    with threadpool_limits(limits=threads):
        if kind == 'logistic':
            pre = Preprocessor().fit(x, data['weight'])
            base = LogisticRegression(max_iter=2000, **PARAMETERS[kind]).fit(
                pre.apply(x), data['y'], sample_weight=data['weight'])
        else:
            pre = None
            x[:, ~np.isfinite(x).any(axis=0)] = 0
            base = HistGradientBoostingClassifier(max_iter=300, min_samples_leaf=50,
                early_stopping=False, random_state=404, **PARAMETERS[kind]).fit(
                    x, data['y'], sample_weight=data['weight'])
    return Classifier(kind, base, pre)


def train_job(inputs, output, cohort, validation, kind, threads):
    # Answer-bearing final test/calibration entries are never passed to workers.
    if any(e['split'] != 'train' for e in cohort) or any(e['split'] != 'validation' for e in validation):
        raise ValueError('small-scale screen only accepts train and validation')
    started = time.perf_counter()
    train = benchmark.load_data(inputs, cohort)
    val = benchmark.load_data(inputs, validation)
    fit_started = time.perf_counter()
    model = fit_parallel(kind, train, threads)
    fit_seconds = time.perf_counter()-fit_started
    with threadpool_limits(limits=threads):
        measured = benchmark.classifications(val, model.raw(val['x']))
    path = Path(output)/f'scenes-{len(cohort):03}'/kind
    path.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, path/'classifier.joblib')
    result = dict(kind=kind, training_scenes=len(cohort), training_rows=len(train['y']),
                  validation_scenes=len(validation), validation_rows=len(val['y']),
                  training_seconds=fit_seconds, total_seconds=time.perf_counter()-started,
                  cpu_threads=threads, parameters=PARAMETERS[kind], calibrated=False,
                  validation=measured, training_ids=[e['id'] for e in cohort])
    save_json(path/'result.json', result)
    return result


def write_report(output, results, phase):
    lines = ['# 不使用天气预测：小规模训练比较', '', f'状态：{phase}', '',
        '复用自行生成的正式 A–D 场景；只读取训练与验证输入。每张卡数量相同，训练场景逐级包含。',
        '统一使用原定 64 个验证场景；场景总权重相同。固定模型参数，不随机拆分曝光记录。',
        '概率尚未独立校准；未读取最终或外部测试集，未评估执行申报的收益。', '',
        '|训练场景|模型|训练记录|训练秒数|判断正确比例|故障状态发现比例|报故障中正确比例|误报比例|分类损失|',
        '|---:|---|---:|---:|---:|---:|---:|---:|---:|']
    for r in sorted(results, key=lambda r: (r['training_scenes'], r['kind'])):
        m = r['validation']
        lines.append(f"|{r['training_scenes']}|{r['kind']}|{r['training_rows']}|{r['training_seconds']:.1f}|"
                     f"{m['accuracy']:.2%}|{m['recall']:.2%}|{m['precision']:.2%}|"
                     f"{m['false_positive_rate']:.2%}|{m['log_loss']:.4f}|")
    lines += ['', '比例按完整场景等权统计；发现比例按反馈时刻的故障状态计算，不是故障事件发现率。',
              '误报比例指没有故障的观测中判为故障的比例；不是每场景实际申报次数。',
              '这些结果用于决定是否扩大训练规模，不能用于宣称最终达标或胜过固定规则。']
    (Path(output)/'REPORT.md').write_text('\n'.join(lines)+'\n')


def run(data, inputs, output, sizes, workers, threads):
    output = Path(output); output.mkdir(parents=True, exist_ok=True)
    entries = json.loads((Path(data)/'manifest.json').read_text())['entries']
    cohorts = nested_cohorts(entries, sizes)
    validation = [e for e in entries if e['split'] == 'validation']
    protocol = dict(version=VERSION, code=benchmark.sources() | {'learning_curve.py': benchmark.digest(__file__)},
        manifest_sha256=benchmark.digest(Path(data)/'manifest.json'), sizes=sorted(cohorts),
        training_ids={str(n): [e['id'] for e in group] for n, group in cohorts.items()},
        distributions={str(n): dict(Counter(str(e['fault_count']) for e in group)) for n, group in cohorts.items()},
        validation_ids=[e['id'] for e in validation], workers=workers, cpu_threads_per_worker=threads,
        parameters=PARAMETERS, final_test_accessed=False, probability_calibrated=False)
    marker = output/'protocol.json'
    if marker.exists() and json.loads(marker.read_text()) != protocol:
        raise ValueError('small-scale configuration/code changed; use a new output directory')
    save_json(marker, protocol)
    results = []
    ctx = multiprocessing.get_context('spawn')
    # The explicitly selected small sizes run together; no automatic expansion.
    with ProcessPoolExecutor(max_workers=workers, mp_context=ctx) as pool:
        jobs = []
        for n, cohort in cohorts.items():
            for kind in PARAMETERS:
                cached = output/f'scenes-{n:03}'/kind/'result.json'
                if cached.exists():
                    results.append(json.loads(cached.read_text()))
                else:
                    jobs.append(pool.submit(train_job, str(inputs), str(output), cohort, validation, kind, threads))
        save_json(output/'_state.json', dict(phase='training', training_sizes=list(cohorts), completed_jobs=len(results)))
        write_report(output, results, f'并行训练 {list(cohorts)} 个场景')
        for future in as_completed(jobs):
            result = future.result(); results.append(result)
            save_json(output/'results.json', results)
            save_json(output/'_state.json', dict(phase='training', training_sizes=list(cohorts), completed_jobs=len(results)))
            write_report(output, results, f'并行训练 {list(cohorts)} 个场景')
            print(json.dumps({k: result[k] for k in ('training_scenes', 'kind', 'training_rows', 'training_seconds', 'validation')}, ensure_ascii=False), flush=True)
    save_json(output/'results.json', results)
    save_json(output/'_state.json', dict(phase='complete', completed_jobs=len(results), final_test_accessed=False))
    write_report(output, results, '已完成小规模验证；未进行最终测试')
    return results


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', type=Path, default=Path('artifacts/instrument_faults'))
    parser.add_argument('--inputs', type=Path, default=Path('artifacts/instrument_faults-feedback'))
    parser.add_argument('--output', type=Path, default=Path('artifacts/instrument_faults-smallscale-4cpu'))
    parser.add_argument('--sizes', type=int, nargs='+', default=[20, 40])
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--threads', type=int, default=2)
    args = parser.parse_args()
    if args.workers < 1 or args.threads < 1 or args.workers*args.threads > (os.cpu_count() or 1):
        parser.error('CPU worker/thread budget exceeds available CPUs')
    run(args.data, args.inputs, args.output, args.sizes, args.workers, args.threads)

"""Additional fixed-length references and actual API timing; no tuning or training."""
from __future__ import annotations

from .tree_runtime import launch
if __name__=='__main__':launch('research.observing_conditions.tree_review')

import copy
import json
import time
from pathlib import Path

import numpy as np
import torch

from .data import DEFAULT_OUTPUT,save_json
from .improvement import load
from .predict import Predictor
from .tree_predict import TreePredictor
from .traditional import DEFAULT_TREES,KINDS,write_report
from .tree_models import ranking_view
from .metrics import metrics,scene_bootstrap
from .priority import evaluate as evaluate_priority


def run(output=DEFAULT_TREES):
    output=Path(output);torch.set_num_threads(1)
    report=json.loads((output/'report.json').read_text())
    frozen=json.loads((output/'frozen.json').read_text())
    original=Predictor(DEFAULT_OUTPUT/'models/selected')
    size=frozen['priority_rules'][frozen['selected']]['rule']['size']
    same_length=dict(size=size,block_max=1.,usable_min=0.,enabled=True)
    four=same_length | dict(size=4)
    for split,results in report['tests'].items():
        data=load(output,[split])
        p=original.numeric(original.normalizer.apply(data))
        np.save(output/'predictions'/split/'original_neural_ensemble.npy',p)
        results['original_neural_ensemble']=dict(prediction=metrics(data['y'],p),
            fixed_four=evaluate_priority(data['y'],p,data['scene'],four)[0])
        for name in (*KINDS,'neural','lookup','formula','original_neural_ensemble'):
            pred=np.load(output/'predictions'/split/f'{name}.npy')
            score_path=output/'predictions'/split/f'{name}-rank-scores.npy'
            scores=np.load(score_path) if score_path.exists() else pred[...,0]
            view=ranking_view(pred,scores)
            # Fixed matched size selected on validation, equal exposure constraints.
            # This additional reference does not affect the frozen winner or policy.
            results[name]['matched_selected_length']=evaluate_priority(data['y'],view,data['scene'],same_length)[0]
            if name in KINDS:
                results[name]['quality_vs_original_ensemble_scene_intervals']=scene_bootstrap(
                    data['y'],pred,p,data['scene'],repetitions=1000)
    predictor=TreePredictor(output/'models')
    original_request=json.loads((DEFAULT_OUTPUT/'example_request.json').read_text())
    save_json(output/'example_request.json',original_request)
    response=predictor.predict(original_request)
    save_json(output/'example_prediction.json',response)
    request=copy.deepcopy(original_request)
    request['candidates']=[dict(c,candidate_id=f'benchmark-{i}') for i,c in enumerate(request['candidates']*32)]
    first=predictor.predict(request)
    measurements=[]
    for _ in range(10):
        begin=time.perf_counter();response=predictor.predict(request)
        measurements.append(dict(total_ms=(time.perf_counter()-begin)*1000,
            preprocessing_ms=response['timing']['preprocessing_seconds']*1000,
            prediction_ms=response['timing']['prediction_seconds']*1000))
    benchmark=dict(candidates=1024,distinct_positions=32,
        eligible_candidates=sum(p['eligible'] for p in first['predictions']),
        model=response['architecture'],threads=1,repetitions=10,
        boundary='complete public request: sky/history features, all tree heads, ranking, calibration and advice; '
                 'excludes JSON decoding/model loading; empirical ten-sample p95',
        samples=measurements,p95_ms={k:float(np.quantile([m[k] for m in measurements],.95)) for k in measurements[0]})
    save_json(output/'request_benchmark.json',benchmark)
    report['matched_reference_note']=(
        'Additional references at the already frozen chosen length; '
        'not used to switch model/threshold after test. Original neural ensemble reused, not retrained.')
    report['cpu_request']=benchmark
    save_json(output/'report.json',report)
    write_report(output,report)
    lines=['','## 首轮完整神经网络参照与同长度比较','',
        '前表的neural是第二轮简短历史模型。以下补充首轮完整MLP三份已有权重的平均，未重新训练。',
        f'所有模型都保留验证阶段已选的 {size} 个候选，用相同长度比较；本表不改变冻结的选型或风险开关。',
        '', '| 集合/模型 | 质量MAE | 同长度丢失好位置率 | 同长度95%范围 |','|---|---:|---:|---:|']
    def percent(v):return '无可比组' if v is None else f'{100*v:.2f}%'
    for split,results in report['tests'].items():
        for name in (*KINDS,'neural','original_neural_ensemble','lookup','formula'):
            m=results[name];risk=m['matched_selected_length'];interval=risk['scene_95_intervals']['failure_rate']
            mae=f'{m["prediction"]["quality_mae"]:.4f}' if m['prediction'] else '不预测天气质量'
            bounds='无可比组' if interval is None else f'{100*interval[0]:.2f}%–{100*interval[1]:.2f}%'
            lines.append(f'| {split}/{name} | {mae} | {percent(risk["failure_rate"])} | {bounds} |')
    lines+=['','## 实际调用入口','',
        'tree_predict接收与原模型相同的公开请求，返回逐候选质量、可用比例、受阻/档位概率、经验残差区间及独立排序分数。',
        '所选排序模型的独立测试清单失败，交付入口不会自动缩小清单、排除或代选。可以查看排序分数和公式清单，不能把排序分数当作质量或正确概率。',
        '',f'CPU单线程1024候选（32不同位置重复）：完整入口p95 **{benchmark["p95_ms"]["total_ms"]:.2f}ms**；输入处理、全部输出树、排序、校准和建议均包含；不含JSON解码/权重加载。',
        '', '```sh','uv run --project research/observing_conditions python -m research.observing_conditions.tree_predict --input request.json --result prediction.json','```',
        '', '## 结论与限制','',
        '随机树主测试质量MAE为0.2264，首轮完整网络为0.2282，差异很小；以完整场景抽样的差异95%范围包含零，未证明质量更好。较强天气变化和新站点的质量预测由首轮完整网络领先。',
        '提升树在三个测试集合的受阻概率误差低于首轮完整网络，但有效时间预测未全面改善。新站点的质量区间覆盖率随机树81.9%、提升树79.0%，不能作为高把握排除的依据。',
        '排序树在固定同长度清单中有价值，主/较强天气测试保留一个位置均未丢失好位置，新站点为1.03%，超过预先设置1%要求；选择冻结不因测试结果改选普通提升树。公开选取最低高度8个候选、同样保留4个时，新站点丢失率为公式8.33%、普通提升树3.07%、排序树5.70%，这是预先约定分项结果，不能推广为所有情况均优于公式。',
        '主测试16场景、外部天气和站点各8场景。未观测到错误时场景重复抽样范围会是零至零，这不证明零风险。未测实际智能体任务完成率或节省的真实耗时；不是正式比赛成绩。',
        'macOS入口通过tree_runtime在进程启动时让PyTorch与LightGBM使用同一运行库；没有改安装文件或重新训练。背景见[LightGBM官方说明](https://lightgbm.readthedocs.io/en/stable/FAQ.html)。']
    with (output/'report.md').open('a') as f:f.write('\n'.join(lines)+'\n')
    print('TREE REVIEW COMPLETE '+json.dumps(benchmark['p95_ms']),flush=True)


if __name__=='__main__':
    run()

from __future__ import annotations

import argparse
import json
import platform
import shutil
import sys
from pathlib import Path

import numpy as np
import torch

from .common import DEFAULT, ROOT, save, digest, verify_engine, source_hashes
from .data import micro_entries, formal_entries, build_micro
from .rollout import episode
from .training import CONFIG, fit, load_checkpoint, evaluate, Policy
from .gates import compare, require_stage
from .benchmark import model_benchmark, worker_benchmark


def immediate(frame):
    return max(range(len(frame["candidates"])), key=lambda i: frame["candidates"][i]["features"]["expected_science"]), {}


def protocol(output):
    path=output/"protocol.json"
    value=dict(schema="global-planning-experiment-v1", objective="official final total only", gamma=1,
        near_term_prediction=False, training=CONFIG, engine_manifest=verify_engine(),
        sources=source_hashes(), baseline_source=digest(ROOT/'agents/global-planner-v1/BASELINE_SOURCE.json'),
        micro=micro_entries(), formal=formal_entries(), final_test_access="after selection freeze only",
        gates=dict(promotion_mean=.01, max_card_regression=.01, max_stress_regression=.02, initializations=[101,202,303]),
        provenance="Formal public inputs or SELF-GENERATED only; no practice cards",
        runtime=dict(python=sys.version,torch=torch.__version__,numpy=np.__version__,platform=platform.platform()),
        paired_models="frozen conditions-reliability and instrument-fault-report; enabled for all experiment arms")
    if path.exists():
        old=json.loads(path.read_text())
        # Code identity is part of the experiment; do not silently mix changed trajectories.
        if old!=value:raise ValueError('Experiment source/config changed; choose a fresh --out')
    else:save(path,value)
    return value


def reference(entries, scenarios, output, *, policy=None, node=None, key="teacher"):
    traces=[];rows=[]
    for entry in entries:
        result=episode(scenarios/entry['id'],output/entry['id'],policy,node=node,models=True,
                       reuse_key=digest(dict(entry=entry,policy=key)))
        result['entry']=entry;traces.append(result)
        rows.append(dict(id=entry['id'],kind=entry['kind'],card=entry['card'],total=result['total'],
                         completion=result['termination']['reason'],wall_seconds=result['wall_seconds']))
        print(json.dumps(dict(phase=key,scene=entry['id'],total=result['total'],choices=len(result['choices']))),flush=True)
    save(output/'summary.json',dict(rows=rows,mean=float(np.mean([r['total'] for r in rows]))))
    return traces,rows


def value_audit(training, evaluation_dir, entries):
    # A time-only reference fitted exclusively on teacher training scenes.
    rows=[r for t in training for r in t['choices']]
    x=np.array([[1,r['frame']['global']['total_time_fraction_left']] for r in rows])
    y=np.array([r['return'] for r in rows])
    coefficient=np.linalg.lstsq(x,y,rcond=None)[0]
    network=[];reference=[]
    for entry in entries:
        trace=json.loads((evaluation_dir/entry['id']/'episode.json').read_text())
        for r in trace['choices']:
            network.append(abs(r['value']-r['return']))
            reference.append(abs(float(np.dot([1,r['frame']['global']['total_time_fraction_left']],coefficient))-r['return']))
    return dict(network_mae=float(np.mean(network)),time_only_mae=float(np.mean(reference)),
        label='Actual remaining return under the evaluated policy; not an optimal-value label',
        reference_fit='Teacher training trajectories only',value_can_be_negative=True)


def run_micro(output, node=None):
    specification=protocol(output)
    from .acceptance import official_run
    official_run(ROOT/'evaluation_assets/formal-v4-observed-timeline-v4/cards/a',output/'stage0/formal-a-configured',node=node)
    entries=micro_entries();scenarios=output/'scenarios'
    train=[e for e in entries if e['split']=='train'];validation=[e for e in entries if e['split']=='validation']
    # Do not materialize held-out test scenarios while choosing architecture/checkpoints.
    for entry in train+validation:build_micro(entry,scenarios/entry['id'])
    device=model_benchmark(output/'stage0')
    worker_benchmark(scenarios,train,output/'stage0/workers',node)
    teacher,teacher_rows=reference(train,scenarios,output/'teacher/train',node=node)
    _,reference_rows=reference(validation,scenarios,output/'teacher/validation',node=node)
    _,greedy_rows=reference(validation,scenarios,output/'immediate/validation',node=node,policy=immediate,key='immediate')
    candidates=[]
    for architecture in ('gru','mlp'):
        checkpoint=fit(architecture,teacher,validation,scenarios,output/architecture,node=node,device=device)
        model,_,normalizer,state=load_checkpoint(checkpoint,device)
        rows=evaluate(model,normalizer,validation,scenarios,output/architecture/'selected-validation',node=node,key=digest(checkpoint))
        candidates.append(dict(architecture=architecture,checkpoint=str(checkpoint),rows=rows,
            comparison_rule=compare(rows,reference_rows),comparison_immediate=compare(rows,greedy_rows),
            best_phase=state['best_phase'],value=value_audit(teacher,output/architecture/'selected-validation',validation)))
    # Prefer the ordered-history architecture on a numerical tie.
    selected=max(candidates,key=lambda c:np.mean([r['total'] for r in c['rows']]))
    frozen=dict(schema='global-planning-selection-v1',architecture=selected['architecture'],
        checkpoint=selected['checkpoint'],checkpoint_sha256=digest(Path(selected['checkpoint'])),
        protocol_sha256=digest(output/'protocol.json'),selected_on='validation only',candidates=candidates)
    freeze_path=output/'frozen.json'
    if freeze_path.exists() and json.loads(freeze_path.read_text())!=frozen:raise ValueError('Frozen selection changed')
    save(freeze_path,frozen)
    test=[e for e in entries if e['split']=='test']
    for entry in test:build_micro(entry,scenarios/entry['id'])
    _,test_rule=reference(test,scenarios,output/'teacher/test',node=node)
    _,test_greedy=reference(test,scenarios,output/'immediate/test',node=node,policy=immediate,key='immediate')
    model,_,normalizer,_=load_checkpoint(selected['checkpoint'],device)
    test_rows=evaluate(model,normalizer,test,scenarios,output/'selected-test',node=node,key=frozen['checkpoint_sha256'])
    rule_comparison=compare(test_rows,test_rule);greedy_comparison=compare(test_rows,test_greedy)
    delayed_ids={e['id'] for e in test if e['kind'] in ('deadline','request_group','delayed_request')}
    greedy_by_id={r['id']:r for r in test_greedy}
    delayed_wins=sum(r['total']>greedy_by_id[r['id']]['total']+1e-6 for r in test_rows if r['id'] in delayed_ids)
    from .oracle import capability
    exact=capability(model,normalizer,output/'capability',node=node)
    # Micro gate checks capability, not the 1% formal-card promotion gate.
    passed=bool(greedy_comparison['complete'] and greedy_comparison['mean_difference']>0 and delayed_wins>0
                and rule_comparison['relative_improvement']>=-.01 and exact['passed'])
    result=dict(stage=1,passed=passed,selected_architecture=selected['architecture'],best_phase=selected['best_phase'],
        comparison_rule=rule_comparison,comparison_immediate=greedy_comparison,delayed_wins=delayed_wins,exact_capability=exact,
        value=value_audit(teacher,output/'selected-test',test),
        limitations=['Micro is self-generated and much shorter than formal cards',
            'Final formal-card and stress holdouts have not been generated or read',
            'Passing this check alone does not establish long-horizon planning or deployment readiness'])
    save(output/'stage1-result.json',result)
    save(output/'stages.json',dict(gates={'1':result},next_stage=2 if passed else None,
        status='ready_for_stage2' if passed else 'stopped_at_stage1_gate',
        remaining=['full-horizon training','expanded region/time choices','target choices','search','Linux final acceptance']))
    render_report(output,result)
    return result


def render_report(output,result):
    lines=['# 全局规划第一轮实测','',f"阶段 1：{'通过能力门槛' if result['passed'] else '未通过能力门槛；未扩大训练'}。",
           f"选定结构：{result['selected_architecture']}；选定训练阶段：{result['best_phase']}。",'',
           '| 对照 | 平均分差 | 相对变化 | 场景配对 95% 区间 |','|---|---:|---:|---|']
    for name,key in [('现有规则','comparison_rule'),('只看眼前科学分','comparison_immediate')]:
        r=result[key];lines.append(f"| {name} | {r['mean_difference']:.6f} | {r['relative_improvement']:.2%} | {r['interval_95']} |")
    lines += ['',f"延期收益类场景超过眼前规则的数量：{result['delayed_wins']}。",'',
              '所有结果来自自行生成的小场景和原始官方评分程序，不代表正式赛程或线上表现。',
              '训练曲线、逐次动作、公开反馈、奖励、最终分项和错误均保存在同目录。',
              '后续层次受本层验收门槛控制；未通过时不训练更大模型，不替换现有智能体。']
    (output/'REPORT.md').write_text('\n'.join(lines)+'\n')


def main():
    parser=argparse.ArgumentParser(description='Local global-planning staged research')
    parser.add_argument('command',choices=['micro','status'])
    parser.add_argument('--out',type=Path,default=DEFAULT)
    parser.add_argument('--node',default=None)
    args=parser.parse_args()
    if args.command=='status':
        print((args.out/'stages.json').read_text());return
    result=run_micro(args.out,args.node)
    print(json.dumps(result,ensure_ascii=False),flush=True)


if __name__=='__main__':main()

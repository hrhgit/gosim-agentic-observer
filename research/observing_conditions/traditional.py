"""One-initialization tree comparison with public features and new scene tests."""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

import lightgbm
import numpy as np
import sklearn
import torch

from .data import ROOT,DEFAULT_OUTPUT,save_json,source_hashes,audit_dataset
from .improvement import DEFAULT_IMPROVEMENT,load,concatenate,generate,digest,restore,predict_pair,apply_calibration as neural_calibration
from .metrics import metrics,StatisticalBaseline,scene_bootstrap
from .priority import select as select_priority,evaluate as evaluate_priority
from .assistance import select_rules,evaluate as evaluate_assistance
from .tree_models import (FEATURE_VERSION,TREE_SETTINGS,TreeBundle,public_features,
    fit_extra,fit_boosted,fit_ranker,fit_calibration,apply_calibration,ranking_view)

DEFAULT_TREES=ROOT/'artifacts/observing_conditions-trees'
KINDS=('extra_trees','lightgbm','lightgbm_ranker')


def entries():
    result=[]
    for ci,card in enumerate('abcd'):
        for split,count,offset in (('test',4,0),('weather_test',2,100),('site_test',2,200)):
            for index in range(count):
                entry=dict(id=f'tree-{card}-{split}-{index}',card=card,split=split,seed=940000+ci*1000+offset+index)
                if split=='site_test':entry['site_index']=index
                result.append(entry)
    return result


def protocol(base,previous,output):
    prior=[json.loads((p/'manifest.json').read_text()) for p in (base,previous,previous/'priority')]
    old=[e for m in prior for e in m['entries']];new=entries()
    assert not {e['seed'] for e in old}&{e['seed'] for e in new}
    p=dict(feature_version=FEATURE_VERSION,source='formal public A-D plus self-generated weather; NOT official performance',
        training_subset='8 evenly spaced groups per 224 original training scenes plus 16 per 32 augmentation scenes; 73728 rows',
        input='same public information as neural models; history summaries and coarse-direction proximity derived numerically',
        neural_reference='reuse second-round compact weights and calibration without retraining; budgets differ',
        new_entries=new,groups_per_scene=64,initialization_repetitions=1,seed=101,
        families=list(KINDS),extra_trees_settings=dict(trees=64,max_depth=16,min_samples_leaf=32,max_features=.4),
        lightgbm_settings=TREE_SETTINGS,fit_calls=6,
        training_note='One multi-output ExtraTrees fit; one LightGBM fit each for Q,U,blocked,band; one ranker fit. '
                      'Different output heads are not repeated seeds; no grid search or stopping limits.',
        selection='validation only: risk-gated priority reduction first, validated loss rate second, '
                  'Q MAE third; independent separate calibration reused; rank scores never treated as Q',
        risk='Priority: validation loss point and scene upper <=1%; independent test point<=1%, upper<=2%. '
             'Strict exclusion and direct choice use existing validation-risk rules.',
        hard_slice='Also check publicly selected eight lowest start-altitude candidates in each group, with fixed top four; '
                   'do not choose hard cases by truth or retune using this slice.',
        source_hashes=source_hashes(),base_manifest_sha256=digest(base/'manifest.json'),
        previous_freeze_sha256=digest(previous/'frozen.json'),
        dependency_versions=dict(lightgbm=lightgbm.__version__,sklearn=sklearn.__version__),
        research_code_sha256={str(p.relative_to(ROOT)):digest(p) for p in
            (Path(__file__),Path(__file__).with_name('tree_models.py'))},
        documentation=['https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.ExtraTreesRegressor.html',
            'https://lightgbm.readthedocs.io/en/stable/pythonapi/lightgbm.LGBMRanker.html'],
        limitations='Single-seed exploratory models; shared validation reused from earlier rounds; '
                   'no actual agent task/time/official competition validation.')
    path=output/'protocol.json'
    if path.exists() and json.loads(path.read_text())!=p:raise ValueError('tree protocol changed; use a new output directory')
    save_json(path,p)
    manifest=dict(prior[1]);manifest.update(entries=new,source=p['source'],groups_per_scene=64)
    save_json(output/'manifest.json',manifest)
    return p


def low_altitude(data,p,scores=None):
    ix=np.argsort(data['candidate'][...,0],axis=1,kind='stable')[:,:8]
    sub=dict(data)
    for key in ('candidate','y'):
        sub[key]=np.take_along_axis(data[key],ix[...,None],axis=1)
    subp=np.take_along_axis(p,ix[...,None],axis=1)
    subscores=np.take_along_axis(scores,ix,axis=1) if scores is not None else None
    return sub,subp,subscores


def safety(results):
    def priority_ok(m):
        return (m['rankable_shortlisted_groups']>0 and m['failure_rate']<=.01
            and m['scene_95_intervals']['failure_rate'][1]<=.02)
    def exclusion_ok(m):
        b=m['scene_95_intervals']['blocked_false_exclusion_rate']
        return m['blocked_excluded']>0 and m['blocked_false_exclusion_rate']<=.01 and b and b[1]<=.02
    def recommend_ok(m):
        b=m['scene_95_intervals']['recommendation_failure_rate']
        return m['recommendation_groups']>=100 and m['recommendation_failure_rate']<=.01 and b and b[1]<=.02
    return dict(priority_shortlist=all(priority_ok(r['priority']) for r in results.values()),
        strict_exclusion=all(exclusion_ok(r['assistance']) for r in results.values()),
        direct_recommendation=all(recommend_ok(r['assistance']) for r in results.values()))


def run(base=DEFAULT_OUTPUT,previous=DEFAULT_IMPROVEMENT,output=DEFAULT_TREES):
    base,previous,output=Path(base),Path(previous),Path(output);output.mkdir(parents=True,exist_ok=True)
    started=time.perf_counter();p=protocol(base,previous,output);torch.set_num_threads(1)
    train=concatenate(load(base,['train'],8),load(previous,['train'],16))
    validation=load(base,['validation']);cal=load(base,['calibration'])
    assert train['candidate'].shape[0]*train['candidate'].shape[1]==73728
    begin=time.perf_counter();x=public_features(train)
    mask=np.ptp(x,axis=0)>1e-8
    models=output/'models';models.mkdir(exist_ok=True)
    np.save(models/'feature_mask.npy',mask)
    identity=hashlib.sha256(train['scene'].tobytes()+train['group_index'].tobytes()+mask.tobytes()).hexdigest()
    save_json(models/'metadata.json',dict(feature_version=FEATURE_VERSION,total_features=x.shape[1],
        used_features=int(mask.sum()),training_rows=len(x),training_identity=identity,protocol_sha256=digest(output/'protocol.json')))
    extra=fit_extra(models/'extra_trees',x,train,mask,identity)
    print('ExtraTrees fit complete',flush=True)
    boosted=fit_boosted(models/'lightgbm',x,train,mask,identity)
    ranker=fit_ranker(models/'lightgbm_ranker',x,train,mask,identity)
    bundle=TreeBundle(mask,extra,boosted,ranker)
    training_wall=time.perf_counter()-begin
    frozen_previous=json.loads((previous/'frozen.json').read_text())
    nn_mode=frozen_previous['selected']
    neural=restore(previous/'models'/nn_mode)
    saved=json.loads((base/'models/lookup.json').read_text());lookup=StatisticalBaseline()
    lookup.constant=saved['constant'];lookup.global_values=np.array(saved['global'])
    lookup.table={int(k):np.array(v) for k,v in saved['table'].items()}
    calibration={};validation_results={};priority_rules={};assistance_rules={}
    for kind in KINDS:
        raw,_=bundle.predict(cal,kind)
        calibration[kind]=fit_calibration(cal['y'],raw,cal)
        raw,rank=bundle.predict(validation,kind);pred=apply_calibration(raw,validation,calibration[kind])
        priority_rules[kind]=select_priority(validation['y'],ranking_view(pred,rank),validation['scene'])
        assistance_rules[kind]=select_rules(validation['y'],pred,validation['scene'])
        validation_results[kind]=dict(prediction=metrics(validation['y'],pred),
            priority=priority_rules[kind]['validation'],assistance=assistance_rules[kind]['validation'])
        print('validation '+kind+' MAE='+str(validation_results[kind]['prediction']['quality_mae'])+
              ' reduction='+str(validation_results[kind]['priority']['comparison_reduction']),flush=True)
    selected=min(KINDS,key=lambda kind:(-validation_results[kind]['priority']['comparison_reduction'],
        validation_results[kind]['priority']['failure_rate'] if validation_results[kind]['priority']['failure_rate'] is not None else 1.,
        validation_results[kind]['prediction']['quality_mae']))
    frozen=dict(selected=selected,protocol_sha256=digest(output/'protocol.json'),calibration=calibration,
        priority_rules=priority_rules,assistance_rules=assistance_rules,validation=validation_results,
        artifact_hashes={str(f.relative_to(models)):digest(f) for f in models.rglob('*') if f.is_file()})
    freeze=output/'frozen.json'
    if freeze.exists() and json.loads(freeze.read_text())!=frozen:raise ValueError('cannot revise frozen tree selection')
    save_json(freeze,frozen)
    print('FROZEN '+selected,flush=True)
    generation_seconds=generate(output,p['new_entries'],4)
    report=dict(selected=selected,validation=validation_results,tests={},
        timing=dict(training_invocation_wall_seconds=training_wall,generation_invocation_seconds=generation_seconds),
        protocol_sha256=digest(output/'protocol.json'),freeze_sha256=digest(freeze))
    fixed_four=dict(size=4,block_max=1.,usable_min=0.,enabled=True)
    for split in ('test','weather_test','site_test'):
        data=load(output,[split]);results={};predictions={}
        for kind in KINDS:
            raw,rank=bundle.predict(data,kind);pred=apply_calibration(raw,data,calibration[kind])
            view=ranking_view(pred,rank)
            priority,decisions=evaluate_priority(data['y'],view,data['scene'],priority_rules[kind]['rule'])
            assistance,_,_=evaluate_assistance(data['y'],pred,data['scene'],assistance_rules[kind]['rule'])
            low,lowp,lowrank=low_altitude(data,pred,rank)
            low_result,_=evaluate_priority(low['y'],ranking_view(lowp,lowrank),low['scene'],fixed_four)
            results[kind]=dict(prediction=metrics(data['y'],pred),priority=priority,assistance=assistance,
                lowest_eight_fixed_four=low_result,
                fixed_four=evaluate_priority(data['y'],view,data['scene'],fixed_four)[0])
            results[kind]['by_card']={str(c):dict(prediction=metrics(data['y'][data['card']==c],pred[data['card']==c]),
                priority=evaluate_priority(data['y'][data['card']==c],view[data['card']==c],data['scene'][data['card']==c],priority_rules[kind]['rule'],False)[0]) for c in np.unique(data['card'])}
            directory=output/'predictions'/split;directory.mkdir(parents=True,exist_ok=True)
            np.save(directory/f'{kind}.npy',pred)
            if rank is not None:np.save(directory/f'{kind}-rank-scores.npy',rank)
            np.savez_compressed(directory/f'{kind}-priority.npz',scene=data['scene'],**decisions)
            predictions[kind]=pred
        nn=neural_calibration(data,predict_pair(neural,data,nn_mode),frozen_previous['calibration'])
        lp=neural_calibration(data,lookup.predict(data),frozen_previous['lookup_calibration'])
        gp=np.zeros_like(nn);gp[...,:4]=data['candidate'][...,28,None];gp[...,4]=1.;gp[...,6]=1.
        for name,pred in (('neural',nn),('lookup',lp),('formula',gp)):
            low,lowp,_=low_altitude(data,pred)
            results[name]=dict(prediction=metrics(data['y'],pred) if name!='formula' else None,
                fixed_four=evaluate_priority(data['y'],pred,data['scene'],fixed_four)[0],
                lowest_eight_fixed_four=evaluate_priority(low['y'],lowp,low['scene'],fixed_four)[0])
            np.save(output/'predictions'/split/f'{name}.npy',pred)
        report['tests'][split]=results
        report['tests'][split]['selected_vs_neural_scene_intervals']=scene_bootstrap(data['y'],predictions[selected],nn,data['scene'],repetitions=1000)
        save_json(output/'report.json',report)
        print('test complete '+split,flush=True)
    acceptance={kind:safety({s:r[kind] for s,r in report['tests'].items()}) for kind in KINDS}
    save_json(models/'policy.json',dict(selected=selected,calibration=calibration[selected],
        evaluated_assistance_rule=assistance_rules[selected]['rule'],
        assistance_rule=assistance_rules[selected]['rule'] | dict(exclude_enabled=acceptance[selected]['strict_exclusion'],
            recommend_enabled=acceptance[selected]['direct_recommendation']),
        priority_rule=priority_rules[selected]['rule'],priority_test_passed=acceptance[selected]['priority_shortlist'],
        assistance_acceptance=acceptance[selected],assistance_test_passed=all(acceptance[selected].values()),
        automatic_actions_enabled=False,source=p['source'],freeze_sha256=digest(freeze),
        validation_evidence=validation_results[selected],ranking_score_is_quality=False))
    report['acceptance']=acceptance;report['completed']=True
    report['timing']['training_cumulative_seconds']=sum(json.loads((models/name/'complete.json').read_text())['elapsed_seconds']
        for name in ('extra_trees','lightgbm','lightgbm_ranker'))
    report['timing']['total_invocation_wall_seconds']=time.perf_counter()-started
    assert source_hashes()==p['source_hashes']
    save_json(output/'report.json',report)
    audit_dataset(output,count=0)
    write_report(output,report)
    print('COMPLETE '+json.dumps(dict(selected=selected,acceptance=acceptance,timing=report['timing'])),flush=True)


def write_report(output,report):
    lines=['# 非神经网络观测条件模型比较','',
        '来源：正式A–D公开输入和自行生成天气。复用训练/验证/校准，规则冻结后生成新的32个场景、65536条候选记录。未接入智能体、没有官方比赛成绩。',
        '固定种子101，不做不同种子重复训练或参数搜索。ExtraTrees一份多输出模型；LightGBM分别训练质量、有效比例、受阻、档位四个不同输出；另训练一次排序树。共6次独立输出拟合，不是6次初始化重复。',
        '73728训练记录，来自原224场景各8组及新增32训练场景各16组。三个方案用同一公开信息；方向邻近和历史摘要是数值变换，不读取真实天气、编号、未来公告或观测反馈。',
        '',f'按验证集规则选定：**{report["selected"]}**。风险合格时优先比较候选数量减少，其次比较丢失率和质量误差；最终测试不调参。',
        '', '## 新场景的预测信息','',
        '| 集合/模型 | 质量MAE | 有效比例MAE | 受阻概率误差 | 档位F1 | 90%区间覆盖 |','|---|---:|---:|---:|---:|---:|']
    for split,models in report['tests'].items():
        for name in (*KINDS,'neural','lookup'):
            m=models[name]['prediction']
            lines.append(f'| {split}/{name} | {m["quality_mae"]:.4f} | {m["usable_mae"]:.4f} | {m["blocked_brier"]:.4f} | {m["band_macro_f1"]:.3f} | {100*m["interval_coverage"]:.1f}% |')
    lines+=['','## 同样保留4个候选的比较','',
        '好位置：有效时间≥50%、质量≥同组最好95%。只缩小相同开始时间、曝光长度且其他目标/任务约束已筛选的候选。',
        '| 集合/模型 | 全32候选中丢失好位置 | 最低高度8候选中丢失好位置 | 全32候选清单质量损失 |',
        '|---|---:|---:|---:|']
    def pct(value):return '无可比组' if value is None else f'{100*value:.2f}%'
    for split,models in report['tests'].items():
        for name in (*KINDS,'neural','lookup','formula'):
            m=models[name]
            lines.append(f'| {split}/{name} | {pct(m["fixed_four"]["failure_rate"])} | {pct(m["lowest_eight_fixed_four"]["failure_rate"])} | {pct(m["fixed_four"]["shortlist_quality_loss"])} |')
    lines+=['','“最低高度8候选”只按公开开始高度筛选，不用答案挑选；它是诊断子集，并非正式比赛分布。',
        '排序树的分数只比较顺序，不当作Q或置信度；其质量/概率由同一LightGBM数值模型提供。',
        '预测区间来自独立校准残差与区间校准，q50是点预测加校准残差中位数，不能保证逐条件覆盖；不是树间差异。',
        '', '## 能力检查','', '```json',json.dumps(report['acceptance'],indent=2),'```',
        '未通过的排除或单位置推荐在交付入口关闭。优先清单也按独立测试检查；自动执行始终关闭。完整概率、数值、场景抽样95%范围、卡片分项和清单掩码在report.json/predictions。',
        '', '## 耗时','', '```json',json.dumps(report['timing'],indent=2),'```',
        '原神经网络权重直接复用，训练预算不同，本轮不是严格隔离模型架构的因果比较。候选比较减少不等于实际智能体耗时或任务成绩提高。',
        '', '## 重跑','', '```sh',
        'uv run --project research/observing_conditions python -m research.observing_conditions.traditional --output artifacts/observing_conditions-trees-rerun','```',
        '', '结构说明：[ExtraTrees官方文档](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.ExtraTreesRegressor.html)、[LightGBM排序官方文档](https://lightgbm.readthedocs.io/en/stable/pythonapi/lightgbm.LGBMRanker.html)。']
    (output/'report.md').write_text('\n'.join(lines)+'\n')


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--base',type=Path,default=DEFAULT_OUTPUT)
    parser.add_argument('--previous',type=Path,default=DEFAULT_IMPROVEMENT)
    parser.add_argument('--output',type=Path,default=DEFAULT_TREES)
    args=parser.parse_args();run(args.base,args.previous,args.output)

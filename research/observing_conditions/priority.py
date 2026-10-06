"""A second, separately frozen evaluation of shorter condition-only priority lists.

This rule is additional to strict exclusion; it is NOT a claim that all other
positions are unusable or less scientifically valuable.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np

from .data import DEFAULT_OUTPUT, save_json, source_hashes
from .metrics import StatisticalBaseline
from .improvement import (DEFAULT_IMPROVEMENT, load, restore, predict_pair,
                          apply_calibration, generate, digest)


def priority_mask(p,rule):
    n,c,_=p.shape;order=np.argsort(-p[...,0],axis=1,kind='stable')
    top=order[:,0];rows=np.arange(n)
    active=((c>rule['size']) & (p[rows,top,5]<=rule['block_max'])
            & (p[rows,top,4]>=rule['usable_min']) & (p[rows,top,0]>1e-8))
    if not rule.get('enabled',True):
        active[:]=False
    kept=np.ones((n,c),bool)
    kept[active]=False
    ix=np.flatnonzero(active)
    kept[ix[:,None],order[ix,:rule['size']]]=True
    return kept,active,order


def group_stats(y,p,rule):
    kept,active,order=priority_mask(p,rule)
    q=y[...,0];u=y[...,1];best=q.max(axis=1)
    near=(u>=.5)&(q>=.95*best[:,None])&(best[:,None]>1e-8)
    available=near.any(axis=1)
    missed=active&available&~(kept&near).any(axis=1)
    left=kept.sum(axis=1)
    selected_best=np.where(kept,q,-np.inf).max(axis=1)
    stats=np.column_stack([np.ones(len(q)),active,active&available,missed,
        q.shape[1]-left,np.full(len(q),q.shape[1]),best-selected_best,best,
        active&~available,(best>1e-8)&~(kept&(q>=best[:,None]-1e-6)).any(axis=1)])
    return stats,kept,active,order


def aggregate(stats):
    s=stats.sum(axis=0)
    return dict(groups=int(s[0]),shortlisted_groups=int(s[1]),
        shortlisted_fraction=float(s[1]/s[0]),rankable_shortlisted_groups=int(s[2]),
        missed_near_best=int(s[3]),failure_rate=float(s[3]/s[2]) if s[2] else None,
        saved_comparisons=int(s[4]),comparison_reduction=float(s[4]/s[5]),
        mean_priority_list_size=float((s[5]-s[4])/s[0]),
        shortlist_quality_loss=float(s[6]/s[7]) if s[7] else None,
        shortlisted_without_usable_near_best=int(s[8]),best_lost_groups=int(s[9]))


def evaluate(y,p,scenes,rule,bootstrap=True):
    stats,kept,active,order=group_stats(y,p,rule)
    result=aggregate(stats)
    result['active_scenes']=len(np.unique(scenes[active]))
    if bootstrap:
        names=np.unique(scenes);totals=np.array([stats[scenes==s].sum(axis=0) for s in names])
        rng=np.random.default_rng(101);draws=[]
        for _ in range(1000):
            indices=rng.integers(len(names),size=len(names))
            draws.append(aggregate(totals[indices]))
        result['scene_95_intervals']={key:[float(np.quantile(v,.025)),float(np.quantile(v,.975))] if v else None
            for key in ('failure_rate','comparison_reduction','shortlist_quality_loss')
            for v in [[d[key] for d in draws if d[key] is not None]]}
    return result,dict(kept=kept,active=active,order=order,stats=stats)


def select(y,p,scenes):
    candidates=[]
    for size in (1,2,4,8,16):
        for block,usable in ((.05,.9),(.1,.75),(1.,0.)):
            rule=dict(size=size,block_max=block,usable_min=usable,enabled=True)
            m,_=evaluate(y,p,scenes,rule,False)
            if m['rankable_shortlisted_groups']>=100 and m['active_scenes']>=8 and m['failure_rate']<=.01:
                candidates.append((m['comparison_reduction'],rule,m))
    selected=dict(size=32,block_max=0.,usable_min=1.,enabled=False)
    for _,rule,_ in sorted(candidates,key=lambda r:r[0],reverse=True):
        m,_=evaluate(y,p,scenes,rule)
        upper=m['scene_95_intervals']['failure_rate'][1]
        if upper<=.01:
            selected=rule;break
    measured,_=evaluate(y,p,scenes,selected)
    return dict(rule=selected,validation=measured,candidates=[dict(rule=r,metrics=m) for _,r,m in candidates])


def run(base=DEFAULT_OUTPUT,output=DEFAULT_IMPROVEMENT):
    base,output=Path(base),Path(output);directory=output/'priority';directory.mkdir(parents=True,exist_ok=True)
    frozen=json.loads((output/'frozen.json').read_text());mode=frozen['selected']
    if mode=='reference':
        from .models import load_model,Normalizer
        pair=(load_model(base/'models/mlp-101')[0],Normalizer.load(base/'models/mlp-101/normalizer.npz'))
    else:
        pair=restore(output/'models'/mode)
    val=load(base,['validation'])
    predictions=apply_calibration(val,predict_pair(pair,val,mode),frozen['calibration'])
    selected=select(val['y'],predictions,val['scene'])
    new=[]
    for ci,card in enumerate('abcd'):
        for split,count in (('test',4),('weather_test',2),('site_test',2)):
            offset={'test':0,'weather_test':100,'site_test':200}[split]
            for i in range(count):
                entry=dict(id=f'priority-{card}-{split}-{i}',card=card,split=split,seed=910000+ci*1000+offset+i)
                if split=='site_test':entry['site_index']=i%2
                new.append(entry)
    before=json.loads((base/'manifest.json').read_text())['entries']+json.loads((output/'manifest.json').read_text())['entries']
    assert not {e['seed'] for e in before}&{e['seed'] for e in new}
    protocol=dict(base_freeze_sha256=digest(output/'frozen.json'),source='self-generated; NOT official weather',
        selected=selected,entries=new,groups_per_scene=64,
        rule_selection='validation only; >=100 active rankable groups, >=8 scenes; '
            'near-best loss point rate and scene-bootstrap upper <=1%; maximize comparison reduction',
        final_test_scope='32 additional independent scenes (16 main, 8 weather, 8 sites), '
            'distinct from both first and second rounds; no retraining or test-based threshold changes',
        semantics='Priority list only, not proof that outside positions are unusable; '
            'valid only among goal-equivalent candidates with equal start/exposure. '
            'Cannot replace agent consideration of target values, requests or slew costs.')
    path=directory/'frozen.json'
    if path.exists() and json.loads(path.read_text())!=protocol:
        raise ValueError('priority protocol changed after freeze')
    save_json(path,protocol)
    manifest=json.loads((output/'manifest.json').read_text())
    manifest['entries']=new;manifest['source']=protocol['source'];save_json(directory/'manifest.json',manifest)
    seconds=generate(directory,new,4)
    result=dict(protocol=protocol,timing={'generation_seconds':seconds},tests={})
    saved=json.loads((base/'models/lookup.json').read_text())
    lookup=StatisticalBaseline();lookup.constant=saved['constant'];lookup.global_values=np.array(saved['global'])
    lookup.table={int(k):np.array(v) for k,v in saved['table'].items()}
    for split in ('test','weather_test','site_test'):
        data=load(directory,[split]);p=apply_calibration(data,predict_pair(pair,data,mode),frozen['calibration'])
        measured,decisions=evaluate(data['y'],p,data['scene'],selected['rule'])
        measured['by_card']={str(c):evaluate(data['y'][data['card']==c],p[data['card']==c],data['scene'][data['card']==c],selected['rule'],False)[0] for c in np.unique(data['card'])}
        result['tests'][split]=measured
        geometry_predictions=np.zeros_like(p)
        geometry_predictions[...,:4]=data['candidate'][...,28,None]
        geometry_predictions[...,4]=1.;geometry_predictions[...,6]=1.
        lookup_predictions=apply_calibration(data,lookup.predict(data),frozen['lookup_calibration'])
        measured['fixed_rule_references']={
            'geometry_only':evaluate(data['y'],geometry_predictions,data['scene'],selected['rule'])[0],
            'lookup':evaluate(data['y'],lookup_predictions,data['scene'],selected['rule'])[0]}
        np.savez_compressed(directory/f'{split}-decisions.npz',scene=data['scene'],predictions=p,**decisions)
    result['empirical_test_passed']=all(m['failure_rate'] is not None and m['failure_rate']<=.01
        and m['scene_95_intervals']['failure_rate'][1]<=.02 for m in result['tests'].values())
    result['limitation']=('Empirical scene-held-out evidence only; 8 shifted/site scenes each. '
        'Zero observed errors do not establish zero risk. No end-to-end agent time was measured.')
    save_json(directory/'report.json',result)
    lines=['# 优先比较清单：单独冻结和独立检验','',
        '不重新训练。使用第二轮验证集选定的公开输入模型，给出较短的优先比较清单。清单外的位置不因此被称为不可用。',
        '仅比较相同开始时间、曝光长度且目标价值与任务约束已由智能体筛选过的候选。',
        '',f'冻结规则：`{json.dumps(selected["rule"])}`','',
        '| 集合 | 优先清单组数 | 好位置全部丢失 | 比例 | 比较数量减少 | 平均清单长度 |',
        '|---|---:|---:|---:|---:|---:|']
    for split,m in [('validation',selected['validation']),*result['tests'].items()]:
        rate='无决策' if m['failure_rate'] is None else f'{100*m["failure_rate"]:.2f}%'
        lines.append(f'| {split} | {m["shortlisted_groups"]}/{m["groups"]} | {m["missed_near_best"]}/{m["rankable_shortlisted_groups"]} | {rate} | {100*m["comparison_reduction"]:.1f}% | {m["mean_priority_list_size"]:.1f} |')
    lines+=['',f'预先规定的独立测试风险检查：{"通过" if result["empirical_test_passed"] else "未通过"}。未依据这些测试修改规则。',
        '“好位置”指可用时间≥50%、质量≥同组最好95%。统计丢失率的分母仅为原本存在好位置且进行了缩小的组。',
        '潜在比较数量减少不等于真实智能体耗时减少。场景抽样区间和没有可用好位置的组数在 report.json；零错误不代表零风险。',
        '所有32个场景独立于此前使用过的场景；天气参数变化和新站点各仅8个场景，结论仍有限。']
    lines+=['','## 相同清单规则的参照','',
        '以下参照只用于比较同样清单长度的收益，没有用测试选择长度或调阈值；附加比较不改变选定模型。','',
        '| 集合 | 模型丢失好位置率 | 只按已知几何因子 | 统计查表 |','|---|---:|---:|---:|']
    for split,m in result['tests'].items():
        refs=m['fixed_rule_references']
        lines.append(f'| {split} | {100*m["failure_rate"]:.2f}% | {100*refs["geometry_only"]["failure_rate"]:.2f}% | {100*refs["lookup"]["failure_rate"]:.2f}% |')
    lines+=['','这批候选中，公式参照在三个集合均未丢失好位置，不能把清单收益归功于神经网络。'
        '当前数据未证明模型在优先排序上有额外价值。公式参照也不保证未来零风险，且不判断天气能否观测。']
    (directory/'report.md').write_text('\n'.join(lines)+'\n')
    print('PRIORITY COMPLETE '+json.dumps(result['tests']),flush=True)
    return result


if __name__=='__main__':
    import torch
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--base',type=Path,default=DEFAULT_OUTPUT)
    parser.add_argument('--output',type=Path,default=DEFAULT_IMPROVEMENT)
    args=parser.parse_args()
    torch.set_num_threads(1)
    run(args.base,args.output)

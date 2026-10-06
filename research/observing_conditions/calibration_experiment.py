"""No network training: compare frozen models and public conditional calibration."""
from __future__ import annotations
import argparse
from concurrent.futures import ProcessPoolExecutor,as_completed
from datetime import datetime,timezone
import json
from pathlib import Path
import shutil
import time
import warnings
import numpy as np
import torch
from .conditional_calibration import SCHEMES,fit,apply,public_keys
from .adaptation_scaling import DEFAULT as SCALING, OLD, entries, fresh_entries, read,concat,take,predict,prepare_worker
from .adaptation_data import digest
from .adaptation_experiment import bootstrap
from .data import ROOT,save_json,source_hashes
from .information_model import measure,calibration_apply,probability_tables,reliability_gate

DEFAULT=ROOT/'artifacts/observing_conditions-calibration'
MODELS=('gru64','gru128','tree64')
METHODS=('old_global',)+SCHEMES


def new_entries():
    return [dict(id=f'calcheck-{card}-{i}',card=card,split='test',seed=1620000+ci*1000+i,
                 policy='baseline',weather_shift=bool(i)) for ci,card in enumerate('abcd') for i in range(2)]


def existing_entries(split):
    if split=='calibration':
        return [(e,OLD/'scenarios'/e['id']) for e in entries() if e['split']=='calibration']+[
            (e,SCALING/'scenarios'/e['id']) for e in fresh_entries() if e['split']=='train']
    if split=='validation':
        return [(e,OLD/'scenarios'/e['id']) for e in entries() if e['split']=='validation']+[
            (e,SCALING/'scenarios'/e['id']) for e in fresh_entries() if e['split']=='test']
    raise ValueError('unknown development split')


def protocol(output):
    output.mkdir(parents=True,exist_ok=True)
    model_hashes={str(p.relative_to(ROOT)):digest(p) for name in MODELS for p in (SCALING/'models'/name).iterdir()
                  if p.suffix in ('.pt','.npz','.joblib')}
    spec=dict(version='conditions-calibration-v1',source='SELF-GENERATED weather/events/requests; formal public A-D unchanged',
        models=MODELS,schemes=METHODS,network_training_runs=0,
        calibration_scenes=[e['id'] for e,_ in existing_entries('calibration')],
        validation_scenes=[e['id'] for e,_ in existing_entries('validation')],final_entries=new_entries(),
        used_test_notice='Previous scaling final scenes explicitly reused as development; no longer independent tests of this change.',
        fitting='12 calibration scenes. Equal scene and decision-group weights. Global/horizon from bulk sampling; '
                'none/partial-history cells from first eight exposures. Primary model gru64; other frozen models are references.',
        group_keys=['public horizon','completed history count 0 / 1-7 / 8'],min_fitting_scenes=8,
        probability_rule='scalar temperature for blocked and bands; at least 3 independent scenes per binary outcome/class '
                         'or inherit parent temperature; probability support remains explicit',
        intervals='empirical 5% lower and 95% upper residual offsets; nonnegative Q with no upper cap; U in [0,1]; preserve medians',
        selection='Primary gru64: among new calibration schemes passing global and sufficiently populated next-night/history '
                  'checks, minimize mean normalized interval/probability errors. If none pass, minimize worst reliability gap '
                  'then error and mark exploratory. Old-global is a diagnostic reference.',
        acceptance='Global Q/U coverage 85-95%, both probability ECE<=5%; Q coverage and both probability ECE for '
                   'next-night/none/partial-history groups with >=8 scenes; U subgroup point masses may give conservative >95% coverage.',
        limitations='No record-level confidence guarantee or guarantee under changed weather. Only 12 fitting scenes and 8 final scenes. '
                    'Weather-shift flags, scene identifiers and truth are evaluation-only and never select a runtime bank.',
        sources=source_hashes(),model_hashes=model_hashes,scaling_freeze_hash=digest(SCALING/'frozen.json'),
        code={str(p.relative_to(ROOT)):digest(p) for p in (Path(__file__),Path(__file__).with_name('conditional_calibration.py'))})
    spec=json.loads(json.dumps(spec));path=output/'protocol.json'
    if path.exists() and json.loads(path.read_text())!=spec:raise ValueError('protocol changed; create a new experiment')
    save_json(path,spec);return spec


def load(output,split,initial=False):
    sources=existing_entries(split) if split!='test' else [(e,output/'scenarios'/e['id']) for e in new_entries()]
    items=[]
    for entry,directory in sources:
        if initial:
            directory=(SCALING if split!='test' else output)/'supplementary/scenarios'/entry['id']
        data=read(directory,entry['id'])
        if initial:data=take(data,np.array([m['observe_index']<8 for m in data['meta']]))
        data['weather']=np.full(len(data['y']),'changed' if entry['weather_shift'] else 'normal')
        items.append(data)
    return concat(items)


def raw_cache(output,split,initial,data):
    path=output/f'{split}_{"initial" if initial else "bulk"}_raw.npz'
    if path.exists():
        with np.load(path) as f:return {k:f[k].copy() for k in MODELS}
    values={k:predict(SCALING,k,data) for k in MODELS}
    np.savez_compressed(path,**values)
    return values


def grouped(data,p):
    leads,phases=public_keys(data['meta'])
    axes=dict(horizon=leads,history=phases,weather=data['weather'],card=np.array([s.split('-')[1] for s in data['scene']]))
    return {name:{str(g):measure(data['y'][axis==g],p[axis==g])|dict(scenes=len(np.unique(data['scene'][axis==g])))
                  for g in np.unique(axis)} for name,axis in axes.items()}


def status(bulk,sub,initial):
    checks={'bulk':reliability_gate(bulk)};gaps=[]
    for name,m in [('bulk',bulk),('next_night',sub['horizon']['next_night'])]+[
                    (phase,initial['history'][phase]) for phase in ('none','partial')]:
        enough=name=='bulk' or m['scenes']>=8
        if enough:
            cover=m['quality_coverage'];prob=max(m['band_ece'],m['blocked_ece'])
            gaps.append(max(.85-cover,cover-.95,0)/.05+max(prob-.05,0)/.05)
            if name!='bulk':checks[name]=.85<=cover<=.95 and prob<=.05
    return dict(passed=all(checks.values()),checks=checks,worst_gap=max(gaps))


def normalized_score(m,reference):
    keys=('quality_interval_score','usable_interval_score','blocked_brier','band_brier')
    return float(np.mean([m[k]/max(reference[k],1e-8) for k in keys]))


def freeze(output):
    if (output/'frozen.json').exists():return
    began=time.perf_counter();cal=load(output,'calibration');ci=load(output,'calibration',True)
    valid=load(output,'validation');vi=load(output,'validation',True)
    cp=raw_cache(output,'calibration',False,cal);cip=raw_cache(output,'calibration',True,ci)
    vp=raw_cache(output,'validation',False,valid);vip=raw_cache(output,'validation',True,vi)
    old=json.loads((SCALING/'frozen.json').read_text())['calibration'];settings={};metrics={};sub={};initial={};checks={};scores={}
    fit_cost={}
    for model in MODELS:
        for scheme in METHODS:
            key=model+'/'+scheme;began_fit=time.perf_counter()
            params=old[model] if scheme=='old_global' else fit(cal,cp[model],ci,cip[model],scheme)
            settings[key]=params;fit_cost[key]=time.perf_counter()-began_fit
            p=calibration_apply(vp[model],params) if scheme=='old_global' else apply(vp[model],valid['meta'],params)
            ip=calibration_apply(vip[model],params) if scheme=='old_global' else apply(vip[model],vi['meta'],params)
            metrics[key]=measure(valid['y'],p);sub[key]=grouped(valid,p);initial[key]=grouped(vi,ip)
            checks[key]=status(metrics[key],sub[key],initial[key])
            print('calibrated',key,'Q coverage',round(metrics[key]['quality_coverage'],3),flush=True)
        reference=metrics[model+'/global']
        for scheme in METHODS:scores[model+'/'+scheme]=normalized_score(metrics[model+'/'+scheme],reference)
    candidates=['gru64/'+s for s in SCHEMES];allowed=[k for k in candidates if checks[k]['passed']]
    selected=min(allowed,key=scores.get) if allowed else min(candidates,key=lambda k:(checks[k]['worst_gap'],scores[k]))
    save_json(output/'frozen.json',dict(selected=selected,validation_passed=checks[selected]['passed'],
        calibration=settings,validation=metrics,validation_subgroups=sub,initial_validation=initial,
        validation_checks=checks,selection_scores=scores,fit_seconds=fit_cost,development_wall_seconds=time.perf_counter()-began,
        calibration_scenes=sorted(set(cal['scene'])),validation_scenes=sorted(set(valid['scene'])),
        network_training_runs=0,freeze_utc=datetime.now(timezone.utc).isoformat(),
        model_hashes=json.loads((output/'protocol.json').read_text())['model_hashes']))
    print('FROZEN',selected,'validation passed',checks[selected]['passed'],flush=True)


def prepare(output,workers):
    if not (output/'frozen.json').exists():raise ValueError('freeze before final generation')
    began=time.perf_counter();results=[]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures=[pool.submit(prepare_worker,(e,output,True)) for e in new_entries()]
        for future in as_completed(futures):
            value=future.result();results.append(value);print('prepared',value['scene'],flush=True)
    save_json(output/'preparation_test.json',dict(wall_seconds=time.perf_counter()-began,workers=workers,scenes=results))


def evaluate(output):
    f=json.loads((output/'frozen.json').read_text())
    for name,h in f['model_hashes'].items():
        if digest(ROOT/name)!=h:raise ValueError('frozen model changed')
    data=load(output,'test');initial=load(output,'test',True)
    raw=raw_cache(output,'test',False,data);iraw=raw_cache(output,'test',True,initial)
    predictions={};ipred={};metrics={};sub={};isub={};checks={}
    for key,settings in f['calibration'].items():
        model,scheme=key.split('/')
        p=calibration_apply(raw[model],settings) if scheme=='old_global' else apply(raw[model],data['meta'],settings)
        ip=calibration_apply(iraw[model],settings) if scheme=='old_global' else apply(iraw[model],initial['meta'],settings)
        predictions[key]=p;ipred[key]=ip;metrics[key]=measure(data['y'],p)
        sub[key]=grouped(data,p);isub[key]=grouped(initial,ip);checks[key]=status(metrics[key],sub[key],isub[key])
    selected=f['selected'];report=dict(source='SELF-GENERATED ONLY; not formal hidden-card scores',selected=selected,
        passed=f['validation_passed'] and checks[selected]['passed'],validation_passed=f['validation_passed'],
        records=len(data['y']),initial_records=len(initial['y']),test_scenes=sorted(set(data['scene'])),
        metrics=metrics,subgroups=sub,initial_subgroups=isub,checks=checks,
        bootstrap_vs_same_model_global={k:bootstrap(data,p,predictions[k.split('/')[0]+'/global']) for k,p in predictions.items()},
        selected_vs_old_global=bootstrap(data,predictions[selected],predictions['gru64/old_global']),
        initial_difference=bootstrap(initial,ipred[selected],ipred['gru64/global']),
        network_training_runs=0,limitation=json.loads((output/'protocol.json').read_text())['limitations'])
    save_json(output/'report.json',report)
    np.savez_compressed(output/'test_predictions.npz',y=data['y'],scene=data['scene'],**predictions)
    np.savez_compressed(output/'initial_predictions.npz',y=initial['y'],scene=initial['scene'],**ipred)
    save_json(output/'probability_reliability.json',{k:probability_tables(data['y'],p,data['scene']) for k,p in predictions.items()})
    lines=['# 观测范围与概率调整','', '正式公开A–D输入与自行生成天气；网络权重未重训。',
        f"验证选择 `{selected}`；完整可靠性检查：{'通过' if report['passed'] else '未通过'}。",'',
        '| 模型 / 调整 | 质量范围评分↓ | 质量覆盖率 | 有效比例覆盖率 | 受阻概率偏差↓ | 档位概率偏差↓ |',
        '|---|---:|---:|---:|---:|---:|']
    for key,m in metrics.items():
        lines.append(f"| {key} | {m['quality_interval_score']:.4f} | {m['quality_coverage']:.1%} | {m['usable_coverage']:.1%} | {m['blocked_ece']:.1%} | {m['band_ece']:.1%} |")
    lines+=['','## 约3万条GRU：重点条件','',
        '| 调整 | 下一夜覆盖 | 天气变化覆盖 | 无历史覆盖 | 1–7次历史覆盖 |',
        '|---|---:|---:|---:|---:|']
    for scheme in METHODS:
        key='gru64/'+scheme;s=sub[key];i=isub[key]
        lines.append(f"| {scheme} | {s['horizon']['next_night']['quality_coverage']:.1%} | {s['weather']['changed']['quality_coverage']:.1%} | {i['history']['none']['quality_coverage']:.1%} | {i['history']['partial']['quality_coverage']:.1%} |")
    lines+=['','old_global：上轮4场景的原校准；global：12场景、场景与决策等权的同类全局校准。',
        'global_sides：分别调整两侧；horizon_sides：再区分预测距离；history_horizon_sides：再区分已完成历史。',
        '校准12独立场景、开发比较12场景、最终8新场景；无/部分历史额外诊断不混入自然采样汇总。',
        '天气变化标签只用于事后评估，从不选择运行时调整组；每组支持场景与稀缺结果数量保存在 frozen.json。',
        '分项覆盖与概率目标不能靠极宽范围代替，须一起看评分、宽度及场景差异范围。',
        '有效比例常恰为0或1，分组覆盖超过95%可能表示保守，不能据此宣称比例模型已经完善。',
        '', '完整分项与场景抽样差异见report.json。旧测试已转作开发，本轮最终测试未用于再调参。']
    (output/'report.md').write_text('\n'.join(lines)+'\n')
    print('EVALUATED',selected,'passed',report['passed'],flush=True)


def export(output):
    f=json.loads((output/'frozen.json').read_text());report=json.loads((output/'report.json').read_text())
    selected=f['selected'];model,scheme=selected.split('/');directory=output/'selected';(directory/'models').mkdir(parents=True,exist_ok=True)
    for name in ('gru.pt','normalizer.npz'):shutil.copyfile(SCALING/'models'/model/name,directory/'models'/name)
    shutil.copytree(OLD/'foundation',directory/'foundation',dirs_exist_ok=True)
    save_json(directory/'conditional.json',f['calibration'][selected])
    # Reuse public feature construction with identity calibration and fixed weights.
    identity=dict(blocked_temperature=1.,band_temperature=1.,interval_correction=0.,usable_interval_correction=0.)
    save_json(directory/'frozen.json',dict(selected_method='gru',calibration={'gru':identity},
        validation_reliability_passed=f['validation_passed']))
    save_json(directory/'report.json',dict(passed=report['passed']))
    save_json(directory/'manifest.json',dict(selected=selected,network_training_runs=0,final_test_passed=report['passed'],
        weights_source=str(SCALING/'models'/model),quality='Q_env; efficiency1; not capped at1',
        scope='Public horizon/history empirical calibration; not per-record confidence guarantee',
        hashes={str(p.relative_to(directory)):digest(p) for p in directory.rglob('*') if p.is_file() and p.name!='manifest.json'}))
    print('EXPORTED',directory,flush=True)


def main():
    p=argparse.ArgumentParser();p.add_argument('stage',choices=('freeze','test','evaluate','export'))
    p.add_argument('--output',type=Path,default=DEFAULT);p.add_argument('--workers',type=int,default=4);a=p.parse_args()
    torch.set_num_threads(1);warnings.filterwarnings('ignore',message='X does not have valid feature names')
    protocol(a.output)
    if a.stage=='freeze':freeze(a.output)
    elif a.stage=='test':prepare(a.output,a.workers)
    elif a.stage=='evaluate':evaluate(a.output)
    else:export(a.output)


if __name__=='__main__':main()

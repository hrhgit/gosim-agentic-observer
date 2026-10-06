"""Single-seed MLP experiments; frozen assistance rules and genuinely new scenes.

Run with `python -m research.observing_conditions.improvement`. No calls to the
first-round multi-architecture/repeated-training runner are made.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import timedelta
from pathlib import Path

import numpy as np
import torch
from torch import nn

from .data import (ROOT, DEFAULT_OUTPUT, GENERATION_VERSION, generate_one,
                   source_hashes, save_json, load_dataset, read_truth)
from .features import VERSION, CONTEXT_NAMES, CANDIDATE_NAMES, utc, altaz
from .models import ConditionsNet, Normalizer, training_loss, infer, load_model
from .metrics import metrics, validation_score, calibrate_fit, calibrate_apply, StatisticalBaseline
from .assistance import select_rules, evaluate
from challenge.v4_scorer import DIRECTIONAL_MULTIPLIER_TYPES, ZERO_SCORE_TYPE, FORCE_CLOSE_TYPE

DEFAULT_IMPROVEMENT = ROOT/'artifacts/observing_conditions-v2'
EPOCHS = 20
SEED = 101


def entries():
    result=[]
    for ci,card in enumerate('abcd'):
        for index in range(8):
            result.append(dict(id=f'v2-{card}-train-{index:03}',card=card,split='train',
                weather_shift=index>=4,seed=860000+ci*1000+index))
        for index in range(8):
            result.append(dict(id=f'v2-{card}-test-{index:03}',card=card,split='test',
                seed=870000+ci*1000+index))
        for index in range(4):
            result.append(dict(id=f'v2-{card}-weather-{index:03}',card=card,split='weather_test',
                seed=880000+ci*1000+index))
        for index in range(2):
            result.append(dict(id=f'v2-{card}-site-{index:03}',card=card,split='site_test',
                site_index=index,seed=890000+ci*1000+index))
    return result


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def protocol(base,output):
    old=json.loads((base/'manifest.json').read_text())
    new=entries()
    assert not {e['seed'] for e in old['entries']} & {e['seed'] for e in new}
    p=dict(version='conditions-improvement-v2',source='formal public A-D plus self-generated weather',
        base_manifest_sha256=digest(base/'manifest.json'),official_sources=source_hashes(),
        entries=new,groups_per_scene=64,seed=SEED,epochs=EPOCHS,training_jobs=4,
        training_subset='16 equally spaced groups per original training scene (224 scenes); '
                        'augmentation adds all groups of 32 new training scenes',
        experiments=['compact','physics','physics_augmented','oracle_diagnostic'],
        initialization_repetitions=1,architecture='MLP only',
        model_selection='existing reference or new public model: maximize validation candidate-comparison '
                        'reduction after risk-gated rules; break ties by quality MAE; oracle excluded',
        calibration_selection='compare global versus conditional on validation half-fit/half-evaluate; '
                              'fit chosen method on existing separate calibration scenes',
        assistance_selection='validation only; <=1% false blocked exclusion, <=1% loss of all '
            'usable >=95%-best positions; shortlist normalized loss <=0.1%; '
            'recommendation <=1% failure (U<0.5 or Q<95% best), >=100 decisions and >=8 active scenes; '
            'empirical scene-bootstrap upper <=2% for key error rates',
        final_test='generate/load final scenes only after selected model, calibration, and all rules frozen',
        caveat='Single initialization exploratory comparison; validation reused from first round; '
               'not an agent end-to-end evaluation or official score. Oracle intentionally sees future '
               'truth at three moments and is never deployable.',
        original_selected_hashes={str(p.relative_to(base)):digest(p) for p in (base/'models/selected').glob('*') if p.is_file()})
    path=output/'protocol.json'
    if path.exists() and json.loads(path.read_text())!=p:
        if (output/'frozen.json').exists() or any((output/'scenarios'/e['id']).exists() for e in new if e['split']!='train'):
            raise ValueError('protocol changed after freeze/test generation; use a fresh output directory')
        save_json(output/'protocol-before-assistance-selection.json',json.loads(path.read_text()))
    save_json(path,p)
    save_json(output/'manifest.json',dict(version=VERSION,generation_version=GENERATION_VERSION,
        sources=p['official_sources'],source=p['source'],entries=new,groups_per_scene=64,
        context_names=CONTEXT_NAMES,candidate_names=CANDIDATE_NAMES,history_shape=[32,75],
        quality_definition='Q_env, efficiency=1, no instrument faults',pilot=False))
    return p


def generate(output,selected,workers):
    begin=time.perf_counter()
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures=[pool.submit(generate_one,(e,str(output),64)) for e in selected]
        for i,future in enumerate(as_completed(futures),1):
            row=future.result()
            print(f'generate {i}/{len(selected)} {row["id"]}',flush=True)
    return time.perf_counter()-begin


def load(output,splits,per_scene=None):
    # New final scenes do not exist at development time, so do not probe other partitions.
    manifest=json.loads((Path(output)/'manifest.json').read_text())
    fields={k:[] for k in ('context','history','candidate','y')}
    names=[];cards=[];indices=[]
    for entry in manifest['entries']:
        if entry['split'] not in splits:
            continue
        directory=Path(output)/'scenarios'/entry['id']
        with np.load(directory/'samples.npz') as a:
            ix=np.linspace(0,len(a['context'])-1,per_scene,dtype=int) if per_scene else np.arange(len(a['context']))
            for key in fields:
                fields[key].append(a[key][ix])
        if entry['split']!='site_test':
            assert (ROOT/'cards'/entry['card']/'public/v4_night_calendar.csv').read_bytes()==(directory/'truth_and_public/v4_night_calendar.csv').read_bytes()
        names.extend([entry['id']]*len(ix));cards.extend([entry['card']]*len(ix));indices.extend(ix)
    result={k:np.concatenate(v) for k,v in fields.items()}
    return result | dict(scene=np.array(names),card=np.array(cards),group_index=np.array(indices))


def concatenate(a,b):
    return {k:np.concatenate([a[k],b[k]]) for k in a}


def summary_history(history):
    valid=history[...,-1]>0
    bits=history[...,:72]*valid[...,None]
    count=np.maximum(valid.sum(axis=1),1)
    frequency=bits.sum(axis=1)/count[:,None]
    ages=np.where(bits>0,history[...,72,None],np.inf).min(axis=1)
    seen=np.isfinite(ages)
    ages=np.where(seen,ages,0)
    pairs=valid[:,1:]&valid[:,:-1]
    changes=(np.abs(bits[:,1:]-bits[:,:-1])*pairs[...,None]).sum(axis=1)/np.maximum(pairs.sum(axis=1),1)[:,None]
    extra=np.column_stack([valid.sum(axis=1)/32,
        (history[...,73]*valid).sum(axis=1)/count,
        np.where(valid,history[...,73],0).max(axis=1)])
    return np.column_stack([frequency,ages,seen.astype(float),changes,extra]).astype(np.float32)


def transform(data,mode):
    result=dict(data)
    result['context']=np.column_stack([data['context'],summary_history(data['history'])]).astype(np.float32)
    result['history']=np.zeros((len(data['context']),32,1),np.float32)
    if mode=='oracle_diagnostic':
        if 'hidden' not in data:
            raise ValueError('oracle needs explicitly hidden physical fields')
        result['candidate']=np.concatenate([data['candidate'],data['hidden']],axis=-1)
    return result


def hidden_features(data,output,cache):
    """Real physical fields at start/middle/end; never copies quality/U/band labels."""
    identity=hashlib.sha256(data['scene'].tobytes()+data['group_index'].tobytes()).hexdigest()
    cache=Path(cache)
    if cache.exists():
        with np.load(cache) as a:
            if str(a['identity'])!=identity:
                raise ValueError('hidden feature cache mismatch')
            return a['hidden'].copy()
    hidden=np.empty((*data['candidate'].shape[:2],24),np.float32)
    for name in np.unique(data['scene']):
        directory=Path(output)/'scenarios'/name
        meta=json.loads((directory/'metadata.json').read_text())
        weather,events=read_truth(directory/'truth_and_public')
        with np.load(directory/'samples.npz') as a:
            coords=a['coordinates'].copy()
        rows=np.flatnonzero(data['scene']==name)
        for row in rows:
            index=int(data['group_index'][row]);info=meta['groups'][index]
            start,end=utc(info['start']),utc(info['end'])
            ra,dec=coords[index].T
            parts=[]
            for moment in (start,start+(end-start)/2,end-timedelta(microseconds=1)):
                si=weather._slot_index(moment);s=weather.slots[si]
                opened=s.start_utc<=moment<s.end_utc and s.is_observable
                alt,az=altaz(ra,dec,moment,meta['site'])
                effects=np.ones((len(ra),4))
                for event in events:
                    if not event.actual_start_utc<=moment<event.actual_end_utc:
                        continue
                    sector=((az>=event.azimuth_start_deg)&(az<=event.azimuth_end_deg)) if event.azimuth_start_deg<=event.azimuth_end_deg else ((az>=event.azimuth_start_deg)|(az<=event.azimuth_end_deg))
                    covered=sector&(alt>=event.min_altitude_deg)&(alt<=event.max_altitude_deg)
                    if event.event_type in DIRECTIONAL_MULTIPLIER_TYPES:
                        effects[covered,:3]*=[event.seeing_multiplier,event.transparency_multiplier,event.sky_quality_multiplier]
                    if event.force_close or event.event_type in (ZERO_SCORE_TYPE, FORCE_CLOSE_TYPE):
                        effects[covered,3]=0
                parts.append(np.column_stack([np.full((len(ra),4),
                    [s.seeing_arcsec if opened else 0,s.transparency if opened else 0,
                     s.sky_quality if opened else 0,float(opened)]),effects]))
            hidden[row]=np.column_stack(parts)
    cache.parent.mkdir(parents=True,exist_ok=True)
    np.savez_compressed(cache,identity=identity,hidden=hidden)
    return hidden


class ImprovementNet(nn.Module):
    def __init__(self,context_dim,history_dim,candidate_dim,physics,normalizer):
        super().__init__()
        self.backbone=ConditionsNet('mlp',context_dim,history_dim,candidate_dim)
        self.physics=physics
        self.register_buffer('g_mean',torch.tensor(float(normalizer.values['candidate_mean'][28])))
        self.register_buffer('g_std',torch.tensor(float(normalizer.values['candidate_std'][28])))

    def forward(self,context,history,candidate):
        result=self.backbone(context,history,candidate)
        if self.physics:
            # Q is linear in geometric factor in the official formula. Average g
            # is a public three-point approximation; weather/g covariance is
            # still learned via candidate/context features, not assumed absent.
            g=(candidate[...,28:29]*self.g_std+self.g_mean).clamp_min(1e-6)
            result=torch.cat([result[...,:4]*g,result[...,4:]],dim=-1)
        return result


def train(directory,mode,train_data,validation):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    train=transform(train_data,mode);val=transform(validation,mode)
    normalizer=Normalizer.fit(train)
    specification=dict(mode=mode,epochs=EPOCHS,seed=SEED,learning_rate=.001,architecture='MLP',
        groups=len(train['context']),context_dim=train['context'].shape[-1],history_dim=1,
        candidate_dim=train['candidate'].shape[-1],physics=mode.startswith('physics'),
        optimizer='AdamW',batch_candidates=512,weight_decay=.0001,dropout=.1,
        training_scene_sha256=hashlib.sha256(train_data['scene'].tobytes()+train_data['group_index'].tobytes()).hexdigest())
    if (directory/'complete.json').exists():
        if json.loads((directory/'metadata.json').read_text())!=specification:
            raise ValueError('training specification changed')
        return restore(directory)
    save_json(directory/'metadata.json',specification);normalizer.save(directory/'normalizer.npz')
    torch.manual_seed(SEED);torch.set_num_threads(1)
    model=ImprovementNet(specification['context_dim'],1,specification['candidate_dim'],specification['physics'],normalizer)
    optimizer=torch.optim.AdamW(model.parameters(),lr=.001,weight_decay=.0001)
    normalized=normalizer.apply(train);vn=normalizer.apply(val)
    tensors={k:torch.as_tensor(v) for k,v in normalized.items()}
    target=torch.as_tensor(train['y']);scale=float(normalizer.values['quality_scale'])
    first=0;best=float('inf');elapsed=0.
    latest=directory/'latest.pt'
    if latest.exists():
        state=torch.load(latest,weights_only=True)
        model.load_state_dict(state['model']);optimizer.load_state_dict(state['optimizer'])
        first=state['epoch']+1;best=state['best'];elapsed=state['elapsed']
        torch.set_rng_state(state['rng'])
        history=directory/'history.jsonl'
        if history.exists():
            completed=[row for row in history.read_text().splitlines() if json.loads(row)['epoch']<=first]
            history.write_text('\n'.join(completed)+'\n')
    for epoch in range(first,EPOCHS):
        begin=time.perf_counter();model.train()
        for param in optimizer.param_groups:
            param['lr']=.001*(.1+.9*.5*(1+math.cos(math.pi*epoch/(EPOCHS-1))))
        order=np.random.default_rng(SEED*1000+epoch).permutation(len(target))
        total=0.
        for offset in range(0,len(order),16):
            ix=order[offset:offset+16];optimizer.zero_grad(set_to_none=True)
            loss=training_loss(model(tensors['context'][ix],tensors['history'][ix],tensors['candidate'][ix]),target[ix],scale)
            loss.backward();torch.nn.utils.clip_grad_norm_(model.parameters(),1);optimizer.step();total+=float(loss.detach())
        measured=metrics(val['y'],infer(model,vn,scale,'cpu'))
        score=validation_score(measured,scale)
        if score<best:
            best=score;torch.save(model.state_dict(),directory/'weights.pt')
            save_json(directory/'best_validation.json',dict(epoch=epoch+1,score=score,metrics=measured))
        elapsed+=time.perf_counter()-begin
        with (directory/'history.jsonl').open('a') as f:
            f.write(json.dumps(dict(epoch=epoch+1,elapsed_seconds=elapsed,metrics=measured,score=score))+'\n')
        temporary=directory/'latest.tmp'
        torch.save(dict(model=model.state_dict(),optimizer=optimizer.state_dict(),rng=torch.get_rng_state(),
            epoch=epoch,best=best,elapsed=elapsed),temporary);temporary.replace(latest)
        print(f'{mode} {epoch+1}/{EPOCHS} Q-MAE={measured["quality_mae"]:.4f} elapsed={elapsed:.1f}s',flush=True)
    save_json(directory/'complete.json',dict(epochs=EPOCHS,elapsed_seconds=elapsed,training_repetitions=1))
    return restore(directory)


def restore(directory):
    directory=Path(directory);meta=json.loads((directory/'metadata.json').read_text())
    norm=Normalizer.load(directory/'normalizer.npz')
    net=ImprovementNet(meta['context_dim'],1,meta['candidate_dim'],meta['physics'],norm)
    net.load_state_dict(torch.load(directory/'weights.pt',weights_only=True,map_location='cpu'))
    return net.eval(),norm


def predict_pair(pair,data,mode):
    model,norm=pair
    processed=transform(data,mode) if mode!='reference' else data
    return infer(model,norm.apply(processed),float(norm.values['quality_scale']),'cpu')


def cells(data):
    return ((data['context'][:,-4]>0).astype(int)*2+(data['context'][:,-3]>.25).astype(int))


def fit_calibration(data,p,conditional=False):
    global_settings=calibrate_fit(data['y'],p)
    result=dict(global_settings=global_settings,conditional=conditional,cells={})
    if conditional:
        for cell in range(4):
            mask=cells(data)==cell
            if len(np.unique(data['scene'][mask]))<8:
                continue
            local=calibrate_fit(data['y'][mask],p[mask])
            for key in ('blocked_temperature','band_temperature'):
                local[key]=float(np.sqrt(local[key]*global_settings[key]))
            result['cells'][str(cell)]=local
    return result


def apply_calibration(data,p,settings):
    result=calibrate_apply(p,settings['global_settings'])
    for cell,local in settings['cells'].items():
        mask=cells(data)==int(cell)
        if mask.any():
            result[mask]=calibrate_apply(p[mask],local)
    return result


def subset(data,mask):
    return {k:v[mask] for k,v in data.items()}


def run(base=DEFAULT_OUTPUT,output=DEFAULT_IMPROVEMENT,workers=4):
    base,output=Path(base),Path(output);output.mkdir(parents=True,exist_ok=True)
    started=time.perf_counter();times={}
    p=protocol(base,output)
    torch.set_num_threads(1)
    # No new final-test data is generated or opened before the freeze marker.
    times['augmentation_generation_seconds']=generate(output,[e for e in p['entries'] if e['split']=='train'],workers)
    begin=time.perf_counter()
    train_data=load(base,['train'],16);val=load(base,['validation']);cal=load(base,['calibration'])
    extra=load(output,['train'])
    augmented=concatenate(train_data,extra)
    reference=(load_model(base/'models/mlp-101')[0],Normalizer.load(base/'models/mlp-101/normalizer.npz'))
    pairs={'reference':reference}
    times['public_data_loading_seconds']=time.perf_counter()-begin
    for mode,data in (('compact',train_data),('physics',train_data),('physics_augmented',augmented)):
        begin=time.perf_counter();pairs[mode]=train(output/'models'/mode,mode,data,val)
        times[mode+'_training_wall_seconds']=time.perf_counter()-begin
    begin=time.perf_counter()
    oracle_train=dict(train_data);oracle_train['hidden']=hidden_features(train_data,base,output/'hidden/train.npz')
    oracle_val=dict(val);oracle_val['hidden']=hidden_features(val,base,output/'hidden/validation.npz')
    times['oracle_feature_preparation_seconds']=time.perf_counter()-begin
    begin=time.perf_counter()
    oracle_pair=train(output/'models/oracle_diagnostic','oracle_diagnostic',oracle_train,oracle_val)
    times['oracle_training_wall_seconds']=time.perf_counter()-begin
    comparison={};raw_val={}
    for mode,pair in pairs.items():
        raw_val[mode]=predict_pair(pair,val,mode)
        comparison[mode]=metrics(val['y'],raw_val[mode])
    comparison['oracle_diagnostic']=metrics(val['y'],predict_pair(oracle_pair,oracle_val,'oracle_diagnostic'))
    from .experiment import validation_calibration_split
    fit_mask,eval_mask=validation_calibration_split(val)
    calibration_by_model={};settings_by_model={};rules_by_model={}
    begin=time.perf_counter()
    for name in pairs:
        choices={}
        for conditional in (False,True):
            adjustment=fit_calibration(subset(val,fit_mask),raw_val[name][fit_mask],conditional)
            calibrated=apply_calibration(subset(val,eval_mask),raw_val[name][eval_mask],adjustment)
            result=metrics(val['y'][eval_mask],calibrated)
            penalty=abs(result['interval_coverage']-.9)+result['blocked_ece']+result['band_ece']
            choices[str(conditional)]=dict(score=penalty,metrics=result)
        use_conditional=min((False,True),key=lambda c:choices[str(c)]['score'])
        settings_by_model[name]=fit_calibration(cal,predict_pair(pairs[name],cal,name),use_conditional)
        rules_by_model[name]=select_rules(val['y'],apply_calibration(val,raw_val[name],settings_by_model[name]),val['scene'])
        calibration_by_model[name]=choices
        comparison[name]['assistance']=rules_by_model[name]['validation']
        print('validated assistance '+name+' reduction='+str(comparison[name]['assistance']['workload_reduction']),flush=True)
    selected=min(pairs,key=lambda name:(-comparison[name]['assistance']['workload_reduction'],comparison[name]['quality_mae']))
    settings=settings_by_model[selected];rule_result=rules_by_model[selected]
    conditional=settings['conditional'];calibration_choices=calibration_by_model[selected]
    reference_settings=settings_by_model['reference'];reference_rules=rules_by_model['reference']
    times['calibration_and_rule_selection_seconds']=time.perf_counter()-begin
    # Statistical lookup was already trained during round one; no additional fitting.
    lookup=StatisticalBaseline();saved=json.loads((base/'models/lookup.json').read_text())
    lookup.constant=saved['constant'];lookup.global_values=np.array(saved['global'])
    lookup.table={int(k):np.array(v) for k,v in saved['table'].items()}
    lookup_settings=fit_calibration(cal,lookup.predict(cal),False)
    lookup_rules=select_rules(val['y'],apply_calibration(val,lookup.predict(val),lookup_settings),val['scene'])
    frozen=dict(protocol_sha256=digest(output/'protocol.json'),selected=selected,
        validation_comparison=comparison,calibration_choices=calibration_choices,
        all_calibration_choices=calibration_by_model,all_calibration=settings_by_model,
        calibration=settings,assistance=rule_result,reference_calibration=reference_settings,
        reference_assistance=reference_rules,lookup_calibration=lookup_settings,lookup_assistance=lookup_rules,
        selected_weights_sha256=digest((base/'models/mlp-101' if selected=='reference' else output/'models'/selected)/'weights.pt'))
    freeze_path=output/'frozen.json'
    if freeze_path.exists() and json.loads(freeze_path.read_text())!=frozen:
        raise ValueError('frozen selection differs; do not revise a used test')
    save_json(freeze_path,frozen)
    print('FROZEN '+selected+' conditional='+str(conditional),flush=True)
    times['final_scene_generation_seconds']=generate(output,[e for e in p['entries'] if e['split']!='train'],workers)
    report=dict(selected=selected,validation_comparison=comparison,calibration_choices=calibration_choices,
        tests={},timing=times,protocol=p,freeze_sha256=digest(freeze_path))
    for split in ('test','weather_test','site_test'):
        data=load(output,[split])
        report['tests'][split]={}
        for name,pair,adjustment,rules in (
            ('reference',reference,reference_settings,reference_rules),
            ('selected',pairs[selected],settings,rule_result),
            ('lookup',None,lookup_settings,lookup_rules)):
            raw=lookup.predict(data) if pair is None else predict_pair(pair,data,'reference' if name=='reference' else selected)
            predictions=apply_calibration(data,raw,adjustment)
            measured,decisions,stats=evaluate(data['y'],predictions,data['scene'],rules['rule'])
            measured['prediction_metrics']=metrics(data['y'],predictions)
            measured['by_card']={str(card):evaluate(data['y'][data['card']==card],predictions[data['card']==card],
                data['scene'][data['card']==card],rules['rule'],False)[0] for card in np.unique(data['card'])}
            report['tests'][split][name]=measured
            directory=output/'predictions'/split;directory.mkdir(parents=True,exist_ok=True)
            np.save(directory/f'{name}.npy',predictions)
            np.savez_compressed(directory/f'{name}-decisions.npz',scene=data['scene'],stats=stats,**decisions)
        report['tests'][split]['public_variant_metrics']={}
        for mode in pairs:
            raw=predict_pair(pairs[mode],data,mode)
            predictions=apply_calibration(data,raw,settings_by_model[mode])
            report['tests'][split]['public_variant_metrics'][mode]=metrics(data['y'],predictions)
        # Diagnostic-only hidden model on new main scenes, not architecture selection.
        if split=='test':
            hidden_data=dict(data);hidden_data['hidden']=hidden_features(data,output,output/'hidden/test.npz')
            report['oracle_final_diagnostic']=metrics(data['y'],predict_pair(oracle_pair,hidden_data,'oracle_diagnostic'))
        save_json(output/'report.json',report)
    for path,value in p['original_selected_hashes'].items():
        assert digest(base/path)==value
    assert p['official_sources']==source_hashes()
    export(output,base,frozen)
    times['training_cumulative_seconds']=sum(json.loads((output/'models'/name/'complete.json').read_text())['elapsed_seconds']
        for name in ('compact','physics','physics_augmented','oracle_diagnostic'))
    times['timing_note']=('Phase wall times describe this invocation (completed work is reused on resume); '
        'training_cumulative_seconds is cumulative across saved checkpoints for all four jobs.')
    times['total_wall_seconds']=time.perf_counter()-started
    report['completed']=True
    save_json(output/'report.json',report)
    write_report(output,report,frozen)
    print('COMPLETE '+json.dumps(dict(selected=selected,timing=times)),flush=True)


def export(output,base,frozen):
    directory=output/'models/deployment';directory.mkdir(parents=True,exist_ok=True)
    selected=frozen['selected']
    source=base/'models/mlp-101' if selected=='reference' else output/'models'/selected
    for filename in ('weights.pt','normalizer.npz','metadata.json'):
        shutil.copyfile(source/filename,directory/filename)
    save_json(directory/'policy.json',dict(selected=selected,calibration=frozen['calibration'],
        assistance_rule=frozen['assistance']['rule'],source='self-generated research; no official score',
        freeze_sha256=digest(output/'frozen.json'),automatic_actions_enabled=False,
        reason='Advisory outputs only; test risk results must be read before agent integration.'))


def write_report(output,report,frozen):
    lines=['# 观测条件第二轮：帮助智能体缩小选择范围','',
        '来源：正式 A–D 公开输入＋明确标注的自行生成天气。未接入智能体，未进行线上提交。',
        '每个新方案仅训练一次，固定种子 101；4 次训练各 20 轮。原模型和查表直接复用。',
        '新增加 32 个训练场景，其中一半天气参数变化较大。冻结规则后生成 32 个主测试、16 个天气变化、8 个新站点场景；每场景 64 组×32 候选。',
        '', '## 验证集模型比较','', '| 方案 | 质量 MAE | 有效比例误差 | 档位 F1 |','|---|---:|---:|---:|']
    for name,m in report['validation_comparison'].items():
        lines.append(f'| {name} | {m["quality_mae"]:.4f} | {m["usable_mae"]:.4f} | {m["band_macro_f1"]:.3f} |')
    lines+=['',f'选定：**{report["selected"]}**；优先选择验证中风险合格且节省比较数量最多的方案，质量误差用于同等结果时比较。真实天气诊断模型不参加选择。',
        '简短历史：事件出现频率、最近出现距今多久、是否见过、变化频率、公告数量和实际间隔。',
        '公式方案：已知高度、月光和大气公式计算几何因子，网络预测剩余环境影响；使用开始/中间/结束的平均几何因子，是时间积分的近似，未宣称未知天气与几何变化完全独立。',
        '诊断模型额外看真实开始/中间/结束天气数值和实际方向影响，不复制答案；它仍未看到整段曝光的全部变化，不能当作不可降低误差的严格下限。',
        '', '## 冻结规则','', '```json',json.dumps(frozen['assistance']['rule'],indent=2),'```',
        '高受阻排除的错误：被此规则排除但 U≥0.5。相对质量排除可能移除可用但较差的位置，另报告失去全部“可用且质量≥同组最好95%”位置的比例。',
        '推荐失败：推荐位置 U<0.5 或质量低于同组最好95%。保留预测质量最高的位置，绝不把整组模型候选全部删除。',
        '风险门槛：验证点估计≤1%，关键错误的场景重复抽样上界≤2%；推荐至少100组、覆盖8个场景。达不到就关闭相应建议。',
        '这是经验规则，不是每条候选的确定性保证；零次错误也不等于零风险。90%质量区间不能当作整组排名正确概率。',
        '', '## 新场景检验','', '| 集合/模型 | 排除比例 | 受阻误排率 | 好位置全部丢失率 | 直接推荐组 | 推荐失败率 | 比较数量减少 |','|---|---:|---:|---:|---:|---:|---:|']
    def pct(v):
        return '无决策' if v is None else f'{100*v:.2f}%'
    for split,models in report['tests'].items():
        for name,m in models.items():
            if name=='public_variant_metrics':
                continue
            lines.append(f'| {split}/{name} | {pct(m["exclusion_fraction"])} | {pct(m["blocked_false_exclusion_rate"])} | {pct(m["near_best_loss_rate"])} | {m["recommendation_groups"]}/{m["groups"]} | {pct(m["recommendation_failure_rate"])} | {pct(m["workload_reduction"])} |')
    lines+=['','“比较数量减少”是假设推荐组仅需复核1个候选时的潜在减少；不是已经测得的智能体耗时或成功率。目标价值、转向成本、完成任务要求都不在这些规则中。',
        '独立场景抽样95%范围、每张卡结果、原始概率与决策掩码均在 report.json / predictions/。变化更大天气和新站点只检验，不调阈值。',
        '', '## 耗时','', '```json',json.dumps(report['timing'],indent=2),'```',
        '', '## 重跑','', '```sh','uv run --project research/observing_conditions python -m research.observing_conditions.improvement --output artifacts/observing_conditions-v2-rerun','```']
    (output/'report.md').write_text('\n'.join(lines)+'\n')


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--base',type=Path,default=DEFAULT_OUTPUT)
    parser.add_argument('--output',type=Path,default=DEFAULT_IMPROVEMENT)
    parser.add_argument('--workers',type=int,default=4)
    args=parser.parse_args();run(args.base,args.output,args.workers)

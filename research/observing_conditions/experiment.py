"""Resumable training, validation-only selection, independent calibration and final tests."""
from __future__ import annotations

import hashlib
import json
import math
import platform
import fcntl
from pathlib import Path
import shutil
import time

import numpy as np
import torch

from .data import load_dataset,save_json
from .models import Normalizer,build_model,training_loss,infer,load_model,cpu_benchmark
from .metrics import (metrics,validation_score,calibrate_fit,calibrate_apply,
    StatisticalBaseline,acceptance,scene_bootstrap,reliability_bins)

ARCHITECTURES=('mlp','gru','tcn')
SEEDS=(101,202,303)


def subset(data,mask):
    return {k:v[mask] for k,v in data.items()}


def ablate(data,mode):
    if mode is None:
        return data
    result=dict(data)
    result['context']=data['context'].copy()
    if mode=='no_history':
        result['history']=np.zeros_like(data['history'])
        result['context'][:,153:225]=0
    elif mode=='no_forecast':
        result['context'][:,72:144]=0
        result['context'][:,226:228]=0
    else:
        raise ValueError(mode)
    return result


def choose_devices(train,normalizer,output):
    """Measure both local backends; choosing CPU for a faster architecture is explicit."""
    path=output/'device_benchmarks.json'
    if path.exists():
        return json.loads(path.read_text())
    sample=normalizer.apply(subset(train,np.arange(min(16,len(train['context'])))))
    result={}
    torch.set_num_threads(1)
    for architecture in ARCHITECTURES:
        result[architecture]={}
        for device in ('cpu','mps'):
            if device=='mps' and not torch.backends.mps.is_available():
                continue
            torch.manual_seed(123)
            model=build_model(architecture,train).to(device)
            optimizer=torch.optim.AdamW(model.parameters(),lr=1e-3,weight_decay=1e-4)
            tensors=[torch.as_tensor(sample[k],device=device) for k in ('context','history','candidate')]
            y=torch.as_tensor(train['y'][:len(sample['context'])],device=device)
            timings=[]
            for step in range(40):
                begin=time.perf_counter()
                optimizer.zero_grad(set_to_none=True)
                training_loss(model(*tensors),y,float(normalizer.values['quality_scale'])).backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(),1)
                optimizer.step()
                if device=='mps':
                    torch.mps.synchronize()
                if step>=10:
                    timings.append(1000*(time.perf_counter()-begin))
            result[architecture][device+'_ms']=float(np.median(timings))
            del model,optimizer,tensors,y
            if device=='mps':
                torch.mps.empty_cache()
        result[architecture]['selected']=min((d for d in ('cpu','mps') if d+'_ms' in result[architecture]),key=lambda d:result[architecture][d+'_ms'])
    save_json(path,result)
    print('device benchmarks '+json.dumps(result),flush=True)
    return result


def train_job(directory,*args,**kwargs):
    directory=Path(directory)
    directory.mkdir(parents=True,exist_ok=True)
    with (directory/'.training.lock').open('a') as lock:
        fcntl.flock(lock.fileno(),fcntl.LOCK_EX)
        return _train_job(directory,*args,**kwargs)


def _train_job(directory,architecture,train,validation,normalizer,lr,epochs,seed,device,identity):
    directory.mkdir(parents=True,exist_ok=True)
    specification=dict(architecture=architecture,learning_rate=lr,epochs=epochs,seed=seed,device=device,
        context_dim=train['context'].shape[-1],history_dim=train['history'].shape[-1],candidate_dim=train['candidate'].shape[-1],
        training_groups=len(train['context']),batch_size=512,identity=identity,precision='float32',
        optimizer='AdamW',weight_decay=1e-4,dropout=0.1,gradient_norm_limit=1,
        learning_rate_schedule='cosine from selected LR to 0.1 times selected LR')
    signature=hashlib.sha256(json.dumps(specification,sort_keys=True).encode()).hexdigest()
    if (directory/'complete.json').exists():
        done=json.loads((directory/'complete.json').read_text())
        if done['signature']!=signature:
            raise ValueError(f'training signature mismatch: {directory}')
        return load_model(directory)[0]
    torch.set_num_threads(1)
    torch.manual_seed(seed)
    if device=='mps':
        torch.mps.manual_seed(seed)
    model=build_model(architecture,train).to(device)
    optimizer=torch.optim.AdamW(model.parameters(),lr=lr,weight_decay=1e-4)
    normalized=normalizer.apply(train); val_normalized=normalizer.apply(validation)
    tensors={k:torch.as_tensor(v,device=device) for k,v in normalized.items()}
    tensors['y']=torch.as_tensor(train['y'],device=device)
    scale=float(normalizer.values['quality_scale'])
    first_epoch=0; best=float('inf'); elapsed=0.0
    latest=directory/'latest.pt'
    if latest.exists():
        state=torch.load(latest,map_location=device,weights_only=True)
        if state['signature']!=signature:
            raise ValueError(f'checkpoint signature mismatch: {directory}')
        model.load_state_dict(state['model']); optimizer.load_state_dict(state['optimizer'])
        first_epoch=state['epoch']+1; best=state['best']; elapsed=state['elapsed']
        torch.set_rng_state(state['cpu_rng'].cpu())
        if device=='mps':
            torch.mps.set_rng_state(state['mps_rng'].cpu())
    save_json(directory/'metadata.json',specification|{'quality_scale':scale,'parameter_count':sum(p.numel() for p in model.parameters()),
        'torch':torch.__version__,'python':platform.python_version(),'device_reason':'measured fastest local backend; no dataset/epoch reduction'})
    normalizer.save(directory/'normalizer.npz')
    for epoch in range(first_epoch,epochs):
        begin=time.perf_counter()
        model.train()
        for param in optimizer.param_groups:
            param['lr']=lr*(0.1+0.9*0.5*(1+math.cos(math.pi*epoch/max(epochs-1,1))))
        order=np.random.default_rng(seed*1000+epoch).permutation(len(train['context']))
        accumulated=torch.zeros((),device=device)
        for offset in range(0,len(order),16):
            indices=torch.as_tensor(order[offset:offset+16],device=device)
            optimizer.zero_grad(set_to_none=True)
            prediction=model(tensors['context'][indices],tensors['history'][indices],tensors['candidate'][indices])
            loss=training_loss(prediction,tensors['y'][indices],scale)
            loss.backward(); torch.nn.utils.clip_grad_norm_(model.parameters(),1)
            optimizer.step()
            accumulated+=loss.detach()
        predictions=infer(model,val_normalized,scale,device)
        measured=metrics(validation['y'],predictions)
        score=validation_score(measured,scale)
        if not np.isfinite(score):
            raise ValueError(f'nonfinite validation score: {directory}')
        if score<best:
            best=score
            torch.save({k:v.detach().cpu() for k,v in model.state_dict().items()},directory/'weights.pt')
            save_json(directory/'best_validation.json',dict(epoch=epoch+1,score=score,metrics=measured))
        elapsed+=time.perf_counter()-begin
        row=dict(epoch=epoch+1,training_loss=float(accumulated.cpu())/math.ceil(len(order)/16),
                 validation_score=score,validation=measured,elapsed_seconds=elapsed)
        with (directory/'history.jsonl').open('a') as handle:
            handle.write(json.dumps(row)+'\n')
        state=dict(signature=signature,model=model.state_dict(),optimizer=optimizer.state_dict(),epoch=epoch,best=best,
            elapsed=elapsed,cpu_rng=torch.get_rng_state(),mps_rng=torch.mps.get_rng_state() if device=='mps' else torch.zeros(1,dtype=torch.uint8))
        temporary=directory/'latest.tmp';torch.save(state,temporary);temporary.replace(latest)
        print(f'{directory.name} epoch {epoch+1}/{epochs} val={score:.5f} Q-MAE={measured["quality_mae"]:.5f} elapsed={elapsed:.1f}s',flush=True)
    save_json(directory/'complete.json',dict(signature=signature,epochs=epochs,best_validation_score=best,elapsed_seconds=elapsed))
    del model,optimizer,tensors
    if device=='mps':
        torch.mps.empty_cache()
    return load_model(directory)[0]


def model_prediction(model,data,normalizer,device='cpu'):
    return infer(model.to(device),normalizer.apply(data),float(normalizer.values['quality_scale']),device)


def validation_calibration_split(data):
    names=np.unique(data['scene'])
    # Equal-sized scene blocks for each public card, avoiding per-record splitting.
    first=[]
    for card in np.unique(data['card']):
        scenes=np.unique(data['scene'][data['card']==card])
        first.extend(scenes[:len(scenes)//2])
    mask=np.isin(data['scene'],first)
    return mask,~mask


def slices(data,predictions):
    result={}
    for name,values in (('card',data['card']),('lead_seconds',np.rint(data['context'][:,-4]*3600).astype(int)),
                        ('requested_seconds',np.rint(data['context'][:,-3]*3600).astype(int))):
        result[name]={str(value):metrics(data['y'][values==value],predictions[values==value]) for value in np.unique(values)}
    result['events']={}
    from .features import KINDS
    for i,kind in enumerate(KINDS):
        mask=data['context'][:,i*9:(i+1)*9].any(axis=1)
        if mask.any():
            result['events'][kind]=metrics(data['y'][mask],predictions[mask])
    result['altitude']={}
    altitude=data['candidate'][...,0]*90
    for low,high in ((30,45),(45,60),(60,75),(75,91)):
        mask=(altitude>=low)&(altitude<high)
        if mask.any():
            m=metrics(data['y'][mask][:,None,:],predictions[mask][:,None,:]);m.pop('ranking_loss')
            result['altitude'][f'{low}-{min(high,90)}']=m
    return result


def run_experiment(output):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    if not (output/'generation_complete.json').exists():
        raise ValueError('complete dataset generation first')
    manifest=json.loads((output/'manifest.json').read_text())
    if manifest['pilot']:
        raise ValueError('pilot data must not be used for full experiment conclusions')
    identity=hashlib.sha256((output/'manifest.json').read_bytes()).hexdigest()
    train=load_dataset(output,['train']);validation=load_dataset(output,['validation'])
    normalizer=Normalizer.fit(train)
    output_models=output/'models';output_models.mkdir(exist_ok=True)
    normalizer.save(output_models/'normalizer.npz')
    devices=choose_devices(train,normalizer,output)
    tune_mask=np.array([i%128<8 for i in range(len(train['context']))])
    tuning=subset(train,tune_mask)
    comparison={};models={};best_lrs={}
    calibration_half,evaluation_half=validation_calibration_split(validation)
    for architecture in ARCHITECTURES:
        device=devices[architecture]['selected']
        choices=[]
        for lr in (3e-4,1e-3):
            path=output_models/f'tune-{architecture}-{lr}'
            train_job(path,architecture,tuning,validation,normalizer,lr,20,404,device,identity)
            choices.append((json.loads((path/'complete.json').read_text())['best_validation_score'],lr))
        lr=min(choices)[1];best_lrs[architecture]=lr
        members=[];single_predictions=[];single_metrics=[]
        for seed in SEEDS:
            path=output_models/f'{architecture}-{seed}'
            model=train_job(path,architecture,train,validation,normalizer,lr,60,seed,device,identity)
            p=model_prediction(model,validation,normalizer,device)
            single_predictions.append(p);single_metrics.append(metrics(validation['y'],p));members.append(model.cpu())
        ensemble=np.mean(single_predictions,axis=0)
        adjustment=calibrate_fit(validation['y'][calibration_half],ensemble[calibration_half])
        measured=metrics(validation['y'][evaluation_half],calibrate_apply(ensemble[evaluation_half],adjustment))
        benchmark=cpu_benchmark(members[0],normalizer.apply(validation))
        reliable=max(measured['blocked_ece'],measured['band_ece'])<=0.05 and 0.85<=measured['interval_coverage']<=0.95
        comparison[architecture]=dict(learning_rate=lr,validation=measured,single_validation=single_metrics,
            validation_reliable=reliable,cpu_single=benchmark,parameters=sum(p.numel() for p in members[0].parameters()))
        models[architecture]=members
        save_json(output/'validation_comparison.json',comparison)
    reliable=[a for a in ARCHITECTURES if comparison[a]['validation_reliable']]
    eligible=reliable or list(ARCHITECTURES)
    lowest=min(comparison[a]['validation']['quality_mae'] for a in eligible)
    tied=[a for a in eligible if comparison[a]['validation']['quality_mae']<=lowest*1.02]
    selected=min(tied,key=lambda a:comparison[a]['cpu_single']['p95_ms'])
    save_json(output/'selection.json',dict(architecture=selected,validation=comparison,
        reliability_gate_passed=bool(reliable),selection_rule='validation only; reliability first, Q MAE then CPU speed within 2%'))
    # Final calibration is disjoint from validation used for architecture/learning-rate choices.
    calibration=load_dataset(output,['calibration'])
    baselines={name:StatisticalBaseline(constant=(name=='constant')).fit(train) for name in ('constant','lookup')}
    settings={}
    for name,baseline in baselines.items():
        save_json(output_models/f'{name}.json',baseline.as_dict())
        settings[name]=calibrate_fit(calibration['y'],baseline.predict(calibration))
    for architecture,members in models.items():
        predictions=[model_prediction(m,calibration,normalizer,devices[architecture]['selected']) for m in members]
        settings[architecture]=calibrate_fit(calibration['y'],np.mean(predictions,axis=0))
        for i,p in enumerate(predictions):
            settings[f'{architecture}-{SEEDS[i]}']=calibrate_fit(calibration['y'],p)
    save_json(output/'calibration.json',settings)
    destination=output_models/'selected';destination.mkdir(exist_ok=True)
    normalizer.save(destination/'normalizer.npz')
    for seed in SEEDS:
        shutil.copyfile(output_models/f'{selected}-{seed}'/'weights.pt',destination/f'weights-{seed}.pt')
    save_json(destination/'metadata.json',dict(architecture=selected,seeds=list(SEEDS),
        context_dim=train['context'].shape[-1],history_dim=train['history'].shape[-1],candidate_dim=train['candidate'].shape[-1],
        calibration=settings[selected],quality_scale=float(normalizer.values['quality_scale']),feature_version=manifest['version'],
        quality_definition=manifest['quality_definition'],source='self-generated training, NOT official competition performance',
        validation_reliable=bool(reliable),dataset_manifest_sha256=identity))
    report=dict(selection=selected,validation=comparison,device_benchmarks=devices,tests={})
    for split in ('test','weather_test','site_test'):
        data=load_dataset(output,[split]);predictions={}
        for name,baseline in baselines.items():
            predictions[name]=calibrate_apply(baseline.predict(data),settings[name])
        for architecture,members in models.items():
            raw=[model_prediction(m,data,normalizer,devices[architecture]['selected']) for m in members]
            predictions[architecture]=calibrate_apply(np.mean(raw,axis=0),settings[architecture])
            for i,p in enumerate(raw):
                predictions[f'{architecture}-{SEEDS[i]}']=calibrate_apply(p,settings[f'{architecture}-{SEEDS[i]}'])
        report['tests'][split]={name:metrics(data['y'],p) for name,p in predictions.items()}
        flat_y=data['y'].reshape(-1,4)
        report['tests'][split]['reliability_bins']={}
        for name in ('constant','lookup',*ARCHITECTURES):
            p=predictions[name].reshape(-1,9)
            report['tests'][split]['reliability_bins'][name]={
                'severe_block':reliability_bins(p[:,5],flat_y[:,2]),
                **{band:reliability_bins(p[:,6+i],flat_y[:,3]==i)
                   for i,band in enumerate(('DARK','BRIGHT','BACKUP'))}}
        report['tests'][split]['scene_intervals']=scene_bootstrap(data['y'],predictions[selected],predictions['lookup'],data['scene'])
        report['tests'][split]['selected_slices']=slices(data,predictions[selected])
        for name,p in predictions.items():
            directory=output/'predictions'/split;directory.mkdir(parents=True,exist_ok=True)
            np.save(directory/f'{name}.npy',p)
        save_json(output/'report.json',report)
    # Fixed-architecture transfer checks; no held-card training, normalization or calibration data.
    report['leave_card_out']={};report['ablations']={}
    device=devices[selected]['selected']
    for card in 'abcd':
        fold_train=subset(train,train['card']!=card);fold_val=subset(validation,validation['card']!=card)
        fold_norm=Normalizer.fit(fold_train)
        path=output_models/f'leave-{card}-{selected}'
        model=train_job(path,selected,fold_train,fold_val,fold_norm,best_lrs[selected],60,101,device,identity+'-leave-'+card)
        fold_cal=subset(calibration,calibration['card']!=card)
        adjustment=calibrate_fit(fold_cal['y'],model_prediction(model,fold_cal,fold_norm,device))
        test=load_dataset(output,['test']);test=subset(test,test['card']==card)
        p=calibrate_apply(model_prediction(model,test,fold_norm,device),adjustment)
        report['leave_card_out'][card]=metrics(test['y'],p)
        save_json(output/'report.json',report)
    for mode in ('no_history','no_forecast'):
        limited=ablate(train,mode);limited_val=ablate(validation,mode);limited_norm=Normalizer.fit(limited)
        path=output_models/f'{mode}-{selected}'
        model=train_job(path,selected,limited,limited_val,limited_norm,best_lrs[selected],60,101,device,identity+'-'+mode)
        limited_cal=ablate(calibration,mode)
        adjustment=calibrate_fit(limited_cal['y'],model_prediction(model,limited_cal,limited_norm,device))
        test=ablate(load_dataset(output,['test']),mode)
        p=calibrate_apply(model_prediction(model,test,limited_norm,device),adjustment)
        report['ablations'][mode]=metrics(test['y'],p)
        save_json(output/'report.json',report)
    # Deployment timing measures the full three-member network ensemble on CPU, single thread.
    from .predict import Predictor
    predictor=Predictor(destination)
    report['cpu_ensemble']=predictor.benchmark(normalizer.apply(validation))
    primary=report['tests']['test']
    report['acceptance']=acceptance(primary[selected],primary['lookup'],report['cpu_ensemble'])
    report['passed']=all(report['acceptance'].values())
    report['completed']=True
    metadata=json.loads((destination/'metadata.json').read_text())
    metadata.update(final_test_passed=report['passed'],acceptance=report['acceptance'])
    save_json(destination/'metadata.json',metadata)
    save_json(output/'report.json',report)
    from .report import write_report
    write_report(output,report)
    print('COMPLETE '+json.dumps({'selected':selected,'passed':report['passed'],'acceptance':report['acceptance']}),flush=True)

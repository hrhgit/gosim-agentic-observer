"""Validation-only report selection, locked final tests and scene-level uncertainty."""
from __future__ import annotations

import hashlib
import itertools
import json
import shutil
import time
from functools import lru_cache
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np
import torch
from threadpoolctl import threadpool_limits

from research.observing_conditions.data import save_json
from .models import load_classifier,load_data,metrics
from .simulation import simulate,load_assets
from .core import FEATURE_NAMES
from .weather import hashes

FREE=(.5,.6,.7,.8,.9)
PAID=(.6,.7,.8,.9,.95,.99)
THRESHOLDS=[(f,p) for f,p in itertools.product(FREE,PAID) if p>=f]
KINDS=('logistic','boosting','forest','neural')


def fingerprint(output):
    output=Path(output);h=hashlib.sha256()
    for path in [output/'manifest.json',output/'weather_dependency.json',Path(__file__).with_name('core.py'),Path(__file__).with_name('simulation.py')]:
        h.update(path.read_bytes())
    return h.hexdigest()[:20]


@lru_cache(maxsize=8)
def assets(directory,track):
    return load_assets(directory,track)


@lru_cache(maxsize=8)
def classifier(directory):
    model,_=load_classifier(directory)
    if model.kind=='forest':model.base.n_jobs=1
    return model


def simulation_job(args):
    output,entry,track,kind,thresholds,signature=args;output=Path(output)
    torch.set_num_threads(1)
    directory=output/'scenarios'/entry['id']
    suffix='-'.join(str(v) for v in thresholds) if thresholds else 'fixed'
    model_hash=hashlib.sha256(json.dumps(hashes(output/'models'/kind),sort_keys=True).encode()).hexdigest()[:16] if kind in KINDS else 'official'
    cache=output/'simulations'/signature/model_hash/entry['id']/f'{kind}-{track}-{suffix}.json'
    if cache.exists():return json.loads(cache.read_text())
    p=np.load(directory/f'weather-prediction-{track}.npy',mmap_mode='r')
    weather=json.loads((output/'weather_dependency.json').read_text())['identity']
    a=assets(str(directory),track)
    # Common no-report opportunities determine event eligibility and delay denominators.
    baseline_path=output/'simulations'/signature/'official'/entry['id']/f'none-{track}-fixed.json'
    if kind!='none':
        if not baseline_path.exists():raise ValueError('baseline must precede dependent simulations')
        baseline=json.loads(baseline_path.read_text())['event_opportunities']
    else:baseline=None
    with threadpool_limits(limits=1):
        _,_,_,summary=simulate(directory,track,p,weather,'model' if kind in KINDS else kind,
            classifier(str(output/'models'/kind)) if kind in KINDS else None,thresholds or (.7,.9),baseline_events=baseline,assets=a)
    summary.update(scene=entry['id'],card=entry['card'],group=entry.get('group','main'),thresholds=list(thresholds),model=kind)
    save_json(cache,summary)
    return summary


def run_jobs(output,entries,kinds,workers=2,thresholds=None):
    output=Path(output);signature=fingerprint(output)
    jobs=[(str(output),entry,track,kind,pair,signature) for entry in entries for track in (0,1) for kind in kinds
          for pair in ((thresholds[kind],) if thresholds is not None and kind in thresholds else THRESHOLDS if kind in KINDS else [()])]
    results=[]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        tasks=[pool.submit(simulation_job,job) for job in jobs]
        for index,future in enumerate(as_completed(tasks),1):
            results.append(future.result())
            if index%20==0 or index==len(tasks):print(f'simulated {index}/{len(tasks)}',flush=True)
    return results


def by_scene(results):
    grouped={}
    for r in results:grouped.setdefault(r['scene'],[]).append(r)
    return grouped


def summarize(results):
    if not results:raise ValueError('empty evaluation')
    scenes=by_scene(results)
    mean=lambda key:float(np.mean([np.mean([r[key] for r in records]) for records in scenes.values()]))
    events=[e for r in results for e in r['events']];eligible=[e for e in events if e['eligible']]
    reports=[p for r in results for p in r['reports']]
    correct=sum(p['correct'] for p in reports)
    delays=[e['delay_exposures'] for e in events if e['detected'] and e['delay_exposures'] is not None]
    seconds=[e['delay_seconds'] for e in events if e['detected'] and e['delay_seconds'] is not None]
    quantiles=lambda a:dict(p50=float(np.median(a)),p90=float(np.quantile(a,.9))) if a else dict(p50=None,p90=None)
    return dict(scenes=len(scenes),traces=len(results),mean_score=mean('total'),false_per_scene=mean('false'),paid_false_per_scene=mean('paid_false'),
                correct_per_scene=mean('correct'),report_precision=correct/len(reports) if reports else None,report_count=len(reports),
                eligible_event_recall=sum(e['detected'] for e in eligible)/len(eligible) if eligible else None,
                all_event_recall=sum(e['detected'] for e in events)/len(events) if events else None,
                eligible_events=len(eligible),all_events=len(events),delay_exposures=quantiles(delays),delay_seconds=quantiles(seconds),
                counting='each scene equal weight; report/event ratios pool the two fixed traces; no-report exposure opportunities define eligibility')


def paired_interval(left,right,repetitions=2000):
    a=by_scene(left);b=by_scene(right)
    if set(a)!=set(b):raise ValueError('paired scenes do not match')
    values=np.array([np.mean([r['total'] for r in a[s]])-np.mean([r['total'] for r in b[s]]) for s in sorted(a)])
    rng=np.random.default_rng(41104)
    draws=values[rng.integers(len(values),size=(repetitions,len(values)))].mean(1)
    return dict(mean=float(values.mean()),ci95=np.quantile(draws,[.025,.975]).tolist(),scenes=len(values),repetitions=repetitions)


def measure(model,x):
    if model.kind=='forest':model.base.n_jobs=1
    inference=[]
    previous=torch.get_num_threads();torch.set_num_threads(1)
    with threadpool_limits(limits=1):
        for i in range(120):
            start=time.perf_counter();model.predict_proba(x[i%len(x):i%len(x)+1]);elapsed=(time.perf_counter()-start)*1000
            if i>=20:inference.append(elapsed)
    torch.set_num_threads(previous)
    return dict(p50_ms=float(np.median(inference)),p95_ms=float(np.quantile(inference,.95)),threads=1,repetitions=100,
                boundary='classifier + train-fitted missing-value transform + sigmoid calibration; excludes history aggregation')


def select(output,workers=2):
    output=Path(output);entries=[e for e in json.loads((output/'manifest.json').read_text())['entries'] if e['split']=='validation']
    lock=output/'selection.json'
    if lock.exists():
        selected=json.loads(lock.read_text())
        if selected['simulation_fingerprint']!=fingerprint(output):raise ValueError('evaluation changed after selection lock')
        if selected['model_hashes']!={k:hashes(output/'models'/k) for k in KINDS}:raise ValueError('model changed after selection lock')
        return selected
    baselines=run_jobs(output,entries,['none','rule','oracle'],workers)
    results=run_jobs(output,entries,KINDS,workers)
    choices={};validation=load_data(output,'validation')
    for kind in KINDS:
        candidates=[]
        for pair in THRESHOLDS:
            selected=[r for r in results if r['model']==kind and r['thresholds']==list(pair)]
            aggregate=summarize(selected)
            feasible=aggregate['false_per_scene']<=.5 and aggregate['paid_false_per_scene']<=.1
            candidates.append(dict(thresholds=list(pair),meets_false_report_targets=feasible,simulation=aggregate))
        feasible=[c for c in candidates if c['meets_false_report_targets']]
        if feasible:chosen=max(feasible,key=lambda c:c['simulation']['mean_score'])
        else:
            # Explicitly reported failed experiment, never silently claim acceptance.
            chosen=min(candidates,key=lambda c:(c['simulation']['paid_false_per_scene'],c['simulation']['false_per_scene'],-c['simulation']['mean_score']))
        model,meta=load_classifier(output/'models'/kind)
        chosen['latency']=measure(model,validation['x']);chosen['classification']=meta['validation_calibrated']
        choices[kind]=chosen|dict(all_thresholds=candidates)
    best=max(v['simulation']['mean_score'] for v in choices.values())
    tied=[kind for kind,v in choices.items() if v['simulation']['mean_score']>=best-5]
    kind=min(tied,key=lambda k:choices[k]['latency']['p95_ms'])
    result=dict(model=kind,thresholds={k:v['thresholds'] for k,v in choices.items()},validation=choices,
                baselines={k:summarize([r for r in baselines if r['model']==k]) for k in ('none','rule','oracle')},
                simulation_fingerprint=fingerprint(output),model_hashes={k:hashes(output/'models'/k) for k in KINDS},
                selection_basis='validation simulations only; within 5 scene-score points choose fastest',frozen=True)
    shutil.copytree(output/'models'/kind,output/'models/selected',dirs_exist_ok=True)
    save_json(lock,result)
    return result


def probability_intervals(data,p,repetitions=2000):
    ids=np.unique(data['scene']);values=[];bins=[]
    for scene in ids:
        mask=data['scene']==scene;y=data['y'][mask];q=p[mask]
        clipped=np.clip(q,1e-7,1-1e-7)
        weight=data['weight'][mask]
        values.append([np.sum(weight*(q-y)**2),np.sum(weight*(-y*np.log(clipped)-(1-y)*np.log1p(-clipped))),weight.sum()])
        groups=np.clip((q*10).astype(int),0,9)
        bins.append([[np.sum(groups==i),q[groups==i].sum(),y[groups==i].sum()] for i in range(10)])
    values=np.array(values);bins=np.array(bins)
    rng=np.random.default_rng(5104);indices=rng.integers(len(ids),size=(repetitions,len(ids)))
    draws=values[indices].sum(1);draws[:,:2]/=draws[:,2:3];totals=bins[indices].sum(1)
    ece=np.abs(totals[:,:,1]-totals[:,:,2]).sum(1)/np.maximum(totals[:,:,0].sum(1),1)
    return dict(brier_ci95=np.quantile(draws[:,0],[.025,.975]).tolist(),log_loss_ci95=np.quantile(draws[:,1],[.025,.975]).tolist(),
                ece_ci95=np.quantile(ece,[.025,.975]).tolist(),repetitions=repetitions,unit='complete scene')


def simulation_intervals(results,repetitions=2000):
    scenes=by_scene(results);ids=sorted(scenes);values=[];delays=[]
    for scene in ids:
        rows=scenes[scene];events=[e for r in rows for e in r['events']];eligible=[e for e in events if e['eligible']]
        correct=sum(r['correct'] for r in rows);reports=sum(len(r['reports']) for r in rows)
        values.append([np.mean([r['total'] for r in rows]),np.mean([r['false'] for r in rows]),np.mean([r['paid_false'] for r in rows]),
                       correct,reports,sum(e['detected'] for e in eligible),len(eligible),sum(e['detected'] for e in events),len(events)])
        delays.append([e['delay_exposures'] for e in events if e['detected'] and e['delay_exposures'] is not None])
    values=np.array(values,float);rng=np.random.default_rng(71994);indices=rng.integers(len(ids),size=(repetitions,len(ids)))
    draws=values[indices];means=draws[:,:,:3].mean(1);sums=draws.sum(1)
    interval=lambda x:np.quantile(x[np.isfinite(x)],[.025,.975]).tolist() if np.isfinite(x).any() else None
    ratio=lambda a,b:np.divide(a,b,out=np.full_like(a,np.nan),where=b>0)
    delay_draws=[]
    for sample in indices:
        a=[v for index in sample for v in delays[index]]
        delay_draws.append(np.quantile(a,[.5,.9]) if a else [np.nan,np.nan])
    delay_draws=np.array(delay_draws)
    return dict(mean_score_ci95=interval(means[:,0]),false_per_scene_ci95=interval(means[:,1]),paid_false_per_scene_ci95=interval(means[:,2]),
                report_precision_ci95=interval(ratio(sums[:,3],sums[:,4])),eligible_recall_ci95=interval(ratio(sums[:,5],sums[:,6])),
                all_recall_ci95=interval(ratio(sums[:,7],sums[:,8])),delay_median_ci95=interval(delay_draws[:,0]),delay_p90_ci95=interval(delay_draws[:,1]),
                repetitions=repetitions,unit='complete scene including both traces')


def slices(data,p):
    x=data['x'];get=lambda name:x[:,FEATURE_NAMES.index(name)]
    groups={'card':data['card'],'weather':np.where(data['weather_quality']<.4,'true Q_env<0.4',np.where(data['weather_quality']<.65,'true Q_env 0.4-0.65','true Q_env>=0.65')),
            'earthquake':np.where(data['quake'],'current public earthquake notice','no current earthquake notice'),
            'fault_strength':np.where(np.isnan(data['fault_multiplier']),'no unrepaired fault',np.where(data['fault_multiplier']<.55,'multiplier<0.55',np.where(data['fault_multiplier']<=.7,'multiplier 0.55-0.70','weak fault >0.70'))),
            'duration':np.where(get('duration')<=300/3600,'<=300s',np.where(get('duration')<=1800/3600,'301-1800s','>1800s')),
            'density':np.where(get('nights3:count')<=6,'<=6 exposures in last 3 observed nights','>6 exposures in last 3 observed nights'),'external_group':data['group']}
    result={}
    for dimension,values in groups.items():
        result[dimension]={}
        for value in np.unique(values):
            mask=values==value;subset={k:v[mask] for k,v in data.items()}
            result[dimension][str(value)]=metrics(subset,p[mask])
    return result


def test(output,workers=2):
    output=Path(output);selection=json.loads((output/'selection.json').read_text())
    if not selection['frozen'] or selection['simulation_fingerprint']!=fingerprint(output):raise ValueError('final-test selection is not frozen')
    if selection['model_hashes']!={k:hashes(output/'models'/k) for k in KINDS}:raise ValueError('model changed after selection lock')
    manifest=json.loads((output/'manifest.json').read_text());report=dict(selection=selection,tests={},source='self-generated fixed observation simulation ONLY')
    for split in ('test','external'):
        entries=[e for e in manifest['entries'] if e['split']==split]
        baseline=run_jobs(output,entries,['none','rule','oracle'],workers)
        results=run_jobs(output,entries,KINDS,workers,selection['thresholds'])
        data=load_data(output,split)
        summaries={k:summarize([r for r in baseline if r['model']==k]) for k in ('none','rule','oracle')}
        for kind in KINDS:
            chosen=[r for r in results if r['model']==kind];model,_=load_classifier(output/'models'/kind)
            with threadpool_limits(limits=1):p=model.predict_proba(data['x'])
            directory=output/'predictions'/split;directory.mkdir(parents=True,exist_ok=True);np.save(directory/f'{kind}.npy',p)
            available=data['x'][:,FEATURE_NAMES.index('evidence_exposures')]>0
            subset={k:v[available] for k,v in data.items()}
            summaries[kind]=dict(simulation=summarize(chosen),simulation_intervals=simulation_intervals(chosen),classification=metrics(data,p),
                 available_classification=metrics(subset,p[available]),probability_intervals=probability_intervals(subset,p[available]),
                 slices=slices(data,p),gain_vs_none=paired_interval(chosen,[r for r in baseline if r['model']=='none']),
                 gain_vs_rule=paired_interval(chosen,[r for r in baseline if r['model']=='rule']),
                 simulation_slices={dimension:{str(value):summarize([r for r in chosen if r[dimension]==value])
                    for value in sorted(set(r[dimension] for r in chosen))} for dimension in ('card','group')})
        report['tests'][split]=summaries
        save_json(output/'report.json',report)
    selected=report['tests']['test'][selection['model']];m=selected['simulation'];latency=selection['validation'][selection['model']]['latency']
    report['acceptance']={
        'report_precision':m['report_precision'] is not None and m['report_precision']>=.95,
        'eligible_event_recall':m['eligible_event_recall'] is not None and m['eligible_event_recall']>=.9,
        'delay_median':m['delay_exposures']['p50'] is not None and m['delay_exposures']['p50']<=3,
        'delay_p90':m['delay_exposures']['p90'] is not None and m['delay_exposures']['p90']<=8,
        'false_reports':m['false_per_scene']<=.5,'paid_false_reports':m['paid_false_per_scene']<=.1,
        'probability_reliability':selected['available_classification']['ece']<=.05,
        'positive_gain':selected['gain_vs_none']['mean']>0,
        'beats_rule_interval':selected['gain_vs_rule']['ci95'][0]>0,'inference_latency':latency['p95_ms']<=5}
    report['passed']=all(report['acceptance'].values());report['completed']=True
    save_json(output/'report.json',report)
    return report

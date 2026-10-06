"""Stateful closed-loop repair simulations with exact official scoring semantics."""
from __future__ import annotations

import bisect
import hashlib
import gzip
import json
import math
import time
from pathlib import Path

import numpy as np

from challenge.v4_runner import load_scenario, _data_loss_plan
from challenge.v4_scorer import WeatherTruth, BestLedger, required_penalty, uniformity_penalty
from research.observing_conditions.data import save_json
from research.observing_conditions.features import utc
from .core import Evidence, FEATURE_NAMES, PROGRAMS


def feature_sources():
    return {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (Path(__file__),Path(__file__).with_name('core.py'))}


def fault_label(weather,now):
    event=weather.active_fault(now)
    return bool(event and event.event_id not in weather.fault_repairs)


def exposure_scores(scenario, row, physics, weather):
    first,last=physics['pointers'][row['observe_index']:row['observe_index']+2]
    quality=physics['raw'][first:last].copy()
    for fi,fault in enumerate(weather.faults,1):
        at=weather.fault_repairs.get(fault.event_id)
        if at is not None:
            if utc(row['start'])<at<utc(row['end']):
                raise ValueError('cached physics requires reports between exposures')
            if at<=utc(row['start']):
                quality[:,fi]=physics['repaired'][first:last,fi]
    quality=quality.sum(axis=1)
    targets=[scenario.targets[i] for i in row['target_indices']]
    config=scenario.score_config
    band=physics['band'][first:last]
    actual=np.where(band>=config['program']['bands']['DARK'],0,np.where(band>=config['program']['bands']['BRIGHT'],1,2))
    mult=np.where(actual==PROGRAMS.index(row['program']),config['program']['multipliers'][row['program']],config['program']['mismatch_multiplier'])
    flux=np.array([t['feature_flux'] for t in targets]);weights=np.array([t['science_weight'] for t in targets])
    factors=np.minimum(flux*row['duration']*quality/(config['flux_zero_point']*config['exposure_zero_point_seconds']),1)
    scores=weights*factors*mult
    return scores,factors,quality,physics['hit'][first:last]


def load_assets(directory,track):
    directory=Path(directory)
    scenario=load_scenario(directory/'scenario.json')
    rows=json.loads((directory/f'schedule-{track}.json').read_text())
    with np.load(directory/f'physics-{track}.npz') as file:
        physics={k:file[k] for k in file.files}
    return scenario,rows,physics


def simulate(directory,track,predictions,weather_version,policy='none',predictor=None,thresholds=(.7,.9),save_records=False,collect_features=False,baseline_events=None,assets=None):
    directory=Path(directory)
    scenario,rows,physics=assets or load_assets(directory,track)
    faults=[e for e in scenario.events if e.event_type=='instrument_fault']
    weather=WeatherTruth(scenario.slots,faults)
    evidence=Evidence(scenario.score_config,weather_version)
    ledger=BestLedger();settlement=0.0;reports=[];feature_rows=[];labels=[];snapshots=[];decisions=[];input_times=[]
    loss=_data_loss_plan(scenario);loss_done=False;new_evidence=set()
    messages=scenario.bulletins+scenario.forecasts
    messages.sort(key=lambda m:utc(m['issued_at_utc']))
    times=[utc(m['issued_at_utc']) for m in messages];message_cursor=0
    report_times=[utc(rows[max(0,math.ceil(len(rows)*fraction)-1)]['start']) for fraction in (.25,.5,.75,1)] if rows else []
    report_cursor=0
    opportunities={f.event_id:[] for f in faults}
    public_path=directory/f'public-records-{track}.jsonl.gz'
    stream=gzip.open(public_path,'wt') if save_records else None

    def receive(now,observed_count):
        nonlocal message_cursor,loss_done
        last=bisect.bisect_right(times,now)
        evidence.messages(messages[message_cursor:last],now)
        message_cursor=last
        if loss and not loss_done and now>=loss.trigger_utc:
            first=math.floor(loss.window_start_fraction*observed_count);end=math.floor(loss.window_end_fraction*observed_count)
            ledger.invalidate_window(first,end,loss.event_id)
            evidence.messages([dict(record_type='state_resync',issued_at_utc=now.isoformat(),
                                    invalidated_window=dict(action_index_start=first,action_index_end_exclusive=end))],now)
            new_evidence.difference_update(range(first,end))
            loss_done=True

    def report(now):
        nonlocal settlement,new_evidence
        event=weather.active_fault(now)
        correct=fault_label(weather,now)
        delta=weather.report_fault(now,scenario.score_config)
        settlement+=delta
        result=dict(record_type='report_result',issued_at_utc=now.isoformat(),correct=correct,repaired=correct,score_delta=delta)
        evidence.messages([result],now)
        reports.append(dict(**result,event_id=event.event_id if correct else None))
        new_evidence.clear()

    try:
        for row in rows:
            start,end=utc(row['start']),utc(row['end'])
            receive(start,row['observe_index'])
            if policy=='scheduled' and track==1:
                while report_cursor<len(report_times) and start>=report_times[report_cursor]:
                    report(start);report_cursor+=1
            if policy=='oracle' and fault_label(weather,start):
                report(start)
            scores,factors,qualities,hits=exposure_scores(scenario,row,physics,weather)
            targets=[scenario.targets[i] for i in row['target_indices']]
            result=dict(action='observe',observe_index=row['observe_index'],assigned_count=len(targets),hit_count=int(hits.sum()),
                        hits=[dict(target_id=t['target_id'],score=round(float(s),6)) for t,s,hit in zip(targets,scores,hits) if hit])
            public=dict(observe_index=row['observe_index'],start=row['start'],end=row['end'],night=row['night'],duration=row['duration'],
                        program=row['program'],altitude=row['altitude'],azimuth=row['azimuth'],result=result,
                        predictions=[dict(target_id=t['target_id'],target={k:t[k] for k in ('feature_flux','science_weight','ra_deg','dec_deg')},
                                          issued_at_utc=row['start'],duration_seconds=row['duration'],weather_version=weather_version,
                                          prediction=p.tolist()) for t,p in zip(targets,predictions[row['observe_index']])])
            for target,factor,value,hit in zip(targets,factors,scores,hits):
                if hit:
                    ledger.record(row['observe_index'],target['target_id'],float(factor),float(value),start,end)
            input_start=time.perf_counter()
            evidence.observation(public)
            # Resync at the next decision must also remove the just-ended exposure if applicable.
            receive(end,row['observe_index']+1)
            info=evidence.info(end)
            vector=evidence.vector(end) if collect_features else None
            y=fault_label(weather,end)
            if collect_features:
                feature_rows.append(vector);labels.append(y)
            comparable=info['evidence_exposure_count']>0 and bool(evidence.records and evidence.records[-1]['observe_index']==row['observe_index'] and evidence.records[-1]['stats']['comparable'])
            if comparable:
                new_evidence.add(row['observe_index'])
                event=weather.active_fault(end)
                if event:
                    opportunities[event.event_id].append(end)
            snapshots.append(dict(observe_index=row['observe_index'],now=end.isoformat(),night=row['night'],label=int(y),
                                  comparable=bool(comparable),program=row['program'],duration=row['duration'],
                                  quake=bool(evidence.quake_active) if hasattr(evidence,'quake_active') else False,
                                  altitude=row['altitude'],assigned=len(targets),
                                  true_environment_band_quality=float(physics['band'][physics['pointers'][row['observe_index']]:physics['pointers'][row['observe_index']+1]].mean()) if targets else None,
                                  true_environment_quality=float(physics.get('environment',physics['band'])[physics['pointers'][row['observe_index']]:physics['pointers'][row['observe_index']+1]].mean()) if targets else None,
                                  true_fault_multiplier=weather.active_fault(end).instrument_efficiency_multiplier if y else None))
            input_elapsed=time.perf_counter()-input_start
            if stream:
                stream.write(json.dumps(public,allow_nan=False)+'\n')
            if policy=='model' and predictor and info['status']=='ready' and len(new_evidence)>=3:
                new_evidence.clear()
                if vector is None:
                    vector_start=time.perf_counter()
                    vector=evidence.vector(end)
                    input_elapsed+=time.perf_counter()-vector_start
                input_times.append(input_elapsed*1000)
                p=float(predictor.predict_proba(vector[None])[0])
                free=weather.false_reports_since_correct<scenario.score_config['reporting']['false_report_free_allowance']
                decisions.append(dict(now=end.isoformat(),observe_index=row['observe_index'],probability=p,
                                      evidence_exposures=info['evidence_exposure_count'],threshold=thresholds[0 if free else 1],free=free))
                if p>=thresholds[0 if free else 1]:
                    report(end)
            elif policy=='rule' and len(new_evidence)>=3:
                new_evidence.clear()
                if evidence.rule():
                    report(end)
        if rows:
            finish=utc(rows[-1]['end'])
            receive(scenario.survey_end,len(rows))
            if policy=='scheduled' and track==1:
                while report_cursor<len(report_times):
                    report(scenario.survey_end);report_cursor+=1
    finally:
        if stream:
            stream.close()
    best=ledger.best();factors=ledger.max_factors()
    missing,required=required_penalty(scenario.targets,factors,scenario.score_config)
    _,uniform=uniformity_penalty(scenario.targets,factors,scenario.score_config)
    science=sum(v[1] for v in best.values())
    event_results=[]
    for event in faults:
        found=next((r for r in reports if r['correct'] and r['event_id']==event.event_id),None)
        available=opportunities[event.event_id]
        if baseline_events is not None:
            available=[utc(t) for t in baseline_events[event.event_id]]
        at=utc(found['issued_at_utc']) if found else None
        eligible=len(available)>=3
        delay=sum(t<=at for t in available) if at and available else None
        event_results.append(dict(event_id=event.event_id,detected=bool(found),eligible=eligible,opportunities=len(available),
                                  delay_exposures=delay,delay_seconds=max(0,(at-available[0]).total_seconds()) if at and available else None,
                                  multiplier=event.instrument_efficiency_multiplier))
    summary=dict(policy=policy,track=track,total=science-required-uniform+settlement,
                 components=dict(science=science,required_penalty=-required,uniformity_penalty=-uniform,report_settlement=settlement),
                 reports=reports,correct=sum(r['correct'] for r in reports),false=sum(not r['correct'] for r in reports),
                 paid_false=sum(not r['correct'] and r['score_delta']<0 for r in reports),events=event_results,
                 exposures=len(rows),required_missing=missing,event_opportunities={k:[t.isoformat() for t in v] for k,v in opportunities.items()},
                 prediction_decisions=decisions,input_timing_ms=dict(p50=float(np.median(input_times)),p95=float(np.quantile(input_times,.95)),count=len(input_times)) if input_times else None,
                 source='self-generated fixed observation schedule, empty requests')
    return np.asarray(feature_rows,np.float32),np.asarray(labels,np.int8),snapshots,summary


def generate_features(output,weather):
    output=Path(output)
    manifest=json.loads((output/'manifest.json').read_text())
    for count,entry in enumerate(manifest['entries'],1):
        directory=output/'scenarios'/entry['id']
        marker=directory/'features_complete.json'
        if marker.exists():
            saved=json.loads(marker.read_text())
            if saved['weather_version']!=weather.version or saved['feature_sources']!=feature_sources():
                raise ValueError('feature weather version mismatch')
            continue
        arrays=[];answers=[]
        for track in (0,1):
            predictions=weather.predict_scene(directory,track)
            x,y,snapshots,summary=simulate(directory,track,predictions,weather.version,'scheduled' if track else 'none',save_records=True,collect_features=True)
            arrays.append(x);answers.append(y)
            save_json(directory/f'feedback-labels-{track}.json',snapshots)
            save_json(directory/f'training-simulation-{track}.json',summary)
        np.savez_compressed(directory/'features.npz',x=np.concatenate(arrays),y=np.concatenate(answers),track=np.repeat([0,1],[len(y) for y in answers]))
        save_json(marker,dict(weather_version=weather.version,rows=sum(map(len,answers)),feature_names=FEATURE_NAMES,
                             feature_sources=feature_sources(),features_sha256=hashlib.sha256((directory/'features.npz').read_bytes()).hexdigest()))
        print(f"features {count}/{len(manifest['entries'])} {entry['id']} {sum(map(len,answers))}",flush=True)

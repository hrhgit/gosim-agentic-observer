"""Continuous baseline trajectories with official hit/scoring functions.

This research runner avoids all-catalogue scans and repeated ledger rebuilding.
It is not a fair-clock or final competition-score benchmark. Public requests,
reports, hit geometry and exposure score feedback retain the official semantics.
"""
from __future__ import annotations

import bisect
import copy
import csv
import gzip
import importlib.util
import json
import math
from pathlib import Path
import sys
import time
from functools import lru_cache

import numpy as np

from challenge.v4_fiber_map import FiberGrid, min_altitude_during, radec_to_altaz
from challenge.v4_runner import normalize_action, _request_public_record
from challenge.v4_scorer import (WeatherTruth, score_target_exposure, program_band,
                                program_multiplier, lunar_quality_factor)
from challenge.v4_workflow import V4Workflow
from challenge.v4_observation_requests import generate_requests
from .data import ROOT, make_config, generate_weather_files, save_json
from .features import utc, minimum_altitude
from .adaptation_feedback import summarize_exposure


@lru_cache(maxsize=1)
def baseline_module():
    directory=ROOT/'agents/claude-baseline-v1/agent'
    if str(directory) not in sys.path:
        sys.path.insert(0,str(directory))
    spec=importlib.util.spec_from_file_location('conditions_baseline_agent',directory/'agent.py')
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def build_bundle(entry, directory):
    directory=Path(directory); bundle=directory/'bundle'
    if (bundle/'config/v4_scenario.json').exists():
        return bundle
    config,site,_,score=make_config(entry)
    public_fiber=json.loads((ROOT/'cards'/entry['card']/'config/v4_fiber_config.json').read_text())
    site=dict(public_fiber['site'],minimum_altitude_deg=30.)
    config['site']={k:v for k,v in site.items() if k!='minimum_altitude_deg'}
    save_json(directory/'weather_config.json',config)
    generate_weather_files(config,bundle/'generated',entry)
    for folder in ('config','public','truth'):
        (bundle/folder).mkdir(parents=True,exist_ok=True)
    import shutil
    for name in ('v4_fiber_config.json','v4_score_config.json'):
        shutil.copyfile(ROOT/'cards'/entry['card']/'config'/name,bundle/'config'/name)
    for name in ('targets.csv','footprint.csv','v4_night_calendar.csv','taskcard.zh.md','taskcard.en.md'):
        shutil.copyfile(ROOT/'cards'/entry['card']/'public'/name,bundle/'public'/name)
    for name in ('v4_bulletins.jsonl','v4_forecasts.jsonl'):
        shutil.copyfile(bundle/'generated'/name,bundle/'public'/name)
    for name in ('v4_slots.csv','v4_events.csv','v4_weather_truth.csv','v4_earthquake_effects.csv'):
        shutil.copyfile(bundle/'generated'/name,bundle/'truth'/name)
    fiber=json.loads((bundle/'config/v4_fiber_config.json').read_text())
    generate_requests(seed=entry['seed']+20000000,targets_csv=bundle/'public/targets.csv',
        night_calendar_csv=bundle/'public/v4_night_calendar.csv',slots_csv=bundle/'truth/v4_slots.csv',
        site=site,minimum_altitude_deg=site['minimum_altitude_deg'],
        output_path=bundle/'truth/v4_observation_requests.jsonl',
        settings=dict(count=6,targets_per_request=8,minimum_completion_fraction=.75,
                      window_nights=3,completion_reward=100.,include_required_targets=False),
        flux_zero_point=score['flux_zero_point'],exposure_zero_point_seconds=score['exposure_zero_point_seconds'],
        min_duration_seconds=fiber['exposure']['min_duration_seconds'],
        max_duration_seconds=fiber['exposure']['max_duration_seconds'])
    products=dict(targets_csv='../public/targets.csv',footprint_csv='../public/footprint.csv',
        night_calendar_csv='../public/v4_night_calendar.csv',bulletins_jsonl='../public/v4_bulletins.jsonl',
        forecasts_jsonl='../public/v4_forecasts.jsonl',slots_csv='../truth/v4_slots.csv',
        weather_truth_csv='../truth/v4_weather_truth.csv',events_csv='../truth/v4_events.csv',
        earthquake_effects_csv='../truth/v4_earthquake_effects.csv',
        observation_requests_jsonl='../truth/v4_observation_requests.jsonl')
    site=dict(public_fiber['site'],sun_altitude_limit_deg=-18.)
    save_json(bundle/'config/v4_scenario.json',dict(schema_version='v4-scenario-v1',
        name=entry['id']+'-SELF-GENERATED',site=site,minimum_altitude_deg=30.,
        fiber_config='v4_fiber_config.json',score_config='v4_score_config.json',products=products,
        limits=dict(global_wallclock_seconds=900),stress=dict(enabled=False),
        task_card=dict(card_id=entry['card'].upper()+'-SELF-GENERATED')))
    save_json(directory/'source.json',dict(source='Formal public A-D + SELF-GENERATED weather/events/requests',
        official_hidden_data=False,entry=entry,stress_loss_or_offset=False))
    return bundle


def public_state(sky):
    rho,w=sky.levels()
    return dict(weather=w,throughput=rho,efficiency=sky.e,has_measurement=sky.measured,
        last_measurement_utc=sky.tonight[-1][0] if sky.tonight else None,
        suspected_fault=sky.suspect,suspected_closure=sky.closed_suspect or sky.site_closed_now,quake_notice=sky.quake_now,
        terrain=[dict(centre=t.centre,sure=t.sure,top=t.top,floor=t.floor,box=t.box) for t in sky.terrain],
        directional_notices=list(sky.dir_now))


def request_snapshot(request, progress):
    public=_request_public_record(request)
    completed=[t for t in request['target_ids'] if progress.get(t,0)>=request['completion_factor_threshold']]
    return dict(public,completed_target_ids=completed,completed_count=len(completed),
                completed=len(completed)>=request['minimum_completed'])


def run(entry, directory, *, official_audit=False):
    directory=Path(directory)
    complete=directory/'trajectory_complete.json'
    if complete.exists():
        return json.loads(complete.read_text())
    started=time.perf_counter(); bundle=build_bundle(entry,directory)
    workflow=V4Workflow(bundle); s=workflow.scenario
    initialize=workflow.initialize_payload(900.,1.)
    module=baseline_module(); module.log=lambda *a:None
    bot=module.Agent(initialize)
    if entry.get('policy')=='alternate':
        bot.knobs['kappa']=.9
        bot.knobs['dark_probe_every']=4.;bot.sv.sky.dark_probe_every=4
    targets={t['target_id']:t for t in s.targets}; grid=FiberGrid.from_config(s.fiber_config)
    weather=WeatherTruth(s.slots,[e for e in s.events if e.event_type=='instrument_fault'])
    directions=[e for e in s.events if e.scope_type=='HORIZON_SECTOR']
    messages=sorted(s.bulletins+s.forecasts,key=lambda m:utc(m['issued_at_utc']))
    mt=[utc(m['issued_at_utc']) for m in messages]; cursor=0
    request_cursor=0;progress={r['request_id']:{} for r in s.observation_requests}
    announced=set(); latest_bulletin=latest_forecast=None
    pending_messages=[]; last_result=None; previous_record=None
    history=[]; traces=[]; exposures=[]; reservoir=[]
    rng=np.random.default_rng(entry['seed']+40000000)
    slot_starts=[slot.start_utc for slot in s.slots]
    night_ends={slot.slot_id.split('-S')[0]:slot.end_utc for slot in s.slots}
    now=s.survey_start; observe_index=0; decision_count=0; best={}; running=0.;reports=0
    audit_rows=[]
    while now < s.survey_end:
        new=list(pending_messages);pending_messages=[]
        end_cursor=bisect.bisect_right(mt,now)
        for m in messages[cursor:end_cursor]:
            new.append(m)
            if m['record_type']=='bulletin':latest_bulletin=m
            elif m['record_type']=='forecast':latest_forecast=m
        cursor=end_cursor
        while request_cursor<len(s.observation_requests) and s.observation_requests[request_cursor]['issued_at_utc']<=now:
            r=s.observation_requests[request_cursor];new.append(_request_public_record(r));request_cursor+=1
        for r in s.observation_requests[:request_cursor]:
            if now>=r['deadline_utc'] and r['request_id'] not in announced:
                snap=request_snapshot(r,progress[r['request_id']]);announced.add(r['request_id'])
                new.append(dict(record_type='request_result',issued_at_utc=now.isoformat(),
                    request_id=r['request_id'],completed=snap['completed'],
                    completed_target_ids=snap['completed_target_ids'],
                    score_delta=r['completion_reward'] if snap['completed'] else 0.))
        snapshot=dict(now_utc=now.isoformat(),survey_end_utc=s.survey_end.isoformat(),
            observe_action_index=observe_index,running_total=round(running,6),latest_bulletin=latest_bulletin,
            latest_forecast=latest_forecast,new_messages=new,last_result=last_result,
            active_requests=[request_snapshot(r,progress[r['request_id']]) for r in s.observation_requests[:request_cursor]
                             if now<r['deadline_utc']])
        raw=bot.decide(snapshot)
        decision_count+=1
        if previous_record is not None:
            previous_record.update(estimated_efficiency=bot.sv.sky.e,
                suspected_fault=bot.sv.sky.suspect,quake_notice=bot.sv.sky.quake_now)
            history.append(summarize_exposure(previous_record,targets,initialize['site'],initialize['scoring']))
            previous_record=None
        state=public_state(bot.sv.sky)
        if state['last_measurement_utc'] is not None:
            from datetime import datetime, timezone
            state['last_measurement_utc']=datetime.fromtimestamp(state['last_measurement_utc'],timezone.utc).isoformat()
        action=normalize_action(raw,s,targets,now)
        kind=action['action']
        if kind=='finish':break
        if kind=='report':
            repaired=weather.active_fault(now) is not None
            delta=weather.report_fault(now,s.score_config)
            last_result=dict(action='report',correct=repaired,repaired=repaired,score_delta=delta);reports+=1
            pending_messages=[dict(record_type='report_result',issued_at_utc=now.isoformat(),
                                   correct=repaired,repaired=repaired,score_delta=delta)]
            continue
        if kind=='wait':
            from datetime import timedelta
            now=min(action.get('until_utc',now+timedelta(seconds=action.get('duration_seconds',0))),s.survey_end)
            last_result=dict(action='wait');continue
        from datetime import timedelta
        end=min(now+timedelta(seconds=action['duration_seconds']),s.survey_end)
        ix=bisect.bisect_right(slot_starts,now)-1
        if ix>=0 and now<s.slots[ix].end_utc:end=min(end,night_ends[s.slots[ix].slot_id.split('-S')[0]])
        duration=int((end-now).total_seconds())
        if duration<=0:raise ValueError('baseline submitted empty exposure')
        state_history=copy.deepcopy(history[-8:])
        item=dict(now_utc=now.isoformat(),baseline_state=state,feedback_history=state_history,
                  requested_duration=action['duration_seconds'],decision_index=decision_count,
                  observe_index=observe_index)
        # Uniform reservoir over actual observe decisions, supplemented by public first/transition cases.
        if len(reservoir)<64:reservoir.append(item)
        else:
            chosen=int(rng.integers(observe_index+1))
            if chosen<64:reservoir[chosen]=item
        if not history or (history and utc(history[-1]['end_utc']).date()!=now.date()) or new:
            traces.append(item)
        active=[e for e in directions if e.overlaps(now,end)]; segments=list(weather.segments(now,end))
        hits=[]
        for fiber_id,target_id in action['assignments'].items():
            target=targets[target_id]
            if min_altitude_during(target['ra_deg'],target['dec_deg'],now,end,s.config)<s.minimum_altitude_deg:continue
            landing=grid.classify_target(target['ra_deg'],target['dec_deg'],now,
                action['pointing']['alt_deg'],action['pointing']['az_deg'],
                s.site['latitude_deg'],s.site['longitude_deg'],0.,0.)
            if landing.region!='glass' or int(landing.fiber_id)!=int(fiber_id):continue
            def position(moment,t=target):
                return radec_to_altaz(t['ra_deg'],t['dec_deg'],moment,s.site['latitude_deg'],s.site['longitude_deg'])
            b=weather.band_quality(now,end,s.score_config,lambda m:position(m)[0],
                lambda m,t=target:lunar_quality_factor(t['ra_deg'],t['dec_deg'],m,s.site,s.score_config))
            mult=program_multiplier(action['program'],program_band(b,s.score_config),s.score_config)
            scored=score_target_exposure(target,segments,active,weather,duration,mult,s.score_config,now,position,s.site)
            value=round(scored.score,6);hits.append(dict(target_id=target_id,score=value))
            prev=best.get(target_id,0.);best[target_id]=max(prev,scored.score);running+=best[target_id]-prev
            for r in s.observation_requests[:request_cursor]:
                if r['issued_at_utc']<=now and end<=r['deadline_utc'] and target_id in r['target_ids']:
                    table=progress[r['request_id']];table[target_id]=max(table.get(target_id,0.),scored.factor)
            if official_audit and len(audit_rows)<64:
                audit_rows.append(dict(target_id=target_id,quality=scored.quality,score=scored.score,
                    band=b,start_utc=now.isoformat(),end_utc=end.isoformat(),program=action['program']))
        record=dict(start_utc=now.isoformat(),end_utc=end.isoformat(),program=action['program'],
            pointing=action['pointing'],assigned_count=len(action['assignments']),hits=hits,
            assignments=action['assignments'],baseline_state_before=state,feedback_history_before=state_history,
            estimated_efficiency=state['efficiency'],suspected_fault=state['suspected_fault'],quake_notice=state['quake_notice'],
            observe_index=observe_index)
        exposures.append(record);previous_record=record
        last_result=dict(action='observe',observe_index=observe_index,assigned_count=record['assigned_count'],
                         hit_count=len(hits),hits=hits)
        observe_index+=1;now=end
    # Retain the true continuous records, regardless of which decision points are trained on.
    chosen={r['decision_index']:r for r in reservoir}
    special=sorted(traces,key=lambda r:r['decision_index'])
    if special:
        for ix in np.linspace(0,len(special)-1,min(16,len(special)),dtype=int):chosen[special[ix]['decision_index']]=special[ix]
    selected=sorted(chosen.values(),key=lambda r:r['decision_index'])
    if len(selected)>64:
        selected=[selected[i] for i in np.linspace(0,len(selected)-1,64,dtype=int)]
    with gzip.open(directory/'exposures.jsonl.gz','wt') as f:
        for r in exposures:f.write(json.dumps(r,separators=(',',':'))+'\n')
    save_json(directory/'decisions.json',selected)
    save_json(directory/'audit_exposures.json',audit_rows)
    complete_value=dict(entry=entry,exposures=observe_index,decisions=decision_count,reports=reports,
        selected_decisions=len(selected),nights=len(initialize['survey']['nights']),
        elapsed_seconds=time.perf_counter()-started,terminated_at=now.isoformat(),
        survey_end_utc=s.survey_end.isoformat(),complete_period=now>=s.survey_end,
        engine='research continuous runner; official normalize/hit/min-height/scorer and public baseline',
        scope='no stress data loss or pointing offset; simulated faults and requests present; not competition score')
    if not complete_value['complete_period']:raise ValueError('continuous trajectory ended before survey end')
    save_json(complete,complete_value);return complete_value

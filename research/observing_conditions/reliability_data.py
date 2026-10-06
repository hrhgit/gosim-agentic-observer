"""Night-opening samples from complete trajectories, retaining causal snapshots.

This changes which recorded decisions are inspected, never the observation policy.
No truth enters the history freshness calculation or the sampling choice.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import gzip
import json
from pathlib import Path
import time
import numpy as np
import torch
from challenge.v4_workflow import V4Workflow
from .adaptation_data import entries, build_samples
from .adaptation_scaling import OLD, DEFAULT as SCALING, read, concat, fresh_entries
from .calibration_experiment import DEFAULT as CAL, existing_entries, new_entries as previous_final
from .data import ROOT, save_json
from .features import utc

DEFAULT=ROOT/'artifacts/observing_conditions-reliability'


def final_entries():
    return [dict(id=f'reliable-{card}-{i}',card=card,split='test',seed=1780000+ci*1000+i,
                 policy='baseline',weather_shift=bool(i%2)) for ci,card in enumerate('abcd') for i in range(4)]


def sources(split, output=DEFAULT):
    if split=='train':return [(e,OLD/'scenarios'/e['id']) for e in entries() if e['split']=='train']
    if split=='calibration':return existing_entries('calibration')
    if split=='validation':
        return existing_entries('validation')+[(e,CAL/'scenarios'/e['id']) for e in previous_final()]
    if split=='test':return [(e,output/'scenarios'/e['id']) for e in final_entries()]
    if split=='calibration_extra':return [(e,OLD/'scenarios'/e['id']) for e in entries() if e['split']=='test']
    raise ValueError('unknown scene partition')


def freshness(now_utc, history, nights):
    now=utc(now_utc)
    current=next((n for n in nights if utc(n['observing_start_utc'])<=now<utc(n['observing_end_utc'])),None)
    if current is None:raise ValueError('decision must be within a public observing night')
    completed=[h for h in history if utc(h['end_utc'])<=now][-8:]
    start=utc(current['observing_start_utc'])
    fresh=sum(start<=utc(h['end_utc'])<=now for h in completed)
    return dict(all_history_count=len(completed),fresh_history_count=fresh,
                history_count=fresh,never_observed=len(completed)==0)


def enrich(data, decisions, initialize):
    lookup={d['observe_index']:d for d in decisions}
    updated=[]
    for row in data['meta']:
        d=lookup[row['observe_index']]
        public=freshness(row['now_utc'],d['feedback_history'],initialize['survey']['nights'])
        updated.append(dict(row,**public,baseline_measurement_available=bool(d['baseline_state']['has_measurement'])))
    data['meta']=updated
    return data


def sample_worker(args):
    entry,original,output=args;torch.set_num_threads(1)
    target=output/'night_samples/scenarios'/entry['id'];target.mkdir(parents=True,exist_ok=True)
    if not (target/'bundle').exists():(target/'bundle').symlink_to(original/'bundle',target_is_directory=True)
    init=V4Workflow(original/'bundle').initialize_payload(900.,1.)
    nights=init['survey']['nights'];groups={};records=[]
    with gzip.open(original/'exposures.jsonl.gz','rt') as stream:
        for line in stream:
            r=json.loads(line);now=utc(r['start_utc'])
            ni=next(i for i,n in enumerate(nights) if utc(n['observing_start_utc'])<=now<utc(n['observing_end_utc']))
            d=dict(now_utc=r['start_utc'],baseline_state=r['baseline_state_before'],
                   feedback_history=r['feedback_history_before'],observe_index=r['observe_index'])
            records.append(d);groups.setdefault(ni,[]).append(d)
    rng=np.random.default_rng(entry['seed']+41000000)
    indices=np.array(sorted(groups));open_nights=np.unique(np.r_[indices[0],rng.choice(indices[1:],min(15,len(indices)-1),replace=False)])
    early_nights=rng.choice(indices,min(16,len(indices)),replace=False)
    selected={d['observe_index']:d for d in records[:8]}
    for ni in open_nights:
        d=groups[int(ni)][0];selected[d['observe_index']]=d
    for ni in early_nights:
        pool=groups[int(ni)][1:8]
        if pool:
            d=pool[int(rng.integers(len(pool)))];selected[d['observe_index']]=d
    decisions=[selected[k] for k in sorted(selected)]
    path=target/'decisions.json'
    if path.exists() and json.loads(path.read_text())!=decisions:raise ValueError('sampling changed')
    save_json(path,decisions)
    info=build_samples(entry,output/'night_samples')
    data=read(target,entry['id']);enrich(data,decisions,init);save_json(target/'sample_metadata.json',data['meta'])
    save_json(target/'sampling.json',dict(source='SELF-GENERATED full continuous observation trajectory',
        available_exposures=len(records),available_observed_nights=len(groups),decision_groups=len(decisions),
        first_survey_indices=[d['observe_index'] for d in records[:8]],
        opening_nights=list(map(int,open_nights)),early_nights=list(map(int,early_nights)),
        input_snapshot='before exposure; full last-eight history retained',
        selection_uses=['public observing calendar','exposure order','independent sampling seed'],truth_used=False))
    return dict(scene=entry['id'],**info,groups=len(decisions))


def prepare(output=DEFAULT,split='development',workers=4):
    output=Path(output);began=time.perf_counter()
    todo=sum([sources(s,output) for s in ('train','calibration','validation')],[]) if split=='development' else sources(split,output)
    result=[]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures=[pool.submit(sample_worker,(e,d,output)) for e,d in todo]
        for future in as_completed(futures):
            value=future.result();result.append(value);print('night samples',value['scene'],value['records'],flush=True)
    save_json(output/f'night_sampling_{split}.json',dict(wall_seconds=time.perf_counter()-began,workers=workers,scenes=result))


def load(output,split,night=False):
    items=[]
    for e,directory in sources(split,output):
        if night:directory=output/'night_samples/scenarios'/e['id']
        init=V4Workflow(directory/'bundle').initialize_payload(900.,1.)
        data=enrich(read(directory,e['id']),json.loads((directory/'decisions.json').read_text()),init)
        data['weather']=np.full(len(data['y']),'changed' if e['weather_shift'] else 'normal')
        items.append(data)
    return concat(items)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,default=DEFAULT)
    p.add_argument('--split',choices=('development','test','calibration_extra'),default='development');p.add_argument('--workers',type=int,default=4)
    a=p.parse_args();prepare(a.output,a.split,a.workers)

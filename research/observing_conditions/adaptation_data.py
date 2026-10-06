"""Small decision-sampled training tables from complete continuous trajectories."""
from __future__ import annotations

import copy
from datetime import timedelta
import gzip
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import torch

from challenge.v4_fiber_map import altaz_to_radec
from .data import ROOT, save_json, read_truth, labels, source_hashes
from .features import PublicTimeline, candidate_features, utc
from .information_predict import InformationPredictor
from .information_model import numeric, calibration_apply
from .adaptation_feedback import history_array, state_array, public_online_bounds, HISTORY_NAMES, STATE_NAMES
from .adaptation_rollout import baseline_module, build_bundle
from challenge.v4_workflow import V4Workflow

VERSION='conditions-adaptation-v1'
DEFAULT=ROOT/'artifacts/observing_conditions-adaptation'
HORIZONS=('15m','30m','60m','next_night')
QUALITY_INDEX=242+29+12


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def entries():
    return [dict(id=f'adapt-{card}-{i}',card=card,split=('train' if i<4 else 'validation' if i==4 else 'calibration' if i==5 else 'test'),
                 seed=1260000+ci*1000+i,policy='alternate' if i==7 else 'baseline',weather_shift=i==7)
            for ci,card in enumerate('abcd') for i in range(8)]


def protocol(output=DEFAULT):
    output=Path(output); output.mkdir(parents=True,exist_ok=True)
    base=ROOT/'artifacts/observing_conditions-information/models/selected'
    sources=source_hashes()
    baseline=ROOT/'agents/claude-baseline-v1/agent'
    sources.update({str(p.relative_to(ROOT)):digest(p) for p in baseline.rglob('*.py')})
    spec=dict(version=VERSION,source='Formal A-D public inputs + explicitly SELF-GENERATED weather/events/requests',
        official_hidden_inputs=False,entries=entries(),sources=sources,
        foundation={str(p.relative_to(ROOT)):digest(p) for p in (base/'weights.pt',base/'normalizer.npz',base/'metadata.json')},
        history=HISTORY_NAMES,state=STATE_NAMES,positions_per_decision=8,decisions_per_scene=64,
        horizons=HORIZONS,exposure_durations=[60,300,600,900,1800,3600],
        split_rule='whole weather scene; no scene crosses partitions',
        training='frozen foundation + one new 32-unit GRU for 10 epochs; each LightGBM output fit once',
        seed=101,initializations=1,learning_rate=.001,optimizer='AdamW',
        online=dict(batch_every_clean_exposures=8,replay_exposures=64,gradient_steps=1,
                    head_learning_rate=.0001,bias_learning_rate=.05,
                    labels='public unsaturated known-match score inversion / ESTIMATED efficiency; no truth',
                    change='quality head only; U and probability heads fixed'),
        foundation_next_night='Existing foundation was trained only up to +60m. Next-night transfer is exploratory and recalibrated.',
        mean_quality='Q_env, efficiency=1, includes weather/directional events; no quality cap at 1',
        acceptance=dict(quality_score_improvement=.05,online_improvement=.05,coverage=[.85,.95],ece=.05),
        test_rule='Freeze model selection, online rules and calibration before fresh test labels; test is not retuned')
    spec=json.loads(json.dumps(spec))
    path=output/'protocol.json'
    if path.exists() and json.loads(path.read_text())!=spec:raise ValueError('protocol changed; use a new directory')
    save_json(path,spec);return spec


from .adaptation_features import public_features


def build_samples(entry,output=DEFAULT):
    torch.set_num_threads(1)
    directory=Path(output)/'scenarios'/entry['id']
    if (directory/'samples_complete.json').exists():return json.loads((directory/'samples_complete.json').read_text())
    workflow=V4Workflow(directory/'bundle');initialize=workflow.initialize_payload(900.,1.)
    foundation=InformationPredictor()
    s=workflow.scenario;timeline=PublicTimeline(s.bulletins,s.forecasts)
    weather,events=read_truth(directory/'bundle/truth')
    records=json.loads((directory/'decisions.json').read_text())
    rng=np.random.default_rng(entry['seed']+30000000)
    fields={k:[] for k in ('x','history','y','base','hold','coordinates')};meta=[]
    skipped=0
    for record in records:
        now=utc(record['now_utc']);duration=int(rng.choice((60,300,600,900,1800,3600)))
        current=next(n for n in initialize['survey']['nights'] if utc(n['observing_start_utc'])<=now<utc(n['observing_end_utc']))
        following=next((n for n in initialize['survey']['nights'] if utc(n['observing_start_utc'])>now),None)
        starts=[now+timedelta(seconds=h) for h in (900,1800,3600)]+[utc(following['observing_start_utc']) if following else None]
        for horizon,start in zip(HORIZONS,starts):
            night=following if horizon=='next_night' else current
            if start is None or start>=utc(night['observing_end_utc']):skipped+=1;continue
            end=min(start+timedelta(seconds=duration),utc(night['observing_end_utc']))
            coords=[]
            for direction in range(8):
                # Eight ground directions, rotating elevation strata across decisions; no catalogue ID input.
                alt=float(rng.uniform(35,85));az=(direction*45+float(rng.uniform(-15,15)))%360
                ra,dec=altaz_to_radec(alt,az,start,initialize['site']['latitude_deg'],initialize['site']['longitude_deg'])
                coords.append((ra,dec))
            coords=np.array(coords)
            x,h,base,hold,eligible=public_features(now,start,end,duration,coords,initialize,timeline,
                record['baseline_state'],record['feedback_history'],foundation)
            if not eligible.all():
                # Keep known ineligible rows out of quality training, not out of runtime output.
                pass
            y=labels(coords[:,0],coords[:,1],start,end,weather,events,initialize['site'],initialize['scoring'])
            for k,v in [('x',x[eligible]),('history',np.broadcast_to(h,(int(eligible.sum()),*h.shape))),
                        ('y',y[eligible]),('base',base[eligible]),('hold',hold[eligible]),('coordinates',coords[eligible])]:
                fields[k].append(v)
            meta.extend([dict(now_utc=now.isoformat(),start_utc=start.isoformat(),end_utc=end.isoformat(),
                duration_seconds=duration,horizon=horizon,observe_index=record['observe_index'],
                history_count=len(record['feedback_history']),suspected_fault=record['baseline_state']['suspected_fault'])
                for _ in range(int(eligible.sum()))])
    arrays={k:np.concatenate(v).astype(np.float32) for k,v in fields.items()}
    np.savez_compressed(directory/'samples.npz',**arrays)
    save_json(directory/'sample_metadata.json',meta)
    value=dict(records=len(meta),skipped_windows=skipped,x_dim=arrays['x'].shape[-1],history_dim=len(HISTORY_NAMES),
               horizons={h:sum(m['horizon']==h for m in meta) for h in HORIZONS})
    save_json(directory/'samples_complete.json',value);return value


def build_online_records(entry,output=DEFAULT):
    """Inputs and proxy labels for online updates. Environment truth is deliberately absent."""
    torch.set_num_threads(1)
    directory=Path(output)/'scenarios'/entry['id']
    if (directory/'online_complete.json').exists():return json.loads((directory/'online_complete.json').read_text())
    workflow=V4Workflow(directory/'bundle');init=workflow.initialize_payload(900.,1.)
    targets={t['target_id']:t for t in workflow.scenario.targets};timeline=PublicTimeline(workflow.scenario.bulletins,workflow.scenario.forecasts)
    foundation=InformationPredictor()
    fields={k:[] for k in ('x','history','lower','upper','available_at','exposure_index')}
    with gzip.open(directory/'exposures.jsonl.gz','rt') as file:
        for line in file:
            record=json.loads(line)
            bounds=public_online_bounds(record,targets,init['scoring'])
            if not bounds:continue
            start,end=utc(record['start_utc']),utc(record['end_utc'])
            coords=np.array([[targets[r['target_id']]['ra_deg'],targets[r['target_id']]['dec_deg']] for r in bounds])
            x,h,base,hold,eligible=public_features(start,start,end,int((end-start).total_seconds()),coords,init,timeline,
                record['baseline_state_before'],record['feedback_history_before'],foundation)
            fields['x'].append(x);fields['history'].append(np.broadcast_to(h,(len(bounds),*h.shape)))
            for key in ('lower','upper'):fields[key].extend(r['quality_'+key] for r in bounds)
            fields['available_at'].extend([end.timestamp()]*len(bounds));fields['exposure_index'].extend([record['observe_index']]*len(bounds))
    if fields['x']:
        arrays={k:(np.concatenate(v) if k in ('x','history') else np.array(v)) for k,v in fields.items()}
    else:
        arrays=dict(x=np.empty((0,304),np.float32),history=np.empty((0,8,len(HISTORY_NAMES)),np.float32),
                    lower=np.array([]),upper=np.array([]),available_at=np.array([]),exposure_index=np.array([]))
    np.savez_compressed(directory/'online_public.npz',**arrays)
    value=dict(public_proxy_rows=len(arrays['x']),clean_exposures=len(np.unique(arrays['exposure_index'])),
               contains_environment_truth=False,label_limitation='estimated instrument efficiency; known-match unsaturated only')
    save_json(directory/'online_complete.json',value);return value

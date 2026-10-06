"""Verify nested splits, unchanged sources, official labels and causal inputs."""
from __future__ import annotations
import argparse
from datetime import timedelta
import json
from pathlib import Path
import numpy as np
import torch
from .adaptation_scaling import DEFAULT, OLD, fresh_entries, entries, read, data_split
from .adaptation_data import digest
from .adaptation_features import public_features
from .data import ROOT, save_json, read_truth, pieces, source_hashes
from .features import utc, PublicTimeline
from .information_predict import InformationPredictor
from challenge.v4_workflow import V4Workflow
from challenge.v4_fiber_map import radec_to_altaz
from challenge.v4_scorer import score_target_exposure, program_band, lunar_quality_factor, ZERO_SCORE_TYPE, FORCE_CLOSE_TYPE


def audit(output,stage):
    torch.set_num_threads(1)
    spec=json.loads((output/'protocol.json').read_text())
    assert spec['sources']==source_hashes()
    assert spec['old_frozen_hash']==digest(OLD/'frozen.json')
    for name,h in spec['old_weights'].items():assert digest(OLD/'models'/name)==h
    previous=json.loads((OLD/'protocol.json').read_text())
    for name,h in previous['foundation'].items():assert digest(ROOT/name)==h
    for name,h in previous['sources'].items():assert digest(ROOT/name)==h
    assert len(set(e['seed'] for e in fresh_entries()))==16
    assert not set(e['seed'] for e in fresh_entries())&set(e['seed'] for e in entries())
    assert len(set(e['id'] for e in fresh_entries()))==16
    if stage=='development':
        if not (output/'frozen.json').exists():
            assert not any((output/'scenarios'/e['id']).exists() for e in fresh_entries() if e['split']=='test'), 'test created before freeze'
        for e in entries():
            if e['split']!='train':continue
            original=read(OLD/'scenarios'/e['id'],e['id'])
            extra=read(output/'supplementary/scenarios'/e['id'],e['id'])
            original_indices={m['observe_index'] for m in original['meta']}
            extra_indices={m['observe_index'] for m in extra['meta'] if m['observe_index']>=8}
            assert not original_indices&extra_indices
        counts={}
        for size in ('32','64','128','balanced64','diverse64'):
            d=data_split(output,'train',size)
            scenes=set(d['scene']);counts[size]=dict(rows=len(d['y']),scenes=len(scenes))
            assert len(scenes)==(24 if size=='diverse64' else 16)
            assert not scenes&set(data_split(output,'validation')['scene'])
            assert not scenes&set(data_split(output,'calibration')['scene'])
    else:
        assert (output/'frozen.json').exists()
        counts={}
    selected=[(e,output/'scenarios'/e['id']) for e in fresh_entries()
              if e['split']==('train' if stage=='development' else 'test')]
    selected += [(e,output/'supplementary/scenarios'/e['id']) for e in entries() if stage=='development' and e['split']=='train']
    cache=[]
    for e,directory in selected:
        workflow=V4Workflow(directory/'bundle');init=workflow.initialize_payload(900.,1.)
        for name in ('targets.csv','footprint.csv','v4_night_calendar.csv','taskcard.zh.md','taskcard.en.md'):
            assert (directory/'bundle/public'/name).read_bytes()==(ROOT/'cards'/e['card']/'public'/name).read_bytes()
        for name in ('v4_fiber_config.json','v4_score_config.json'):
            assert (directory/'bundle/config'/name).read_bytes()==(ROOT/'cards'/e['card']/'config'/name).read_bytes()
        data=read(directory,e['id']);decisions={r['observe_index']:r for r in json.loads((directory/'decisions.json').read_text())}
        assert all(np.isfinite(data[k]).all() for k in ('x','history','y','base','hold','coordinates'))
        assert data['history'].shape[1:]==(8,22)
        for d in decisions.values():
            now=utc(d['now_utc'])
            assert all(utc(h['end_utc'])<=now for h in d['feedback_history'])
            last=d['baseline_state']['last_measurement_utc']
            assert last is None or utc(last)<=now
        cache.append((workflow,init,data,decisions,*read_truth(directory/'bundle/truth')))
    foundation=InformationPredictor();rng=np.random.default_rng(1490200);maxerror=0.;causal=0
    for i in range(200):
        w,init,data,decisions,weather,events=cache[i%len(cache)]
        ix=int(rng.integers(len(data['y'])));m=data['meta'][ix];answer=data['y'][ix]
        ra,dec=map(float,data['coordinates'][ix]);start,end=utc(m['start_utc']),utc(m['end_utc']);duration=int((end-start).total_seconds())
        target=dict(target_id='audit',ra_deg=ra,dec_deg=dec,feature_flux=1.,science_weight=1.)
        position=lambda t:radec_to_altaz(ra,dec,t,init['site']['latitude_deg'],init['site']['longitude_deg'])
        expected=score_target_exposure(target,list(weather.segments(start,end)),[e for e in events if e.overlaps(start,end)],
            weather,duration,1.,init['scoring'],start,position,init['site'])
        error=abs(expected.quality-float(answer[0]));maxerror=max(error,maxerror);assert error<5e-6
        band=program_band(weather.band_quality(start,end,init['scoring'],lambda t:position(t)[0],
            lambda t:lunar_quality_factor(ra,dec,t,init['site'],init['scoring'])),init['scoring'])
        assert int(answer[3])==('DARK','BRIGHT','BACKUP').index(band)
        usable=0.
        for moment,seconds,slot in pieces(weather,events,start,end,True):
            alt,az=position(moment)
            closed=any(e.actual_start_utc<=moment<e.actual_end_utc and e.covers_altaz(alt,az) and
                       (e.force_close or e.event_type in (ZERO_SCORE_TYPE,FORCE_CLOSE_TYPE)) for e in events)
            if slot and slot.is_observable and not closed:usable+=seconds
        assert abs(usable/duration-float(answer[1]))<1e-6
        if i<24:
            d=decisions[m['observe_index']];now=utc(m['now_utc']);args=(now,start,end,m['duration_seconds'],np.array([[ra,dec]]),init)
            original=public_features(*args,PublicTimeline(w.scenario.bulletins,w.scenario.forecasts),
                d['baseline_state'],d['feedback_history'],foundation)
            np.testing.assert_allclose(original[0][0],data['x'][ix],atol=2e-5,rtol=1e-5)
            np.testing.assert_array_equal(original[1],data['history'][ix])
            future=dict(record_type='bulletin',issued_at_utc=(now+timedelta(seconds=1)).isoformat(),
                        notices=[dict(event_kind='storm',direction='ALL')])
            changed=public_features(*args,PublicTimeline(w.scenario.bulletins+[future],w.scenario.forecasts),
                d['baseline_state'],d['feedback_history'],foundation)
            for a,b in zip(original,changed):np.testing.assert_array_equal(a,b)
            causal+=1
    result=dict(passed=True,stage=stage,official_answers=200,maximum_quality_error=maxerror,
        causal_future_bulletin_checks=causal,train_counts=counts,formal_public_unchanged=True,old_weights_unchanged=True)
    save_json(output/f'audit_{stage}.json',result);print(json.dumps(result),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('stage',choices=('development','test'));p.add_argument('--output',type=Path,default=DEFAULT)
    a=p.parse_args();audit(a.output,a.stage)

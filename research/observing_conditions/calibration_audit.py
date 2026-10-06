"""Calibration contract, split, causality, official label and public entry audits."""
from __future__ import annotations
import argparse
import copy
from datetime import timedelta
import json
from pathlib import Path
import numpy as np
import torch
from .calibration_experiment import DEFAULT,OLD,SCALING,existing_entries,new_entries,load
from .conditional_calibration import apply,fit,weights,public_keys
from .adaptation_data import entries,digest
from .data import ROOT,save_json,read_truth,pieces,source_hashes
from .information_model import measure,NAMES
from .features import PublicTimeline,utc
from .adaptation_features import public_features
from .information_predict import InformationPredictor
from challenge.v4_workflow import V4Workflow
from challenge.v4_fiber_map import radec_to_altaz
from challenge.v4_scorer import score_target_exposure,program_band,lunar_quality_factor,ZERO_SCORE_TYPE,FORCE_CLOSE_TYPE


def contract(output):
    spec=json.loads((output/'protocol.json').read_text());f=json.loads((output/'frozen.json').read_text())
    assert spec['sources']==source_hashes()
    for name,h in spec['model_hashes'].items():assert digest(ROOT/name)==h
    for name,h in spec['code'].items():assert digest(ROOT/name)==h
    train={e['id'] for e in entries() if e['split']=='train'}
    cal={e['id'] for e,_ in existing_entries('calibration')};valid={e['id'] for e,_ in existing_entries('validation')}
    test={e['id'] for e in new_entries()}
    assert len(cal)==len(valid)==12 and len(test)==8
    assert not cal&valid and not cal&test and not valid&test and not train&(cal|valid|test)
    assert len(set(e['seed'] for e in new_entries()))==8
    assert f['network_training_runs']==0
    data=load(output,'calibration');initial=load(output,'calibration',True)
    with np.load(output/'calibration_bulk_raw.npz') as r:raw=r['gru64'].copy()
    with np.load(output/'calibration_initial_raw.npz') as r:iraw=r['gru64'].copy()
    settings=f['calibration']['gru64/history_horizon_sides'];p,evidence=apply(raw,data['meta'],settings,True)
    np.testing.assert_array_equal(p[:,[0,2,4,6]],raw[:,[0,2,4,6]])
    assert (p[:,1]<=p[:,2]).all() and (p[:,2]<=p[:,3]).all() and (p[:,5]<=p[:,6]).all() and (p[:,6]<=p[:,7]).all()
    assert (p[:,:4]>=0).all() and p[:,:4].max()>1 and ((p[:,4:9]>=0)&(p[:,4:9]<=1)).all()
    np.testing.assert_allclose(p[:,9:12].sum(1),1,atol=2e-7)
    altered=[dict(m,weather_shift=True,seed=-1,card_id='hidden',event_strength=999) for m in data['meta']]
    np.testing.assert_array_equal(p,apply(raw,altered,settings))
    w=weights(data)
    scene_weights=[w[data['scene']==s].sum() for s in np.unique(data['scene'])]
    np.testing.assert_allclose(scene_weights,1/12,atol=1e-12)
    # More restrictive support threshold exercises explicit pooling, not fabricated subgroup fits.
    pooled=fit(data,raw,initial,iraw,'history_horizon_sides',min_scenes=20)
    pp,pe=apply(raw,data['meta'],pooled,True)
    assert all(e['pooled'] for e in pe) and set(pooled['banks'])=={'global'}
    assert len(pooled['unsupported_groups'])==16
    # Construct a known narrow signed interval; ensure narrowing is supported and medians survive.
    narrow=copy.deepcopy(settings);bank=narrow['banks']['global'];narrow['scheme']='global_sides'
    bank['offsets']['quality']=[.1,-.1];r=raw[:1].copy();r[0,:4]=[2.,1.,2.,3.]
    q=apply(r,[data['meta'][0]],narrow)
    assert abs(q[0,1]-1.1)<1e-6 and abs(q[0,3]-2.9)<1e-6
    y=data['y'];oracle=np.zeros_like(raw);oracle[:,:4]=y[:,0,None];oracle[:,4:8]=y[:,1,None]
    oracle[:,8]=y[:,2];oracle[:,9:12]=np.eye(3)[y[:,3].astype(int)]
    ideal=measure(y,oracle);bad=measure(y,np.zeros_like(raw))
    assert ideal['quality_interval_score']==0 and ideal['quality_mae']==0 and ideal['band_ece']<1e-6
    assert bad['quality_mae']>.1 and bad['band_brier']>.5
    # Existing public snapshots, including targeted early history, must remain causal.
    for entry,directory in existing_entries('calibration'):
        for path in (directory,SCALING/'supplementary/scenarios'/entry['id']):
            for d in json.loads((path/'decisions.json').read_text()):
                now=utc(d['now_utc'])
                assert all(utc(h['end_utc'])<=now for h in d['feedback_history'])
                last=d['baseline_state']['last_measurement_utc'];assert last is None or utc(last)<=now
    previous=json.loads((OLD/'protocol.json').read_text())
    for name,h in previous['foundation'].items():assert digest(ROOT/name)==h
    for name,h in previous['sources'].items():assert digest(ROOT/name)==h
    value=dict(passed=True,checks=12,model_weights_unchanged=True,network_training_runs=0,
        splits_disjoint=True,calibration_scenes=12,validation_scenes=12,
        hidden_group_fields_ignored=True,known_good_bad_metrics_checked=True,
        per_row_confidence_guarantee=False,explicit_limited_scene_evidence=True)
    save_json(output/'audit_contract.json',value);print(json.dumps(value),flush=True)


def official(output):
    torch.set_num_threads(1);cache=[];maxerror=0.;foundation=InformationPredictor()
    for entry in new_entries():
        directory=output/'scenarios'/entry['id'];w=V4Workflow(directory/'bundle');init=w.initialize_payload(900.,1.)
        assert json.loads((directory/'trajectory_complete.json').read_text())['complete_period']
        for name in ('targets.csv','footprint.csv','v4_night_calendar.csv','taskcard.zh.md','taskcard.en.md'):
            assert (directory/'bundle/public'/name).read_bytes()==(ROOT/'cards'/entry['card']/'public'/name).read_bytes()
        for name in ('v4_fiber_config.json','v4_score_config.json'):
            assert (directory/'bundle/config'/name).read_bytes()==(ROOT/'cards'/entry['card']/'config'/name).read_bytes()
        for directory in (directory,output/'supplementary/scenarios'/entry['id']):
            with np.load(directory/'samples.npz') as d:data={k:d[k].copy() for k in d.files}
            meta=json.loads((directory/'sample_metadata.json').read_text())
            decisions={r['observe_index']:r for r in json.loads((directory/'decisions.json').read_text())}
            cache.append((w,init,data,meta,decisions,*read_truth(directory/'bundle/truth')))
    rng=np.random.default_rng(1629999)
    for i in range(200):
        w,init,data,meta,decisions,weather,events=cache[i%len(cache)];ix=int(rng.integers(len(data['y'])))
        m=meta[ix];answer=data['y'][ix];ra,dec=map(float,data['coordinates'][ix]);start,end=utc(m['start_utc']),utc(m['end_utc'])
        duration=int((end-start).total_seconds());target=dict(target_id='audit',ra_deg=ra,dec_deg=dec,feature_flux=1.,science_weight=1.)
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
            d=decisions[m['observe_index']];now=utc(m['now_utc'])
            args=(now,start,end,m['duration_seconds'],np.array([[ra,dec]]),init)
            a=public_features(*args,PublicTimeline(w.scenario.bulletins,w.scenario.forecasts),
                d['baseline_state'],d['feedback_history'],foundation)
            fake=dict(record_type='bulletin',issued_at_utc=(now+timedelta(seconds=1)).isoformat(),
                      notices=[dict(event_kind='storm',direction='ALL')])
            b=public_features(*args,PublicTimeline(w.scenario.bulletins+[fake],w.scenario.forecasts),
                d['baseline_state'],d['feedback_history'],foundation)
            for left,right in zip(a,b):np.testing.assert_array_equal(left,right)
            np.testing.assert_allclose(a[0][0],data['x'][ix],atol=2e-5,rtol=1e-5)
    value=dict(passed=True,official_answers=200,max_quality_error=maxerror,causal_checks=24,formal_public_unchanged=True)
    save_json(output/'audit_official.json',value);print(json.dumps(value),flush=True)


def runtime(output):
    from .conditional_predict import ConditionalPredictor
    from .conditional_calibration import apply
    from .adaptation_scaling import predict
    predictor=ConditionalPredictor(output/'selected');settings=predictor.settings
    maximum=0.;checks=0;example=None
    for entry in new_entries():
        directory=output/'scenarios'/entry['id'];w=V4Workflow(directory/'bundle');init=w.initialize_payload(900.,1.)
        for phase in ('none','partial','eight'):
            data_dir=directory if phase=='eight' else output/'supplementary/scenarios'/entry['id']
            with np.load(data_dir/'samples.npz') as f:data={k:f[k].copy() for k in f.files}
            meta=json.loads((data_dir/'sample_metadata.json').read_text());decisions={r['observe_index']:r for r in json.loads((data_dir/'decisions.json').read_text())}
            indices=[i for i,m in enumerate(meta) if ('none' if m['history_count']==0 else 'partial' if m['history_count']<8 else 'eight')==phase]
            indices=[indices[i] for i in np.linspace(0,len(indices)-1,min(4,len(indices)),dtype=int)]
            for ix in indices:
                m=meta[ix];d=decisions[m['observe_index']];ra,dec=map(float,data['coordinates'][ix])
                request=dict(initialize=init,now_utc=m['now_utc'],bulletins=w.scenario.bulletins,forecasts=w.scenario.forecasts,
                    baseline_state=d['baseline_state'],feedback_history=d['feedback_history'],
                    candidates=[dict(candidate_id='audit',ra_deg=ra,dec_deg=dec,start_utc=m['start_utc'],duration_seconds=m['duration_seconds'])])
                value=predictor.predict(request);row=value['predictions'][0];assert row['eligible']
                raw=predict(SCALING,'gru64',{k:v[ix:ix+1] for k,v in data.items()})
                expected=apply(raw,[m],settings)[0];actual=np.array([row[n] for n in NAMES])
                error=float(np.abs(actual-expected).max());maximum=max(maximum,error);assert error<5e-5,error
                assert 'calibration_evidence' in row and value['online_updates']==0 and not value['automatic_actions']
                checks+=1;example=(request,value)
    save_json(output/'example_request.json',example[0]);save_json(output/'example_prediction.json',example[1])
    value=dict(passed=True,public_calls=checks,max_output_difference=maximum,online_updates=0,automatic_actions=False)
    save_json(output/'audit_runtime.json',value);print(json.dumps(value),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('stage',choices=('contract','official','runtime'));p.add_argument('--output',type=Path,default=DEFAULT)
    a=p.parse_args();torch.set_num_threads(1);{'contract':contract,'official':official,'runtime':runtime}[a.stage](a.output)

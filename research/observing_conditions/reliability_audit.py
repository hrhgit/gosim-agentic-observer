"""Audits authorized by the ongoing model reliability experiment."""
from __future__ import annotations
import argparse
import copy
from datetime import timedelta
import json
from pathlib import Path
import time
import numpy as np
import torch
from challenge.v4_workflow import V4Workflow
from challenge.v4_fiber_map import radec_to_altaz
from challenge.v4_scorer import score_target_exposure,program_band,lunar_quality_factor,ZERO_SCORE_TYPE,FORCE_CLOSE_TYPE
from . import reliability_data as rd,reliability_calibration as rc,endpoint_model as em
from .adaptation_data import digest
from .adaptation_scaling import DEFAULT as SCALING,predict,take,read
from .data import ROOT,save_json,read_truth,pieces,source_hashes
from .features import utc,PublicTimeline
from .adaptation_features import public_features
from .information_predict import InformationPredictor
from .information_model import NAMES


def contract(output):
    f=json.loads((output/'frozen.json').read_text())
    sets={split:{e['id'] for e,_ in rd.sources(split,output)} for split in ('train','calibration','calibration_extra','validation','test')}
    for a,sa in sets.items():
        for b,sb in sets.items():
            if a!=b:assert not sa&sb,(a,b)
    seeds=[e['seed'] for split in sets for e,_ in rd.sources(split,output)];assert len(seeds)==len(set(seeds))
    assert f['sources']==source_hashes()
    for field,root in [('model_hashes',ROOT),('endpoint_hashes',output),('scientific_code',ROOT)]:
        for name,h in f[field].items():assert digest(root/name)==h
    for entry,directory in rd.sources('test',output):
        assert json.loads((directory/'trajectory_complete.json').read_text())['complete_period']
        assert utc(f['freeze_utc'])<utc(json.loads((directory/'trajectory_complete.json').read_text())['completed_utc']) if 'completed_utc' in json.loads((directory/'trajectory_complete.json').read_text()) else directory.stat().st_mtime>utc(f['freeze_utc']).timestamp()
        for ddir in (directory,output/'night_samples/scenarios'/entry['id']):
            for d in json.loads((ddir/'decisions.json').read_text()):
                now=utc(d['now_utc']);assert all(utc(h['end_utc'])<=now for h in d['feedback_history'])
                last=d['baseline_state']['last_measurement_utc'];assert last is None or utc(last)<=now
                original=digest(ddir/'bundle/config/v4_score_config.json');assert original==digest(ROOT/'cards'/entry['card']/'config/v4_score_config.json')
        for name in ('targets.csv','footprint.csv','v4_night_calendar.csv','taskcard.zh.md','taskcard.en.md'):
            assert (directory/'bundle/public'/name).read_bytes()==(ROOT/'cards'/entry['card']/'public'/name).read_bytes()
    data=rd.load(output,'test');raw=np.load(output/'test_bulk_raw.npz')['gru64']
    p=rc.apply(raw,data['meta'],f['settings']);altered=[dict(m,scene_id='fake',seed=-1,card='hidden',weather_shift=True,event_strength=999) for m in data['meta']]
    np.testing.assert_array_equal(p,rc.apply(raw,altered,f['settings']))
    np.testing.assert_array_equal(p[:,[0,2,4,6]],raw[:,[0,2,4,6]])
    pred=np.load(output/'test_predictions.npz')[f['selected']];dist=np.load(output/'endpoint_test_distributions.npz')['bulk']
    np.testing.assert_allclose(dist[:,:3].sum(1),1,atol=2e-7)
    np.testing.assert_allclose(pred[:,4],dist[:,2]+dist[:,1]*dist[:,3]/(dist[:,3]+dist[:,4]),atol=2e-7)
    from scipy.special import betainc
    # SciPy's float32 beta CDF has visibly lower precision. The actual entry
    # deliberately computes this CDF in float64 before returning float32.
    d64=dist.astype(np.float64)
    np.testing.assert_allclose(pred[:,8],d64[:,0]+d64[:,1]*betainc(d64[:,3],d64[:,4],.5),atol=2e-7)
    assert (pred[:,1]<=pred[:,2]).all() and (pred[:,2]<=pred[:,3]).all()
    assert (pred[:,5]<=pred[:,6]).all() and (pred[:,6]<=pred[:,7]).all()
    assert (pred[:,:4]>=0).all() and pred[:,:4].max()>1
    assert ((pred[:,4:12]>=0)&(pred[:,4:12]<=1)).all()
    exact=np.array([[1,0,0,1,1],[0,0,1,1,1]],np.float32);fake=em.numerical(exact,np.zeros((2,12),np.float32))
    np.testing.assert_array_equal(fake[:,4:9],np.array([[0,0,0,0,1],[1,1,1,1,0]],np.float32))
    value=dict(passed=True,checks=14,scene_counts={k:len(v) for k,v in sets.items()},seed_disjoint=True,
        main_weights_unchanged=True,formal_public_unchanged=True,hidden_group_fields_ignored=True,
        mixture_coherence=True,known_endpoint_controls=True,complete_period=True,final_after_freeze=True,
        initializations=1,automatic_actions=False,per_candidate_guarantee=False)
    save_json(output/'audit_contract.json',value);print('contract',value,flush=True)


def official(output):
    foundation=InformationPredictor();cache=[];maxerror=0.
    for e,directory in rd.sources('test',output):
        w=V4Workflow(directory/'bundle');init=w.initialize_payload(900.,1.)
        for ddir in (directory,output/'night_samples/scenarios'/e['id']):
            with np.load(ddir/'samples.npz') as z:data={k:z[k].copy() for k in z.files}
            meta=json.loads((ddir/'sample_metadata.json').read_text());decisions={d['observe_index']:d for d in json.loads((ddir/'decisions.json').read_text())}
            cache.append((w,init,data,meta,decisions,*read_truth(directory/'bundle/truth')))
    rng=np.random.default_rng(1789999)
    for i in range(200):
        w,init,data,meta,decisions,weather,events=cache[i%len(cache)];ix=int(rng.integers(len(data['y'])))
        m=meta[ix];answer=data['y'][ix];ra,dec=map(float,data['coordinates'][ix]);start,end=utc(m['start_utc']),utc(m['end_utc'])
        duration=int((end-start).total_seconds());target=dict(target_id='audit',ra_deg=ra,dec_deg=dec,feature_flux=1.,science_weight=1.)
        position=lambda t:radec_to_altaz(ra,dec,t,init['site']['latitude_deg'],init['site']['longitude_deg'])
        expected=score_target_exposure(target,list(weather.segments(start,end)),[e for e in events if e.overlaps(start,end)],weather,duration,1.,init['scoring'],start,position,init['site'])
        error=abs(expected.quality-float(answer[0]));maxerror=max(maxerror,error);assert error<5e-6
        band=program_band(weather.band_quality(start,end,init['scoring'],lambda t:position(t)[0],lambda t:lunar_quality_factor(ra,dec,t,init['site'],init['scoring'])),init['scoring'])
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
            a=public_features(*args,PublicTimeline(w.scenario.bulletins,w.scenario.forecasts),d['baseline_state'],d['feedback_history'],foundation)
            future=dict(record_type='bulletin',issued_at_utc=(now+timedelta(seconds=1)).isoformat(),notices=[dict(event_kind='storm',direction='ALL')])
            b=public_features(*args,PublicTimeline(w.scenario.bulletins+[future],w.scenario.forecasts),d['baseline_state'],d['feedback_history'],foundation)
            for left,right in zip(a,b):np.testing.assert_array_equal(left,right)
            np.testing.assert_allclose(a[0][0],data['x'][ix],atol=2e-5,rtol=1e-5)
    value=dict(passed=True,official_answers=200,max_quality_error=maxerror,independent_U_and_band_checks=200,causal_checks=24)
    save_json(output/'audit_official.json',value);print('official',value,flush=True)


def runtime(output):
    from .reliability_predict import ReliabilityPredictor
    predictor=ReliabilityPredictor(output/'selected');f=json.loads((output/'frozen.json').read_text());checks=0;maximum=0.;example=None
    for e,directory in rd.sources('test',output):
        w=V4Workflow(directory/'bundle');init=w.initialize_payload(900.,1.)
        ddir=output/'night_samples/scenarios'/e['id'];data=rd.enrich(read(ddir,e['id']),json.loads((ddir/'decisions.json').read_text()),init)
        decisions={d['observe_index']:d for d in json.loads((ddir/'decisions.json').read_text())};_,phases=rc.public_keys(data['meta'])
        for phase in ('never','stale','partial'):
            indices=np.flatnonzero(phases==phase)
            for ix in indices[np.linspace(0,len(indices)-1,2,dtype=int)]:
                m=data['meta'][int(ix)];d=decisions[m['observe_index']];ra,dec=map(float,data['coordinates'][ix])
                request=dict(initialize=init,now_utc=m['now_utc'],bulletins=w.scenario.bulletins,forecasts=w.scenario.forecasts,
                    baseline_state=d['baseline_state'],feedback_history=d['feedback_history'],
                    candidates=[dict(candidate_id='audit',ra_deg=ra,dec_deg=dec,start_utc=m['start_utc'],duration_seconds=m['duration_seconds'])])
                value=predictor.predict(request);row=value['predictions'][0];assert row['eligible']
                one=take(data,np.arange(len(data['y']))==ix);raw=predict(SCALING,'gru64',one)
                dist=em.predict(output/'endpoint_head',one,raw);expected=em.numerical(dist,rc.apply(raw,one['meta'],f['settings']))[0]
                actual=np.array([row[n] for n in NAMES]);error=float(np.abs(actual-expected).max());maximum=max(maximum,error);assert error<5e-5,error
                np.testing.assert_allclose([row['p_usable_zero'],row['p_usable_partial'],row['p_usable_one']],dist[0,:3],atol=5e-6)
                assert not value['automatic_actions'] and value['online_updates']==0
                if example is None:example=request;save_json(output/'example_request.json',request);save_json(output/'example_prediction.json',value)
                checks+=1
    # Interleaved time groups and a known ineligible position; preserve input order.
    request=copy.deepcopy(example);original=request['candidates'][0];later=copy.deepcopy(original)
    later['start_utc']=(utc(request['now_utc'])+timedelta(minutes=30)).isoformat();later['candidate_id']='later'
    bad=copy.deepcopy(original);bad['candidate_id']='below';bad['dec_deg']=-89.
    request['candidates']=[bad,later,original];value=predictor.predict(request)
    assert [r['candidate_id'] for r in value['predictions']]==['below','later','audit']
    assert len(value['predictions'])==3 and not value['predictions'][0]['eligible']
    reference=predictor.predict(example)['predictions'][0]
    np.testing.assert_allclose([value['predictions'][2][n] for n in NAMES],[reference[n] for n in NAMES],atol=3e-6)
    assert not value['automatic_actions']
    invalid=copy.deepcopy(example);invalid['truth_weather']=1
    try:predictor.predict(invalid)
    except ValueError:pass
    else:raise AssertionError('hidden input accepted')
    # Pure neural inference + distribution/probability adjustment, 1024 candidates.
    data=rd.load(output,'test');ix=np.arange(1024);small=take(data,ix);raw=predict(SCALING,'gru64',small)
    times=[];head=predictor.head;n=predictor.endpoint_normalizer;model,normalizer,_,_=predictor.models
    for i in range(31):
        started=time.perf_counter();p=predictor.online.predict(small);adjusted=rc.apply(p,small['meta'],f['settings'])
        x=em.features(small,p,model,normalizer);dist=em.infer(head,((x-n['mean'])/n['std']).astype(np.float32));em.numerical(dist,adjusted)
        if i:times.append(time.perf_counter()-started)
    # Actual public request separately includes geometry, Moon and announcement work.
    request=copy.deepcopy(example);request['candidates']=[dict(request['candidates'][0],candidate_id=str(i)) for i in range(1024)]
    timings=[]
    for i in range(6):
        v=predictor.predict(request)
        if i:timings.append(v['timing'])
    value=dict(passed=True,public_calls=checks+3,max_numeric_difference=maximum,all_candidates_preserved=True,
        interleaved_groups_checked=True,unknown_private_field_rejected=True,online_parameter_updates=0,
        threads=torch.get_num_threads(),pure_inference_1024_p95_ms=float(np.quantile(times,.95)*1000),
        public_1024_mean_timings={k:float(np.mean([t[k] for t in timings])) for k in timings[0]},
        benchmark_scope='Local macOS CPU single thread; repeated public coordinates; not Linux contest deployment')
    save_json(output/'audit_runtime.json',value);print('runtime',value,flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('stage',choices=('contract','official','runtime','all'));p.add_argument('--output',type=Path,default=rd.DEFAULT)
    a=p.parse_args();torch.set_num_threads(1)
    if a.stage in ('contract','all'):contract(a.output)
    if a.stage in ('official','all'):official(a.output)
    if a.stage in ('runtime','all'):runtime(a.output)

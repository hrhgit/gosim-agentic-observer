"""Label, causal-input, feedback ambiguity and official hit-feedback audits."""
from __future__ import annotations
import argparse
from datetime import timedelta
import gzip
import json
from pathlib import Path
import numpy as np
import torch
from .adaptation_data import DEFAULT, entries, protocol
from .adaptation_features import public_features
from .adaptation_feedback import decode_target, history_array
from .features import PublicTimeline, utc
from .data import read_truth, pieces, save_json, source_hashes
from .information_predict import InformationPredictor
from challenge.v4_workflow import V4Workflow
from challenge.v4_runner import run_scenario
from challenge.v4_fiber_map import radec_to_altaz
from challenge.v4_scorer import score_target_exposure,program_band,lunar_quality_factor,ZERO_SCORE_TYPE,FORCE_CLOSE_TYPE


def audit(output=DEFAULT,count=200):
    output=Path(output);spec=protocol(output);torch.set_num_threads(1)
    assert spec['sources'].items()>=source_hashes().items()
    assert len({e['seed'] for e in entries()})==32
    development=[e for e in entries() if e['split']!='test'];cache={};total=0
    for e in development:
        directory=output/'scenarios'/e['id'];w=V4Workflow(directory/'bundle');init=w.initialize_payload(900.,1.)
        for name in ('targets.csv','footprint.csv','v4_night_calendar.csv','taskcard.zh.md','taskcard.en.md'):
            from .data import ROOT
            assert (directory/'bundle/public'/name).read_bytes()==(ROOT/'cards'/e['card']/'public'/name).read_bytes()
        for name in ('v4_score_config.json','v4_fiber_config.json'):
            assert (directory/'bundle/config'/name).read_bytes()==(ROOT/'cards'/e['card']/'config'/name).read_bytes()
        a=dict(np.load(directory/'samples.npz'));meta=json.loads((directory/'sample_metadata.json').read_text())
        for k,v in a.items():assert np.isfinite(v).all(),(e['id'],k)
        assert a['history'].shape[1:]==(8,22);total+=len(a['y'])
        online=dict(np.load(directory/'online_public.npz'))
        assert set(online)=={'x','history','lower','upper','available_at','exposure_index'}
        assert (online['lower']<=online['upper']).all()
        cache[e['id']]=(w,init,a,meta,*read_truth(directory/'bundle/truth'))
    rng=np.random.default_rng(10104);maxerror=0.;causal_checks=0
    foundation=InformationPredictor()
    for i in range(count):
        e=development[i%len(development)];w,init,a,meta,weather,events=cache[e['id']]
        j=int(rng.integers(len(a['y'])));row=meta[j];ra,dec=map(float,a['coordinates'][j]);answer=a['y'][j]
        start,end=utc(row['start_utc']),utc(row['end_utc']);duration=int((end-start).total_seconds())
        target=dict(target_id='audit',ra_deg=ra,dec_deg=dec,feature_flux=1.,science_weight=1.)
        func=lambda t:radec_to_altaz(ra,dec,t,init['site']['latitude_deg'],init['site']['longitude_deg'])
        expected=score_target_exposure(target,list(weather.segments(start,end)),[e for e in events if e.overlaps(start,end)],
            weather,duration,1.,init['scoring'],start,func,init['site'])
        error=abs(expected.quality-float(answer[0]));maxerror=max(maxerror,error);assert error<5e-6,error
        band=program_band(weather.band_quality(start,end,init['scoring'],lambda t:func(t)[0],
            lambda t:lunar_quality_factor(ra,dec,t,init['site'],init['scoring'])),init['scoring'])
        assert int(answer[3])==('DARK','BRIGHT','BACKUP').index(band)
        open_seconds=0.
        for t,seconds,slot in pieces(weather,events,start,end,True):
            alt,az=func(t)
            closed=any(e.actual_start_utc<=t<e.actual_end_utc and e.covers_altaz(alt,az)
                and (e.force_close or e.event_type in (ZERO_SCORE_TYPE,FORCE_CLOSE_TYPE)) for e in events)
            if slot and slot.is_observable and not closed:open_seconds+=seconds
        assert abs(open_seconds/duration-float(answer[1]))<1e-6
        if i<24:
            decision=next(r for r in json.loads((output/'scenarios'/e['id']/'decisions.json').read_text())
                          if r['observe_index']==row['observe_index'])
            now=utc(row['now_utc']);timeline=PublicTimeline(w.scenario.bulletins,w.scenario.forecasts)
            args=(now,start,end,row['duration_seconds'],np.array([[ra,dec]]),init)
            original=public_features(*args,timeline,decision['baseline_state'],decision['feedback_history'],foundation)
            fake=dict(record_type='bulletin',issued_at_utc=(now+timedelta(seconds=1)).isoformat(),
                notices=[dict(event_kind='storm',direction='ALL')])
            future=PublicTimeline(w.scenario.bulletins+[fake],w.scenario.forecasts)
            changed=public_features(*args,future,decision['baseline_state'],decision['feedback_history'],foundation)
            for left,right in zip(original,changed):np.testing.assert_array_equal(left,right)
            h=decision['feedback_history']
            if h:
                fake_feedback=dict(h[-1],end_utc=(now+timedelta(seconds=1)).isoformat(),throughput_mid=9999.)
                np.testing.assert_array_equal(history_array(h,now),history_array(h+[fake_feedback],now))
            causal_checks+=1
    w,init,_,_,_,_=cache['adapt-a-0'];s=init['scoring'];t=w.scenario.targets[0]
    mismatch=s['program']['mismatch_multiplier'];weight=t['science_weight']
    ambiguous=decode_target(weight*mismatch,t,600,'DARK',s)
    assert ambiguous['match']=='unknown' and ambiguous['saturated'] and ambiguous['quality_upper'] is None
    mu=s['program']['multipliers']['DARK']
    definite=decode_target(weight*(mismatch+mu)/2,t,600,'DARK',s)
    assert definite['match']=='matched' and not definite['saturated']
    # Replay eight actual actions through the entire official runner's hit-selection path.
    with gzip.open(output/'scenarios/adapt-a-0/exposures.jsonl.gz','rt') as f:
        prefix=[json.loads(next(f)) for _ in range(8)]
    cursor=0;checked_feedback=[]
    def factory(_):
        def decide(snapshot):
            nonlocal cursor
            last=snapshot.get('last_result')
            if last and last.get('action')=='observe' and len(checked_feedback)<cursor:checked_feedback.append(last)
            if cursor==len(prefix):return dict(action='finish')
            r=prefix[cursor];now=utc(snapshot['now_utc'])
            if now<utc(r['start_utc']):return dict(action='wait',until_utc=r['start_utc'])
            assert now==utc(r['start_utc'])
            cursor+=1
            return dict(action='observe',pointing=r['pointing'],assignments=r['assignments'],program=r['program'],
                        duration_seconds=int((utc(r['end_utc'])-utc(r['start_utc'])).total_seconds()))
        return decide
    result=run_scenario(output/'scenarios/adapt-a-0/bundle/config/v4_scenario.json',factory,output/'audit_official_prefix')
    assert len(checked_feedback)==8
    for expected,received in zip(prefix,checked_feedback):
        assert expected['hits']==received['hits'],(expected['observe_index'],expected['hits'],received['hits'])
    report=dict(passed=True,label_checks=count,maximum_quality_error=maxerror,causal_checks=causal_checks,
        structural_rows=total,public_files_unchanged=True,unique_independent_scenes=32,
        online_proxy_files_have_no_environment_labels=True,ambiguous_scores_preserved=True,
        official_hit_feedback_prefix_checks=8,official_prefix_termination=result.get('termination_reason'),
        limitation='Prefix replay checks exact feedback semantics; research full trajectories are not fair-clock agent scores')
    save_json(output/'audit.json',report);print(json.dumps(report),flush=True);return report


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=DEFAULT)
    args=parser.parse_args();audit(args.output)

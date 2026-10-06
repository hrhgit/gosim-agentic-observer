import json
from copy import deepcopy
from collections import Counter
from pathlib import Path

import numpy as np
import pytest

from research.instrument_faults.core import Evidence, FEATURE_NAMES, reference_scores
from research.instrument_faults.data import entries, read_public, ROOT
from research.instrument_faults.models import Preprocessor, Classifier
from research.instrument_faults.simulation import fault_label
from challenge.v4_scorer import WeatherTruth, SlotTruth, ScoreEvent
from research.observing_conditions.features import utc


@pytest.fixture
def score():
    return read_public('a')[2]


def record(index=0,ratio=1,hit=True):
    t=f'2026-01-01T0{index}:00:00+00:00'
    end=f'2026-01-01T0{index}:01:00+00:00'
    return dict(observe_index=index,start=t,end=end,night='n1',duration=60,program='DARK',altitude=70,azimuth=index*90,
                predictions=[dict(target_id='t1',target={'feature_flux':1.,'science_weight':1.},
                prediction=[1,.8,1,1.2,1,0,1,0,0],issued_at_utc=t,duration_seconds=60,weather_version='weather')],
                result={'assigned_count':1,'hit_count':int(hit),'hits':[{'target_id':'t1','score':ratio*.2}] if hit else []})


def test_scene_split_balance_and_independence():
    main=[e for e in entries() if e['split']!='external']
    assert len(main)==512 and len(entries(True))==8
    assert len(set(e['seed'] for e in entries()+entries(True)))==584
    for card in 'abcd':
        for split,total in [('train',80),('validation',16),('calibration',16),('test',16)]:
            rows=[e for e in main if e['card']==card and e['split']==split]
            assert len(rows)==total
            assert Counter(e['fault_count'] for e in rows)=={0:total//4,1:total//2,3:total//4}
            for count in (0,1,3):
                subset=[e for e in rows if e['fault_count']==count]
                assert sum(e['quake'] for e in subset)==len(subset)//2
                assert sum(e['uniform_onset'] for e in subset)==len(subset)//2
    assert Counter(e['group'] for e in entries() if e['split']=='external')==dict.fromkeys(['weak_fault','weather_shift','quake_overlap','sparse_stress'],16)


def test_proxy_cap_and_band_are_separate(score):
    target={'feature_flux':10.,'science_weight':3.}
    reference,lower,upper,capped=reference_scores(target,[7,6,7,8,1,0,0,0,1],3600,'DARK',score)
    assert capped and reference==3*score['program']['mismatch_multiplier']
    assert lower<=reference<=upper
    with pytest.raises(ValueError):
        reference_scores(target,[1,3,2,4,1,0,1,0,0],60,'DARK',score)


def test_null_is_not_healthy_and_evidence_counts(score):
    state=Evidence(score,'weather')
    assert state.info('2026-01-01T00:00:00Z')['fault_probability'] is None
    assert state.info('2026-01-01T00:00:00Z')['status']=='no_observation_evidence'
    for i in range(3):
        state.observation(record(i))
    assert state.info('2026-01-01T02:01:00Z')['status']=='ready'
    assert state.info('2026-01-01T02:01:00Z')['evidence_exposure_count']==3
    assert len(state.vector('2026-01-01T02:01:00Z'))==len(FEATURE_NAMES)
    assert state.info('2026-01-08T02:01:00Z')['last_evidence_age_seconds']==7*86400
    absent=Evidence(score,None)
    assert absent.info('2026-01-01T00:00:00Z')['status']=='weather_unavailable'


def test_repair_clears_old_evidence_confirmation_expires_resync_is_recomputed(score):
    state=Evidence(score,'weather')
    for i in range(3):state.observation(record(i))
    previous=deepcopy(state.records)
    state.messages([{'record_type':'state_resync','issued_at_utc':'2026-01-01T02:01:00Z',
                     'invalidated_window':{'action_index_start':0,'action_index_end_exclusive':2}}],'2026-01-01T02:01:00Z')
    assert state.info('2026-01-01T02:01:00Z')['evidence_exposure_count']==1
    assert previous[0]['predictions'][0]['prediction']==[1,.8,1,1.2,1,0,1,0,0]
    state.messages([{'record_type':'report_result','issued_at_utc':'2026-01-01T02:01:00Z','correct':True}],'2026-01-01T02:01:00Z')
    assert state.info('2026-01-01T02:01:00Z')['fault_probability']==0
    assert state.info('2026-01-01T02:01:01Z')['fault_probability'] is None
    assert not state.records
    with pytest.raises(ValueError):state.observation(record(2))
    state.observation(record(3))
    assert state.info('2026-01-01T03:01:00Z')['evidence_exposure_count']==1


def test_false_report_preserves_evidence_all_misses_do_not_produce_healthy_probability(score):
    state=Evidence(score,'weather');state.observation(record(hit=False))
    assert state.info('2026-01-01T00:01:00Z')['status']=='no_observation_evidence'
    state.messages([{'record_type':'report_result','issued_at_utc':'2026-01-01T00:01:00Z','correct':False}],'2026-01-01T00:01:00Z')
    assert state.info('2026-01-01T00:01:00Z')['fault_probability']==0
    assert len(state.records)==1
    assert state.info('2026-01-01T00:01:01Z')['fault_probability'] is None


def test_future_and_hidden_inputs_rejected(score):
    state=Evidence(score,'weather')
    with pytest.raises(ValueError):state.messages([{'record_type':'bulletin','issued_at_utc':'2026-01-02T00:00:00Z','notices':[]}],'2026-01-01T00:00:00Z')
    r=record();r['truth_efficiency']=.5
    with pytest.raises(ValueError):state.observation(r)
    r=record();r['predictions'][0]['issued_at_utc']=r['end']
    with pytest.raises(ValueError):state.observation(r)
    r=record();r['predictions'][0]['weather_version']='other'
    with pytest.raises(ValueError):state.observation(r)
    state.observation(record())
    with pytest.raises(ValueError):state.vector('2026-01-01T00:00:00Z')


def test_train_only_missing_preprocessing():
    p=Preprocessor().fit(np.array([[1,np.nan],[3,np.nan]],np.float32),np.ones(2))
    out=p.apply(np.array([[np.nan,100]],np.float32))
    assert p.median.tolist()==[2,0]
    assert np.isfinite(out).all() and out[0,2]==1 and out[0,3]==0


def test_mid_exposure_fault_and_repair_then_new_fault(score):
    from datetime import timedelta
    start=utc('2026-01-01T00:00:00Z')
    def fault(name,a,b):
        return ScoreEvent(name,'instrument_fault',start+timedelta(seconds=a),start+timedelta(seconds=b),
                          'GLOBAL',None,None,None,None,1,1,1,.5,False)
    first,second=fault('first',30,100),fault('second',100,200)
    w=WeatherTruth([SlotTruth('slot',start,start+timedelta(seconds=200),True,1,1,1,.5)],[first,second])
    assert not fault_label(w,start)
    assert fault_label(w,start+timedelta(seconds=60))
    assert w.report_fault(start+timedelta(seconds=60),score)==100
    assert not fault_label(w,start+timedelta(seconds=61))
    assert fault_label(w,start+timedelta(seconds=100))
    assert w.report_fault(start+timedelta(seconds=110),score)==100
    assert not fault_label(w,start+timedelta(seconds=111))
    assert [w.report_fault(start+timedelta(seconds=120+i),score) for i in range(3)]==[0,0,-150]


def test_all_day_bad_weather_and_earthquake_do_not_create_fake_hits(score):
    from research.observing_conditions.features import KINDS
    state=Evidence(score,'weather')
    for i in range(3):
        r=record(i,ratio=0);r['predictions'][0]['prediction']=[0,0,0,.1,0,1,0,0,1]
        state.observation(r)
    assert state.info('2026-01-01T02:01:00Z')['fault_probability'] is None
    assert not state.rule()
    r=record(3,ratio=.2);state.observation(r)
    state.messages([dict(record_type='bulletin',issued_at_utc='2026-01-01T03:01:00Z',notices=[{'event_kind':'earthquake','directions':['N']}])],'2026-01-01T03:01:00Z')
    assert not state.rule()


def test_quake_exclusion_counts_public_calendar_nights_including_unobserved_nights(score):
    state=Evidence(score,'weather')
    for day in range(1,6):
        at=f'2026-01-0{day}T00:00:00Z'
        state.messages([dict(record_type='bulletin',issued_at_utc=at,night_id=f'n{day}',
                       notices=[{'event_kind':'earthquake','direction':'N'}] if day==1 else [])],at)
        if day<5:assert len(state.nights)-state.quake_night<4
    assert len(state.nights)-state.quake_night==4


def test_constant_age_column_remains_finite_and_reasonable_after_long_gap():
    p=Preprocessor().fit(np.array([[0,2],[0,4]],np.float32),np.ones(2))
    out=p.apply(np.array([[.8,3]],np.float32))
    assert out[0,0]==pytest.approx(.8) and out[0,1]==pytest.approx(0)

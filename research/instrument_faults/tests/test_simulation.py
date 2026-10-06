from types import SimpleNamespace
from datetime import timedelta
import json

import numpy as np
from challenge.v4_scorer import ScoreEvent,SlotTruth
from research.instrument_faults.core import Evidence,FEATURE_NAMES
from research.instrument_faults.data import read_public
from research.instrument_faults.simulation import simulate
from research.instrument_faults.models import Classifier,save_classifier
from research.instrument_faults.predict import Predictor
from research.instrument_faults.evaluation import paired_interval,summarize
from research.observing_conditions.features import utc
from sklearn.dummy import DummyClassifier


def toy():
    start=utc('2026-01-01T00:00:00Z');site,_,score,_,_=read_public('a')
    fault=ScoreEvent('fault','instrument_fault',start,start+timedelta(hours=1),'GLOBAL',None,None,None,None,1,1,1,.5,False)
    slot=SlotTruth('n1-S1',start,start+timedelta(hours=1),True,1,1,1,.5)
    scene=SimpleNamespace(slots=[slot],events=[fault],stress_enabled=False,site=site,score_config=score,
                          targets=[{'target_id':'t','science_weight':1.,'feature_flux':1.,'ra_deg':0.,'dec_deg':0.,'required':False}],
                          survey_start=start,survey_end=start+timedelta(hours=1),bulletins=[],forecasts=[])
    rows=[dict(observe_index=i,start=(start+timedelta(seconds=60*i)).isoformat(),end=(start+timedelta(seconds=60*(i+1))).isoformat(),
               duration=60,requested=60,program='DARK',night='n1',altitude=80,azimuth=45*i,assignments={'0':'t'},target_indices=[0]) for i in range(12)]
    physics={'pointers':np.arange(13),'raw':np.tile([0,.5],(12,1)),'repaired':np.tile([0,1.],(12,1)),
             'band':np.ones(12),'hit':np.ones(12,bool)}
    p=np.tile([1,.8,1,1.2,1,0,1,0,0],(12,1,1))
    return scene,rows,physics,p


class CertainFault:
    def predict_proba(self,x):return np.ones(len(x))


class Healthy:
    def predict_proba(self,x):return np.zeros(len(x))


def test_closed_loop_repairs_change_subsequent_scores_and_new_evidence_gate(tmp_path):
    scene,rows,physics,p=toy();assets=scene,rows,physics
    _,_,_,none=simulate(tmp_path,0,p,'weather',assets=assets)
    _,_,_,healthy=simulate(tmp_path,0,p,'weather','model',Healthy(),assets=assets)
    _,_,_,rule=simulate(tmp_path,0,p,'weather','rule',assets=assets)
    _,_,_,oracle=simulate(tmp_path,0,p,'weather','oracle',assets=assets)
    _,_,_,certain=simulate(tmp_path,0,p,'weather','model',CertainFault(),assets=assets)
    assert none['total']==healthy['total'] and not healthy['reports']
    assert rule['correct']==1 and rule['false']==0
    assert rule['components']['science']>none['components']['science']
    assert oracle['components']['science']>=rule['components']['science']
    assert [d['observe_index'] for d in certain['prediction_decisions']]==[2,5,8,11]
    assert certain['correct']==1 and certain['false']==3 and certain['paid_false']==1
    assert rule['events'][0]['delay_exposures']==3


def test_baseline_event_denominator_survives_repair(tmp_path):
    scene,rows,physics,p=toy();a=scene,rows,physics
    _,_,_,none=simulate(tmp_path,0,p,'weather',assets=a)
    _,_,_,oracle=simulate(tmp_path,0,p,'weather','oracle',assets=a,baseline_events=none['event_opportunities'])
    assert oracle['events'][0]['eligible'] and oracle['events'][0]['opportunities']==12
    assert oracle['events'][0]['delay_exposures']==0 and oracle['events'][0]['delay_seconds']==0


def test_prediction_entry_null_confirmation_and_version(tmp_path):
    from research.instrument_faults.tests.test_contract import record
    base=DummyClassifier(strategy='prior').fit(np.zeros((2,len(FEATURE_NAMES))),[0,1])
    save_classifier(tmp_path/'models/selected',Classifier('boosting',base),{'weather_version':'weather'})
    (tmp_path/'weather_dependency.json').write_text(json.dumps({'identity':'weather'}))
    predictor=Predictor(tmp_path);score=read_public('a')[2];state=predictor.state(score)
    assert predictor.predict(state,'2026-01-01T00:00:00Z')['fault_probability'] is None
    state.observation(record())
    result=predictor.predict(state,'2026-01-01T00:01:00Z')
    assert result['status']=='limited_evidence' and result['fault_probability']==.5
    state.messages([{'record_type':'report_result','issued_at_utc':'2026-01-01T00:01:00Z','correct':True}],'2026-01-01T00:01:00Z')
    assert predictor.predict(state,'2026-01-01T00:01:00Z')['fault_probability']==0
    assert predictor.predict(Evidence(score,None),'2026-01-01T00:01:00Z')['status']=='weather_unavailable'


def test_scene_bootstrap_keeps_both_traces_together():
    a=[{'scene':str(i),'total':10+i+track} for i in range(4) for track in (0,1)]
    b=[r|{'total':r['total']-3} for r in a]
    result=paired_interval(a,b)
    assert result['scenes']==4 and result['ci95']==[3,3]

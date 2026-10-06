import math
import json
from datetime import timedelta

import numpy as np
import pytest

from challenge.v4_fiber_map import FiberGrid,altaz_to_radec
from research.observing_conditions.data import read_card,ROOT
from research.observing_conditions.features import utc
from research.observing_conditions.predict import Predictor
from research.observing_conditions.plan_predict import PlanPredictor


class FixedPredictor(Predictor):
    def __init__(self):
        self.metadata=dict(model_version='fixture',architecture='fixture',quality_definition='Q_env',source='synthetic test',
            validation_reliable=False,final_test_passed=False,acceptance={})
    def prepare(self,arrays):return arrays
    def numeric(self,arrays):
        return np.broadcast_to([1.2,.8,1.2,1.5,.9,.2,.7,.2,.1],(*arrays['candidate'].shape[:2],9)).copy()


def request(card='a'):
    site,_,_,score=read_card(card);start=utc('2026-10-02T04:00:00Z')
    config=json.loads((ROOT/'cards'/card/'config/v4_fiber_config.json').read_text())
    grid=FiberGrid.from_config(config);a,z=np.radians([80.,45.])
    center=np.array([np.cos(a)*np.cos(z),np.cos(a)*np.sin(z),np.sin(a)])
    up=np.array([-np.sin(a)*np.cos(z),-np.sin(a)*np.sin(z),np.cos(a)])
    right=np.array([-np.sin(z),np.cos(z),0.])
    columns=['target_id','ra_deg','dec_deg','target_class','feature_flux','science_weight','required']
    rows=[]
    for fid in range(grid.n_fibers):
        dx,dy=grid.fiber_center_offset(fid)
        v=center+math.radians(dx)*up+math.radians(dy)*right;v/=np.linalg.norm(v)
        alt=np.degrees(np.arcsin(v[2]));az=np.degrees(np.arctan2(v[1],v[0]))%360
        ra,dec=altaz_to_radec(alt,az,start,site['latitude_deg'],site['longitude_deg'])
        rows.append([f't-{fid}',ra,dec,'test',1.,2.,True])
    plan=dict(plan_id='p',pointing=dict(alt_deg=80.,az_deg=45.),assignments={str(i):f't-{i}' for i in range(grid.n_fibers)},
        start_utc=start.isoformat(),duration_seconds=300,program='DARK')
    return dict(initialize=dict(site=site,scoring=score,instrument=dict(**config['field'],
        exposure=dict(min_duration_seconds=60,max_duration_seconds=3600)),targets=dict(columns=columns,rows=rows),
        survey=dict(nights=[dict(night_date='2026-10-01',observing_start_utc=start.isoformat(),
            observing_end_utc=(start+timedelta(seconds=120)).isoformat())])),now_utc=start.isoformat(),
        bulletins=[],forecasts=[],observation_plans=[plan])


def test_complete_sixteen_target_plans_keep_all_plans_and_per_target_predictions():
    req=request();req['observation_plans']=[req['observation_plans'][0] | dict(plan_id=str(i)) for i in range(6)]
    result=PlanPredictor(FixedPredictor()).predict(req)
    assert len(result['plans'])==6 and result['fixed_top_k'] is None
    p=result['plans'][0];assert len(p['targets'])==16 and p['summary']['nominally_scoring_targets']==16
    assert p['summary']['estimated_targets_with_at_least_half_usable_time']==pytest.approx(12.8)
    t=p['targets'][0];assert t['mean_quality']==1.2 and t['actual_duration_seconds']==120
    expected=120*1.2/(.5*900)
    assert t['raw_factor_at_mean_quality']==pytest.approx(expected)
    assert t['base_score_at_mean_quality']==pytest.approx(2*expected)
    assert t['predicted_interval_vs_required_threshold']=='below'
    assert t['declared_program_match_probability']==.7
    assert not result['weather_exclusion_enabled'] and result['joint_success_probability'] is None


def test_wrong_fiber_is_nominal_mismatch_not_claimed_weather_exclusion():
    req=request();req['observation_plans'][0]['assignments']={'0':'t-1'}
    p=PlanPredictor(FixedPredictor()).predict(req)['plans'][0]
    assert p['targets'][0]['eligible'] and not p['targets'][0]['nominal_fiber_match']
    assert not p['targets'][0]['known_unavailable']
    assert p['summary']['nominally_scoring_targets']==0 and p['summary']['base_score_at_mean_quality']==0


@pytest.mark.parametrize('mutate',[
    lambda r:r['observation_plans'][0]['assignments'].update({'16':'t-0'}),
    lambda r:r['observation_plans'][0]['assignments'].update({'1':'t-0'}),
    lambda r:r['observation_plans'][0].update(duration_seconds=59),
    lambda r:r['observation_plans'][0].update(program='UNKNOWN'),
    lambda r:r.update(hidden_weather=1),
    lambda r:r['observation_plans'][0].update(truth_score=1),
])
def test_plan_contract_rejects_invalid_assignments_and_private_fields(mutate):
    req=request();mutate(req)
    with pytest.raises(ValueError):PlanPredictor(FixedPredictor()).predict(req)


def test_future_public_messages_do_not_change_plan_outputs():
    req=request();predictor=PlanPredictor(FixedPredictor());first=predictor.predict(req)
    req['bulletins']=[dict(issued_at_utc=(utc(req['now_utc'])+timedelta(days=1)).isoformat(),
        notices=[dict(event_kind='storm',direction='ALL')])]
    second=predictor.predict(req)
    assert first['plans']==second['plans']


@pytest.mark.parametrize('card,capacity',[('b',25),('c',9),('d',100)])
def test_each_formal_card_capacity_is_read_from_instrument(card,capacity):
    req=request(card)
    response=PlanPredictor(FixedPredictor()).predict(req)
    assert response['max_targets_per_observation']==capacity
    assert len(response['plans'][0]['targets'])==capacity
    assert response['plans'][0]['summary']['nominally_scoring_targets']==capacity
    assert response['fixed_top_k'] is None

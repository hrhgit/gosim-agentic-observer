import numpy as np
import pytest
import torch

from research.observing_conditions.assistance import decide, sufficient, summarize, scene_intervals, select_rules
from research.observing_conditions.improvement import summary_history, transform, ImprovementNet, entries
from research.observing_conditions.models import Normalizer


def rule(**kwargs):
    return dict(block_threshold=.99,dominance_ratio=1.,recommend_gap=.02,
                recommend_block_max=.05,recommend_usable_min=.9,
                exclude_enabled=True,recommend_enabled=True) | kwargs


def predictions():
    return np.array([[[1.,.9,1.,1.1,1.,.001,.9,.09,.01],
                      [.1,0,.1,.2,0.,.999,.01,.09,.9],
                      [.5,.3,.5,.7,1.,.01,.2,.7,.1]]])


def test_rule_never_reads_truth_and_protects_leader():
    p=predictions();d=decide(p,rule())
    np.testing.assert_array_equal(d['removed'],[[False,True,True]])
    assert d['recommend'][0] and d['recommended'][0]==0
    p[...,5]=1
    d=decide(p,rule())
    assert d['kept'].sum()==1 and not d['recommend'][0]


def test_geometry_exclusion_is_separate_and_no_valid_group_never_recommends():
    p=predictions();d=decide(p,rule(),np.zeros((1,3),bool))
    assert not d['recommend'].any() and not d['removed'].any()
    with pytest.raises(ValueError):decide(p,rule(),np.ones((2,3)))


def test_grader_detects_good_bad_and_usable_relative_exclusion():
    p=predictions();y=np.array([[[1,1,0,0],[0,0,1,2],[.5,1,0,1]]])
    result=summarize(sufficient(y,p,decide(p,rule())))
    assert result['blocked_false_exclusion_rate']==0
    assert result['excluded_but_usable']==1  # dominated does not mean physically unusable
    assert result['recommendation_failure_rate']==0
    assert result['workload_reduction']==pytest.approx(2/3)
    y[0,1,:2]=[2,1]
    bad=summarize(sufficient(y,p,decide(p,rule())))
    assert bad['blocked_false_exclusion_rate']==1
    assert bad['near_best_loss_rate']==1
    assert bad['recommendation_failure_rate']==1


def test_missing_history_has_seen_marker_and_no_spurious_changes():
    h=np.zeros((1,32,75),np.float32)
    h[0,-2:,74]=1;h[0,-2,0]=1;h[0,-2,72]=.3;h[0,-1,72]=.1
    s=summary_history(h)
    assert s.shape==(1,291)
    assert s[0,0]==.5 and s[0,72]==pytest.approx(.3)
    assert s[0,144]==1 and s[0,145]==0
    assert s[0,216]==1
    np.testing.assert_array_equal(summary_history(np.zeros_like(h)),np.zeros_like(s))


def test_formula_head_uses_raw_public_geometry_after_normalization():
    data=dict(context=np.zeros((1,242),np.float32),history=np.zeros((1,32,75),np.float32),
              candidate=np.ones((1,3,29),np.float32),y=np.ones((1,3,4),np.float32))
    data['candidate'][0,:,28]=[.5,1.,2.]
    t=transform(data,'physics');n=Normalizer.fit(t)
    model=ImprovementNet(t['context'].shape[-1],1,29,True,n).eval()
    x=n.apply(t)
    tensors=[torch.as_tensor(x[k]) for k in ('context','history','candidate')]
    with torch.no_grad():
        plain=model.backbone(*tensors);out=model(*tensors)
    np.testing.assert_allclose(out[...,:4],plain[...,:4]*data['candidate'][...,28,None],rtol=1e-6)
    np.testing.assert_array_equal(out[...,4:],plain[...,4:])


def test_scene_bootstrap_resamples_complete_groups_and_empty_decisions_are_unknown():
    p=np.repeat(predictions(),8,axis=0)
    y=np.repeat(np.array([[[1,1,0,0],[0,0,1,2],[.5,1,0,1]]]),8,axis=0)
    stats=sufficient(y,p,decide(p,rule()))
    bounds=scene_intervals(stats,np.array(['a']*2+['b']*6),100)
    assert bounds['recommendation_fraction']==[1.,1.]
    null=summarize(sufficient(y,p,decide(p,rule(recommend_enabled=False,exclude_enabled=False))))
    assert null['recommendation_failure_rate'] is None
    assert null['blocked_false_exclusion_rate'] is None and null['workload_reduction']==0


def test_new_scenes_have_unique_disjoint_seeds_and_balanced_roles():
    from research.observing_conditions.data import scenario_entries
    new=entries();old=scenario_entries()
    assert len(new)==88 and len({e['seed'] for e in new})==88
    assert not {e['seed'] for e in new}&{e['seed'] for e in old}
    assert sum(e['split']=='train' for e in new)==32
    assert sum(e.get('weather_shift',False) for e in new)==16


def test_validation_with_wrong_confident_outputs_disables_recommendations():
    p=np.repeat(predictions(),128,axis=0)
    y=np.repeat(np.array([[[0,0,1,2],[1,1,0,0],[.5,1,0,1]]]),128,axis=0)
    scenes=np.repeat(np.arange(8),16)
    chosen=select_rules(y,p,scenes)
    assert not chosen['rule']['recommend_enabled']
    assert chosen['validation']['blocked_excluded']==0


def test_priority_grader_handles_ties_zero_quality_and_missed_good_position():
    from research.observing_conditions.priority import evaluate
    p=np.repeat(predictions(),8,axis=0)
    y=np.repeat(np.array([[[1,1,0,0],[0,0,1,2],[1,1,0,0]]]),8,axis=0)
    r=dict(size=1,block_max=1.,usable_min=0.,enabled=True)
    good,d=evaluate(y,p,np.arange(8),r)
    assert good['failure_rate']==0 and good['best_lost_groups']==0
    y[:,0,0]=.1
    bad,_=evaluate(y,p,np.arange(8),r)
    assert bad['failure_rate']==1
    y[...,0]=0
    zero,_=evaluate(y,p,np.arange(8),r)
    assert zero['failure_rate'] is None


def test_advisory_api_uses_public_data_and_keeps_failed_capabilities_disabled(tmp_path):
    import json
    from datetime import timedelta
    from research.observing_conditions.features import public_context,candidate_features,utc
    from research.observing_conditions.data import read_card
    from challenge.v4_fiber_map import altaz_to_radec
    from research.observing_conditions.advisory_predict import AdvisoryPredictor
    site,_,_,score=read_card('a');now=utc('2026-10-02T04:00:00Z');end=now+timedelta(seconds=300)
    ra,dec=altaz_to_radec(80,45,now,site['latitude_deg'],site['longitude_deg'])
    bad_ra,bad_dec=altaz_to_radec(-30,45,now,site['latitude_deg'],site['longitude_deg'])
    c,h=public_context(now,now,300,300,'2026-10-01',now+timedelta(hours=2),site,score,[],[])
    x,_=candidate_features(np.array([ra]),np.array([dec]),now,end,site,score)
    raw=dict(context=c[None],history=h[None],candidate=x[None],y=np.ones((1,1,4),np.float32))
    t=transform(raw,'compact');norm=Normalizer.fit(t);norm.save(tmp_path/'normalizer.npz')
    torch.manual_seed(101)
    net=ImprovementNet(t['context'].shape[-1],1,29,False,norm)
    torch.save(net.state_dict(),tmp_path/'weights.pt')
    meta=dict(context_dim=t['context'].shape[-1],candidate_dim=29,physics=False)
    (tmp_path/'metadata.json').write_text(json.dumps(meta))
    policy=dict(selected='compact',source='test fixture',calibration=dict(global_settings=dict(
        blocked_temperature=1,band_temperature=1,interval_correction=0),conditional=False,cells={}),
        assistance_rule=rule(exclude_enabled=False,recommend_enabled=False),
        priority_rule=dict(size=1,block_max=1.,usable_min=0.,enabled=True),priority_test_passed=True,
        assistance_acceptance=dict(strict_exclusion=False,direct_recommendation=False,priority_shortlist=True))
    (tmp_path/'policy.json').write_text(json.dumps(policy))
    request=dict(initialize=dict(site=site,scoring=score,survey=dict(nights=[dict(
        night_date='2026-10-01',observing_start_utc=now.isoformat(),observing_end_utc=(now+timedelta(hours=2)).isoformat())])),
        now_utc=now.isoformat(),bulletins=[],forecasts=[],candidates=[dict(
        candidate_id=str(i),ra_deg=ra if i<2 else bad_ra,dec_deg=dec if i<2 else bad_dec,
        start_utc=now.isoformat() if i!=1 else now.isoformat().replace('+00:00','Z'),duration_seconds=300) for i in range(3)])
    predictor=AdvisoryPredictor(tmp_path);response=predictor.predict(request)
    assert len(response['groups'])==1
    assert response['groups'][0]['eligible_candidates']==2
    assert len(response['groups'][0]['priority_candidate_ids'])==1
    assert response['predictions'][2]['formula_excluded']
    assert response['predictions'][2]['condition_rank'] is None
    assert not any(p['suggest_exclude'] or p['suggest_priority'] for p in response['predictions'])
    assert response['automatic_actions_enabled'] is False
    request['hidden_weather']=1
    with pytest.raises(ValueError,match='unsupported input fields'):predictor.predict(request)

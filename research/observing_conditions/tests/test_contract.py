from datetime import timedelta
import numpy as np
import pytest
import torch

from challenge.v4_fiber_map import radec_to_altaz,min_altitude_during,altaz_to_radec
from challenge.v4_scorer import SlotTruth,ScoreEvent,WeatherTruth,score_target_exposure,lunar_quality_factor
from research.observing_conditions.features import (utc,altaz,minimum_altitude,public_context,PublicTimeline,
    candidate_features,CONTEXT_NAMES,CANDIDATE_NAMES)
from research.observing_conditions.data import read_card,labels
from research.observing_conditions.models import ConditionsNet,training_loss
from research.observing_conditions.metrics import metrics,calibrate_fit,calibrate_apply


def test_geometry_matches_official():
    site,_,_,score=read_card('c');start=utc('2026-12-02T06:00:00Z');end=start+timedelta(seconds=3600)
    rng=np.random.default_rng(17);ra=rng.uniform(0,360,100);dec=rng.uniform(-80,80,100)
    a,z=altaz(ra,dec,start,site);low=minimum_altitude(ra,dec,start,end,site)
    for i in range(100):
        expected=radec_to_altaz(ra[i],dec[i],start,site['latitude_deg'],site['longitude_deg'])
        np.testing.assert_allclose([a[i],z[i]],expected,atol=1e-7)
        assert abs(low[i]-min_altitude_during(ra[i],dec[i],start,end,{'site':site}))<1e-7


def test_future_messages_do_not_change_features_and_terrain_persists():
    site,_,_,score=read_card('a');start=utc('2026-10-02T00:00:00Z')
    bulletins=[dict(issued_at_utc=(start+timedelta(seconds=i*900)).isoformat(),notices=
        [{'event_kind':'terrain_obstruction','direction':'NE'}] if i==0 else []) for i in range(40)]
    future=dict(issued_at_utc=(start+timedelta(days=2)).isoformat(),notices=[{'event_kind':'storm','direction':'ALL'}])
    now=start+timedelta(seconds=39*900+20);args=(now,now,900,900,'2026-10-01',now+timedelta(hours=1),site,score)
    first=public_context(*args,bulletins,[]);second=public_context(*args,bulletins+[future],[])
    for a,b in zip(first,second):np.testing.assert_array_equal(a,b)
    assert first[0][144+1]==1
    cached=PublicTimeline(bulletins+[future],[]).snapshot(*args)
    for a,b in zip(first,cached):np.testing.assert_allclose(a,b,atol=1e-6)


@pytest.mark.parametrize('offset,closed',[(0,False),(0,True),(350,False)])
def test_partial_closure_and_directional_quality(offset,closed):
    site,_,_,score=read_card('a');start=utc('2026-10-02T04:14:30Z');end=start+timedelta(seconds=900)
    slots=[SlotTruth(str(i),start+timedelta(seconds=450*i),start+timedelta(seconds=450*(i+1)),not(closed and i==1),0.5,1,1.5,0.4) for i in range(2)]
    # read_truth standardizes efficiency; labels intentionally ignore input efficiency as well.
    slots=[SlotTruth(s.slot_id,s.start_utc,s.end_utc,s.is_observable,s.seeing_arcsec,s.transparency,s.sky_quality,1) for s in slots]
    weather=WeatherTruth(slots,None)
    event=ScoreEvent('e','cloudy',start+timedelta(seconds=100),end-timedelta(seconds=200),'HORIZON_SECTOR',
        float(offset),float((offset+80)%360),0,90,1.25,0.5,0.5,1,False)
    ra=np.array([20.,110.,220.]);dec=np.array([-35.,-55.,-20.])
    actual=labels(ra,dec,start,end,weather,[event],site,score)
    for i in range(3):
        target=dict(target_id='x',ra_deg=ra[i],dec_deg=dec[i],feature_flux=1,science_weight=1)
        callback=lambda t:radec_to_altaz(ra[i],dec[i],t,site['latitude_deg'],site['longitude_deg'])
        expected=score_target_exposure(target,list(weather.segments(start,end)),[event],weather,900,1,score,start,callback,site)
        assert abs(actual[i,0]-expected.quality)<2e-6
    np.testing.assert_allclose(actual[:,1],0.5 if closed else 1)


@pytest.mark.parametrize('architecture',['mlp','gru','tcn'])
def test_network_constraints_and_gradients(architecture):
    model=ConditionsNet(architecture,len(CONTEXT_NAMES),75,len(CANDIDATE_NAMES))
    c=torch.randn(2,len(CONTEXT_NAMES));h=torch.randn(2,32,75);x=torch.randn(2,32,len(CANDIDATE_NAMES))
    result=model(c,h,x)
    assert result.shape==(2,32,9)
    assert torch.all(result[...,0]>0)
    assert torch.all(result[...,1]<=result[...,2]) and torch.all(result[...,2]<=result[...,3])
    y=torch.zeros(2,32,4);y[...,0]=2.2;y[...,1]=1
    loss=training_loss(result,y,0.8);loss.backward()
    assert torch.isfinite(loss)
    assert all(torch.isfinite(p.grad).all() for p in model.parameters() if p.grad is not None)


def test_metrics_detect_bad_predictions_and_calibration_preserves_probabilities():
    y=np.array([[[0.1,0,1,2],[1.2,1,0,0]]],dtype=np.float32)
    good=np.array([[[0.1,0,0.1,0.2,0,1,0,0,1],[1.2,1.1,1.2,1.3,1,0,1,0,0]]],dtype=np.float32)
    bad=good.copy();bad[...,0]=good[...,0][:,::-1];bad[...,5]=0.5
    assert metrics(y,good)['quality_mae']<metrics(y,bad)['quality_mae']
    assert metrics(y,good)['ranking_loss']<metrics(y,bad)['ranking_loss']
    result=calibrate_apply(good,calibrate_fit(y,good))
    np.testing.assert_allclose(result[...,6:9].sum(axis=-1),1,atol=1e-6)
    assert (result[...,1]<=result[...,3]).all()


def test_good_sky_quality_can_exceed_one():
    site,_,_,score=read_card('a');start=utc('2026-10-02T04:00:00Z');end=start+timedelta(seconds=300)
    ra,dec=altaz_to_radec(80,45,start,site['latitude_deg'],site['longitude_deg'])
    weather=WeatherTruth([SlotTruth('s',start,end,True,0.5,1,1.5,1)],None)
    answer=labels(np.array([ra]),np.array([dec]),start,end,weather,[],site,score)
    assert answer[0,0]>1


def test_prediction_public_contract_and_night_end(tmp_path):
    import json
    from research.observing_conditions.models import Normalizer
    from research.observing_conditions.predict import Predictor
    from research.observing_conditions.features import VERSION
    site,_,_,score=read_card('a');now=utc('2026-10-02T04:00:00Z')
    end=now+timedelta(seconds=120)
    c,h=public_context(now,now,900,120,'2026-10-01',end,site,score,[],[])
    ra,dec=altaz_to_radec(80,45,now,site['latitude_deg'],site['longitude_deg'])
    x,_=candidate_features(np.array([ra]),np.array([dec]),now,end,site,score)
    data={'context':c[None],'history':h[None],'candidate':x[None],
          'y':np.array([[[1.,1.,0.,0.]]],dtype=np.float32)}
    Normalizer.fit(data).save(tmp_path/'normalizer.npz')
    meta=dict(architecture='mlp',context_dim=len(CONTEXT_NAMES),history_dim=75,candidate_dim=len(CANDIDATE_NAMES),
        seeds=[101],quality_scale=1.,feature_version=VERSION,quality_definition='Q_env',source='self-generated',
        validation_reliable=True,final_test_passed=False,acceptance={'bands':False},
        calibration=dict(blocked_temperature=1,band_temperature=1,interval_correction=0))
    (tmp_path/'metadata.json').write_text(json.dumps(meta))
    torch.save(ConditionsNet('mlp',len(CONTEXT_NAMES),75,len(CANDIDATE_NAMES)).state_dict(),tmp_path/'weights-101.pt')
    predictor=Predictor(tmp_path)
    request={'initialize':{'site':site,'scoring':score,'survey':{'nights':[dict(night_date='2026-10-01',observing_start_utc=now.isoformat(),observing_end_utc=end.isoformat())]}},
        'now_utc':now.isoformat(),'bulletins':[],'forecasts':[],
        'candidates':[dict(candidate_id='p',ra_deg=ra,dec_deg=dec,start_utc=now.isoformat(),duration_seconds=900)]}
    response=predictor.predict(request)
    assert response['validation_reliable'] is True
    assert response['final_test_passed'] is False and response['acceptance']=={'bands':False}
    row=response['predictions'][0]
    assert row['actual_duration_seconds']==120
    assert abs(row['p_dark']+row['p_bright']+row['p_backup']-1)<1e-6
    assert row['q05']<=row['q50']<=row['q95']
    request['seed']=123
    with pytest.raises(ValueError,match='unsupported input fields'):
        predictor.predict(request)


def test_formal_calendar_complete_and_byte_identical(tmp_path):
    from research.observing_conditions.data import make_config,generate_weather_files,ROOT
    import csv
    entry=dict(card='b',split='train',seed=730000,id='calendar-test')
    config,_,_,_=make_config(entry)
    assert config['survey']['end_date']=='2027-12-01'
    generate_weather_files(config,tmp_path,entry)
    official=ROOT/'cards/b/public/v4_night_calendar.csv'
    assert (tmp_path/'v4_night_calendar.csv').read_bytes()==official.read_bytes()
    expected=list(csv.DictReader(official.open()))
    actual=list(csv.DictReader((tmp_path/'v4_weather_truth.csv').open()))
    assert len(actual)==sum(int(row['slot_count']) for row in expected)
    assert actual[-1]['night_id']==expected[-1]['night_id']


def test_empty_public_history_has_explicit_missing_values():
    site,_,_,score=read_card('a');now=utc('2026-10-02T04:00:00Z')
    args=(now,now,60,60,'2026-10-01',now+timedelta(hours=2),site,score)
    expected=public_context(*args,[],[])
    cached=PublicTimeline([],[]).snapshot(*args)
    for left,right in zip(expected,cached):np.testing.assert_array_equal(left,right)
    assert not expected[1].any()
    assert expected[0][CONTEXT_NAMES.index('bulletin_age')]==-1
    assert expected[0][CONTEXT_NAMES.index('forecast_age')]==-1


def test_cross_night_gap_and_forecast_publication_boundary():
    from research.observing_conditions.features import age_value
    site,_,_,score=read_card('a');first=utc('2026-10-02T09:00:00Z')
    now=first+timedelta(hours=18)
    bulletins=[dict(issued_at_utc=t.isoformat(),notices=[{'event_kind':'haze','direction':'E'}])
               for t in (first,now)]
    forecast=dict(issued_at_utc=(now+timedelta(seconds=1)).isoformat(),
        coverage_start_utc=now.isoformat(),coverage_end_utc=(now+timedelta(days=2)).isoformat(),
        notices=[{'event_kind':'storm','direction':'ALL','nights':['2026-10-02']}])
    args=(now,now,60,60,'2026-10-02',now+timedelta(hours=2),site,score)
    context,history=PublicTimeline(bulletins,[forecast]).snapshot(*args)
    assert history[-1,73]==pytest.approx(age_value(18*3600))
    assert context[CONTEXT_NAMES.index('forecast_covers')]==0
    assert not context[72:144].any()
    forecast['issued_at_utc']=now.isoformat()
    published,_=public_context(*args,bulletins,[forecast])
    assert published[CONTEXT_NAMES.index('forecast:storm:ALL')]==1
    assert published[CONTEXT_NAMES.index('forecast_covers')]==1


def test_overlapping_closures_are_counted_once_and_do_not_set_band():
    site,_,_,score=read_card('a');start=utc('2026-10-02T04:00:00Z');end=start+timedelta(seconds=60)
    ra,dec=altaz_to_radec(80,45,start,site['latitude_deg'],site['longitude_deg'])
    weather=WeatherTruth([SlotTruth('s',start,end,True,0.5,1,1.5,1)],None)
    events=[ScoreEvent(str(i),'terrain_obstruction',start+timedelta(seconds=a),start+timedelta(seconds=b),
        'HORIZON_SECTOR',0,359.999,0,90,1,1,1,1,True) for i,(a,b) in enumerate(((10,40),(30,50)))]
    coords=(np.array([ra]),np.array([dec]))
    blocked=labels(*coords,start,end,weather,events,site,score)
    clear=labels(*coords,start,end,weather,[],site,score)
    assert blocked[0,1]==pytest.approx(1/3)
    assert blocked[0,2]==1
    assert blocked[0,0]<clear[0,0]
    assert blocked[0,3]==clear[0,3]


@pytest.mark.parametrize('threshold',['DARK','BRIGHT'])
def test_independent_band_threshold_boundary(threshold):
    import copy
    site,_,_,original=read_card('a');score=copy.deepcopy(original)
    score['q0']=1;score['airmass_exponent']=0;score['lunar_model']['maximum_penalty']=0
    start=utc('2026-10-02T04:00:00Z');end=start+timedelta(seconds=60)
    ra,dec=altaz_to_radec(80,45,start,site['latitude_deg'],site['longitude_deg'])
    value=score['program']['bands'][threshold]
    expected={'DARK':0,'BRIGHT':1}[threshold]
    for delta,band in ((-1e-6,expected+1),(0,expected),(1e-6,expected)):
        weather=WeatherTruth([SlotTruth('s',start,end,True,1,1,value+delta,1)],None)
        result=labels(np.array([ra]),np.array([dec]),start,end,weather,[],site,score)
        assert result[0,3]==band


@pytest.mark.parametrize('split',['train','weather_test'])
def test_rocket_durations_follow_config_variation(split):
    from research.observing_conditions.data import make_config
    intervals=[]
    low,high=(0.75,1.25) if split=='train' else (0.5,1.5)
    for index in range(20):
        config,_,_,_=make_config(dict(card='a',split=split,seed=730000+index))
        durations=config['rocket_launch']['duration_slots']
        assert all(reference*low-0.5<=actual<=reference*high+0.5
                   for reference,actual in zip((2,5),durations))
        intervals.append(tuple(durations))
    assert len(set(intervals))>1


def test_scene_bootstrap_recomputes_pooled_metrics():
    from research.observing_conditions.metrics import scene_bootstrap
    y=np.array([[[0.1,1,0,2],[1.2,1,0,0]],[[0.1,1,0,2],[0.3,1,0,1]]],dtype=np.float32)
    p=np.zeros((2,2,9),dtype=np.float32);p[...,0]=y[...,0]
    p[...,1]=0;p[...,2]=y[...,0];p[...,3]=2;p[...,4]=1;p[...,6:9]=1/3
    bad=p.copy();bad[...,0]=p[...,0][:,::-1]
    intervals=scene_bootstrap(y,p,bad,np.array(['first','second']),repetitions=200)
    expected=metrics(y,p)['ranking_loss']-metrics(y,bad)['ranking_loss']
    assert intervals['ranking_loss']['metric_difference']==pytest.approx(expected)
    lower,upper=intervals['ranking_loss']['ci95']
    assert lower<=expected<=upper
    assert intervals['band_macro_f1']['metric_difference']==0


def test_lookup_retains_public_terrain_after_one_announcement():
    from research.observing_conditions.metrics import StatisticalBaseline
    context=np.zeros((2,len(CONTEXT_NAMES)),dtype=np.float32)
    context[0,CONTEXT_NAMES.index('terrain:N')]=1
    candidate=np.zeros((2,1,len(CANDIDATE_NAMES)),dtype=np.float32)
    candidate[...,0]=40/90;candidate[...,2]=1
    keys=StatisticalBaseline().keys(dict(context=context,candidate=candidate))
    assert keys[0,0]!=keys[1,0]
    assert not context[:,:72].any()

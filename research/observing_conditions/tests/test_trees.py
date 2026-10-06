import numpy as np
import pytest

from research.observing_conditions.tree_models import public_features,numeric_heads,ranking_view,fit_calibration,apply_calibration
from research.observing_conditions.traditional import entries,low_altitude


def small_data():
    rng=np.random.default_rng(101)
    return dict(context=np.zeros((2,242),np.float32),history=np.zeros((2,32,75),np.float32),
                candidate=np.abs(rng.normal(size=(2,4,29))).astype(np.float32),y=np.zeros((2,4,4),np.float32))


def test_tree_features_ignore_truth_ids_and_future_private_data():
    data=small_data();x=public_features(data)
    data.update(y=np.ones_like(data['y'])*1234,scene=np.array(['secret','secret']),card='d',hidden=np.ones((2,4,24)),seed=99)
    np.testing.assert_array_equal(x,public_features(data))
    assert x.shape==(8,570) and np.isfinite(x).all()


def test_tree_outputs_preserve_quality_above_one_and_valid_probabilities():
    data=small_data();data['candidate'][...,28]=2
    heads=np.tile([1.5,1.1,-.1,.2,.3,.5],(8,1))
    p=numeric_heads(heads,data)
    assert (p[...,0]==3).all()
    assert (p[...,4]==1).all() and (p[...,5]==0).all()
    np.testing.assert_allclose(p[...,6:9].sum(axis=-1),1)
    data['y'][...,0]=np.arange(8).reshape(2,4)*.5
    data['y'][...,1]=1;data['y'][...,3]=1
    adjustment=fit_calibration(data['y'],p,data)
    result=apply_calibration(p,data,adjustment)
    assert np.isfinite(result).all()
    assert (result[...,1]<=result[...,2]).all() and (result[...,2]<=result[...,3]).all()
    np.testing.assert_allclose(result[...,6:9].sum(axis=-1),1,atol=1e-6)


def test_rank_view_does_not_overwrite_quality_or_change_rank_order():
    p=numeric_heads(np.tile([1.,1.,.2,.2,.3,.5],(8,1)),small_data())
    original=p.copy();scores=np.array([[-4,-3,-2,-1],[4,3,2,1]])
    rank=ranking_view(p,scores)
    np.testing.assert_array_equal(p,original)
    np.testing.assert_array_equal(rank[...,1:],original[...,1:])
    np.testing.assert_array_equal(rank[...,0].argmax(axis=1),scores.argmax(axis=1))
    with pytest.raises(ValueError):ranking_view(p,scores[:1])


def test_low_altitude_subset_is_chosen_by_public_height_not_answers():
    data=small_data()
    data['candidate']=np.tile(data['candidate'],(1,4,1));data['y']=np.tile(data['y'],(1,4,1))
    p=numeric_heads(np.tile([1,1,0,.2,.3,.5],(32,1)),data)
    a,ap,_=low_altitude(data,p)
    data['y'][:]=999
    b,bp,_=low_altitude(data,p)
    np.testing.assert_array_equal(a['candidate'],b['candidate'])
    assert a['candidate'].shape[1]==8
    np.testing.assert_array_equal(ap,bp)


def test_tree_new_scenes_are_disjoint_from_previous_rounds():
    from research.observing_conditions.data import scenario_entries
    from research.observing_conditions.improvement import entries as previous
    new=entries();used=scenario_entries()+previous()
    assert len(new)==32 and len({e['seed'] for e in new})==32
    assert not {e['seed'] for e in new}&{e['seed'] for e in used}


def test_api_rank_scores_follow_candidate_indices_when_group_order_changes():
    from research.observing_conditions.tree_predict import TreePredictor
    predictor=TreePredictor.__new__(TreePredictor)
    predictor._rank_by_index={}
    predictor._current_scores=np.array([[10.,20.]])
    predictor.record_group_predictions([2,4])
    predictor._current_scores=np.array([[30.,40.]])
    predictor.record_group_predictions([1,3])
    p=np.zeros((1,2,9));p[...,0]=[.9,.8]
    a=predictor.priority_predictions(p,[1,3])
    b=predictor.priority_predictions(p,[2,4])
    assert a[0,:,0].argmax()==1 and b[0,:,0].argmax()==1
    assert predictor._rank_by_index=={2:10.,4:20.,1:30.,3:40.}
    np.testing.assert_array_equal(p[0,:,0],[.9,.8])


def test_mac_api_guard_requires_shared_runtime_before_loading_models(monkeypatch):
    from research.observing_conditions import tree_runtime
    monkeypatch.setattr(tree_runtime,'shared_library_directory',lambda:'/virtual/torch/lib')
    monkeypatch.delenv('DYLD_LIBRARY_PATH',raising=False)
    with pytest.raises(RuntimeError,match='tree_runtime'):
        tree_runtime.require_shared_runtime()
    monkeypatch.setenv('DYLD_LIBRARY_PATH','/virtual/torch/lib:/other')
    tree_runtime.require_shared_runtime()

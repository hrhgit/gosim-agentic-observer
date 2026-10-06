"""Traditional tree models using only the same public information as the MLP.

Separate ranking scores never replace quality predictions. Interval estimates
come from independent calibration residuals, not tree-to-tree disagreement.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import joblib
import lightgbm as lgb
import numpy as np
from sklearn.ensemble import ExtraTreesRegressor

from .data import save_json
from .improvement import summary_history
from .metrics import calibrate_fit,calibrate_apply

FEATURE_VERSION='public-trees-v1'
SEED=101
TREE_SETTINGS=dict(n_estimators=120,num_leaves=31,max_depth=-1,learning_rate=.05,
    min_child_samples=80,colsample_bytree=.8,reg_lambda=1.,n_jobs=4,random_state=SEED,
    verbosity=-1,deterministic=True,force_col_wise=True)


def public_features(data):
    """No y/scene/card/seed/hidden fields are accessed here."""
    context=data['context'];history=data['history'];candidate=data['candidate']
    shape=candidate.shape[:2]
    shared=np.column_stack([context,summary_history(history)])
    shared=np.broadcast_to(shared[:,None,:],(*shape,shared.shape[-1]))
    current=context[:,:72].reshape(-1,8,9).copy()
    current[:,-1]=np.maximum(current[:,-1],context[:,144:153])
    angle=np.radians(np.arange(8)*45)
    proximity=candidate[...,1,None]*np.sin(angle)+candidate[...,2,None]*np.cos(angle)
    nearby=((proximity>.70710678)[:,:,None,:]*current[:,None,:,:8]).sum(axis=-1)+current[:,None,:,8]
    # Direction proximity is derived from coarse public directions, not a true
    # event boundary. Its assumptions are not treated as deterministic closure.
    x=np.concatenate([shared,candidate,nearby],axis=-1)
    return x.reshape(-1,x.shape[-1]).astype(np.float32)


def geometry_factor(data):
    return np.maximum(data['candidate'][...,28],1e-6)


def numeric_heads(heads,data):
    shape=data['candidate'].shape[:2];heads=np.asarray(heads).reshape(*shape,6)
    p=np.zeros((*shape,9),np.float32)
    p[...,0]=np.maximum(0,heads[...,0])*geometry_factor(data)
    p[...,1:4]=p[...,0,None]
    p[...,4:6]=np.clip(heads[...,1:3],0,1)
    bands=np.maximum(heads[...,3:6],1e-7)
    p[...,6:9]=bands/bands.sum(axis=-1,keepdims=True)
    return p


def fit_calibration(y,p,data):
    g=geometry_factor(data)
    residual=(y[...,0]-p[...,0])/g
    offsets=np.quantile(residual.ravel(),[.05,.5,.95]).tolist()
    preliminary=p.copy()
    preliminary[...,1:4]=np.maximum(0,p[...,0,None]+g[...,None]*np.asarray(offsets))
    result=dict(residual_offsets=offsets,probability_and_interval=calibrate_fit(y,preliminary),
        interval_method='held-out normalized signed residual quantiles plus CQR; not ensemble disagreement',
        median_method='point prediction plus median calibration residual; not guaranteed conditional median',
        limitation='Empirical calibration, no individual or distribution-shift guarantee')
    return result


def apply_calibration(p,data,settings):
    result=p.copy();g=geometry_factor(data)
    result[...,1:4]=np.maximum(0,result[...,0,None]+g[...,None]*np.asarray(settings['residual_offsets']))
    return calibrate_apply(result,settings['probability_and_interval'])


def ranking_view(p,scores=None):
    if scores is None:
        return p
    scores=np.asarray(scores)
    if scores.shape!=p.shape[:2] or not np.isfinite(scores).all():
        raise ValueError('invalid separate ranking scores')
    result=p.copy()
    # Positive, ordering-preserving scores for existing priority-only grader.
    # This view is never passed to Q metrics or exported as mean_quality.
    result[...,0]=scores-scores.min(axis=1,keepdims=True)+1.
    return result


def fit_extra(directory,x,train,mask,identity):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    settings=dict(n_estimators=64,max_depth=16,min_samples_leaf=32,max_features=.4,
        n_jobs=4,random_state=SEED,criterion='squared_error')
    if (directory/'complete.json').exists():
        saved=json.loads((directory/'complete.json').read_text())
        if saved['identity']!=identity or saved['settings']!=settings:raise ValueError('extra trees mismatch')
        return joblib.load(directory/'model.joblib')
    y=train['y'];target=np.concatenate([(y[...,0]/geometry_factor(train))[...,None],
        y[...,1:3],np.eye(3)[y[...,3].astype(int)]],axis=-1).reshape(-1,6)
    begin=time.perf_counter();model=ExtraTreesRegressor(**settings).fit(x[:,mask],target)
    joblib.dump(model,directory/'model.joblib')
    save_json(directory/'complete.json',dict(identity=identity,settings=settings,
        elapsed_seconds=time.perf_counter()-begin,initialization_repetitions=1,fit_calls=1))
    return model


def fit_boosted(directory,x,train,mask,identity):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    targets=dict(quality=(train['y'][...,0]/geometry_factor(train)).ravel(),
        usable=train['y'][...,1].ravel(),blocked=train['y'][...,2].ravel(),band=train['y'][...,3].ravel().astype(int))
    kinds=dict(quality='regression',usable='regression',blocked='binary',band='multiclass')
    for name,target in targets.items():
        path=directory/f'{name}.txt'
        if path.exists() and (directory/f'{name}-complete.json').exists():continue
        begin=time.perf_counter()
        cls=lgb.LGBMRegressor if name in ('quality','usable') else lgb.LGBMClassifier
        model=cls(**TREE_SETTINGS,objective=kinds[name]).fit(x[:,mask],target)
        model.booster_.save_model(str(path))
        save_json(directory/f'{name}-complete.json',dict(elapsed_seconds=time.perf_counter()-begin,
            target=name,initialization_repetitions=1,boosting_iterations=model.booster_.current_iteration()))
        print('LightGBM fitted '+name,flush=True)
    save_json(directory/'complete.json',dict(identity=identity,settings=TREE_SETTINGS,
        elapsed_seconds=sum(json.loads((directory/f'{n}-complete.json').read_text())['elapsed_seconds'] for n in targets),
        initialization_repetitions=1,fit_calls=4,
        note='Four distinct outputs, one fit per output; no repeated seeds'))
    return {name:lgb.Booster(model_file=str(directory/f'{name}.txt')) for name in targets}


def fit_ranker(directory,x,train,mask,identity):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    if (directory/'complete.json').exists():
        saved=json.loads((directory/'complete.json').read_text())
        if saved['identity']!=identity:raise ValueError('ranker mismatch')
        return lgb.Booster(model_file=str(directory/'ranker.txt'))
    q=train['y'][...,0];labels=np.floor(20*q/np.maximum(q.max(axis=1,keepdims=True),1e-8)).astype(int)
    labels=np.where(train['y'][...,1]>=.5,labels,0)
    begin=time.perf_counter()
    model=lgb.LGBMRanker(**TREE_SETTINGS,objective='lambdarank',label_gain=list(range(21)),
        lambdarank_truncation_level=7).fit(x[:,mask],labels.ravel(),group=[q.shape[1]]*len(q))
    model.booster_.save_model(str(directory/'ranker.txt'))
    save_json(directory/'complete.json',dict(identity=identity,settings=TREE_SETTINGS,
        elapsed_seconds=time.perf_counter()-begin,fit_calls=1,initialization_repetitions=1,
        ranking_labels='0..20 normalized within group; U<0.5 gets 0; linear gains',
        boosting_iterations=model.booster_.current_iteration()))
    return model.booster_


class TreeBundle:
    def __init__(self,mask,extra,boosted,ranker):
        self.mask=mask;self.extra=extra;self.boosted=boosted;self.ranker=ranker

    def predict(self,data,kind):
        x=public_features(data)[:,self.mask]
        if kind=='extra_trees':
            heads=self.extra.predict(x)
        elif kind in ('lightgbm','lightgbm_ranker'):
            values={k:m.predict(x,num_threads=1) for k,m in self.boosted.items()}
            heads=np.column_stack([values['quality'],values['usable'],values['blocked'],values['band']])
        else:raise ValueError(kind)
        p=numeric_heads(heads,data)
        scores=self.ranker.predict(x,num_threads=1).reshape(p.shape[:2]) if kind=='lightgbm_ranker' else None
        return p,scores

    @classmethod
    def load(cls,directory):
        directory=Path(directory)
        mask=np.load(directory/'feature_mask.npy')
        extra=joblib.load(directory/'extra_trees/model.joblib')
        boosted={name:lgb.Booster(model_file=str(directory/f'lightgbm/{name}.txt')) for name in ('quality','usable','blocked','band')}
        ranker=lgb.Booster(model_file=str(directory/'lightgbm_ranker/ranker.txt'))
        return cls(mask,extra,boosted,ranker)

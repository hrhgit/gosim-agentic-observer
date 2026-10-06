"""Small public-history probability and interval adjustments; frozen base weights."""
from __future__ import annotations
import numpy as np
from scipy.optimize import minimize
from scipy.special import softmax
from . import conditional_calibration as old

PHASES=('never','stale','partial','eight')
METHODS=('fresh_temperature','fresh_affine','fresh_affine_shrink')


def public_keys(metadata):
    leads,_=old.public_keys(metadata)
    for m in metadata:
        total=m['all_history_count'];fresh=m['fresh_history_count']
        if isinstance(total,bool) or not isinstance(total,(int,np.integer)) or not 0<=fresh<=total<=8:
            raise ValueError('history freshness must count completed public observations')
    return leads,np.array(['never' if m['all_history_count']==0 else 'stale' if m['fresh_history_count']==0
                           else 'partial' if m['fresh_history_count']<8 else 'eight' for m in metadata])


def fit_group(data,p,parent,affine,penalty):
    bank=old.fit_group(data,p,parent)
    if not affine:
        bank['band_bias']=[0.,0.,0.];return bank
    weights=old.weights(data);logits=np.log(np.clip(p[:,9:12],1e-7,1));labels=data['y'][:,3].astype(int)
    pt=(parent or {}).get('band_temperature',1.);pb=np.array((parent or {}).get('band_bias',[0.,0.,0.]))
    # Three fitted degrees of freedom, sharing a temperature and centered biases.
    initial=np.r_[np.log(pt),pb[:2]]
    def unpack(v):return np.exp(v[0]),np.r_[v[1:3],-v[1]-v[2]]
    def objective(v):
        temperature,bias=unpack(v);prob=softmax(logits/temperature+bias,axis=1)
        return float(-np.sum(weights*np.log(np.maximum(prob[np.arange(len(labels)),labels],1e-12)))
                     +penalty*((v[0]-initial[0])**2+np.sum((bias-pb)**2)))
    solution=minimize(objective,initial,method='L-BFGS-B',bounds=[(-4,4),(-5,5),(-5,5)])
    if not solution.success:raise RuntimeError('regularized probability fit failed: '+solution.message)
    temperature,bias=unpack(solution.x)
    bank.update(band_temperature=float(temperature),band_bias=bias.tolist(),
                band_fit='temperature and centered bias; regularized toward parent',band_penalty=penalty,
                band_optimizer_success=True,band_parent_prior=True)
    return bank


def fit(bulk,raw,night,night_raw,method,min_scenes=8):
    if method not in METHODS:raise ValueError('unknown public-history adjustment')
    affine=method!='fresh_temperature';penalty=.01 if method=='fresh_affine_shrink' else .001
    banks={'global':fit_group(bulk,raw,None,affine,penalty)};unsupported={}
    for lead in old.HORIZONS:
        mask=np.array([m['horizon']==lead for m in bulk['meta']]);d=old.subset(bulk,mask)
        banks[lead]=fit_group(d,raw[mask],banks['global'],affine,penalty)
    # Night samples span the calendar. Duplicate decision/lead groups share weight.
    data=dict(y=np.concatenate([bulk['y'],night['y']]),scene=np.concatenate([bulk['scene'],night['scene']]),
              meta=bulk['meta']+night['meta']);p=np.concatenate([raw,night_raw]);leads,phases=public_keys(data['meta'])
    for lead in old.HORIZONS:
        for phase in PHASES:
            mask=(leads==lead)&(phases==phase);d=old.subset(data,mask);key=lead+'/'+phase
            if len(np.unique(d['scene']))>=min_scenes:
                banks[key]=fit_group(d,p[mask],banks[lead],affine,penalty)
            else:unsupported[key]=dict(scenes=len(np.unique(d['scene'])),parent=lead)
    return dict(version='conditions-reliability-v2',scheme='history_horizon_sides',method=method,banks=banks,
        unsupported_groups=unsupported,min_fitting_scenes=min_scenes,
        public_group_fields=['horizon','fresh_history_count','all_history_count'],coverage_target=.9,
        limitation='Empirical whole-scene evidence only. Rare bands and night-opening states need separate checks; '
                   'no per-candidate confidence guarantee or guarantee for changed weather.')


def apply(raw,metadata,settings,return_evidence=False):
    leads,phases=public_keys(metadata)
    if np.asarray(raw).shape!=(len(metadata),12) or not np.isfinite(raw).all():raise ValueError('invalid predictions')
    result=np.asarray(raw,dtype=np.float64).copy();evidence=[None]*len(raw)
    for lead in np.unique(leads):
        for phase in np.unique(phases[leads==lead]):
            ix=np.flatnonzero((leads==lead)&(phases==phase));key,bank,pooled=old.select_bank(settings,str(lead),str(phase))
            tmp=dict(settings,scheme='global_sides',banks={'global':bank})
            result[ix]=old.apply(raw[ix],[metadata[int(i)] for i in ix],tmp)
            result[ix,9:12]=softmax(np.log(np.clip(raw[ix,9:12],1e-7,1))/bank['band_temperature']+
                                    np.array(bank.get('band_bias',[0.,0.,0.])),axis=1)
            if return_evidence:
                value=dict(group=key,pooled=pooled,**bank['evidence'],
                    probability_fit_supported=bank['probability_fit_supported'],
                    band_adjustment=bank.get('band_fit','temperature only'),
                    band_parent_prior=bank.get('band_parent_prior',False),
                    probability_parent_used=bank['probability_parent_used'])
                for i in ix:evidence[int(i)]=value
    return (result.astype(np.float32),evidence) if return_evidence else result.astype(np.float32)

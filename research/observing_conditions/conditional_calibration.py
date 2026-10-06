"""Frozen numerical predictions -> public-history conditional ranges/probabilities.

Only horizon and completed-history count determine the runtime group. Calibration
labels, scenario identifiers and hidden weather flags cannot determine that group.
"""
from __future__ import annotations
import numpy as np
from scipy.optimize import minimize_scalar
from scipy.special import expit, softmax

HORIZONS=('15m','30m','60m','next_night')
PHASES=('none','partial','eight')
SCHEMES=('global','global_sides','horizon_sides','history_horizon_sides')


def public_keys(metadata):
    leads=[];phases=[]
    for row in metadata:
        lead=row['horizon'];count=row['history_count']
        if lead not in HORIZONS:raise ValueError('unsupported public horizon')
        if isinstance(count,bool) or not isinstance(count,(int,np.integer)) or count<0:
            raise ValueError('history count must count completed observations')
        leads.append(lead);phases.append('none' if count==0 else 'partial' if count<8 else 'eight')
    return np.array(leads),np.array(phases)


def weights(data):
    """Each scene equal, each exposure/horizon group equal, positions share weight."""
    groups=[(str(s),m['observe_index'],m['horizon']) for s,m in zip(data['scene'],data['meta'])]
    counts={};scene_groups={}
    for group in groups:counts[group]=counts.get(group,0)+1
    for group in counts:scene_groups[group[0]]=scene_groups.get(group[0],0)+1
    w=np.array([1/(counts[g]*scene_groups[g[0]]) for g in groups],float)
    return w/w.sum()


def quantile(values,w,level):
    order=np.argsort(values,kind='stable');cumulative=np.cumsum(w[order]);cumulative/=cumulative[-1]
    return float(np.asarray(values)[order[min(int(np.searchsorted(cumulative,level)),len(order)-1)]])


def subset(data,mask):
    ix=np.flatnonzero(mask)
    return dict(y=data['y'][mask],scene=data['scene'][mask],meta=[data['meta'][int(i)] for i in ix])


def fit_group(data,p,parent=None,symmetric=False):
    y=data['y'];w=weights(data);scene=np.asarray(data['scene'])
    evidence=dict(rows=len(y),scenes=len(np.unique(scene)),decision_groups=len(set(
        (str(s),m['observe_index'],m['horizon']) for s,m in zip(scene,data['meta']))))
    evidence['limited_scene_evidence']=evidence['scenes']<20
    binary=np.clip(p[:,8],1e-7,1-1e-7);blogit=np.log(binary/(1-binary))
    mlogit=np.log(np.clip(p[:,9:12],1e-7,1))
    positive=len(np.unique(scene[y[:,2]==1]));negative=len(np.unique(scene[y[:,2]==0]))
    classes=[len(np.unique(scene[y[:,3]==i])) for i in range(3)]
    evidence.update(blocked_positive_scenes=positive,blocked_negative_scenes=negative,band_class_scenes=classes)
    supported=dict(blocked=min(positive,negative)>=3,band=min(classes)>=3)
    bt=(parent or {}).get('blocked_temperature',1.);mt=(parent or {}).get('band_temperature',1.)
    if supported['blocked']:
        loss=lambda t:float(np.sum(w*(np.logaddexp(0,blogit/np.exp(t))-y[:,2]*blogit/np.exp(t))))
        bt=float(np.exp(minimize_scalar(loss,bounds=(-4,4),method='bounded').x))
    if supported['band']:
        loss=lambda t:float(-np.sum(w*np.log(np.maximum(softmax(mlogit/np.exp(t),axis=1)[np.arange(len(y)),y[:,3].astype(int)],1e-12))))
        mt=float(np.exp(minimize_scalar(loss,bounds=(-4,4),method='bounded').x))
    offsets={}
    for name,column,lo,hi in [('quality',0,1,3),('usable',1,5,7)]:
        if symmetric:
            correction=max(0.,quantile(np.maximum(p[:,lo]-y[:,column],y[:,column]-p[:,hi]),w,.9))
            offsets[name]=[-correction,correction]
        else:
            offsets[name]=[quantile(y[:,column]-p[:,lo],w,.05),quantile(y[:,column]-p[:,hi],w,.95)]
    return dict(offsets=offsets,blocked_temperature=bt,band_temperature=mt,evidence=evidence,
                probability_fit_supported=supported,probability_parent_used={k:not v for k,v in supported.items()})


def fit(bulk,raw,initial,initial_raw,scheme,min_scenes=8):
    if scheme not in SCHEMES:raise ValueError('unknown calibration scheme')
    leads,phases=public_keys(bulk['meta']);ileads,iphases=public_keys(initial['meta'])
    banks={'global':fit_group(bulk,raw,symmetric=scheme=='global')};unsupported={}
    if scheme in ('horizon_sides','history_horizon_sides'):
        for lead in HORIZONS:
            mask=leads==lead;data=subset(bulk,mask)
            if len(np.unique(data['scene']))>=min_scenes:
                banks[lead]=fit_group(data,raw[mask],banks['global'])
            else:unsupported[lead]=dict(scenes=len(np.unique(data['scene'])),parent='global')
    if scheme=='history_horizon_sides':
        for lead in HORIZONS:
            for phase in PHASES:
                mask=(leads==lead)&(phases==phase) if phase=='eight' else (ileads==lead)&(iphases==phase)
                data=subset(bulk if phase=='eight' else initial,mask)
                key=lead+'/'+phase;parent=banks.get(lead,banks['global'])
                if len(np.unique(data['scene']))>=min_scenes:
                    banks[key]=fit_group(data,(raw if phase=='eight' else initial_raw)[mask],parent)
                else:unsupported[key]=dict(scenes=len(np.unique(data['scene'])),parent=lead if lead in banks else 'global')
    return dict(version='public-conditional-calibration-v1',scheme=scheme,banks=banks,
        unsupported_groups=unsupported,min_fitting_scenes=min_scenes,
        public_group_fields=['horizon','history_count'],coverage_target=.9,
        limitation='Empirical scene-held-out assessment; correlated rows, sparse tail events and changed weather preclude per-row guarantees.')


def select_bank(settings,lead,phase):
    scheme=settings['scheme'];banks=settings['banks']
    requested=lead+'/'+phase if scheme=='history_horizon_sides' else lead if scheme=='horizon_sides' else 'global'
    if requested in banks:return requested,banks[requested],False
    parent=lead if lead in banks else 'global'
    return parent,banks[parent],True


def apply(raw,metadata,settings,return_evidence=False):
    raw=np.asarray(raw)
    if raw.shape!=(len(metadata),12) or not np.isfinite(raw).all():raise ValueError('invalid frozen predictions')
    leads,phases=public_keys(metadata);result=raw.astype(np.float64).copy();evidence=[None]*len(raw)
    for lead in np.unique(leads):
        for phase in np.unique(phases[leads==lead]):
            ix=np.flatnonzero((leads==lead)&(phases==phase));key,bank,pooled=select_bank(settings,str(lead),str(phase))
            for name,lo,median,hi in [('quality',1,2,3),('usable',5,6,7)]:
                lower=np.maximum(0,result[ix,lo]+bank['offsets'][name][0])
                upper=np.maximum(0,result[ix,hi]+bank['offsets'][name][1])
                if name=='usable':lower=np.minimum(lower,1.);upper=np.minimum(upper,1.)
                result[ix,lo]=np.minimum(lower,result[ix,median]);result[ix,hi]=np.maximum(upper,result[ix,median])
            probability=np.clip(raw[ix,8],1e-7,1-1e-7)
            result[ix,8]=expit(np.log(probability/(1-probability))/bank['blocked_temperature'])
            result[ix,9:12]=softmax(np.log(np.clip(raw[ix,9:12],1e-7,1))/bank['band_temperature'],axis=1)
            if return_evidence:
                value=dict(group=key,pooled=pooled,**bank['evidence'],
                           probability_fit_supported=bank['probability_fit_supported'],
                           probability_parent_used=bank['probability_parent_used'])
                for j in ix:evidence[int(j)]=value
    return (result.astype(np.float32),evidence) if return_evidence else result.astype(np.float32)

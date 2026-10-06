"""Shared public-only feature builder for training and inference.

Accepts published announcements, estimated baseline state and completed feedback;
never reads simulator weather/event truth or an actual observation score target.
"""
from __future__ import annotations
import math
import numpy as np
import torch
from .features import PublicTimeline, candidate_features, utc
from .information_model import numeric, calibration_apply
from .adaptation_feedback import history_array, state_array, STATE_NAMES
from .adaptation_rollout import baseline_module
QUALITY_INDEX = 242 + 29 + 12

def sky_from_state(state,scoring):
    module=baseline_module();from core.conditions import Sky,Terrain
    sky=Sky(scoring);sky.W=state['weather'];sky.rho=state['throughput'];sky.e=state['efficiency']
    sky.dir_now=[tuple(r) for r in state.get('directional_notices',[])]
    for p in state.get('terrain',[]):
        t=Terrain(p['centre']);t.sure=p['sure'];t.top=p['top'];t.floor=p['floor'];t.box=p['box'];sky.terrain.append(t)
    return sky


def held_prediction(state,ra,dec,start,end,site,score):
    features,eligible=candidate_features(ra,dec,start,end,site,score)
    geometric=features[:,-1]*score['q0']
    sky=sky_from_state(state,score)
    middle=start+(end-start)/2
    from .features import altaz
    altitude,azimuth=altaz(ra,dec,middle,site)
    direction=np.array([sky.dir_factor(float(a),float(z)) for a,z in zip(altitude,azimuth)])
    closure=state['suspected_closure']
    u=np.zeros(len(ra)) if closure else direction
    q=state['weather']*geometric*direction*(not closure)
    band_level=state['weather']*geometric*(not closure)
    x=np.log(np.maximum(band_level,1e-12))
    sigmoid=lambda z:1/(1+np.exp(-np.clip(z,-30,30)))
    pd=sigmoid((x-math.log(score['program']['bands']['DARK']))/.07)
    pb=sigmoid((x-math.log(score['program']['bands']['BRIGHT']))/.07)
    return np.column_stack([q,q,q,q,u,u,u,u,1-u,pd,pb-pd,1-pb]).astype(np.float32)


def public_features(now,start,end,requested,coordinates,initialize,timeline,state,history,foundation):
    """No weather/event truth arguments: shared train/runtime public feature path."""
    site=initialize['site'];score=initialize['scoring']
    night=next(n for n in initialize['survey']['nights'] if utc(n['observing_start_utc'])<=start<utc(n['observing_end_utc']))
    context,ann_history=timeline.snapshot(now,start,requested,(end-start).total_seconds(),night['night_date'],
        night['observing_end_utc'],site,score)
    candidate,eligible=candidate_features(coordinates[:,0],coordinates[:,1],start,end,site,score)
    data=dict(context=context[None],history=ann_history[None],candidate=candidate[None])
    normalized=foundation.normalizer.apply(data)
    with torch.inference_mode():
        out=foundation.model(*[torch.from_numpy(normalized[k]) for k in ('context','history','candidate')])
        base=calibration_apply(numeric(out,foundation.metadata['quality_scale']),foundation.metadata['calibration'])[0]
    hold=held_prediction(state,coordinates[:,0],coordinates[:,1],start,end,site,score)
    states=np.broadcast_to(state_array(state,now,len(history)),(len(coordinates),len(STATE_NAMES)))
    x=np.concatenate([np.broadcast_to(context,(len(coordinates),len(context))),candidate,base,hold,states],axis=1).astype(np.float32)
    h=history_array(history,now)
    return x,h,base,hold,eligible



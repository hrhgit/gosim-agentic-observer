from __future__ import annotations
import copy, math
import numpy as np
from ..features import encode as original
VERSION='public-staged-features-v2'
KINDS=('region','duration','program','fiber')

def encode(frame):
    view={**frame,"candidates":[{**cand,"action":{**cand["action"],"program":cand["action"].get("program","WAIT")}} for cand in frame["candidates"]]}
    x,c=original(view)
    kind=frame.get('kind','region')
    x=np.concatenate([x,np.array([kind==k for k in KINDS],np.float32)])
    extra=[]
    for cand in frame['candidates']:
        a,f=cand['action'],cand['features'];t=f.get('fiber_target') or {};req=t.get('request')
        extra.append([a['action']=='wait',t.get('required',False),t.get('science_weight',0)/1000,
                      t.get('best',0)/1000,t.get('factor_estimate',0),t.get('flux',0),float(req is not None),
                      req[0]/1000 if req else 0,float(t=={})])
    return x.astype(np.float32),np.concatenate([c,np.array(extra,np.float32)],axis=1)

class Normalizer:
    def __init__(self,state):self.state=state
    @classmethod
    def fit(cls,trajectories):
        totals={}
        for trajectory in trajectories:
            for row in trajectory.rows():
                for name,data in [('global',row['x'][None,:]),('candidate',row['c'])]:
                    n,total,square=totals.get(name,(0,np.zeros(data.shape[1]),np.zeros(data.shape[1])))
                    totals[name]=(n+len(data),total+data.sum(0,dtype=np.float64),square+np.square(data.astype(np.float64)).sum(0))
        state={'version':VERSION}
        for name,(n,total,square) in totals.items():
            mean=total/n;std=np.sqrt(np.maximum(0,square/n-mean**2));state[name]={'mean':mean.tolist(),'std':np.maximum(.01,std).tolist()}
        return cls(state)
    def apply_arrays(self,x,c):
        if self.state['version']!=VERSION:raise ValueError('Feature version mismatch')
        return tuple(((a-np.array(self.state[k]['mean'],np.float32))/np.array(self.state[k]['std'],np.float32)).astype(np.float32)
                     for a,k in [(x,'global'),(c,'candidate')])
    def apply(self,frame):return self.apply_arrays(*encode(frame))

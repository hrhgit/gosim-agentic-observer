"""One small head over a frozen GRU: atoms at U=0/1 plus a beta interior.

The three masses are objective outcomes, never candidate exclusion decisions.
Severe obstruction and U quantiles are computed from the same distribution.
"""
from __future__ import annotations
import json
from pathlib import Path
import time
import numpy as np
from scipy.special import betainc,betaincinv,softmax
from scipy.optimize import minimize
import torch
from torch import nn
from torch.nn import functional as F
from .adaptation_model import CorrectionNet,Normalizer
from .adaptation_scaling import DEFAULT as SCALING
from .conditional_calibration import weights
from .information_model import measure
from .data import save_json


def public_extra(metadata):
    leads=('15m','30m','60m','next_night')
    return np.array([[*(float(m['horizon']==h) for h in leads),m['fresh_history_count']/8,
                      m['all_history_count']/8,float(m['baseline_measurement_available'])] for m in metadata],np.float32)


@torch.inference_mode()
def features(data,raw,model=None,normalizer=None):
    if model is None:
        normalizer=Normalizer.load(SCALING/'models/gru64/normalizer.npz')
        model=CorrectionNet(scale=float(normalizer.values['quality_scale']))
        model.load_state_dict(torch.load(SCALING/'models/gru64/gru.pt',weights_only=True,map_location='cpu'))
    model.eval();n=normalizer.apply(data);encoded=[]
    for start in range(0,len(raw),1024):
        x=torch.from_numpy(n['x'][start:start+1024]);h=torch.from_numpy(n['history'][start:start+1024])
        sequence=model.encoder(h)[0][:,-1];encoded.append(model.shared(torch.cat([x,sequence],dim=-1)).numpy())
    return np.concatenate([np.concatenate(encoded),data['hold'],raw,public_extra(data['meta'])],axis=1).astype(np.float32)


class EndpointHead(nn.Module):
    def __init__(self,dimension):
        super().__init__();self.net=nn.Sequential(nn.Linear(dimension,16),nn.ReLU(),nn.Linear(16,5))

    def forward(self,x):
        v=self.net(x)
        shapes=.2+19.8*torch.sigmoid(v[:,3:])
        return v[:,:3],shapes


def target_class(y):return np.where(y==0,0,np.where(y==1,2,1)).astype(np.int64)


def distribution_numeric(logits,shapes):
    return np.concatenate([softmax(logits,axis=1),shapes],axis=1).astype(np.float32)


def infer(head,x):
    head.eval();result=[]
    with torch.inference_mode():
        for start in range(0,len(x),1024):
            logits,shapes=head(torch.from_numpy(x[start:start+1024]))
            result.append(distribution_numeric(logits.numpy(),shapes.numpy()))
    return np.concatenate(result)


def numerical(distribution,raw):
    p=raw.astype(np.float64).copy();masses=distribution[:,:3].astype(float);a,b=distribution[:,3:5].T.astype(float)
    p[:,4]=masses[:,2]+masses[:,1]*a/(a+b)
    for column,level in ((5,.05),(6,.5),(7,.95)):
        inner=np.clip((level-masses[:,0])/np.maximum(masses[:,1],1e-12),0,1)
        value=betaincinv(a,b,inner)
        p[:,column]=np.where(level<=masses[:,0],0,np.where(level>=1-masses[:,2],1,value))
    p[:,8]=masses[:,0]+masses[:,1]*betainc(a,b,.5)
    return p.astype(np.float32)


def mass_metrics(y,distribution):
    cls=target_class(y[:,1]);onehot=np.eye(3)[cls];m=distribution[:,:3]
    from .metrics import probability_error
    return dict(endpoint_brier=float(((m-onehot)**2).sum(1).mean()),
        endpoint_log_loss=float(-np.log(np.maximum(m[np.arange(len(cls)),cls],1e-7)).mean()),
        endpoint_ece={n:probability_error(m[:,i],onehot[:,i]) for i,n in enumerate(('zero','interior','one'))},
        true_counts=np.bincount(cls,minlength=3).tolist(),mean_predicted_mass=m.mean(0).tolist())


def train(directory,train,raw,valid,vraw):
    directory.mkdir(parents=True,exist_ok=True)
    if (directory/'complete.json').exists():return
    began=time.perf_counter();torch.manual_seed(101);np.random.seed(101)
    tx=features(train,raw);vx=features(valid,vraw)
    mean=tx.mean(0);std=np.maximum(tx.std(0),.05);tx=(tx-mean)/std;vx=(vx-mean)/std
    np.savez(directory/'normalizer.npz',mean=mean,std=std)
    head=EndpointHead(tx.shape[1]);optimizer=torch.optim.AdamW(head.parameters(),lr=.002,weight_decay=.0001)
    x=torch.from_numpy(tx);y=torch.from_numpy(train['y'][:,1]);cls=torch.from_numpy(target_class(train['y'][:,1]))
    w=torch.from_numpy((weights(train)*len(y)).astype(np.float32));history=[];best=float('inf')
    # No seed ensemble or learning-rate search. Only this tiny head is optimized.
    for epoch in range(12):
        head.train();order=torch.randperm(len(y));losses=[]
        for start in range(0,len(y),512):
            ix=order[start:start+512];logits,shapes=head(x[ix]);a,b=shapes.T;u=y[ix].clamp(1e-4,1-1e-4)
            beta_nll=torch.lgamma(a)+torch.lgamma(b)-torch.lgamma(a+b)-(a-1)*torch.log(u)-(b-1)*torch.log1p(-u)
            loss=((F.cross_entropy(logits,cls[ix],reduction='none')+beta_nll*(cls[ix]==1))*w[ix]).mean()
            optimizer.zero_grad();loss.backward();nn.utils.clip_grad_norm_(head.parameters(),1.);optimizer.step();losses.append(float(loss.detach()))
        dist=infer(head,vx);p=numerical(dist,vraw);metrics=measure(valid['y'],p);mass=mass_metrics(valid['y'],dist)
        score=metrics['usable_pinball']+metrics['blocked_brier']+mass['endpoint_brier']
        if score<best:best=score;torch.save(head.state_dict(),directory/'head.pt')
        history.append(dict(epoch=epoch+1,loss=float(np.mean(losses)),selection_score=score,metrics=metrics,mass=mass))
        print('endpoint epoch',epoch+1,'score',round(score,4),flush=True)
    save_json(directory/'history.json',history)
    save_json(directory/'complete.json',dict(training_seconds=time.perf_counter()-began,rows=len(y),
        scenes=len(np.unique(train['scene'])),parameters=sum(p.numel() for p in head.parameters()),epochs=12,
        initializations=1,frozen_trunk=True,online_updates=False,selected_epoch=min(history,key=lambda v:v['selection_score'])['epoch']))


def predict(directory,data,raw,model=None,normalizer=None):
    n=np.load(directory/'normalizer.npz');x=(features(data,raw,model,normalizer)-n['mean'])/n['std']
    head=EndpointHead(x.shape[1]);head.load_state_dict(torch.load(directory/'head.pt',weights_only=True,map_location='cpu'))
    return infer(head,x.astype(np.float32))


def fit_mass(data,distribution):
    cls=target_class(data['y'][:,1]);w=weights(data);logits=np.log(np.clip(distribution[:,:3],1e-7,1))
    def obj(v):
        bias=np.r_[v[1:3],-v[1]-v[2]];p=softmax(logits/np.exp(v[0])+bias,axis=1)
        return float(-np.sum(w*np.log(np.maximum(p[np.arange(len(cls)),cls],1e-12)))+.01*np.sum(v*v))
    solution=minimize(obj,np.zeros(3),method='L-BFGS-B',bounds=[(-4,4),(-5,5),(-5,5)])
    if not solution.success:raise RuntimeError(solution.message)
    v=solution.x
    return dict(temperature=float(np.exp(v[0])),bias=[float(v[1]),float(v[2]),float(-v[1]-v[2])],
        scenes=len(np.unique(data['scene'])),class_scenes=[len(np.unique(data['scene'][cls==i])) for i in range(3)],
        fit='global scene-weighted temperature and centered biases; penalty .01',beta_shape_recalibrated=False)


def apply_mass(distribution,settings):
    d=distribution.copy();d[:,:3]=softmax(np.log(np.clip(d[:,:3],1e-7,1))/settings['temperature']+np.array(settings['bias']),axis=1)
    return d

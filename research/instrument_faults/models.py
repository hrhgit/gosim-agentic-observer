"""Scene-weighted classifier comparison and held-out sigmoid calibration."""
from __future__ import annotations

import itertools
import hashlib
import json
import time
from pathlib import Path

import joblib
import numpy as np
import torch
from torch import nn
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score
from threadpoolctl import threadpool_limits

from research.observing_conditions.data import save_json
from .core import FEATURE_NAMES, FEATURE_VERSION
from .simulation import feature_sources


def dataset_identity(output):
    output=Path(output);digest=hashlib.sha256((output/'manifest.json').read_bytes())
    for entry in json.loads((output/'manifest.json').read_text())['entries']:
        if entry['split'] in ('train','validation','calibration'):
            digest.update((output/'scenarios'/entry['id']/'features_complete.json').read_bytes())
    return digest.hexdigest()


def load_data(output,split):
    output=Path(output);manifest=json.loads((output/'manifest.json').read_text())
    xs=[];ys=[];scenes=[];cards=[];weather_quality=[];fault_multiplier=[];quake=[];groups=[]
    for entry in manifest['entries']:
        if entry['split']!=split:
            continue
        marker=json.loads((output/'scenarios'/entry['id']/'features_complete.json').read_text())
        if marker['feature_names']!=FEATURE_NAMES or marker['weather_version']!=json.loads((output/'weather_dependency.json').read_text())['identity']:
            raise ValueError('training feature/weather contract mismatch')
        if marker['feature_sources']!=feature_sources() or marker['features_sha256']!=hashlib.sha256((output/'scenarios'/entry['id']/'features.npz').read_bytes()).hexdigest():
            raise ValueError('feature code or data changed')
        with np.load(output/'scenarios'/entry['id']/'features.npz') as a:
            xs.append(a['x']);ys.append(a['y'])
            scenes.extend([entry['id']]*len(a['y']));cards.extend([entry['card']]*len(a['y']))
            groups.extend([entry.get('group','main')]*len(a['y']))
        for track in (0,1):
            rows=json.loads((output/'scenarios'/entry['id']/f'feedback-labels-{track}.json').read_text())
            weather_quality.extend([r['true_environment_quality'] if r['true_environment_quality'] is not None else np.nan for r in rows])
            fault_multiplier.extend([r['true_fault_multiplier'] if r['true_fault_multiplier'] is not None else np.nan for r in rows])
            quake.extend([r['quake'] for r in rows])
    if not xs:
        raise ValueError(f'no completed {split} data')
    scenes=np.array(scenes)
    unique,counts=np.unique(scenes,return_counts=True)
    mapping=dict(zip(unique,1/counts))
    weights=np.array([mapping[s] for s in scenes],np.float64)
    weights/=weights.mean()
    return dict(x=np.concatenate(xs),y=np.concatenate(ys),scene=scenes,card=np.array(cards),weight=weights,
                weather_quality=np.asarray(weather_quality),fault_multiplier=np.asarray(fault_multiplier),quake=np.asarray(quake),group=np.array(groups))


class Preprocessor:
    def fit(self,x,weight):
        self.median=np.array([np.median(c[np.isfinite(c)]) if np.isfinite(c).any() else 0 for c in x.T],np.float32)
        a=np.where(np.isfinite(x),x,self.median)
        self.mean=np.average(a,axis=0,weights=weight).astype(np.float32)
        self.std=np.sqrt(np.average((a-self.mean)**2,axis=0,weights=weight)).astype(np.float32)
        # A constant training column uses scale 1, like StandardScaler. Dividing
        # a future evidence age by epsilon would create a huge unseen input.
        self.std=np.where(self.std<1e-5,1,self.std)
        return self

    def apply(self,x):
        missing=~np.isfinite(x)
        a=(np.where(missing,self.median,x)-self.mean)/self.std
        return np.column_stack([a,missing]).astype(np.float32)


class FaultNet(nn.Module):
    def __init__(self,dimensions):
        super().__init__()
        self.layers=nn.Sequential(nn.Linear(dimensions,64),nn.ReLU(),nn.Dropout(.1),
                                  nn.Linear(64,32),nn.ReLU(),nn.Dropout(.1),nn.Linear(32,1))
    def forward(self,x):
        return self.layers(x).squeeze(-1)


class Classifier:
    def __init__(self,kind,base,preprocessor=None,calibrator=None,features_mask=None):
        self.kind,self.base,self.preprocessor,self.calibrator=kind,base,preprocessor,calibrator
        self.features_mask=features_mask

    def raw(self,x):
        if self.features_mask is not None:
            x=x.copy();x[:,self.features_mask]=np.nan
        if self.preprocessor is not None:
            x=self.preprocessor.apply(x)
        if self.kind=='neural':
            result=[];self.base.cpu().eval()
            with torch.inference_mode():
                for first in range(0,len(x),4096):
                    result.append(torch.sigmoid(self.base(torch.from_numpy(x[first:first+4096]))).numpy())
            return np.concatenate(result)
        return self.base.predict_proba(x)[:,1]

    def predict_proba(self,x):
        p=self.raw(x)
        if self.calibrator is not None:
            p=self.calibrator.predict_proba(logit(p)[:,None])[:,1]
        return p


def logit(p):
    p=np.clip(p,1e-7,1-1e-7)
    return np.log(p/(1-p))


def loss(y,p,weight):
    p=np.clip(p,1e-7,1-1e-7)
    return float(np.average(-y*np.log(p)-(1-y)*np.log1p(-p),weights=weight))


def metrics(data,p):
    y,w=data['y'],data['weight']
    ece=0.0;groups=[]
    for i in range(10):
        mask=(p>=i/10)&((p<(i+1)/10) if i<9 else (p<=1))
        if mask.any():
            forecast=float(p[mask].mean());actual=float(y[mask].mean())
            ece+=mask.mean()*abs(forecast-actual)
            groups.append(dict(bin=i,count=int(mask.sum()),predicted=forecast,actual=actual))
    return dict(log_loss=loss(y,p,w),brier=float(np.average((p-y)**2,weights=w)),ece=float(ece),
                positive_fraction=float(np.average(y,weights=w)),rows=len(y),scenes=len(np.unique(data['scene'])),
                average_precision=float(average_precision_score(y,p,sample_weight=w)) if len(np.unique(y))==2 else None,
                roc_auc=float(roc_auc_score(y,p,sample_weight=w)) if len(np.unique(y))==2 else None,calibration_bins=groups)


def save_classifier(path,classifier,metadata):
    path=Path(path);path.mkdir(parents=True,exist_ok=True)
    if classifier.kind=='neural':
        torch.save(classifier.base.state_dict(),path/'weights.pt')
        joblib.dump(dict(preprocessor=classifier.preprocessor,calibrator=classifier.calibrator,features_mask=classifier.features_mask),path/'components.joblib')
    else:
        joblib.dump(classifier,path/'classifier.joblib')
    save_json(path/'metadata.json',metadata|dict(kind=classifier.kind,feature_version=FEATURE_VERSION,feature_names=FEATURE_NAMES))


def load_classifier(path):
    path=Path(path);metadata=json.loads((path/'metadata.json').read_text())
    if metadata['feature_version']!=FEATURE_VERSION or metadata['feature_names']!=FEATURE_NAMES:
        raise ValueError('fault feature contract mismatch')
    if metadata['kind']=='neural':
        components=joblib.load(path/'components.joblib')
        model=FaultNet(2*len(FEATURE_NAMES))
        model.load_state_dict(torch.load(path/'weights.pt',map_location='cpu',weights_only=True))
        return Classifier('neural',model,**components),metadata
    return joblib.load(path/'classifier.joblib'),metadata


def neural_fit(train,validation,preprocessor,lr,seed,directory):
    if not torch.backends.mps.is_available():
        raise RuntimeError('planned MPS training is unavailable')
    torch.manual_seed(seed);rng=np.random.default_rng(seed)
    device='mps';model=FaultNet(2*len(FEATURE_NAMES)).to(device)
    optimizer=torch.optim.AdamW(model.parameters(),lr=lr,weight_decay=1e-4)
    x=preprocessor.apply(train['x']);vx=preprocessor.apply(validation['x'])
    best=float('inf');best_state=None;best_epoch=None;history=[]
    for epoch in range(40):
        model.train()
        order=rng.permutation(len(x))
        for first in range(0,len(order),1024):
            idx=order[first:first+1024]
            bx=torch.as_tensor(x[idx],device=device);by=torch.as_tensor(train['y'][idx].astype(np.float32),device=device)
            bw=torch.as_tensor(train['weight'][idx].astype(np.float32),device=device)
            optimizer.zero_grad(set_to_none=True)
            value=(nn.functional.binary_cross_entropy_with_logits(model(bx),by,reduction='none')*bw).mean()
            value.backward();optimizer.step()
        model.eval();p=[]
        with torch.inference_mode():
            for first in range(0,len(vx),4096):
                p.append(torch.sigmoid(model(torch.as_tensor(vx[first:first+4096],device=device))).cpu().numpy())
        score=loss(validation['y'],np.concatenate(p),validation['weight'])
        history.append(dict(epoch=epoch+1,validation_log_loss=score))
        if score<best:
            best=score;best_epoch=epoch+1;best_state={k:v.detach().cpu().clone() for k,v in model.state_dict().items()}
        print(f'neural lr={lr} seed={seed} epoch={epoch+1}/40 validation={score:.6f}',flush=True)
        save_json(directory/'history.json',history)
    model.cpu().load_state_dict(best_state)
    return Classifier('neural',model,preprocessor),dict(lr=lr,seed=seed,best_epoch=best_epoch)


def train_all(output):
    output=Path(output)
    dependency=json.loads((output/'weather_dependency.json').read_text())
    identity=dataset_identity(output)
    train=load_data(output,'train');validation=load_data(output,'validation');calibration=load_data(output,'calibration')
    preprocessing=Preprocessor().fit(train['x'],train['weight'])
    grids={
        'logistic':[dict(C=c) for c in (.1,1,10)],
        'boosting':[dict(learning_rate=lr,max_leaf_nodes=leaves,l2_regularization=l2) for lr,leaves,l2 in itertools.product((.05,.1),(15,31),(1,10))],
        'forest':[dict(max_depth=depth,min_samples_leaf=leaf) for depth,leaf in itertools.product((12,20),(20,100))],
        'neural':[dict(lr=lr,seed=404) for lr in (.0003,.001)]}
    results={}
    for kind,grid in grids.items():
        candidates=[]
        for index,parameters in enumerate(grid):
            directory=output/'models'/f'{kind}-candidate-{index}'
            meta=directory/'metadata.json'
            if meta.exists():
                classifier,metadata=load_classifier(directory)
                if metadata.get('dataset_identity')!=identity:
                    raise ValueError('resumed training data changed')
            else:
                directory.mkdir(parents=True,exist_ok=True)
                begin=time.perf_counter()
                with threadpool_limits(limits=2):
                    if kind=='logistic':
                        base=LogisticRegression(max_iter=2000,**parameters).fit(preprocessing.apply(train['x']),train['y'],sample_weight=train['weight'])
                        classifier=Classifier(kind,base,preprocessing)
                    elif kind=='boosting':
                        base=HistGradientBoostingClassifier(max_iter=300,min_samples_leaf=50,early_stopping=False,random_state=404,**parameters).fit(train['x'],train['y'],sample_weight=train['weight'])
                        classifier=Classifier(kind,base)
                    elif kind=='forest':
                        base=RandomForestClassifier(n_estimators=300,max_features='sqrt',n_jobs=2,random_state=404,**parameters).fit(preprocessing.apply(train['x']),train['y'],sample_weight=train['weight'])
                        classifier=Classifier(kind,base,preprocessing)
                    else:
                        classifier,parameters=neural_fit(train,validation,preprocessing,**parameters,directory=directory)
                metadata=dict(parameters=parameters,training_seconds=time.perf_counter()-begin,weather_version=dependency['identity'],dataset_identity=identity,feature_sources=feature_sources())
                with threadpool_limits(limits=1):
                    metadata['validation']=metrics(validation,classifier.raw(validation['x']))
                save_classifier(directory,classifier,metadata)
            if metadata['weather_version']!=dependency['identity']:
                raise ValueError('resumed candidate weather mismatch')
            candidates.append((metadata['validation']['log_loss'],directory,metadata))
            print(f'trained {kind} {index+1}/{len(grid)} validation={candidates[-1][0]:.6f}',flush=True)
        _,chosen,metadata=min(candidates,key=lambda c:c[0])
        # Independently repeat stochastic finalists; validation alone picks a single member.
        finalists=[(chosen,metadata)]
        if kind in ('forest','neural'):
            parameters=metadata['parameters']
            for seed in (101,202):
                directory=output/'models'/f'{kind}-repeat-{seed}'
                if (directory/'metadata.json').exists():
                    _,repeat_meta=load_classifier(directory)
                    if repeat_meta.get('dataset_identity')!=identity:
                        raise ValueError('resumed finalist data changed')
                else:
                    directory.mkdir(parents=True,exist_ok=True);begin=time.perf_counter()
                    if kind=='forest':
                        with threadpool_limits(limits=2):
                            base=RandomForestClassifier(n_estimators=300,max_features='sqrt',n_jobs=2,random_state=seed,**parameters).fit(preprocessing.apply(train['x']),train['y'],sample_weight=train['weight'])
                        classifier=Classifier(kind,base,preprocessing);repeat_parameters=parameters|dict(seed=seed)
                    else:
                        classifier,repeat_parameters=neural_fit(train,validation,preprocessing,parameters['lr'],seed,directory)
                    with threadpool_limits(limits=1):
                        measured=metrics(validation,classifier.raw(validation['x']))
                    repeat_meta=dict(parameters=repeat_parameters,validation=measured,training_seconds=time.perf_counter()-begin,weather_version=dependency['identity'],dataset_identity=identity,feature_sources=feature_sources())
                    save_classifier(directory,classifier,repeat_meta)
                finalists.append((directory,repeat_meta))
        chosen,metadata=min(finalists,key=lambda c:c[1]['validation']['log_loss'])
        classifier,_=load_classifier(chosen)
        with threadpool_limits(limits=1):
            cp=classifier.raw(calibration['x'])
            classifier.calibrator=LogisticRegression(C=1e6,max_iter=2000).fit(logit(cp)[:,None],calibration['y'],sample_weight=calibration['weight'])
            validation_metrics=metrics(validation,classifier.predict_proba(validation['x']))
        metadata=metadata|dict(validation_calibrated=validation_metrics,finalists=[str(p) for p,_ in finalists],calibration='held-out sigmoid')
        save_classifier(output/'models'/kind,classifier,metadata)
        results[kind]=metadata
    save_json(output/'model_comparison.json',results)
    return results

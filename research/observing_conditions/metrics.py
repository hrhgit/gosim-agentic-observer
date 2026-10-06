"""Numeric evaluation only; no language-model grader."""
from __future__ import annotations
import numpy as np
from scipy.optimize import minimize_scalar
from scipy.special import expit, softmax


def probability_error(p,y):
    result=0.0
    for index in range(10):
        low,high=index/10,(index+1)/10
        mask=(p>=low)&((p<high) if index<9 else (p<=1))
        if mask.any():
            result+=mask.mean()*abs(float(p[mask].mean()-y[mask].mean()))
    return float(result)


def reliability_bins(p,y):
    result=[]
    for index in range(10):
        low=index/10;high=(index+1)/10
        mask=(p>=low)&((p<high) if index<9 else (p<=high))
        result.append(dict(lower=low,upper=high,count=int(mask.sum()),
            predicted_probability=float(p[mask].mean()) if mask.any() else None,
            actual_frequency=float(y[mask].mean()) if mask.any() else None))
    return result


def metrics(y,p):
    flat=y.reshape(-1,4); pred=p.reshape(-1,9)
    q=flat[:,0]; error=np.abs(pred[:,0]-q)
    bands=flat[:,3].astype(int); chosen=pred[:,6:9].argmax(axis=1)
    onehot=np.eye(3)[bands]
    f1=[]
    for index in range(3):
        tp=((chosen==index)&(bands==index)).sum()
        fp=((chosen==index)&(bands!=index)).sum(); fn=((chosen!=index)&(bands==index)).sum()
        f1.append(float(2*tp/max(2*tp+fp+fn,1)))
    lower,upper=pred[:,1],pred[:,3]
    interval=upper-lower+20*np.maximum(lower-q,0)+20*np.maximum(q-upper,0)
    delta=q[:,None]-pred[:,1:4]; levels=np.array([0.05,0.5,0.95])
    true_q=y[...,0]; group_best=true_q.max(axis=1)
    selected=true_q[np.arange(len(y)),p[...,0].argmax(axis=1)]
    return dict(samples=len(flat),quality_mae=float(error.mean()),quality_wape=float(error.sum()/max(q.sum(),1e-12)),
        usable_mae=float(np.abs(pred[:,4]-flat[:,1]).mean()),blocked_brier=float(np.mean((pred[:,5]-flat[:,2])**2)),
        band_brier=float(np.mean(np.sum((pred[:,6:9]-onehot)**2,axis=1))),band_macro_f1=float(np.mean(f1)),
        band_f1=f1,blocked_ece=probability_error(pred[:,5],flat[:,2]),
        band_ece=float(max(probability_error(pred[:,6+i],onehot[:,i]) for i in range(3))),
        interval_coverage=float(np.mean((q>=lower)&(q<=upper))),interval_width=float(np.mean(upper-lower)),
        interval_score=float(interval.mean()),pinball=float(np.maximum(levels*delta,(levels-1)*delta).mean()),
        ranking_loss=float(np.sum(group_best-selected)/max(np.sum(group_best),1e-12)),
        blocked_prevalence=float(flat[:,2].mean()),band_counts=np.bincount(bands,minlength=3).tolist())


def validation_score(m,scale):
    return m['quality_mae']/scale+m['usable_mae']+m['blocked_brier']+m['band_brier']/3+m['pinball']/scale


def calibrate_fit(y,p):
    flat=y.reshape(-1,4); pred=p.reshape(-1,9)
    binary_logits=np.log(np.clip(pred[:,5],1e-7,1-1e-7)/(1-np.clip(pred[:,5],1e-7,1-1e-7)))
    band_logits=np.log(np.clip(pred[:,6:9],1e-7,1))
    def binary_loss(log_temperature):
        logits=binary_logits/np.exp(log_temperature)
        return float(np.mean(np.logaddexp(0,logits)-flat[:,2]*logits))
    def band_loss(log_temperature):
        probabilities=softmax(band_logits/np.exp(log_temperature),axis=1)
        return float(-np.log(np.maximum(probabilities[np.arange(len(flat)),flat[:,3].astype(int)],1e-12)).mean())
    binary=minimize_scalar(binary_loss,bounds=(-4,4),method='bounded')
    band=minimize_scalar(band_loss,bounds=(-4,4),method='bounded')
    residual=np.maximum(pred[:,1]-flat[:,0],flat[:,0]-pred[:,3])
    correction=max(0.0,float(np.quantile(residual,0.9,method='higher')))
    return dict(blocked_temperature=float(np.exp(binary.x)),band_temperature=float(np.exp(band.x)),
                interval_correction=correction,coverage_target=0.9,
                limitation='Empirical scene-held-out calibration; no independent-row or distribution-shift guarantee.')


def calibrate_apply(p,settings):
    result=p.astype(np.float64).copy()
    probability=np.clip(result[...,5],1e-7,1-1e-7)
    result[...,5]=expit(np.log(probability/(1-probability))/settings['blocked_temperature'])
    result[...,6:9]=softmax(np.log(np.clip(result[...,6:9],1e-7,1))/settings['band_temperature'],axis=-1)
    result[...,1]=np.maximum(0,result[...,1]-settings['interval_correction'])
    result[...,3]+=settings['interval_correction']
    return result.astype(np.float32)


def acceptance(m,baseline,latency):
    return {'quality_relative':m['quality_mae']<=0.85*baseline['quality_mae'],
            'quality_absolute':m['quality_wape']<=0.20,'usable':m['usable_mae']<=0.10,
            'blocked_probability':m['blocked_brier']<=0.90*baseline['blocked_brier'],
            'bands':m['band_macro_f1']>=0.80,'probability_reliability':max(m['blocked_ece'],m['band_ece'])<=0.05,
            'interval_coverage':0.85<=m['interval_coverage']<=0.95,'interval_score':m['interval_score']<baseline['interval_score'],
            'ranking':m['ranking_loss']<=0.90*baseline['ranking_loss'],'latency':latency['p95_ms']<=100}


def scene_bootstrap(y,p,baseline,scenes,repetitions=2000):
    """Bootstrap independent complete scenes, preserving correlated groups/candidates."""
    unique=np.unique(scenes)
    if len(unique)<2:
        raise ValueError('scene bootstrap needs at least two scenes')
    def sufficient(predictions):
        rows=[]
        for scene in unique:
            mask=scenes==scene;truth=y[mask];prediction=predictions[mask]
            flat=truth.reshape(-1,4);pred=prediction.reshape(-1,9);q=flat[:,0]
            chosen=pred[:,6:9].argmax(axis=1);bands=flat[:,3].astype(int)
            confusion=np.bincount(3*bands+chosen,minlength=9)
            probability_residuals=[]
            for probability,actual in [(pred[:,5],flat[:,2])]+[(pred[:,6+i],bands==i) for i in range(3)]:
                for index in range(10):
                    low,high=index/10,(index+1)/10
                    selected=(probability>=low)&((probability<high) if index<9 else (probability<=1))
                    probability_residuals.append(np.sum(probability[selected]-actual[selected],dtype=np.float64))
            low,high=pred[:,1],pred[:,3]
            interval=high-low+20*np.maximum(low-q,0)+20*np.maximum(q-high,0)
            group_best=truth[...,0].max(axis=1)
            selected=truth[...,0][np.arange(len(truth)),prediction[...,0].argmax(axis=1)]
            onehot=np.eye(3)[bands]
            values=[len(flat),q.sum(dtype=np.float64),np.abs(pred[:,0]-q).sum(dtype=np.float64),
                np.abs(pred[:,4]-flat[:,1]).sum(dtype=np.float64),
                np.square(pred[:,5]-flat[:,2]).sum(dtype=np.float64),np.square(pred[:,6:9]-onehot).sum(),
                ((q>=low)&(q<=high)).sum(),(high-low).sum(dtype=np.float64),interval.sum(dtype=np.float64),
                (group_best-selected).sum(dtype=np.float64),group_best.sum(dtype=np.float64)]
            rows.append(np.r_[values,confusion,probability_residuals])
        return np.array(rows)
    def evaluate(totals):
        n=totals[:,0];q=totals[:,1];confusion=totals[:,11:20].reshape(-1,3,3)
        tp=np.diagonal(confusion,axis1=1,axis2=2)
        denominator=confusion.sum(axis=1)+confusion.sum(axis=2)
        f1=2*tp/np.maximum(denominator,1)
        residuals=totals[:,20:].reshape(-1,4,10)
        ece=np.abs(residuals).sum(axis=-1)/n[:,None]
        return dict(quality_mae=totals[:,2]/n,quality_wape=totals[:,2]/np.maximum(q,1e-12),
            usable_mae=totals[:,3]/n,blocked_brier=totals[:,4]/n,band_brier=totals[:,5]/n,
            interval_coverage=totals[:,6]/n,interval_width=totals[:,7]/n,interval_score=totals[:,8]/n,
            ranking_loss=totals[:,9]/np.maximum(totals[:,10],1e-12),band_macro_f1=f1.mean(axis=1),
            blocked_ece=ece[:,0],band_ece=ece[:,1:].max(axis=1))
    rng=np.random.default_rng(8841)
    indices=rng.integers(0,len(unique),size=(repetitions,len(unique)))
    weights=np.zeros((repetitions,len(unique)))
    np.add.at(weights,(np.repeat(np.arange(repetitions),len(unique)),indices.ravel()),1)
    selected_stats=sufficient(p);reference_stats=sufficient(baseline)
    selected=evaluate(weights@selected_stats);reference=evaluate(weights@reference_stats)
    point_selected=evaluate(selected_stats.sum(axis=0,keepdims=True))
    point_reference=evaluate(reference_stats.sum(axis=0,keepdims=True))
    return {key:{'metric_difference':float(point_selected[key][0]-point_reference[key][0]),
        'ci95':np.quantile(selected[key]-reference[key],[0.025,0.975]).tolist(),
        'scene_count':len(unique),'repetitions':repetitions,
        'aggregation':'pooled metric recomputed after sampling complete scenes'} for key in selected}


class StatisticalBaseline:
    def __init__(self,constant=False):
        self.constant=constant

    def keys(self,data):
        x=data['candidate']; ctx=data['context']
        if self.constant:
            return np.zeros(x.shape[:2],dtype=int)
        az=np.degrees(np.arctan2(x[...,1],x[...,2]))%360
        distance=np.abs((az[...,None]-np.arange(8)*45+180)%360-180)
        notices=ctx[:,:72].reshape(-1,8,9).copy()
        notices[:,-1,:]=np.maximum(notices[:,-1,:],ctx[:,144:153])
        nearby=((distance[:,:,None,:]<=45)&(notices[:,None,:,:8]>0)).any(axis=-1)|(notices[:,None,:,8]>0)
        bitmask=np.sum(nearby*(2**np.arange(8)),axis=-1).astype(int)
        altitude=np.minimum(3,np.maximum(0,((x[...,0]*90-30)//15).astype(int)))
        lead=np.searchsorted([0,900,1800,3600],np.rint(ctx[:,-4]*3600).astype(int))[:,None]
        season=((np.arctan2(ctx[:,-6],ctx[:,-5])%(2*np.pi))/(np.pi/2)).astype(int)[:,None]
        return ((bitmask*4+altitude)*4+lead)*4+season

    def fit(self,data):
        keys=self.keys(data).ravel(); y=data['y'].reshape(-1,4)
        g=np.ones(len(y)) if self.constant else np.maximum(data['candidate'][...,-1].ravel(),1e-6)
        ratio=y[:,0]/g
        def estimate(indices):
            target=y[indices]
            return np.r_[ratio[indices].mean(),np.quantile(ratio[indices],[0.05,0.5,0.95]),
                         target[:,1:3].mean(axis=0),np.bincount(target[:,3].astype(int),minlength=3)/len(target)]
        self.global_values=estimate(np.arange(len(y)))
        self.table={}
        for key in np.unique(keys):
            indices=np.flatnonzero(keys==key)
            weight=len(indices)/(len(indices)+100)
            self.table[int(key)]=weight*estimate(indices)+(1-weight)*self.global_values
        return self

    def predict(self,data):
        keys=self.keys(data)
        result=np.array([self.table.get(int(key),self.global_values) for key in keys.ravel()]).reshape(*keys.shape,9)
        if not self.constant:
            result[...,:4]*=data['candidate'][...,-1,None]
        return result.astype(np.float32)

    def as_dict(self):
        return {'constant':self.constant,'global':self.global_values.tolist(),'table':{str(k):v.tolist() for k,v in self.table.items()}}

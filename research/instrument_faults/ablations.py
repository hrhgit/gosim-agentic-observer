"""Fixed architecture/config comparisons; final tests never pick an ablation."""
from pathlib import Path
import json
import time

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier,RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from threadpoolctl import threadpool_limits

from .core import FEATURE_NAMES
from .models import load_data,load_classifier,save_classifier,Classifier,Preprocessor,neural_fit,logit,metrics,dataset_identity
from research.observing_conditions.data import save_json


def removed_features(mode):
    weather={'ratio','delta','delta_std','below_lower','saturated_fraction','reference','q_mean','q_median','q_width','usable','blocked','p_dark','p_bright','p_backup'}
    if mode=='no_weather':
        return [i for i,name in enumerate(FEATURE_NAMES) if any(part in weather for part in name.split(':')) or 'ratio_trend' in name or 'low_run' in name]
    if mode=='no_history':
        return [i for i,name in enumerate(FEATURE_NAMES) if name.startswith(('last','nights','direction:'))]
    if mode=='no_quake':
        return [i for i,name in enumerate(FEATURE_NAMES) if 'earthquake' in name or name.startswith('quake_')]
    raise ValueError(mode)


def masked(data,indices):
    result=data.copy();result['x']=data['x'].copy();result['x'][:,indices]=np.nan
    return result


def train(output):
    output=Path(output);kind=json.loads((output/'selection.json').read_text())['model']
    _,base_meta=load_classifier(output/'models'/kind);identity=dataset_identity(output)
    original={s:load_data(output,s) for s in ('train','validation','calibration')}
    for mode in ('no_weather','no_history','no_quake'):
        directory=output/'models'/mode
        if (directory/'metadata.json').exists():
            _,metadata=load_classifier(directory)
            if metadata['dataset_identity']!=identity:raise ValueError('ablation data changed')
            continue
        indices=removed_features(mode);data={s:masked(v,indices) for s,v in original.items()};prep=Preprocessor().fit(data['train']['x'],data['train']['weight'])
        parameters={k:v for k,v in base_meta['parameters'].items() if k not in ('seed','best_epoch')}
        begin=time.perf_counter();directory.mkdir(parents=True,exist_ok=True)
        with threadpool_limits(limits=2):
            if kind=='logistic':
                base=LogisticRegression(max_iter=2000,**parameters).fit(prep.apply(data['train']['x']),data['train']['y'],sample_weight=data['train']['weight'])
                model=Classifier(kind,base,prep)
            elif kind=='boosting':
                # A removed column is constant and cannot inform a split.
                # sklearn's installed binning routine fails on all-NaN columns;
                # ordinary missing values in retained columns remain NaN.
                train_x=data['train']['x'].copy()
                train_x[:,~np.isfinite(train_x).any(axis=0)]=0
                base=HistGradientBoostingClassifier(max_iter=300,min_samples_leaf=50,early_stopping=False,random_state=404,**parameters).fit(train_x,data['train']['y'],sample_weight=data['train']['weight'])
                model=Classifier(kind,base)
            elif kind=='forest':
                base=RandomForestClassifier(n_estimators=300,max_features='sqrt',n_jobs=2,random_state=404,**parameters).fit(prep.apply(data['train']['x']),data['train']['y'],sample_weight=data['train']['weight'])
                model=Classifier(kind,base,prep)
            else:
                model,parameters=neural_fit(data['train'],data['validation'],prep,parameters['lr'],404,directory)
        cp=model.raw(data['calibration']['x'])
        model.calibrator=LogisticRegression(C=1e6,max_iter=2000).fit(logit(cp)[:,None],data['calibration']['y'],sample_weight=data['calibration']['weight'])
        model.features_mask=indices
        validation=metrics(original['validation'],model.predict_proba(original['validation']['x']))
        save_classifier(directory,model,dict(parameters=parameters,validation_calibrated=validation,dataset_identity=identity,weather_version=base_meta['weather_version'],
                     training_seconds=time.perf_counter()-begin,removed_features=[FEATURE_NAMES[i] for i in indices],selection='full-model architecture/config fixed; ablation epoch may use validation; no test selection'))
        print('trained ablation '+mode,flush=True)


def evaluate(output):
    output=Path(output);report={}
    for split in ('test','external'):
        data=load_data(output,split);report[split]={}
        for mode in ('no_weather','no_history','no_quake'):
            model,metadata=load_classifier(output/'models'/mode)
            with threadpool_limits(limits=1):p=model.predict_proba(data['x'])
            report[split][mode]=dict(metrics=metrics(data,p),removed_features=metadata['removed_features'])
    save_json(output/'ablation_comparison.json',report)
    return report

"""Native LightGBM model-text inference; avoids serialized sklearn estimators."""
from __future__ import annotations
from pathlib import Path
import numpy as np
from .adaptation_model import tree_features
from .adaptation_features import QUALITY_INDEX


class PortableTreeCorrection:
    def __init__(self,directory):
        import lightgbm as lgb
        self.models=[lgb.Booster(model_file=str(Path(directory)/f'output-{i}.txt')) for i in range(10)]

    def predict(self,data):
        x=tree_features(data);held=data['x'][:,QUALITY_INDEX];p=np.zeros((len(x),12),np.float32)
        for i,model in enumerate(self.models[:8]):p[:,i]=model.predict(x,num_threads=1)+(held if i<4 else 0)
        p[:,:4]=np.maximum(p[:,:4],0);p[:,1:4]=np.sort(p[:,1:4],axis=1)
        p[:,4:8]=np.clip(p[:,4:8],0,1);p[:,5:8]=np.sort(p[:,5:8],axis=1)
        p[:,8]=self.models[8].predict(x,num_threads=1)
        p[:,9:12]=self.models[9].predict(x,num_threads=1)
        return p

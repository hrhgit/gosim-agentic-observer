"""Public prediction API. Reporting decisions stay outside this module."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import time
import torch

from threadpoolctl import threadpool_limits

from .core import Evidence, FEATURE_VERSION
from .models import load_classifier
from .simulation import feature_sources
from research.observing_conditions.features import utc


class Predictor:
    def __init__(self,output):
        output=Path(output)
        self.model,self.metadata=load_classifier(output/'models/selected')
        if self.model.kind=='neural':torch.set_num_threads(1)
        if 'feature_sources' in self.metadata and self.metadata['feature_sources']!=feature_sources():
            raise ValueError('fault input implementation changed after training')
        if self.model.kind=='forest':self.model.base.n_jobs=1
        self.weather=json.loads((output/'weather_dependency.json').read_text())
        if self.metadata['weather_version']!=self.weather['identity']:
            raise ValueError('fault/weather model version mismatch')
        digest=hashlib.sha256()
        for path in sorted((output/'models/selected').iterdir()):
            if path.is_file():digest.update(path.read_bytes())
        self.version='faults-v1-'+digest.hexdigest()[:16]

    def state(self,score):
        return Evidence(score,self.weather['identity'])

    def predict(self,state,now):
        if state.weather_version is not None and state.weather_version!=self.weather['identity']:
            raise ValueError('evidence/weather model version mismatch')
        start=time.perf_counter();result=state.info(now)
        if result['status'] in ('ready','limited_evidence'):
            x=state.vector(now)
            preprocessing=time.perf_counter()-start;start=time.perf_counter()
            with threadpool_limits(limits=1):
                result['fault_probability']=float(self.model.predict_proba(x[None])[0])
        else:
            preprocessing=time.perf_counter()-start;start=time.perf_counter()
        result['versions']={'fault_model':self.version,'weather_model':self.weather['identity'],'input_format':FEATURE_VERSION}
        result['timing']={'input_seconds':preprocessing,'model_seconds':time.perf_counter()-start}
        return result

    def predict_request(self,request):
        allowed={'now_utc','scoring','weather_version','observations','messages'}
        if set(request)-allowed:
            raise ValueError('unsupported input fields, including truth/labels, are forbidden')
        now=utc(request['now_utc'])
        if request['weather_version']!=self.weather['identity']:
            raise ValueError('saved predictions have a different weather version')
        state=self.state(request['scoring'])
        events=[(utc(r['end']),0,r) for r in request.get('observations',[])]+[(utc(m['issued_at_utc']),1,m) for m in request.get('messages',[])]
        for at,kind,payload in sorted(events,key=lambda e:(e[0],e[1])):
            if at>now:raise ValueError('future input')
            if kind==0:state.observation(payload)
            else:state.messages([payload],at)
        return self.predict(state,now)

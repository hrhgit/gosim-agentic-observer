"""Public request -> numeric predictions; independent of all training truth/labels."""
from __future__ import annotations
import json
import hashlib
from datetime import timedelta
from pathlib import Path
import time

import numpy as np
import torch

from .features import VERSION,utc,public_context,candidate_features
from .models import Normalizer,ConditionsNet,numeric_output
from .metrics import calibrate_apply

OUTPUT_NAMES=('mean_quality','q05','q50','q95','usable_fraction','severe_block_probability','p_dark','p_bright','p_backup')


class Predictor:
    def __init__(self,directory):
        self.directory=Path(directory)
        self.metadata=json.loads((self.directory/'metadata.json').read_text())
        if self.metadata['feature_version']!=VERSION:
            raise ValueError('feature version mismatch')
        self.normalizer=Normalizer.load(self.directory/'normalizer.npz')
        digest=hashlib.sha256()
        digest.update((self.directory/'metadata.json').read_bytes())
        digest.update((self.directory/'normalizer.npz').read_bytes())
        self.models=[]
        for seed in self.metadata['seeds']:
            digest.update((self.directory/f'weights-{seed}.pt').read_bytes())
            model=ConditionsNet(self.metadata['architecture'],self.metadata['context_dim'],
                                self.metadata['history_dim'],self.metadata['candidate_dim'])
            model.load_state_dict(torch.load(self.directory/f'weights-{seed}.pt',map_location='cpu',weights_only=True))
            self.models.append(model.eval())
        self.metadata['model_version']='conditions-v1-'+digest.hexdigest()[:16]

    @torch.inference_mode()
    def numeric(self,normalized):
        tensors=[torch.as_tensor(normalized[k]) for k in ('context','history','candidate')]
        outputs=[numeric_output(model(*tensors),self.metadata['quality_scale']) for model in self.models]
        return calibrate_apply(np.mean(outputs,axis=0),self.metadata['calibration'])

    def predict(self,request):
        """Only these public keys are used. Unknown/requested truth keys raise, rather than become features."""
        allowed={'initialize','now_utc','bulletins','forecasts','candidates'}
        if set(request)-allowed:
            raise ValueError(f'unsupported input fields: {sorted(set(request)-allowed)}')
        initialize=request['initialize'];site=initialize['site'];score=initialize['scoring']
        nights=initialize['survey']['nights'];now=utc(request['now_utc'])
        begin=time.perf_counter();result=[]
        grouped={}
        for index,candidate in enumerate(request['candidates']):
            if set(candidate)-{'candidate_id','ra_deg','dec_deg','start_utc','duration_seconds'}:
                raise ValueError('candidate contains unsupported fields')
            start=utc(candidate['start_utc']);requested=candidate['duration_seconds']
            exposure=initialize.get('instrument',{}).get('exposure',{'min_duration_seconds':60,'max_duration_seconds':3600})
            if isinstance(requested,bool) or not isinstance(requested,int) or not exposure['min_duration_seconds']<=requested<=exposure['max_duration_seconds']:
                raise ValueError('requested exposure outside public limits')
            if (start-now).total_seconds() not in (0,900,1800,3600):
                raise ValueError('v1 supports starts at now, +15, +30, +60 minutes')
            ra,dec=float(candidate['ra_deg']),float(candidate['dec_deg'])
            if not np.isfinite([ra,dec]).all() or not 0<=ra<360 or not -90<=dec<=90:
                raise ValueError('invalid sky coordinates')
            night=next((n for n in nights if utc(n['observing_start_utc'])<=start<utc(n['observing_end_utc'])),None)
            row={'candidate_id':candidate.get('candidate_id',str(index)),'eligible':False}
            result.append(row)
            if night is None:
                row['reason']='outside observing night';continue
            end=min(start+timedelta(seconds=requested),utc(night['observing_end_utc']))
            x,eligible=candidate_features(np.array([ra]),np.array([dec]),start,end,site,score)
            if not eligible[0]:
                row['reason']='below minimum altitude during exposure';continue
            row['eligible']=True;row['actual_duration_seconds']=(end-start).total_seconds()
            key=(start,requested)
            if key not in grouped:
                context,history=public_context(now,start,requested,(end-start).total_seconds(),night['night_date'],
                    night['observing_end_utc'],site,score,request.get('bulletins',[]),request.get('forecasts',[]))
                grouped[key]={'context':context,'history':history,'candidate':[],'indices':[]}
            grouped[key]['candidate'].append(x[0]);grouped[key]['indices'].append(index)
        preprocessing=time.perf_counter()-begin
        begin=time.perf_counter()
        for group in grouped.values():
            arrays={key:np.array([group[key]],dtype=np.float32) for key in ('context','history','candidate')}
            predictions=self.numeric(self.normalizer.apply(arrays))[0]
            for index,prediction in zip(group['indices'],predictions):
                result[index].update({name:float(value) for name,value in zip(OUTPUT_NAMES,prediction)})
        return {'model_version':self.metadata.get('model_version',VERSION),'architecture':self.metadata['architecture'],'quality_definition':self.metadata['quality_definition'],
            'interval_coverage_target':0.9,'training_source':self.metadata['source'],
            'validation_reliable':self.metadata['validation_reliable'],'predictions':result,
            'final_test_passed':self.metadata.get('final_test_passed'),
            'acceptance':self.metadata.get('acceptance'),
            'timing':{'preprocessing_seconds':preprocessing,'prediction_seconds':time.perf_counter()-begin}}

    def benchmark(self,normalized):
        previous=torch.get_num_threads();torch.set_num_threads(1)
        arrays={k:normalized[k][:1] for k in ('context','history','candidate')}
        arrays['candidate']=np.tile(arrays['candidate'],(1,32,1))
        tensors=[torch.as_tensor(arrays[k]) for k in ('context','history','candidate')]
        times=[]
        with torch.inference_mode():
            for index in range(110):
                begin=time.perf_counter()
                outputs=[numeric_output(model(*tensors),self.metadata['quality_scale']) for model in self.models]
                np.mean(outputs,axis=0)
                if index>=10:
                    times.append(1000*(time.perf_counter()-begin))
        torch.set_num_threads(previous)
        return {'candidates':1024,'threads':1,'members':len(self.models),'p50_ms':float(np.median(times)),
                'p95_ms':float(np.quantile(times,0.95)),'boundary':'network, output probability conversion and ensemble; excluding feature calculation/calibration'}

"""Public-condition batch inference. No strategy, ranking or candidate reduction."""
from __future__ import annotations
import argparse
from datetime import timedelta
import json
import hashlib
from pathlib import Path
import time
import numpy as np
import torch
from .adaptation_data import DEFAULT, VERSION
from .adaptation_model import Normalizer, CorrectionNet
from .adaptation_feedback import public_online_bounds
from .adaptation_features import public_features
from .adaptation_model import OnlineUpdate, infer_gru
from .features import PublicTimeline, utc
from .information_predict import InformationPredictor
from .information_model import NAMES, calibration_apply
from .adaptation_cache import PublicCache


class AdaptationPredictor:
    def __init__(self,directory=DEFAULT):
        torch.set_num_threads(1);self.directory=Path(directory)
        self.frozen=json.loads((self.directory/'frozen.json').read_text())
        self.method=self.frozen['selected_method']
        normalizer=Normalizer.load(self.directory/'models/normalizer.npz')
        model=CorrectionNet(len(normalizer.values['x_mean']),len(normalizer.values['history_mean']),float(normalizer.values['quality_scale']))
        model.load_state_dict(torch.load(self.directory/'models/gru.pt',map_location='cpu',weights_only=True));model.eval()
        if self.method=='tree':
            from .adaptation_portable_tree import PortableTreeCorrection
            tree=PortableTreeCorrection(self.directory/'models/portable-tree')
        else:tree=None
        self.models=(model,normalizer,tree,None)
        foundation_path=self.directory/'foundation'
        self.foundation=InformationPredictor(foundation_path) if foundation_path.exists() else InformationPredictor()
        self.model_version=VERSION+'-'+hashlib.sha256((self.directory/'frozen.json').read_bytes()+
            (self.directory/'models/gru.pt').read_bytes()+(self.directory/'models/normalizer.npz').read_bytes()).hexdigest()[:16]
        self.reset()

    def reset(self):
        model,normalizer,_,_=self.models
        mode={'gru_bias':'bias','gru_head':'head'}.get(self.method,'fixed')
        self.online=OnlineUpdate(model,normalizer,mode)
        self.last_feedback_at=None;self.last_exposure_index=-1
        self.public_cache=PublicCache();self.target_table=None;self.targets=None

    def update(self,record,initialize,bulletins,forecasts,now_utc):
        """Optional public update; call after exposure completion, exactly once per index."""
        now=utc(now_utc);end=utc(record['end_utc']);start=utc(record['start_utc'])
        if end>now:raise ValueError('feedback is from the future')
        if end<=start:raise ValueError('empty exposure')
        if self.last_feedback_at is not None and end<self.last_feedback_at:raise ValueError('out-of-order feedback time')
        measured=record['baseline_state_before'].get('last_measurement_utc')
        if measured is not None and utc(measured)>start:raise ValueError('pre-exposure state includes later measurements')
        if record['observe_index']<=self.last_exposure_index:raise ValueError('feedback must arrive once in chronological order')
        table=initialize['targets']
        if self.target_table is not table:
            self.targets={r['target_id']:r for r in (dict(zip(table['columns'],row)) for row in table['rows'])}
            self.target_table=table
        targets=self.targets
        bounds=public_online_bounds(record,targets,initialize['scoring'])
        if bounds:
            coordinates=np.array([[targets[r['target_id']]['ra_deg'],targets[r['target_id']]['dec_deg']] for r in bounds])
            x,h,_,_,_=public_features(start,start,end,int((end-start).total_seconds()),coordinates,initialize,
                self.public_cache.sync(bulletins,forecasts),record['baseline_state_before'],record['feedback_history_before'],self.foundation)
            self.online.observe(dict(x=x,history=np.broadcast_to(h,(len(x),*h.shape)),
                lower=np.array([b['quality_lower'] for b in bounds]),upper=np.array([b['quality_upper'] for b in bounds])))
        self.last_exposure_index=record['observe_index'];self.last_feedback_at=end
        return dict(public_proxy_rows=len(bounds),parameter_updates=self.online.updates,
                    update_cpu_seconds=self.online.cpu_seconds,label_source='public scores / estimated efficiency')

    def predict(self,request):
        allowed={'initialize','now_utc','bulletins','forecasts','baseline_state','feedback_history','candidates'}
        if set(request)-allowed:raise ValueError('unsupported public input fields')
        begin=time.perf_counter();init=request['initialize'];now=utc(request['now_utc'])
        measured=request['baseline_state'].get('last_measurement_utc')
        if measured is not None and utc(measured)>now:raise ValueError('estimated state includes later measurements')
        if self.last_feedback_at is not None and self.last_feedback_at>now:raise ValueError('online state contains later feedback; reset and replay')
        timeline=self.public_cache.sync(request.get('bulletins',[]),request.get('forecasts',[]));groups={};rows=[]
        nights=init['survey']['nights']
        following=next((n for n in nights if utc(n['observing_start_utc'])>now),None)
        for index,c in enumerate(request['candidates']):
            if set(c)-{'candidate_id','ra_deg','dec_deg','start_utc','duration_seconds'}:raise ValueError('unsupported candidate fields')
            start=utc(c['start_utc']);duration=c['duration_seconds']
            if isinstance(duration,bool) or not isinstance(duration,int) or not 60<=duration<=3600:raise ValueError('duration outside trained public range')
            lead=(start-now).total_seconds()
            next_night=following is not None and start==utc(following['observing_start_utc'])
            if lead not in (900,1800,3600) and not next_night:raise ValueError('supports +15/+30/+60 minutes or next-night start')
            ra,dec=float(c['ra_deg']),float(c['dec_deg'])
            if not np.isfinite([ra,dec]).all() or not 0<=ra<360 or not -90<=dec<=90:raise ValueError('invalid sky coordinates')
            night=next((n for n in nights if utc(n['observing_start_utc'])<=start<utc(n['observing_end_utc'])),None)
            row=dict(candidate_id=c.get('candidate_id',str(index)),eligible=False,start_utc=start.isoformat(),
                     next_night_transfer_exploratory=next_night);rows.append(row)
            row['feedback_history_available']=min(8,sum(utc(h['end_utc'])<=now for h in request.get('feedback_history',[])))
            row['limited_history']=row['feedback_history_available']<8
            row['baseline_measurement_available']=bool(request['baseline_state']['has_measurement'])
            row['suspected_instrument_fault']=bool(request['baseline_state']['suspected_fault'])
            if night is None:row['reason']='outside observing night';continue
            end=min(start+timedelta(seconds=duration),utc(night['observing_end_utc']))
            row['actual_duration_seconds']=(end-start).total_seconds()
            group=groups.setdefault((start,end,duration),dict(indices=[],coordinates=[]))
            group['indices'].append(index);group['coordinates'].append((ra,dec))
        feature_seconds=0.;infer_seconds=0.
        for (start,end,duration),g in groups.items():
            started=time.perf_counter()
            x,h,base,held,ok=public_features(now,start,end,duration,np.array(g['coordinates']),init,timeline,
                request['baseline_state'],request.get('feedback_history',[]),self.foundation)
            feature_seconds+=time.perf_counter()-started
            data=dict(x=x,history=np.broadcast_to(h,(len(x),*h.shape)),hold=held)
            started=time.perf_counter()
            p=np.zeros((len(x),12),np.float32)
            if ok.any():
                usable={k:v[ok] for k,v in data.items()}
                if self.method=='tree':values=self.models[2].predict(usable)
                elif self.method in ('gru','gru_bias','gru_head'):values=self.online.predict(usable)
                else:raise ValueError('unknown frozen correction method')
                p[ok]=calibration_apply(values,self.frozen['calibration'][self.method])
            infer_seconds+=time.perf_counter()-started
            for i,index in enumerate(g['indices']):
                row=rows[index];row['eligible']=bool(ok[i])
                if not ok[i]:row['reason']='below minimum altitude during exposure';continue
                row.update({name:float(p[i,j]) for j,name in enumerate(NAMES)})
                row.update({name:float(p[i,j]-held[i,0]) for j,name in enumerate(('delta_mean','delta_q05','delta_q50','delta_q95'))})
                row['reference_held_quality']=float(held[i,0]);row['frozen_foundation_quality']=float(base[i,0])
        return dict(model_version=self.model_version,method=self.method,quality_definition='Q_env, instrument efficiency=1, not capped at 1',
            delta_definition='future quality minus holding current estimated weather/directional state at the SAME future geometry',
            interval_coverage_target=.9,validation_reliability_passed=self.frozen['validation_reliability_passed'],
            final_test_passed=json.loads((self.directory/'report.json').read_text()).get('passed') if (self.directory/'report.json').exists() else None,
            automatic_actions=False,source='SELF-GENERATED training; official hidden competition performance unverified',
            reliability_scope='Whole-scene aggregate calibration; short feedback history and next-night transfer lack reliable subgroup evidence',
            public_feedback_limitation='Score inversion can be ambiguous or saturated; estimated instrument efficiency may be wrong',
            predictions=rows,timing=dict(feature_seconds=feature_seconds,correction_seconds=infer_seconds,
                total_seconds=time.perf_counter()-begin),online_updates=self.online.updates)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--input',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path);parser.add_argument('--model',type=Path,default=DEFAULT)
    args=parser.parse_args();result=AdaptationPredictor(args.model).predict(json.loads(args.input.read_text()))
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__':main()

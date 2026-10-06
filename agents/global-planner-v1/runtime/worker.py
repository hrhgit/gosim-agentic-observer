"""Persistent public-only inference worker. stdout is reserved for request/response JSONL."""
from __future__ import annotations
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(Path(__file__).parent/'public_source'))
import numpy as np
import torch
from scipy.special import expit
from research.observing_conditions.reliability_predict import ReliabilityPredictor
from research.observing_conditions.adaptation_feedback import summarize_exposure
from research.observing_conditions.features import utc,candidate_features
from research.instrument_faults.feedback_only import FeedbackEvidence
from research.instrument_faults.report_model import band_evidence
from research.instrument_faults.benchmark import public_exposure_inputs

class PortableFault:
    def __init__(self,directory):
        self.path=directory/'manifest.json';self.manifest=json.loads(self.path.read_text())
        expected=json.loads((ROOT/'runtime/snapshot.json').read_text())['fault_manifest_sha256']
        if hashlib.sha256(self.path.read_bytes()).hexdigest()!=expected:raise ValueError('fault model changed')
        self.version=self.manifest['version']+'-'+hashlib.sha256(self.path.read_bytes()).hexdigest()[:16]
    def raw(self,x):
        x=np.asarray(x,np.float32)
        if x.ndim!=2 or x.shape[1]!=self.manifest['feature_count']:raise ValueError('wrong fault feature shape')
        total=np.full(len(x),self.manifest['baseline'],np.float64)
        for nodes in self.manifest['trees']:
            for row,values in enumerate(x):
                i=0
                while not nodes[i]['is_leaf']:
                    node=nodes[i];v=values[node['feature_idx']]
                    left=node['missing_go_to_left'] if np.isnan(v) else v<=node['num_threshold']
                    i=node['left'] if left else node['right']
                total[row]+=nodes[i]['value']
        return expit(total)

class Session:
    def __init__(self,initialize,enabled):
        self.initialize=initialize;self.enabled=enabled
        table=initialize.get('targets',{'columns':[],'rows':[]})
        self.targets={r['target_id']:r for r in (dict(zip(table['columns'],row)) for row in table['rows'])}
        self.conditions=ReliabilityPredictor(ROOT/'models/conditions-reliability') if 'conditions-reliability' in enabled else None
        self.fault=PortableFault(ROOT/'models/instrument-fault-report') if 'instrument-fault-report' in enabled else None
        self.evidence=FeedbackEvidence(initialize['scoring']);self.history=[];self.bulletins=[];self.forecasts=[];self.seen=set();self.new=set()
        self.latest_extra=None;self.latest_rec=None;self.latest_hits=None
    def sync(self,request):
        now=utc(request['now_utc']);record=request.get('completed_exposure')
        # Include the just-completed exposure before report/resync invalidation.
        if record:
            if utc(record['end_utc'])>now:raise ValueError('future feedback')
            ids=record['target_ids'];targets=[self.targets[i] for i in ids]
            hits=record['hits'];duration=(utc(record['end_utc'])-utc(record['start_utc'])).total_seconds()
            public=dict(observe_index=record['observe_index'],start=record['start_utc'],end=record['end_utc'],night=record['night'],duration=duration,program=record['program'],altitude=record['pointing']['alt_deg'],azimuth=record['pointing']['az_deg'],targets=[{k:t[k] for k in ('target_id','ra_deg','dec_deg','feature_flux','science_weight')} for t in targets],result=dict(assigned_count=record['assigned_count'],hit_count=len(hits),hits=hits))
            self.evidence.observation(public)
            summary=summarize_exposure(record,self.targets,self.initialize['site'],self.initialize['scoring'])
            summary['observe_index']=record['observe_index'];self.history.append(summary);self.history=self.history[-8:]
            if self.evidence.records[-1]['stats']['usable_targets']>0:self.new.add(record['observe_index'])
            self.latest_rec=dict(program=record['program'],items=record['items'])
            self.latest_hits={record['target_ids'].index(h['target_id']):h['score'] for h in hits}
            # Item IDs are local to this completed exposure, independent of catalogue order.
            self.latest_rec['items']=[list(item) for item in record['items']]
            for i,item in enumerate(self.latest_rec['items']):item[0]=i
            # The exposure's public geometry/announcement features were captured before it began.
            self.latest_extra=np.asarray(record['public_inputs'],np.float32) if 'public_inputs' in record else self.exposure_inputs(record)
        messages=[]
        for message in request.get('messages',[]):
            if message.get('record_type') not in ('bulletin','forecast','report_result','state_resync'):continue
            if utc(message['issued_at_utc'])>now:raise ValueError('future published message')
            key=json.dumps(message,sort_keys=True)
            if key in self.seen:continue
            self.seen.add(key);messages.append(message)
            if message['record_type']=='bulletin':self.bulletins.append(message)
            elif message['record_type']=='forecast':self.forecasts.append(message)
        self.evidence.messages(messages,now)
        for message in messages:
            if message['record_type']=='report_result':
                at=utc(message['issued_at_utc'])
                self.evidence.records=[r for r in self.evidence.records if utc(r['end'])>at];self.new.clear()
                self.latest_rec=self.latest_extra=self.latest_hits=None
            elif message['record_type']=='state_resync':
                w=message['invalidated_window'];first,last=w['action_index_start'],w['action_index_end_exclusive']
                self.history=[r for r in self.history if not first<=r['observe_index']<last]
                self.new.difference_update(range(first,last))
                # Withdraw all geometry/band estimates that relied on the removed exposure.
                self.latest_rec=self.latest_extra=self.latest_hits=None
        self.bulletins.sort(key=lambda m:utc(m['issued_at_utc']));self.forecasts.sort(key=lambda m:utc(m['issued_at_utc']))
        self.sky=request['sky'];self.state=request['baseline_state'];self.remaining_gain=request.get('remaining_gain',0.)
        return dict(feedback_history_count=len(self.history),new_fault_exposure_count=len(self.new))
    def exposure_inputs(self,record):
        from research.observing_conditions.features import PublicTimeline
        start,end=utc(record['start_utc']),utc(record['end_utc'])
        night=next(n for n in self.initialize['survey']['nights'] if utc(n['observing_start_utc'])<=start<utc(n['observing_end_utc']))
        duration=(end-start).total_seconds()
        timeline=PublicTimeline(self.bulletins,self.forecasts)
        context,_=timeline.snapshot(start,start,duration,duration,night['night_date'],night['observing_end_utc'],self.initialize['site'],self.initialize['scoring'])
        positions=np.array([[self.targets[i]['ra_deg'],self.targets[i]['dec_deg']] for i in record['target_ids']])
        candidate=candidate_features(positions[:,0],positions[:,1],start,end,self.initialize['site'],self.initialize['scoring'])[0] if len(positions) else np.empty((0,29))
        return public_exposure_inputs(context,candidate)
    def predict_conditions(self,request):
        if not self.conditions:raise ValueError('conditions model disabled')
        if request is None:raise ValueError('conditions request missing')
        result=self.conditions.predict(request)
        return dict(result,model_id='conditions-reliability')
    def predict_fault(self,request):
        if not self.fault:raise ValueError('fault model disabled')
        now=utc(request['now_utc']);info=self.evidence.info(now)
        result=dict(model_id='instrument-fault-report',model_version=self.fault.version,**info,report_score=None,score_is_calibrated_fault_probability=False,independent_final_test_passed=None,evidence_limitation='Cost-weighted report score, not a calibrated fault probability; validation scenes were previously used for diagnostics; independent final competition performance unverified',automatic_actions=False,recommend_report=False,new_exposure_count=len(self.new),source=self.fault.manifest['source'])
        ready=info['status']=='ready' and self.latest_rec is not None and len(self.new)>=3
        result['decision_due']=ready
        if self.latest_rec is not None:
            extra=band_evidence(self.latest_rec,self.latest_hits,SimpleNamespace(**self.sky),self.initialize['scoring'],now,self.remaining_gain)
            vector=np.r_[self.evidence.vector(now),self.latest_extra,extra].astype(np.float32)
            result['report_score']=float(self.fault.raw(vector[None])[0]);result['feature_count']=len(vector)
        thresholds=self.fault.manifest['thresholds'];threshold=thresholds[0 if self.sky['free_left']>0 else 1]
        result.update(report_threshold=threshold,public_guard_satisfied=bool(request.get('guard_satisfied',False)))
        result['recommend_report']=bool(ready and request.get('guard_satisfied',False) and result['report_score']>=threshold)
        if ready:self.new.clear()
        return result

session=None
for line in sys.stdin:
    try:
        message=json.loads(line);began=time.process_time();wall=time.perf_counter();op=message['operation'];data=message.get('input')
        if op=='initialize':
            torch.set_num_threads(1);torch.set_num_interop_threads(1) if session is None else None
            session=Session(data['initialize'],data['enabled']);result={'initialized':data['enabled']}
        elif op=='conditions':
            # Direct task calls also work without an agent session.
            if session is None:session=Session(data['initialize'],['conditions-reliability'])
            result=session.predict_conditions(data)
        elif op=='session_conditions':
            if session is None:raise ValueError('initialize first')
            result=session.predict_conditions(dict(data,feedback_history=session.history,bulletins=session.bulletins,forecasts=session.forecasts))
        elif op=='sync':
            if session is None:raise ValueError('initialize first')
            result=session.sync(data)
        elif op=='fault':
            if session is None:raise ValueError('initialize first')
            result=session.predict_fault(data)
        else:raise ValueError('unknown model operation')
        response=dict(id=message['id'],result=result,cpu_seconds=time.process_time()-began,wall_seconds=time.perf_counter()-wall)
    except Exception as error:
        import traceback
        traceback.print_exc(file=sys.stderr)
        response=dict(id=message.get('id') if isinstance(locals().get('message'),dict) else None,error=f'{type(error).__name__}: {error}')
    print(json.dumps(response,allow_nan=False,separators=(',',':')),flush=True)

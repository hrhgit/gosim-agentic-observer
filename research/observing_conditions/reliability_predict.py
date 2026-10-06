"""Public-only numerical entry; a research candidate, never an action selector."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import time
import numpy as np
import torch
from .adaptation_predict import AdaptationPredictor
from . import reliability_data as rd
from . import reliability_calibration as rc
from . import endpoint_model as em
from .information_model import NAMES
from .features import utc


class ReliabilityPredictor(AdaptationPredictor):
    def __init__(self,directory=rd.DEFAULT/'selected'):
        super().__init__(directory)
        self.manifest=json.loads((self.directory/'manifest.json').read_text())
        for name,h in self.manifest['hashes'].items():
            if hashlib.sha256((self.directory/name).read_bytes()).hexdigest()!=h:raise ValueError('bundle changed')
        self.settings=json.loads((self.directory/'reliability.json').read_text())
        self.support=json.loads((self.directory/'support.json').read_text())
        with np.load(self.directory/'endpoint_head/normalizer.npz') as n:self.endpoint_normalizer={k:n[k].copy() for k in n.files}
        self.head=em.EndpointHead(len(self.endpoint_normalizer['mean']))
        self.head.load_state_dict(torch.load(self.directory/'endpoint_head/head.pt',map_location='cpu',weights_only=True));self.head.eval()
        self.model_version='conditions-reliability-v2-'+hashlib.sha256((self.directory/'manifest.json').read_bytes()).hexdigest()[:16]

    def predict(self,request):
        begin=time.perf_counter();captured=[];original_predict=self.online.predict
        def capture(data):
            raw=original_predict(data);captured.append((data,raw));return raw
        self.online.predict=capture
        try:result=super().predict(request)
        finally:self.online.predict=original_predict
        started=time.perf_counter()
        fresh=rd.freshness(request['now_utc'],request.get('feedback_history',[]),request['initialize']['survey']['nights'])
        now=utc(request['now_utc']);groups={}
        # Preserve group insertion order even when its first candidate is ineligible.
        for row,candidate in zip(result['predictions'],request['candidates']):
            group=groups.setdefault((row['start_utc'],candidate['duration_seconds']),[])
            row.update(current_night_feedback_available=fresh['fresh_history_count'],
                       feedback_history_available=fresh['all_history_count'])
            if row['eligible']:group.append(row)
        groups=[rows for rows in groups.values() if rows]
        if len(groups)!=len(captured):raise RuntimeError('public batch group alignment failed')
        for rows,(data,raw) in zip(groups,captured):
            if len(rows)!=len(raw):raise RuntimeError('candidate group size differs')
            metadata=[]
            for row in rows:
                lead=(utc(row['start_utc'])-now).total_seconds()
                metadata.append(dict(horizon='next_night' if row['next_night_transfer_exploratory'] else {900:'15m',1800:'30m',3600:'60m'}[lead],
                    **fresh,baseline_measurement_available=bool(request['baseline_state']['has_measurement'])))
            adjusted,evidence=rc.apply(raw,metadata,self.settings,True)
            model,normalizer,_,_=self.models
            x=em.features(dict(data,meta=metadata),raw,model,normalizer)
            n=self.endpoint_normalizer;dist=em.infer(self.head,((x-n['mean'])/n['std']).astype(np.float32))
            values=em.numerical(dist,adjusted);_,phases=rc.public_keys(metadata)
            for row,p,m,support,phase in zip(rows,values,dist,evidence,phases):
                row.update({name:float(v) for name,v in zip(NAMES,p)})
                row.update({name:float(p[i]-row['reference_held_quality']) for i,name in enumerate(('delta_mean','delta_q05','delta_q50','delta_q95'))})
                row.update(p_usable_zero=float(m[0]),p_usable_partial=float(m[1]),p_usable_one=float(m[2]),
                    usable_interior_alpha=float(m[3]),usable_interior_beta=float(m[4]),
                    public_history_phase=str(phase),limited_current_night_history=phase!='eight')
                support.update(blocked_probability_source='frozen endpoint mixture, not bank temperature',
                    usable_range_source='endpoint mixture, not bank residual offsets',per_candidate_guarantee=False)
                row['calibration_evidence']=support
                group_metrics=self.support['night'].get(str(phase)) if phase!='eight' else None
                if group_metrics is not None:
                    row['empirical_group_evidence']=dict(source='16 independent SELF-GENERATED final scenes; aggregate only',
                        scenes=group_metrics['scenes'],quality_coverage=group_metrics['quality_coverage'],
                        band_probability_error=group_metrics['band_ece'],
                        subgroup_check_passed=self.support['checks']['checks'].get(str(phase),False))
                row['first_observation_band_evidence_insufficient']=phase=='never'
        result.update(method=self.manifest['selected'],final_test_passed=self.manifest['final_test_passed'],
            network_initializations=1,trunk_training_runs=0,online_updates=0,
            reliability_scope=self.settings['limitation'],reliability_limitations=self.support['limits'],
            usable_distribution='Atoms at0/1 and beta interior; mean, quantiles and P(U<.5) from the SAME distribution',
            usable_interval_note='Nominal90% central interval; discrete0/1 mass gives conservative coverage, not exact90%.',
            calibration_group_fields=self.settings['public_group_fields'],automatic_actions=False)
        result['timing'].update(reliability_head_seconds=time.perf_counter()-started,total_seconds=time.perf_counter()-begin)
        return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--input',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--model',type=Path,default=rd.DEFAULT/'selected');a=p.parse_args()
    a.output.write_text(json.dumps(ReliabilityPredictor(a.model).predict(json.loads(a.input.read_text())),ensure_ascii=False,indent=2)+'\n')

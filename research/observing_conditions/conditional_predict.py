"""Public batch entry point for a frozen conditional calibration bundle."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import time
import numpy as np
from .adaptation_predict import AdaptationPredictor
from .calibration_experiment import DEFAULT
from .conditional_calibration import apply
from .information_model import NAMES
from .features import utc


class ConditionalPredictor(AdaptationPredictor):
    def __init__(self,directory=DEFAULT/'selected'):
        super().__init__(directory)
        self.settings=json.loads((self.directory/'conditional.json').read_text())
        self.manifest=json.loads((self.directory/'manifest.json').read_text())
        for name,value in self.manifest['hashes'].items():
            if hashlib.sha256((self.directory/name).read_bytes()).hexdigest()!=value:raise ValueError('bundle changed')
        self.model_version='conditions-conditional-v1-'+hashlib.sha256(
            (self.directory/'manifest.json').read_bytes()).hexdigest()[:16]

    def predict(self,request):
        began=time.perf_counter()
        result=super().predict(request);adjusted_at=time.perf_counter()
        eligible=[r for r in result['predictions'] if r['eligible']]
        if eligible:
            now=utc(request['now_utc']);metadata=[]
            for row in eligible:
                lead=(utc(row['start_utc'])-now).total_seconds()
                metadata.append(dict(horizon='next_night' if row['next_night_transfer_exploratory'] else
                                     {900:'15m',1800:'30m',3600:'60m'}[lead],
                                     history_count=row['feedback_history_available']))
            raw=np.array([[r[n] for n in NAMES] for r in eligible],np.float32)
            values,evidence=apply(raw,metadata,self.settings,True)
            for row,p,support in zip(eligible,values,evidence):
                row.update({name:float(v) for name,v in zip(NAMES,p)})
                row.update({name:float(p[i]-row['reference_held_quality']) for i,name in
                            enumerate(('delta_mean','delta_q05','delta_q50','delta_q95'))})
                row['calibration_evidence']=support
        result.update(method=self.manifest['selected'],
            reliability_scope=self.settings['limitation'],
            calibration_group_fields=self.settings['public_group_fields'],
            final_test_passed=self.manifest['final_test_passed'],network_training_runs=0)
        result['timing'].update(conditional_calibration_seconds=time.perf_counter()-adjusted_at,
                               total_seconds=time.perf_counter()-began)
        return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--input',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--model',type=Path,default=DEFAULT/'selected')
    a=p.parse_args();value=ConditionalPredictor(a.model).predict(json.loads(a.input.read_text()))
    a.output.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')

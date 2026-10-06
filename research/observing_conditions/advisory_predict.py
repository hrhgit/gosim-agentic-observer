"""Single-model public API with numeric, risk-gated condition advisories."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import torch

from .predict import Predictor, OUTPUT_NAMES
from .improvement import restore, transform, apply_calibration, DEFAULT_IMPROVEMENT
from .models import Normalizer, load_model, numeric_output
from .assistance import decide
from .priority import priority_mask
from .data import save_json


class AdvisoryPredictor(Predictor):
    def __init__(self,directory=DEFAULT_IMPROVEMENT/'models/deployment'):
        self.directory=Path(directory)
        self.policy=json.loads((self.directory/'policy.json').read_text())
        self.mode=self.policy['selected']
        if self.mode=='reference':
            model,meta=load_model(self.directory)
            self.normalizer=Normalizer.load(self.directory/'normalizer.npz')
        else:
            model,self.normalizer=restore(self.directory)
            meta=json.loads((self.directory/'metadata.json').read_text())
        self.models=[model.eval()]
        checksum=hashlib.sha256()
        for name in ('weights.pt','normalizer.npz','metadata.json','policy.json'):
            checksum.update((self.directory/name).read_bytes())
        self.metadata=dict(model_version='conditions-v2-'+checksum.hexdigest()[:16],
            architecture='MLP-single',quality_definition='Q_env, efficiency=1, no instrument faults',
            source=self.policy['source'],validation_reliable=None,
            final_test_passed=self.policy.get('assistance_test_passed',False),
            acceptance=self.policy.get('assistance_acceptance',{}),
            quality_scale=float(self.normalizer.values['quality_scale']))

    def prepare(self,arrays):
        processed=arrays if self.mode=='reference' else transform(arrays,self.mode)
        return self.normalizer.apply(processed)

    def priority_predictions(self,predictions,indices):
        return predictions

    @torch.inference_mode()
    def numeric(self,normalized):
        tensors=[torch.as_tensor(normalized[k]) for k in ('context','history','candidate')]
        output=numeric_output(self.models[0](*tensors),self.metadata['quality_scale'])
        context=normalized['context']*self.normalizer.values['context_std']+self.normalizer.values['context_mean']
        return apply_calibration({'context':context[:,:242]},output,self.policy['calibration'])

    def predict(self,request):
        result=super().predict(request)
        # Metadata distinguishes previous numerical acceptance from this assistance
        # evaluation; no model prediction itself is a deterministic exclusion.
        result.pop('final_test_passed',None);result.pop('acceptance',None)
        result['assistance_test_passed']=self.policy.get('assistance_test_passed',False)
        result['assistance_acceptance']=self.policy.get('assistance_acceptance',{})
        result['automatic_actions_enabled']=False
        result['priority_list_empirically_validated']=self.policy.get('priority_test_passed',False)
        result['assistance_scope']='observation conditions only; compare within equal start and requested duration'
        result['validation_evidence']=self.policy.get('validation_evidence')
        groups={}
        for index,(candidate,row) in enumerate(zip(request['candidates'],result['predictions'])):
            row['formula_excluded']=not row['eligible']
            row['suggest_exclude']=False;row['suggest_priority']=False
            row['in_priority_list']=False;row['condition_rank']=None
            row['advice_reason_code']=-1 if not row['eligible'] else 0
            if row['eligible']:
                # Canonical UTC parsed by the underlying predictor; semantically
                # identical ISO timestamp spellings must belong to the same group.
                from .features import utc
                key=(utc(candidate['start_utc']),candidate['duration_seconds'])
                groups.setdefault(key,[]).append(index)
        summaries=[]
        for (start,duration),indices in groups.items():
            p=np.array([[ [result['predictions'][i][name] for name in OUTPUT_NAMES] for i in indices]])
            decisions=decide(p,self.policy['assistance_rule'])
            priority_rule=self.policy.get('priority_rule',dict(size=len(indices),block_max=0.,usable_min=1.,enabled=False))
            priority_values=self.priority_predictions(p,indices)
            priority_kept,priority_active,priority_order=priority_mask(priority_values,priority_rule | dict(
                enabled=bool(self.policy.get('priority_test_passed',False))))
            rank=np.empty(len(indices),int);rank[priority_order[0]]=np.arange(1,len(indices)+1)
            formula_order=np.argsort([-result['predictions'][i]['known_geometry_factor'] for i in indices],kind='stable')
            formula_rank=np.empty(len(indices),int);formula_rank[formula_order]=np.arange(1,len(indices)+1)
            selected=int(decisions['recommended'][0])
            for j,i in enumerate(indices):
                row=result['predictions'][i]
                row['suggest_exclude']=bool(decisions['removed'][0,j])
                row['suggest_priority']=bool(decisions['recommend'][0] and j==selected)
                row['condition_rank']=int(rank[j])
                row['formula_condition_rank']=int(formula_rank[j])
                row['in_priority_list']=bool(priority_kept[0,j])
                row['advice_reason_code']=(1 if decisions['blocked'][0,j] else 2 if decisions['dominated'][0,j]
                    else 3 if row['suggest_priority'] else 0)
            summaries.append(dict(start_utc=start.isoformat(),duration_seconds=duration,
                eligible_candidates=len(indices),suggested_exclusions=int(decisions['removed'].sum()),
                priority_list_applied=bool(priority_active[0]),
                formula_priority_reference_candidate_ids=[result['predictions'][indices[int(j)]]['candidate_id'] for j in formula_order[:4]],
                priority_candidate_ids=[result['predictions'][indices[int(j)]]['candidate_id'] for j in priority_order[0]
                    if priority_kept[0,int(j)]],
                suggested_priority_candidate_id=result['predictions'][indices[selected]]['candidate_id'] if decisions['recommend'][0] else None))
        result['groups']=summaries
        return result


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--model',type=Path,default=DEFAULT_IMPROVEMENT/'models/deployment')
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--result',type=Path,required=True)
    args=parser.parse_args()
    torch.set_num_threads(1)
    save_json(args.result,AdvisoryPredictor(args.model).predict(json.loads(args.input.read_text())))

"""Public-only traditional model batch API; ranking remains distinct from quality."""
from __future__ import annotations

from .tree_runtime import launch,require_shared_runtime
if __name__=='__main__':launch('research.observing_conditions.tree_predict')

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from .advisory_predict import AdvisoryPredictor
from .data import save_json
from .traditional import DEFAULT_TREES
from .tree_models import TreeBundle,apply_calibration,ranking_view


class TreePredictor(AdvisoryPredictor):
    def __init__(self,directory=DEFAULT_TREES/'models'):
        require_shared_runtime()
        self.directory=Path(directory)
        self.policy=json.loads((self.directory/'policy.json').read_text())
        self.mode=self.policy['selected'];self.bundle=TreeBundle.load(self.directory)
        self.bundle.extra.n_jobs=1
        checksum=hashlib.sha256()
        for path in sorted(self.directory.rglob('*')):
            if path.is_file():
                checksum.update(str(path.relative_to(self.directory)).encode());checksum.update(path.read_bytes())
        self.metadata=dict(model_version='conditions-trees-v1-'+checksum.hexdigest()[:16],
            architecture=self.mode,quality_definition='Q_env, efficiency=1, no instrument faults',
            source=self.policy['source'],validation_reliable=None,final_test_passed=None,acceptance=None)
        self._current_scores=None;self._rank_by_index={}

    def prepare(self,arrays):
        return arrays

    def numeric(self,public):
        p,scores=self.bundle.predict(public,self.mode)
        self._current_scores=scores
        return apply_calibration(p,public,self.policy['calibration'])

    def record_group_predictions(self,indices):
        if self._current_scores is not None:
            self._rank_by_index.update({index:float(score) for index,score in zip(indices,self._current_scores[0])})

    def priority_predictions(self,predictions,indices):
        scores=(np.array([[self._rank_by_index[index] for index in indices]])
            if self._rank_by_index else None)
        return ranking_view(predictions,scores)

    def predict(self,request):
        self._current_scores=None;self._rank_by_index={}
        result=super().predict(request)
        for index,score in self._rank_by_index.items():
            result['predictions'][index]['ranking_score']=score
        result['ranking_score_is_quality']=False
        result['interval_method']=self.policy['calibration']['interval_method']
        result['median_method']=self.policy['calibration']['median_method']
        return result


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--model',type=Path,default=DEFAULT_TREES/'models')
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--result',type=Path,required=True)
    args=parser.parse_args()
    save_json(args.result,TreePredictor(args.model).predict(json.loads(args.input.read_text())))

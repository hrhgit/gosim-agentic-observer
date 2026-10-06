"""Verify saved traditional artifacts and real public API without fitting."""
from __future__ import annotations

from .tree_runtime import launch
if __name__=='__main__':launch('research.observing_conditions.tree_verify')

import copy
import hashlib
import json
from pathlib import Path

import numpy as np

from .data import ROOT,DEFAULT_OUTPUT,save_json,source_hashes,audit_dataset
from .traditional import DEFAULT_TREES
from .tree_predict import TreePredictor
from .predict import OUTPUT_NAMES


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(output=DEFAULT_TREES):
    output=Path(output)
    frozen=json.loads((output/'frozen.json').read_text())
    protocol=json.loads((output/'protocol.json').read_text())
    assert digest(output/'protocol.json')==frozen['protocol_sha256']
    assert source_hashes()==protocol['source_hashes']
    for relative,expected in frozen['artifact_hashes'].items():
        assert digest(output/'models'/relative)==expected
    for relative,expected in protocol['research_code_sha256'].items():
        assert digest(ROOT/relative)==expected
    original=json.loads((DEFAULT_OUTPUT/'completion_audit.json').read_text())
    for relative,expected in original['selected_artifact_sha256'].items():
        assert digest(DEFAULT_OUTPUT/'models/selected'/relative)==expected
    old=[json.loads((p/'manifest.json').read_text()) for p in
        (DEFAULT_OUTPUT,ROOT/'artifacts/observing_conditions-v2',ROOT/'artifacts/observing_conditions-v2/priority')]
    assert not {e['seed'] for e in protocol['new_entries']}&{e['seed'] for m in old for e in m['entries']}
    audit_dataset(output,count=32,sample_splits=('test','weather_test','site_test'))
    predictor=TreePredictor(output/'models')
    request=json.loads((output/'example_request.json').read_text())
    response=predictor.predict(request)
    cli=json.loads((output/'cli_prediction.json').read_text())
    assert response['predictions']==cli['predictions']
    assert response['groups']==cli['groups']
    assert response['automatic_actions_enabled'] is False
    assert not any(response['assistance_acceptance'].values())
    assert not any(r['suggest_exclude'] or r['suggest_priority'] for r in response['predictions'])
    assert all(not g['priority_list_applied'] for g in response['groups'])
    for row in response['predictions']:
        if row['eligible']:
            assert np.isfinite([row[k] for k in OUTPUT_NAMES]+[row['ranking_score']]).all()
            assert row['q05']<=row['q50']<=row['q95']
            assert abs(sum(row[k] for k in ('p_dark','p_bright','p_backup'))-1)<1e-6
    # One group first appears as an unusable position, so its scoring order and
    # later advisory order differ. Compare against separate requests per group.
    mixed=copy.deepcopy(request)
    a,b,c,d=copy.deepcopy(request['candidates'][:4])
    blocked=copy.deepcopy(a);blocked.update(candidate_id='below-horizon',ra_deg=(a['ra_deg']+180)%360,dec_deg=-a['dec_deg'])
    b.update(candidate_id='other-duration-1',duration_seconds=600)
    d.update(candidate_id='other-duration-2',duration_seconds=600)
    mixed['candidates']=[blocked,b,a,d,c]
    prediction=predictor.predict(mixed)
    assert not prediction['predictions'][0]['eligible']
    for duration in (300,600):
        single=copy.deepcopy(mixed)
        single['candidates']=[p for p in mixed['candidates'] if p['duration_seconds']==duration]
        answer=predictor.predict(single)
        by_id={p['candidate_id']:p for p in prediction['predictions']}
        for row in answer['predictions']:
            if row['eligible']:
                np.testing.assert_allclose([row[k] for k in (*OUTPUT_NAMES,'ranking_score')],
                    [by_id[row['candidate_id']][k] for k in (*OUTPUT_NAMES,'ranking_score')],atol=1e-6)
    try:predictor.predict(request | dict(weather_truth='private'))
    except ValueError:pass
    else:raise AssertionError('private field accepted')
    jobs={name:json.loads((output/'models'/name/'complete.json').read_text())
        for name in ('extra_trees','lightgbm','lightgbm_ranker')}
    assert all(j['initialization_repetitions']==1 for j in jobs.values())
    assert sum(j['fit_calls'] for j in jobs.values())==6
    files=list((ROOT/'research/observing_conditions').glob('*.py'))
    files+=[ROOT/'research/observing_conditions'/n for n in ('README.md','pyproject.toml','uv.lock')]
    save_json(output/'implementation_sources.json',{str(p.relative_to(ROOT)):digest(p) for p in files})
    result=dict(passed=True,checks='saved hashes, fresh scene separation, official oracle, public API, mixed groups, CLI equivalence, private-field rejection',
        model_selected_before_test=frozen['selected'],official_source_files=len(protocol['source_hashes']),
        original_selected_unchanged=True,frozen_tree_artifacts_unchanged=True,training_fit_calls=6,
        initialization_repetitions=1,new_scenes=32,new_samples=65536,
        oracle_check=json.loads((output/'audit.json').read_text()),capabilities=response['assistance_acceptance'],
        model_version=response['model_version'],limitations='Artifact/API checks passed; selected priority rule failed final acceptance; self-generated data only')
    save_json(output/'completion_audit.json',result)
    print(json.dumps(result),flush=True)


if __name__=='__main__':run()

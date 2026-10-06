from copy import deepcopy

import numpy as np
import pytest

from research.instrument_faults.data import read_public
from research.instrument_faults.feedback_only import FeedbackEvidence, FEATURE_NAMES, fit


def record(index=0, hit=True, score=.2):
    return dict(observe_index=index, start=f'2026-01-01T0{index}:00:00Z', end=f'2026-01-01T0{index}:01:00Z',
                night='n1', duration=60, program='DARK', altitude=70, azimuth=index*90,
                targets=[dict(target_id='t1', feature_flux=1., science_weight=1., ra_deg=0., dec_deg=0.)],
                result=dict(assigned_count=1, hit_count=int(hit), hits=[dict(target_id='t1', score=score)] if hit else []))


def test_feedback_only_does_not_need_weather_and_rejects_hidden_or_predicted_fields():
    state = FeedbackEvidence(read_public('a')[2])
    assert state.info('2026-01-01T00:00:00Z')['status'] == 'no_observation_evidence'
    for bad in ('truth_quality', 'fault_multiplier', 'predictions'):
        r = record(); r[bad] = 1
        with pytest.raises(ValueError):
            state.observation(r)
    r = record(); r['targets'][0]['true_band'] = 'DARK'
    with pytest.raises(ValueError):
        state.observation(r)
    for index in range(3):
        state.observation(record(index, score=0 if index == 2 else .2))
    info = state.info('2026-01-01T02:01:00Z')
    assert info['status'] == 'ready' and info['evidence_exposure_count'] == 3
    assert info['evidence_target_count'] == 3 and info['direction_count'] == 3
    x = state.vector('2026-01-01T02:01:00Z')
    assert x.shape == (len(FEATURE_NAMES),) and x[FEATURE_NAMES.index('zero_fraction')] == 1
    with pytest.raises(ValueError):
        state.vector('2026-01-01T02:00:00Z')


def test_feedback_repair_resync_all_misses_and_confirmation_expiry():
    state = FeedbackEvidence(read_public('a')[2])
    state.observation(record(hit=False))
    assert state.info('2026-01-01T00:01:00Z')['status'] == 'no_observation_evidence'
    state.observation(record(1)); state.observation(record(2))
    previous = deepcopy(state.records)
    state.messages([dict(record_type='state_resync', issued_at_utc='2026-01-01T02:01:00Z',
                         invalidated_window=dict(action_index_start=1, action_index_end_exclusive=2))], '2026-01-01T02:01:00Z')
    assert state.info('2026-01-01T02:01:00Z')['evidence_target_count'] == 1
    assert len(previous) == 3
    state.messages([dict(record_type='report_result', issued_at_utc='2026-01-01T02:01:00Z', correct=False)], '2026-01-01T02:01:00Z')
    assert state.info('2026-01-01T02:01:00Z')['fault_probability'] == 0
    assert state.info('2026-01-01T02:01:01Z')['status'] == 'limited_evidence'
    assert len(state.records) == 2
    state.messages([dict(record_type='report_result', issued_at_utc='2026-01-01T02:01:01Z', correct=True)], '2026-01-01T02:01:01Z')
    assert not state.records
    assert state.info('2026-01-01T02:01:02Z')['fault_probability'] is None
    with pytest.raises(ValueError):
        state.observation(record(2))


@pytest.mark.parametrize('kind,parameters', [('logistic', dict(C=1)),
    ('boosting', dict(learning_rate=.05, max_leaf_nodes=15, l2_regularization=1))])
def test_feedback_classifier_masks_developer_quake_confound(kind, parameters):
    x = np.zeros((200, len(FEATURE_NAMES)), np.float32)
    x[:, 0] = np.tile([0, 1], 100)
    y = x[:, 0].astype(np.int8)
    quake = FEATURE_NAMES.index('current:earthquake:N')
    x[:, quake] = y
    x[:, FEATURE_NAMES.index('repair_age')] = np.nan
    model = fit(kind, dict(x=x, y=y, weight=np.ones(200)), parameters, [quake])
    a = x[:2].copy(); b = a.copy(); b[:, quake] = 1-b[:, quake]
    assert np.allclose(model.raw(a), model.raw(b))
    assert model.raw(a)[1] > model.raw(a)[0]


def test_full_boosting_ablations_train_with_removed_columns(tmp_path, monkeypatch):
    import json
    from research.instrument_faults import ablations
    from research.instrument_faults.core import FEATURE_NAMES as full_names
    from research.instrument_faults.models import load_classifier

    x = np.full((200, len(full_names)), np.nan, np.float32)
    x[:, full_names.index('observed')] = np.tile([0, 1], 100)
    data = dict(x=x, y=x[:, full_names.index('observed')].astype(np.int8),
                scene=np.repeat(['scene1', 'scene2'], 100), weight=np.ones(200))
    (tmp_path/'selection.json').write_text(json.dumps(dict(model='boosting')))
    metadata = dict(parameters=dict(learning_rate=.05, max_leaf_nodes=15, l2_regularization=1), weather_version='test-version')
    monkeypatch.setattr(ablations, 'load_data', lambda *_: data)
    monkeypatch.setattr(ablations, 'dataset_identity', lambda *_: 'test-data')
    monkeypatch.setattr(ablations, 'load_classifier', lambda *_: (None, metadata))
    ablations.train(tmp_path)
    for mode in ('no_weather', 'no_history', 'no_quake'):
        classifier, meta = load_classifier(tmp_path/'models'/mode)
        assert meta['removed_features']
        p = classifier.predict_proba(x)
        assert np.isfinite(p).all() and p[1] > p[0]

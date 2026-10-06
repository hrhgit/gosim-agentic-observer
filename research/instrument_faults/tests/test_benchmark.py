import json

import numpy as np
import pytest

from research.instrument_faults import benchmark, feedback_only
from research.instrument_faults.tests.test_simulation import toy
from research.observing_conditions.features import CONTEXT_NAMES, CANDIDATE_NAMES


def test_independent_model_finishes_without_weather_and_test_stays_locked(tmp_path, monkeypatch):
    data = tmp_path/'data'; output = tmp_path/'model'; data.mkdir()
    entries = [dict(id=f'scene-{split}', card='a', split=split)
               for split in ('train', 'validation', 'calibration', 'test', 'external')]
    (data/'manifest.json').write_text(json.dumps(dict(entries=entries, source='unit-test synthetic scenes')))
    scene, rows, physics, _ = toy()
    physics['environment'] = np.ones(len(rows))
    monkeypatch.setattr(feedback_only, 'load_assets', lambda *_: (scene, rows, physics))
    for entry in entries:
        directory = data/'scenarios'/entry['id']; directory.mkdir(parents=True)
        (directory/'prepared.json').write_text('{}')
        for track in (0, 1):
            np.savez(directory/f'weather-input-{track}.npz', context=np.zeros((len(rows), len(CONTEXT_NAMES))),
                     candidate=np.ones((len(rows), 1, len(CANDIDATE_NAMES))), counts=np.ones(len(rows), int))
    monkeypatch.setattr(benchmark, 'GRIDS', dict(logistic=[dict(C=1)],
        boosting=[dict(learning_rate=.05, max_leaf_nodes=15, l2_regularization=1)]))
    original = benchmark.prepare_scene; seen_test = []

    def guarded(*args):
        if args[2]['split'] in ('test', 'external'):
            assert (output/'selection.json').exists()
            assert json.loads((output/'selection.json').read_text())['frozen']
            seen_test.append(args[2]['split'])
        return original(*args)

    monkeypatch.setattr(benchmark, 'prepare_scene', guarded)
    result = benchmark.run(data, output)
    assert result['completed'] and result['weather_predictions_used'] is False
    assert seen_test == ['test', 'external']
    assert not (data/'weather_dependency.json').exists()
    assert result['tests']['test']['logistic']['classification']['rows'] == 24
    assert json.loads((output/'label_audit.json').read_text())['samples'] == 200
    from research.instrument_faults.tests.test_feedback_only import record
    state = feedback_only.FeedbackEvidence(scene.score_config)
    predictor = benchmark.Predictor(output/'models/selected')
    assert predictor.predict(state, '2026-01-01T00:00:00Z')['fault_probability'] is None
    state.observation(record())
    public = dict(issued_at_utc='2026-01-01T00:00:00Z', observe_index=0,
                  context=np.zeros(len(CONTEXT_NAMES)), candidates=np.ones((1, len(CANDIDATE_NAMES))))
    prediction = predictor.predict(state, '2026-01-01T00:01:00Z', public)
    assert 0 <= prediction['fault_probability'] <= 1 and prediction['versions']['weather_model'] is None
    with pytest.raises(ValueError, match='precede'):
        predictor.predict(state, '2026-01-01T00:01:00Z', public | dict(issued_at_utc='2026-01-01T00:01:00Z'))
    before = (output/'report.json').read_bytes()
    assert benchmark.run(data, output)['completed']
    assert (output/'report.json').read_bytes() == before


def test_paired_comparison_uses_identical_rows_and_scene_units(tmp_path):
    y = np.array([0, 1, 0, 1]); ids = np.array(['a/0/0', 'a/1/0', 'b/0/0', 'b/1/0'])
    scene = np.array(['a', 'a', 'b', 'b'])
    np.savez(tmp_path/'old.npz', y=y, row_id=ids, scene=scene, p=np.full(4, .5))
    np.savez(tmp_path/'new.npz', y=y, row_id=ids, scene=scene, p=np.array([.1, .9, .1, .9]))
    result = benchmark.paired_difference(tmp_path/'old.npz', tmp_path/'new.npz')
    assert result['brier']['scenes'] == 2 and result['brier']['ci95'] == pytest.approx([.24, .24])
    assert result['accuracy']['improvement'] == pytest.approx(.5)
    assert result['brier']['evidence_of_improvement']
    np.savez(tmp_path/'wrong.npz', y=y, row_id=ids[::-1], scene=scene, p=np.full(4, .5))
    with pytest.raises(ValueError):
        benchmark.paired_difference(tmp_path/'old.npz', tmp_path/'wrong.npz')


def test_weather_arm_only_adds_weather_fields_to_identical_base(tmp_path):
    n = 6; directory = tmp_path/'base'/'inputs'/'s'; directory.mkdir(parents=True)
    x = np.arange(n*len(benchmark.BASE_NAMES), dtype=np.float32).reshape(n, -1)
    y = np.tile([0, 1], 3)
    np.savez(directory/'features.npz', x=x, y=y, valid=np.ones(n, bool), track=[0, 0, 0, 1, 1, 1], observe_index=[0, 1, 2, 0, 1, 2])
    (directory/'complete.json').write_text(json.dumps(dict(sha256=benchmark.digest(directory/'features.npz'))))
    weather = tmp_path/'weather'; scene = weather/'scenarios'/'s'; scene.mkdir(parents=True)
    wx = np.full((n, len(benchmark.WEATHER_FEATURE_NAMES)), .7, np.float32)
    np.savez(scene/'features.npz', x=wx, y=y)
    (weather/'weather_dependency.json').write_text(json.dumps(dict(identity='fixed-weather')))
    (scene/'features_complete.json').write_text(json.dumps(dict(features_sha256=benchmark.digest(scene/'features.npz'),
        feature_names=benchmark.WEATHER_FEATURE_NAMES, weather_version='fixed-weather')))
    entry = dict(id='s', split='train', card='a')
    a = benchmark.load_data(tmp_path/'base', [entry]); b = benchmark.load_data(tmp_path/'base', [entry], weather)
    assert np.array_equal(a['x'], b['x'][:, :len(benchmark.BASE_NAMES)])
    assert np.array_equal(a['y'], b['y']) and np.array_equal(a['row_id'], b['row_id'])
    assert np.all(b['x'][:, len(benchmark.BASE_NAMES):] == .7)
    np.savez(scene/'features.npz', x=wx, y=1-y)
    marker = json.loads((scene/'features_complete.json').read_text()); marker['features_sha256'] = benchmark.digest(scene/'features.npz')
    (scene/'features_complete.json').write_text(json.dumps(marker))
    with pytest.raises(ValueError, match='identical'):
        benchmark.load_data(tmp_path/'base', [entry], weather)

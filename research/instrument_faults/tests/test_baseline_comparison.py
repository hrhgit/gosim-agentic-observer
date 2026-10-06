from datetime import timedelta
from types import SimpleNamespace

import numpy as np
from challenge.v4_scorer import ScoreEvent, SlotTruth
from research.instrument_faults import baseline_comparison as comparison
from research.instrument_faults.data import read_public
from research.instrument_faults.simulation import simulate
from research.observing_conditions.features import utc


def toy():
    start = utc('2026-01-01T00:00:00Z'); site, _, score, _, _ = read_public('a')
    fault = ScoreEvent('fault', 'instrument_fault', start, start+timedelta(hours=1), 'GLOBAL', None, None, None, None, 1, 1, 1, .5, False)
    slot = SlotTruth('n1-S1', start, start+timedelta(hours=1), True, 1, 1, 1, .5)
    scene = SimpleNamespace(slots=[slot], events=[fault], stress_enabled=False, site=site, score_config=score,
                            targets=[{'target_id': 't', 'science_weight': 1., 'feature_flux': 1., 'ra_deg': 0., 'dec_deg': 0., 'required': False}],
                            survey_start=start, survey_end=start+timedelta(hours=1), bulletins=[], forecasts=[])
    rows = [dict(observe_index=i, start=(start+timedelta(seconds=60*i)).isoformat(), end=(start+timedelta(seconds=60*(i+1))).isoformat(),
                 duration=60, requested=60, program='DARK', night='n1', night_date='2026-01-01', altitude=80, azimuth=45*i,
                 assignments={'0': 't'}, target_indices=[0]) for i in range(12)]
    physics = {'pointers': np.arange(13), 'raw': np.tile([0, .5], (12, 1)), 'repaired': np.tile([0, 1.], (12, 1)),
               'band': np.ones(12), 'hit': np.ones(12, bool)}
    return scene, rows, physics


class CertainFault:
    def predict_proba(self, x): return np.ones(len(x))


def test_engine_reproduces_the_existing_simulator(tmp_path):
    assets = toy()
    predictions = np.tile([1, .8, 1, 1.2, 1, 0, 1, 0, 0], (12, 1, 1))
    for policy in ('none', 'oracle'):
        reference = simulate(tmp_path, 0, predictions, 'w', policy, assets=assets)[3]
        own = comparison.closed_loop(assets, policy)
        assert abs(own['total']-reference['total']) < 1e-9 and own['correct'] == reference['correct']
        assert own['event_opportunities'] == reference['event_opportunities']


def test_model_policy_decides_once_per_three_usable_exposures():
    assets = toy()
    extra = np.zeros((12, len(comparison.benchmark.BASE_NAMES)-comparison.N_FEEDBACK), np.float32)
    none = comparison.closed_loop(assets, 'none')
    judge = comparison.ModelJudge(assets[0], CertainFault(), (.5, .9), extra)
    result = comparison.closed_loop(assets, 'model', judge, none['event_opportunities'])
    # Same counts as the existing simulator's always-certain classifier: it keeps reporting after the repair.
    assert result['correct'] == 1 and result['false'] == 3 and result['paid_false'] == 1
    assert result['components']['science'] > none['components']['science']
    assert result['events'][0]['delay_exposures'] == 3 and [free for _, free in judge.decisions] == [True, True, True, False]


def test_baseline_judge_runs_the_unmodified_estimator_on_a_fixed_schedule():
    assets = toy()
    shapes = comparison.geometry(assets[0], assets[1])
    result = comparison.closed_loop(assets, 'baseline', comparison.BaselineJudge(assets[0], assets[1], shapes))
    assert result['paid_false'] == 0 and len(shapes) == 12 and all(len(s[3]) == 1 for s in shapes)


def test_window_variant_changes_only_the_drop_reference():
    _, original = comparison.baseline_sky()
    _, widened = comparison.baseline_sky(7200.0)
    score = toy()[0].score_config
    item = (0, 0, 0, .5, 1.0, 1.0, 70.0, 0.0, True, 1.0)
    beliefs = []
    for sky in (original(score), widened(score)):
        for t0, throughput in ((0.0, 1.0), (5400.0, .5)):
            rec = dict(t0=t0, teff=600.0, program='DARK', items=[item], night=1, clear_dirs=True, notices=frozenset())
            sky.update(rec, {0: .5*throughput*sky.mult['DARK']}, 1)
        beliefs.append(sky.suspect)
    assert beliefs == [False, True]


def test_scene_counts_weigh_scenes_equally():
    y = np.array([1, 1, 0, 0]); flag = np.array([True, False, True, False])
    counts = comparison.confusion(y, flag)
    assert counts == dict(tp=.25, fn=.25, fp=.25, tn=.25)
    rates = comparison.rates([dict(counts=dict(x=counts)), dict(counts=dict(x=comparison.confusion(y, y == 1)))], 'x', 50)
    assert rates['accuracy'] == .75 and rates['recall'] == .75

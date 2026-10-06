from datetime import timedelta
from types import SimpleNamespace

import numpy as np

from research.instrument_faults import baseline_comparison as comparison
from research.instrument_faults.report_model import BAND_NAMES, PAIRS, ReportJudge, band_evidence, select_action_policy
from research.instrument_faults.tests.test_baseline_comparison import toy, CertainFault
from research.observing_conditions.features import utc


def test_band_match_is_identifiable_and_ambiguous_stays_unknown():
    scene, _, _ = toy()
    sky = comparison.baseline_sky()[1](scene.score_config)
    weight = 1.
    item = (0, 0, 0, .5, 1., 1., 80., 0., True, weight)
    rec = dict(program='DARK', items=[item])
    mu = scene.score_config['program']['multipliers']['DARK']
    mismatch = scene.score_config['program']['mismatch_multiplier']
    now = utc('2026-01-01T00:00:00Z')
    clear = dict(zip(BAND_NAMES, band_evidence(rec, {0: mu}, sky, scene.score_config, now, 0)))
    uncertain = dict(zip(BAND_NAMES, band_evidence(rec, {0: mismatch*.2}, sky, scene.score_config, now, 0)))
    assert clear['match_fraction'] == 1 and np.isfinite(clear['weather_lower_bound'])
    assert uncertain['ambiguous_fraction'] == 1 and np.isnan(uncertain['weather_lower_bound'])


def test_false_report_discards_old_evidence_and_sets_cooldown():
    assets = toy(); scenario, rows, _ = assets
    extra = np.zeros((len(rows), len(comparison.benchmark.BASE_NAMES)-comparison.N_FEEDBACK), np.float32)
    judge = ReportJudge(scenario, rows, comparison.geometry(scenario, rows), CertainFault(), (.5, .9), extra)
    judge.state.records = [dict(end=rows[0]['end'])]
    judge.new = {0, 1, 2}
    now = utc(rows[0]['end'])
    judge.reported(dict(record_type='report_result', issued_at_utc=now.isoformat(), correct=False, repaired=False, score_delta=0), now)
    assert judge.state.records == [] and not judge.new
    assert judge.band.sky.cooldown_until == now.timestamp()+8*3600
    assert not judge.band.sky.suspect and judge.band.sky.low_count == 0


def test_resync_invalidates_band_suspicion():
    assets = toy(); scenario, rows, _ = assets
    extra = np.zeros((len(rows), len(comparison.benchmark.BASE_NAMES)-comparison.N_FEEDBACK), np.float32)
    judge = ReportJudge(scenario, rows, comparison.geometry(scenario, rows), None, None, extra)
    judge.band.sky.suspect = True; judge.band.sky.low_count = 5
    now = utc(rows[0]['end'])
    judge.messages([dict(record_type='state_resync', issued_at_utc=now.isoformat(),
        invalidated_window=dict(action_index_start=0, action_index_end_exclusive=1))], now)
    assert not judge.band.sky.suspect and judge.band.sky.low_count == 0


def test_selection_uses_paid_false_cost_and_score_not_classification_accuracy():
    runs = []
    for pair, total, false, paid in zip(PAIRS, [1000., 100., 50.], [3, 0, 0], [1, 0, 0]):
        runs.append(dict(model='new_model', thresholds=list(pair), scene='s', track=0,
                         total=total, false=false, paid_false=paid, correct=0,
                         components={'report_settlement': -150.*paid}, events=[], reports=[]))
    chosen, candidates = select_action_policy(runs)
    assert chosen['thresholds'] == list(PAIRS[1])
    assert chosen['meets_false_report_targets']
    assert not candidates[0]['meets_false_report_targets']

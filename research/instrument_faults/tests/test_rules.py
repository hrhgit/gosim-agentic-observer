import numpy as np

from research.instrument_faults import benchmark
from research.instrument_faults.rules import INDEX, WEATHER_COLUMNS, decisions, rates


def test_public_rules_evidence_and_notices():
    x = np.zeros((4, len(benchmark.BASE_NAMES)), np.float32)
    x[:, INDEX['last3:count']] = 3
    x[:, INDEX['usable_targets']] = 10
    x[:, INDEX['last3:response_median:mean']] = .3
    x[:, INDEX['last30:response_median:mean']] = 1
    x[:, INDEX['last30:count']] = 10
    x[:, INDEX['quake_nights']] = np.nan
    x[0, INDEX['last3:count']] = 2
    x[1, INDEX['quake_nights']] = 1
    x[2, WEATHER_COLUMNS[0]] = 1
    predicted, enough = decisions(x, dict(family='guarded_low3', threshold=.5))
    assert predicted.tolist() == [False, False, False, True]
    assert enough.tolist() == [False, True, True, True]
    predicted, _ = decisions(x, dict(family='drop', threshold=.7))
    assert predicted.tolist() == [False, True, True, True]
    x[3, INDEX['last30:response_median:mean']] = 0
    predicted, enough = decisions(x, dict(family='drop', threshold=.7))
    assert not enough[3] and not predicted[3]


def test_binary_rule_metrics_match_existing_comparison():
    y = np.array([0, 0, 1, 1]); w = np.array([.2, .3, .1, .4])
    predicted = np.array([False, True, False, True])
    expected = benchmark.classifications(dict(y=y, weight=w, scene=np.array(['a']*4)), predicted.astype(float))
    actual = rates(y, predicted, w)
    for name in ('accuracy', 'precision', 'recall', 'false_positive_rate'):
        assert np.isclose(actual[name], expected[name])

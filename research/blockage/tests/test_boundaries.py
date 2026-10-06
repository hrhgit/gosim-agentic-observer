import numpy as np
import pandas as pd

from research.blockage.dataset import PublicState, extract_run, history_features, mean_direction, note_features, ts
from research.blockage.evaluate import reliability, weights
from research.blockage.train import sequences


def test_received_time_and_night_boundaries():
    state = PublicState()
    start = ts("2026-10-02T00:00:00Z")
    future = dict(record_type="bulletin", issued_at_utc="2026-10-02T00:15:00Z", night_id="N20261001", notices=[dict(event_kind="rain", direction="ALL")])
    state.ingest([future], start)
    assert state.features(start, 0, "N20261001")["current_rain_active"] == 0
    state.ingest([future], start + 900)
    assert state.features(start + 900, 0, "N20261001")["current_rain_all"] == 1
    assert state.features(start + 86400, 0, "N20261002")["current_rain_active"] == 0


def test_notes_require_actual_receipt_and_strict_prior_publication():
    state = PublicState()
    note = dict(id="request", terrain=[], utc=dict(issued="2026-10-02T00:00:00Z", windows=[dict(t0="2026-10-02T00:00:00Z", t1="2026-10-03T00:00:00Z", all=True, kind="other", effect="closed", conf="low")]))
    t = ts(note["utc"]["issued"])
    assert not note_features([note], state, t + 60, 50, 0)["note_available"]
    state.received["request"] = t
    assert not note_features([note], state, t, 50, 0)["note_available"]
    f = note_features([note], state, t + 60, 50, 0)
    assert f["note_other_closed"] and f["note_uncertain"]


def test_spherical_direction_wraps_and_history_does_not_mix_runs():
    ra, dec = mean_direction([(359, 0), (1, 0)])
    assert min(ra, 360 - ra) < 1e-6 and abs(dec) < 1e-6
    frame = pd.DataFrame(dict(run=["a", "a", "b", "b"], start=[0, 2, 0, 2], end=[1, 3, 1, 3], zero=[1, 0, 0, 1], f_x=[10, 20, 30, 40]))
    current, past = sequences(frame, ["f_x"])
    assert np.all(past[[0, 2]] == 0)
    assert list(past[1, -1]) == [10, 1, 1]
    assert list(past[3, -1]) == [30, 0, 1]
    changed = frame.copy()
    changed.loc[1, "zero"] = 1
    assert np.array_equal(sequences(changed, ["f_x"])[1][1], past[1])


def test_reliability_includes_zero_and_one_and_all_bins():
    r = reliability(np.array([0] * 50 + [1] * 50), np.array([0] * 50 + [1] * 50), np.ones(100))
    assert len(r["bin_details"]) == 10 and r["passes"]
    assert r["bin_details"][9]["count"] == 50


def test_source_weights_and_missing_history_flags():
    frame = pd.DataFrame(dict(card=["a1"] * 4, source=["baseline", "baseline", "probe", "probe"], f_duration=[60, 180, 60, 60]))
    w = weights(frame)
    assert w[1] == 3 * w[0] and w[:2].sum() == w[2:].sum()
    assert history_features([], 100, 50, 0)["history_insufficient"] == 1


def test_result_request_messages_cannot_explain_its_own_exposure(tmp_path):
    """Self-generated tiny protocol fixture, not an official practice card."""
    import json
    initialize = dict(task_card=dict(card_id="A"), site=dict(latitude_deg=-24.6, longitude_deg=-70.4),
                      survey=dict(start_utc="2026-10-02T00:00:00Z", end_utc="2026-10-03T00:00:00Z", nights=[dict(night_id="N20261001", observing_start_utc="2026-10-02T00:00:00Z", observing_end_utc="2026-10-02T09:00:00Z")]))
    first = dict(now_utc="2026-10-02T00:00:00Z", last_result=None, new_messages=[])
    second = dict(now_utc="2026-10-02T00:01:00Z", last_result=dict(action="observe", hits=[dict(target_id="self-generated", score=0)]),
                  new_messages=[dict(record_type="bulletin", night_id="N20261001", issued_at_utc="2026-10-02T00:00:00Z", notices=[dict(event_kind="rain", direction="ALL")])])
    run = tmp_path / "self-generated-run"
    run.mkdir()
    raw = [dict(message=dict(message_type=k, payload=v)) for k, v in [("initialize", initialize), ("decision_request", first), ("decision_request", second)]]
    (run / "captured.jsonl").write_text("\n".join(json.dumps(r) for r in raw), encoding="utf-8")
    row = extract_run(run, {"self-generated": (0, 0)}, [])[0]
    assert row["zero"] == 1 and row["f_current_rain_active"] == 0
    second["last_result"]["hits"][0]["score"] = 1
    (run / "captured.jsonl").write_text("\n".join(json.dumps(r) for r in raw), encoding="utf-8")
    changed = extract_run(run, {"self-generated": (0, 0)}, [])[0]
    assert changed["zero"] == 0
    assert {k: v for k, v in row.items() if k.startswith("f_")} == {k: v for k, v in changed.items() if k.startswith("f_")}

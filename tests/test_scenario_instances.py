"""Real generator / official scorer checks for the private instance boundary."""
import json
import math
import importlib.util
from pathlib import Path

import pytest

from challenge.challenge_workflow import ChallengeWorkflow
from project_platform.scenario_instances import (
    InstanceError, PANEL_VERSION, POLICIES, acceptance_reasons, calibrated_score,
    directory_digest, generate_candidate, measure_difficulty,
    prepare_instance, benchmark_policy,
)

TEMPLATE = Path(__file__).resolve().parents[1] / "archive/starter_kit_v3/scenarios/finals-preview"


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    root = tmp_path_factory.mktemp("instances")
    first, same, different = (root / name for name in ("first", "same", "different"))
    a = generate_candidate(TEMPLATE, first, seed="a1" * 32, candidate=0)
    b = generate_candidate(TEMPLATE, same, seed="a1" * 32, candidate=0)
    c = generate_candidate(TEMPLATE, different, seed="b2" * 32, candidate=0)
    return first, same, different, a, b, c


def test_reproducible_new_weather_and_fixed_public_task(generated):
    first, same, different, a, b, c = generated
    assert a == b and a["instance_digest"] != c["instance_digest"]
    assert a["template_digest"] == directory_digest(TEMPLATE)
    for name in ("weather.csv", "weather_forecasts.csv", "weather_events.csv", "tile_anomalies.csv"):
        path = Path("outputs/reference") / name
        assert (first / path).read_bytes() == (same / path).read_bytes()
        assert (first / path).read_bytes() != (different / path).read_bytes()
    for name in ("tiles.csv", "targets.csv", "slots.csv", "tile_windows.csv", "observation_requests.csv"):
        path = Path("outputs/reference") / name
        assert (first / path).read_bytes() == (TEMPLATE / path).read_bytes()
    initial = ChallengeWorkflow(first).initial_publication()
    assert initial == ChallengeWorkflow(different).initial_publication()
    assert initial == ChallengeWorkflow(TEMPLATE).initial_publication()
    text = json.dumps(initial)
    assert a["seed"] not in text and '"seed"' not in text
    assert "weather_events" not in text and "tile_anomalies" not in text


def test_generation_never_overwrites_and_retries_have_stable_candidate(generated, tmp_path):
    first, _, _, a, _, _ = generated
    before = directory_digest(first)
    with pytest.raises(FileExistsError):
        generate_candidate(TEMPLATE, first, seed="a1" * 32, candidate=0)
    assert directory_digest(first) == before
    next_candidate = generate_candidate(TEMPLATE, tmp_path / "next", seed="a1" * 32, candidate=1)
    assert next_candidate["instance_digest"] != a["instance_digest"]
    with pytest.raises(InstanceError):
        generate_candidate(TEMPLATE, tmp_path / "bad", seed="public-team-id", candidate=0)
    assert not (tmp_path / "bad").exists()


def test_geometry_reuse_preserves_exact_official_score(generated):
    for scenario in generated[:3]:
        assert benchmark_policy(scenario, 'gain_rate') == benchmark_policy(scenario, 'gain_rate', reuse_windows=False)
        result = ChallengeWorkflow(scenario, clock=lambda: 0).run(lambda _snapshot, _deadline: {'action': 'wait'})
        assert benchmark_policy(scenario, 'wait')['score'] == result['score_report']['score']['total']


def test_geometry_cache_reuses_an_entire_180_night_template(monkeypatch):
    from collections import OrderedDict
    from datetime import date, timedelta
    from types import SimpleNamespace
    import project_platform.scenario_instances as instances
    monkeypatch.setattr(instances, '_WINDOW_CACHE', OrderedDict())
    calls=[]
    def geometry(night, days):
        calls.append((night,days))
        return [{'night':night.isoformat(),'tile_id':'T00001'}]
    workflow=SimpleNamespace(scorer=SimpleNamespace(geometry=SimpleNamespace(get_tile_windows=geometry)))
    instances._reuse_geometry_windows(workflow,TEMPLATE)
    nights=[date(2026,10,5)+timedelta(days=i) for i in range(180)]
    for _policy in range(3):
        for night in nights:
            rows=workflow.scorer.geometry.get_tile_windows(night,1)
            assert rows==[{'night':night.isoformat(),'tile_id':'T00001'}]
            rows[0]['tile_id']='caller mutation'
    # Every reference policy must reuse earlier geometry, with detached rows.
    # A cache smaller than one scenario thrashes on each sequential policy pass.
    assert len(calls)==len(nights)


def test_real_panel_and_negative_raw_score_calibration(generated):
    difficulty = measure_difficulty(generated[0])
    assert difficulty["wait_score"] < 0
    assert calibrated_score(difficulty["wait_score"], difficulty) == 0
    assert calibrated_score(difficulty["reference_score"], difficulty) == 10000
    values = [calibrated_score(difficulty["policies"][n]["score"], difficulty) for n in POLICIES]
    assert sum(values) / len(values) == pytest.approx(10000, abs=1e-5)
    assert calibrated_score(difficulty["reference_score"] + 1, difficulty) > 10000
    for bad in (math.nan, math.inf, -math.inf):
        with pytest.raises(InstanceError):
            calibrated_score(bad, difficulty)
    with pytest.raises(InstanceError):
        calibrated_score(1, {**difficulty, "span": 0})
    profile = {"panel_version": PANEL_VERSION, "bounds": {
        "span": [difficulty["span"] - 1, difficulty["span"] + 1], "open_fraction": [0, 1],
        **{name: [-1e9, 1e9] for name in POLICIES}}}
    assert acceptance_reasons(difficulty, profile) == []
    profile["bounds"]["span"] = [0, difficulty["span"] - 1]
    assert acceptance_reasons(difficulty, profile) == ["span"]
    del profile["bounds"]["gain_rate"]
    with pytest.raises(InstanceError, match="incomplete"):
        acceptance_reasons(difficulty, profile)


def test_outliers_are_skipped_deterministically_and_never_silently_served(tmp_path, monkeypatch):
    difficulty = {"panel_version": PANEL_VERSION, "span": 100, "wait_score": -20,
                  "reference_score": 80, "open_fraction": 0.9,
                  "policies": {p: {"score": 80} for p in POLICIES}}
    profile = {"schema_version": "observer-calibration-profile-v1", "panel_version": PANEL_VERSION,
               "template_digest": directory_digest(TEMPLATE), "bounds": {
                   "span": [90, 110], "open_fraction": [0.8, 1], **{p: [9000, 11000] for p in POLICIES}}}
    monkeypatch.setattr('project_platform.scenario_instances.measure_difficulty',
        lambda path: {**difficulty, "open_fraction": 0.5} if path.name == 'candidate-00' else difficulty)
    first, record = prepare_instance(TEMPLATE, tmp_path / 'first', seed='ab' * 32, profile=profile)
    again, repeated = prepare_instance(TEMPLATE, tmp_path / 'again', seed='ab' * 32, profile=profile)
    assert record == repeated and record['candidate'] == 1
    assert directory_digest(first) == directory_digest(again) == record['instance_digest']
    assert record['rejected_candidates'][0]['reasons'] == ['open_fraction']
    with pytest.raises(InstanceError, match='no_comparable_scenario'):
        prepare_instance(TEMPLATE, tmp_path / 'failed', seed='ab' * 32, profile=profile, max_candidates=1)
    assert list((tmp_path / 'failed').iterdir()) == []
    with pytest.raises(InstanceError, match='template_mismatch'):
        prepare_instance(TEMPLATE, tmp_path / 'wrong', seed='ab' * 32,
                         profile={**profile, 'template_digest': '0' * 64})
    assert not (tmp_path / 'wrong').exists()


def test_organizer_replay_verifies_generated_files_calibration_and_committed_csv(generated, tmp_path):
    script = TEMPLATE.parents[3] / 'scripts/replay-observer-instance.py'
    spec = importlib.util.spec_from_file_location('replay_instance', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    scenario, _, _, generation, _, _ = generated
    record = {**generation, 'difficulty': measure_difficulty(scenario)}
    workflow = ChallengeWorkflow(scenario, clock=lambda: 0)
    result = workflow.run(lambda _snapshot, _deadline: {'action': 'wait'})
    workflow.write_outputs(tmp_path / 'result', result)
    verified = module.replay(TEMPLATE, record, tmp_path / 'result/decisions.csv')
    assert verified == {'instance_commitment': record['instance_digest'],
                        'raw_score': result['score_report']['score']['total'],
                        'calibrated_score': 0.0, 'verified': True}
    with pytest.raises(InstanceError, match='digest_mismatch'):
        module.replay(TEMPLATE, {**record, 'instance_digest': '0' * 64}, tmp_path / 'result/decisions.csv')


def test_bounded_preparation_reproduces_selection_and_stops_without_leaking_inputs(tmp_path, capsys):
    from project_platform.scenario_job import prepare_bounded
    profile = {'schema_version': 'observer-calibration-profile-v1', 'panel_version': PANEL_VERSION,
               'template_digest': directory_digest(TEMPLATE), 'bounds': {
                   'span': [1, 1e9], 'open_fraction': [0, 1], **{p: [-1e9, 1e9] for p in POLICIES}}}
    arguments = {'seed': 'a1' * 32, 'profile': profile, 'max_candidates': 1}
    direct, expected = prepare_instance(TEMPLATE, tmp_path/'direct', **arguments)
    child, actual = prepare_bounded(TEMPLATE, tmp_path/'child', **arguments)
    assert actual == expected and directory_digest(child) == directory_digest(direct)
    with pytest.raises(InstanceError, match='scenario_preparation_timeout'):
        prepare_bounded(TEMPLATE, tmp_path/'expired', **arguments, timeout_seconds=0.001)
    with pytest.raises(InstanceError, match='scenario_preparation_failed'):
        prepare_bounded(TEMPLATE, tmp_path/'invalid', **{**arguments, 'seed': 'invalid-secret-value'})
    assert capsys.readouterr() == ('', '')


def test_parallel_difficulty_matches_the_sequential_measurement(tmp_path):
    scenario = tmp_path / 'candidate'
    generate_candidate(TEMPLATE, scenario, seed='b' * 64, candidate=0)
    assert measure_difficulty(scenario, workers=4) == measure_difficulty(scenario, workers=1)


def test_panel_never_repeats_completed_tiles_without_anomaly_mechanics():
    # Playground scenarios (no anomaly mechanics) reject repeat observations of a
    # completed tile. The panel must not offer them, or it scores below waiting
    # and the scenario cannot be calibrated at all (invalid_calibration_span).
    pre_anomaly = Path(__file__).resolve().parents[1] / "archive/starter_kit_v3/scenarios/demo-week"
    assert not ChallengeWorkflow(pre_anomaly).scorer.mechanics
    wait = benchmark_policy(pre_anomaly, "wait")["score"]
    assert all(benchmark_policy(pre_anomaly, name)["score"] > wait for name in POLICIES)
    assert measure_difficulty(pre_anomaly, workers=1)["span"] > 0

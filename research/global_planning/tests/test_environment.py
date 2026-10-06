import copy
import json
import math
from pathlib import Path

import pytest

from research.global_planning.common import ROOT, verify_engine
from research.global_planning.data import micro_entries, formal_entries, build_micro
from research.global_planning.environment import OfficialEnvironment, rewards_from_episode, returns_to_go
from research.global_planning.rollout import episode


def test_engine_source_and_whole_scene_splits():
    assert len(verify_engine()) == 64
    entries = micro_entries() + formal_entries()
    assert len({e['seed'] for e in entries}) == len(entries)
    assert len({e['id'] for e in entries}) == len(entries)
    assert {s: sum(e['split'] == s for e in micro_entries()) for s in ('train','validation','test')} == {'train':32,'validation':8,'test':16}


def test_rewards_include_negative_revocations_and_terminal_settlement():
    snapshots = [{'running_total': n} for n in [0, 12, 25, 4]]
    report = {'termination': {'reason': 'survey_complete'}, 'total': -33.123456}
    rewards = rewards_from_episode(snapshots, report)
    assert rewards[:3] == [12, 13, -21]
    assert math.fsum(rewards) == pytest.approx(report['total'], abs=1e-6)
    assert returns_to_go(rewards)[0] == pytest.approx(report['total'])
    report['termination']['reason'] = 'agent_error'
    with pytest.raises(ValueError): rewards_from_episode(snapshots, report)


def test_official_best_contribution_and_request_revision_after_data_loss():
    from datetime import datetime, timedelta, timezone
    from challenge.v4_scorer import BestLedger, observation_request_status, required_penalty, uniformity_penalty
    from challenge.v4_runner import _request_result
    start = datetime(2026, 6, 1, tzinfo=timezone.utc)
    ledger = BestLedger()
    ledger.record(0, 'T0', .4, 4, start, start + timedelta(seconds=60))
    ledger.record(1, 'T0', .8, 8, start + timedelta(seconds=60), start + timedelta(seconds=120))
    # Better science and larger completion factor need not come from the same exposure.
    ledger.record(2, 'T0', .6, 9, start + timedelta(seconds=120), start + timedelta(seconds=180))
    assert ledger.best()['T0'] == (.6, 9)
    assert ledger.max_factors()['T0'] == .8
    request = dict(request_id='R', issued_at_utc=start, deadline_utc=start + timedelta(seconds=120),
                   target_ids=['T0'], minimum_completed=1, completion_factor_threshold=.5, completion_reward=30)
    assert observation_request_status(request, ledger)['reward'] == 30
    assert _request_result(request, ledger, request['deadline_utc'], False)['score_delta'] == 30
    assert ledger.invalidate_window(1, 3, 'SELF-GENERATED-loss') == 2
    assert ledger.best()['T0'] == (.4, 4)
    assert _request_result(request, ledger, start + timedelta(seconds=180), True, 30)['score_delta'] == -30
    assert ledger.invalidate_window(1, 3, 'repeat-loss') == 0
    targets = [dict(target_id='T0', required=True, ra_deg=10), dict(target_id='T1', required=False, ra_deg=100)]
    scoring = json.loads((ROOT/'cards/a/config/v4_score_config.json').read_text())
    missing, penalty = required_penalty(targets, ledger.max_factors(), scoring)
    assert missing == 1 and penalty == scoring['required']['penalty_per_missing']
    _, uneven = uniformity_penalty(targets, ledger.max_factors(), scoring)
    assert uneven == scoring['uniformity']['weight']
    total = sum(score for _, score in ledger.best().values()) - penalty - uneven
    reward = rewards_from_episode([{'running_total': n} for n in (0, 4, 8, 9, 4)],
                                 dict(total=total, termination=dict(reason='survey_complete')))
    assert math.fsum(reward) == pytest.approx(total, abs=1e-6)


def test_original_runner_waits_and_false_reports(tmp_path):
    bundle = build_micro(micro_entries()[0], tmp_path/'bundle')
    env = OfficialEnvironment(bundle, tmp_path/'run')
    try:
        _, event = env.reset()
        snapshots = []
        for _ in range(3):
            snapshots.append(event['payload'])
            event = env.step({'action':'report'})
        while event['type'] != 'done':
            snapshots.append(event['payload'])
            event = env.step({'action':'wait','duration_seconds':60})
        assert event['report']['components']['report_settlement'] == -150
        assert 'organizer_only' not in event['report']
        assert abs(sum(rewards_from_episode(snapshots,event['report']))-event['report']['total']) < 1e-6
    finally: env.close()


@pytest.mark.parametrize('entry_index', range(8))
def test_candidate_teacher_preserves_rule_actions_and_score(tmp_path, entry_index):
    bundle = build_micro(micro_entries()[entry_index], tmp_path/'bundle')
    rule = episode(bundle,tmp_path/'rule',models=False,rule_only=True)
    candidates = episode(bundle,tmp_path/'candidate',models=False)
    assert candidates['choices']
    assert candidates['actions'] == rule['actions']
    assert candidates['total'] == rule['total']
    assert abs(candidates['reward_sum']-candidates['total']) < 1e-6


def test_future_weather_requests_bulletins_do_not_change_current_inputs(tmp_path):
    import shutil
    from research.global_planning.features import encode
    a = build_micro(micro_entries()[6], tmp_path/'a')
    b = tmp_path/'b'; shutil.copytree(a,b)
    rows = (b/'truth/v4_weather_truth.csv').read_text().splitlines()
    # Only change the final weather slot, after the first learned decision.
    fields = rows[-1].split(','); fields[3] = '0.13'; rows[-1] = ','.join(fields)
    (b/'truth/v4_weather_truth.csv').write_text('\n'.join(rows)+'\n')
    req_path = b/'truth/v4_observation_requests.jsonl'
    req=json.loads(req_path.read_text());req['completion_reward']=9999;req_path.write_text(json.dumps(req)+'\n')
    (b/'public/v4_bulletins.jsonl').write_text(json.dumps(dict(record_type='bulletin',
        issued_at_utc=req['issued_at_utc'], events=[], source='SELF-GENERATED future boundary test'))+'\n')
    left=episode(a,tmp_path/'left',models=False)
    right=episode(b,tmp_path/'right',models=False)
    # The pending request is issued at +120s, first learned decision is +60s.
    lx,lc=encode(left['choices'][0]['frame']);rx,rc=encode(right['choices'][0]['frame'])
    import numpy as np
    np.testing.assert_array_equal(lx,rx);np.testing.assert_array_equal(lc,rc)

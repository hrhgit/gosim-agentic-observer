"""Random private scenario -> real Edge rounds -> calibrated, replayable result."""
import concurrent.futures
import json
import os
from pathlib import Path
import secrets
import time
import uuid

import pytest
from psycopg.types.json import Jsonb

from challenge.scoring_core import score_files
from project_platform.scenario_instances import directory_digest, prepare_instance
from project_platform.session import SessionClient, SessionError, wait_until
from project_platform.trusted_engine import run_session, result_summary
from test_project_database import identity, query, rpc
from test_project_http import edge_stack  # noqa: F401

pytestmark = pytest.mark.skipif(not os.environ.get('OBSERVER_DENO_BIN') or not os.environ.get('SAC_POSTGREST_BIN'),
                               reason='Local Edge toolchain required')
TEMPLATE = Path(__file__).resolve().parents[1] / 'archive/starter_kit_v3/scenarios/finals-preview'


def test_private_instance_rounds_and_official_replay(edge_stack, tmp_path):
    uri = edge_stack['harness'].db_uri
    user, team = identity(uri)
    phase, scenario_id, profile_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    query(uri, "insert into public.phases(id,slug,name_en,name_zh) values(%s,%s,'Study','Study')", (phase, str(phase)))
    query(uri, "insert into public.scenarios(id,slug,name) values(%s,%s,'Study')", (scenario_id, str(scenario_id)))
    query(uri, 'insert into public.phase_scenarios values(%s,%s)', (phase, scenario_id))
    query(uri, 'insert into public.observer_phase_settings(phase_id,local_sessions_enabled,runtime_seconds) values(%s,true,120)', (phase,))
    query(uri, "insert into private.observer_scenario_bundles values(%s,'fixture/template.zip',%s)", (scenario_id, 'a' * 64))
    profile = {'schema_version': 'observer-calibration-profile-v1', 'panel_version': 'observer-reference-panel-v1',
               'template_digest': directory_digest(TEMPLATE), 'bounds': {'span': [1, 1e9], 'open_fraction': [0, 1],
                 **{p: [-1e9, 1e9] for p in ('gain_rate', 'required_first', 'requests_first')}}}
    query(uri, 'insert into private.observer_calibration_profiles(id,scenario_id,bundle_digest,profile) values(%s,%s,%s,%s)',
          (profile_id, scenario_id, 'a' * 64, Jsonb(profile)))
    query(uri, 'insert into private.observer_scenario_calibration values(%s,%s,%s)', (phase, scenario_id, profile_id))
    batch = rpc(uri, 'observer_create_batch', phase, None, role='authenticated', user=user)
    run = query(uri, 'select id from public.observer_runs where batch_id=%s', (batch,))[0][0]
    ptoken, etoken = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    rpc(uri, 'observer_open_session', run, ptoken, etoken)
    url = edge_stack['urls']['observer-session']
    engine, participant = SessionClient(url, f'obs_{run}.{etoken}'), SessionClient(url, f'obs_{run}.{ptoken}')
    specification = rpc(uri, 'observer_instance_input', run)
    scenario, record = prepare_instance(TEMPLATE, tmp_path / 'private', seed=specification['seed'], profile=profile)
    with pytest.raises(SessionError, match='instance_not_recorded'):
        engine.call('initialize', publication={'unrecorded': True})
    with pytest.raises(SessionError, match='invalid_or_expired_capability'):
        participant.call('record_instance', record=record)
    engine.call('record_instance', record=record)
    output = tmp_path / 'participant-results'

    def trusted():
        result, digest = run_session(scenario, output, engine, wallclock_seconds=120, instance_record=record)
        engine.call('finish', summary=result_summary(result), decisions_digest=digest, result_path='private/random-study')
        return result, digest

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(trusted)
        initial = wait_until(lambda: participant.call('poll')['publication'], deadline=time.monotonic()+30)
        assert initial['evaluation']['instance_commitment'] == record['instance_digest']
        assert 'seed' not in json.dumps(initial) and specification['seed'] not in json.dumps(initial)
        participant.call('ready')
        sequence = 0
        deadline = time.monotonic() + 120
        while not future.done() and time.monotonic() < deadline:
            try:
                message = participant.call('poll', initialized=True)
            except SessionError as error:
                assert error.code == 'invalid_or_expired_capability'
                break
            if message['observation'] is None or message['sequence'] == sequence:
                time.sleep(0.01)
                continue
            assert message['sequence'] == sequence + 1
            sequence = message['sequence']
            response = {'protocol_version': 'participant-agent-protocol-v2', 'message_type': 'decision_response',
                        'decision_sequence': sequence, 'action': 'wait'}
            if sequence == 1:
                with pytest.raises(SessionError, match='step_not_published'):
                    participant.call('respond', sequence=2, response={**response, 'decision_sequence': 2})
            participant.call('respond', sequence=sequence, response=response)
        result, digest = future.result(timeout=10)
    assert result['termination_reason'] == 'survey_complete'
    assert result['calibration']['adjusted_score'] == 0  # exact all-wait control
    replay = score_files(scenario, output/'decisions.csv', tmp_path/'replayed.json', result['termination_reason'])
    assert replay['score'] == result['score_report']['score']
    serialized = (output/'workflow_result.json').read_text()
    assert specification['seed'] not in serialized and 'rejected_candidates' not in serialized
    assert query(uri, 'select count(*) from private.observer_messages where run_id=%s and committed is not null', (run,)) == [(sequence,)]
    rpc(uri, 'observer_accept_csv', run, user, digest)
    assert query(uri, 'select team_id,score from public.observer_leaderboard(%s)', (phase,), role='anon') == [(team, 0)]

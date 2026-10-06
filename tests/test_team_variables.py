"""Team variables and allowed domains in real PostgreSQL (migration 20261004040000)."""
import uuid

import psycopg
import pytest
from test_project_database import database, setup, session, identity, query, rpc  # noqa: F401

CIPHER = 'v1.' + 'A' * 16 + '.' + 'B' * 40 + '=='


def env(uri, user):
    return rpc(uri, 'observer_team_environment', role='authenticated', user=user)


def save(uri, user, name, *, secret=True, value=None, hint='wxyz', vid=None):
    rpc(uri, 'observer_save_team_variable', user, vid or uuid.uuid4(), name, secret,
        CIPHER if secret else None, None if secret else value, hint if secret else '')


def test_members_manage_variables_and_domains_without_reading_secrets(setup):
    s = setup; uri = s['uri']
    teammate, _ = identity(uri, team=s['team'])
    outsider, _ = identity(uri)
    assert env(uri, s['user'])['variables'] == [] and env(uri, s['user'])['domains'] == []
    save(uri, s['user'], 'KIMI_API_KEY')
    save(uri, s['user'], 'KIMI_MODEL', secret=False, value='k3')
    view = env(uri, teammate)
    assert [(v['name'], v['secret'], v['hint'], v['value']) for v in view['variables']] == [
        ('KIMI_API_KEY', True, 'wxyz', None), ('KIMI_MODEL', False, '', 'k3')]
    assert CIPHER not in str(view) and env(uri, outsider)['variables'] == []
    # Replacing keeps one row per name.
    save(uri, s['user'], 'KIMI_MODEL', secret=False, value='kimi-for-coding')
    assert [v['value'] for v in env(uri, s['user'])['variables'] if v['name'] == 'KIMI_MODEL'] == ['kimi-for-coding']
    # Rules: reserved names, bad names and both/neither values are refused.
    for name in ('OBSERVER_RUN_TOKEN', 'SAC_X', 'HTTPS_PROXY', 'FTP_PROXY', 'PATH', 'lower', '1X', 'A' * 65):
        with pytest.raises(psycopg.Error, match='invalid_team_variable'):
            save(uri, s['user'], name)
    with pytest.raises(psycopg.Error, match='invalid_team_variable'):
        rpc(uri, 'observer_save_team_variable', s['user'], uuid.uuid4(), 'X', True, CIPHER, 'plain', '')
    # Participants cannot write values directly or read the tables.
    with pytest.raises(psycopg.Error, match='permission denied'):
        rpc(uri, 'observer_save_team_variable', s['user'], uuid.uuid4(), 'X', False, None, 'v', '',
            role='authenticated', user=s['user'])
    for table in ('observer_team_variables', 'observer_team_domains'):
        with pytest.raises(psycopg.Error, match='permission denied'):
            query(uri, f'select * from private.{table}', role='authenticated', user=s['user'])
    # At most 20 variables.
    for i in range(18):
        save(uri, s['user'], f'V{i}', secret=False, value='x')
    with pytest.raises(psycopg.Error, match='team_variable_limit'):
        save(uri, s['user'], 'ONE_TOO_MANY', secret=False, value='x')
    assert rpc(uri, 'observer_delete_team_variable', 'V0', role='authenticated', user=teammate) is True
    assert rpc(uri, 'observer_delete_team_variable', 'KIMI_MODEL', role='authenticated', user=outsider) is False
    # Domains: public names only, at most ten, replaced as a set.
    assert rpc(uri, 'observer_set_team_domains', s['user'], ['api.kimi.com', 'api.deepseek.com']) == [
        'api.kimi.com', 'api.deepseek.com']
    assert env(uri, s['user'])['domains'] == ['api.deepseek.com', 'api.kimi.com']
    rpc(uri, 'observer_set_team_domains', s['user'], ['api.kimi.com'])
    assert env(uri, teammate)['domains'] == ['api.kimi.com']
    for bad in (['127.0.0.1'], ['localhost'], ['x.internal'], ['API.KIMI.COM'], ['api.kimi.com:443'],
                ['https://api.kimi.com'], ['a.com', 'a.com'], [f'd{i}.com' for i in range(11)], ['svc.local']):
        with pytest.raises(psycopg.Error, match='invalid_team_domains'):
            rpc(uri, 'observer_set_team_domains', s['user'], bad)


def test_scheduler_reads_a_runs_team_variables_and_domains(setup):
    s = setup; uri = s['uri']
    save(uri, s['user'], 'OPENAI_API_KEY')
    save(uri, s['user'], 'OPENAI_MODEL', secret=False, value='k3')
    rpc(uri, 'observer_set_team_domains', s['user'], ['api.kimi.com'])
    run, _, _ = session(s)
    egress = rpc(uri, 'observer_run_team_egress', run)
    assert egress['domains'] == ['api.kimi.com']
    assert [(v['name'], v['secret'], v['encrypted_value'], v['plain_value']) for v in egress['variables']] == [
        ('OPENAI_API_KEY', True, CIPHER, None), ('OPENAI_MODEL', False, None, 'k3')]
    with pytest.raises(psycopg.Error, match='permission denied'):
        rpc(uri, 'observer_run_team_egress', run, role='authenticated', user=s['user'])
    assert rpc(uri, 'observer_hardening')['team_egress'] is False


def test_backfill_turns_a_saved_model_api_into_variables_and_a_domain(setup):
    s = setup; uri = s['uri']
    provider = uuid.uuid4()
    rpc(uri, 'observer_save_team_model', s['user'], provider, 'https://api.kimi.com/coding/v1', 'k3', CIPHER, 'Qx2z',
        'anthropic')
    assert query(uri, 'select private.observer_backfill_team_variables()')[0][0] == 1
    rows = query(uri, '''select id,name,secret,encrypted_value,plain_value,hint from private.observer_team_variables
        where team_id=%s order by name''', (s['team'],))
    assert [(r[1], r[2], r[3], r[4], r[5]) for r in rows] == [
        ('ANTHROPIC_API_KEY', True, CIPHER, None, 'Qx2z'),
        ('ANTHROPIC_BASE_URL', False, None, 'https://api.kimi.com/coding/v1', ''),
        ('ANTHROPIC_MODEL', False, None, 'k3', '')]
    # The key keeps its ciphertext and the id it is bound to.
    assert rows[0][0] == provider
    assert env(uri, s['user'])['domains'] == ['api.kimi.com']
    # Idempotent: a team that already has variables is left alone.
    assert query(uri, 'select private.observer_backfill_team_variables()')[0][0] == 0


def test_relay_teams_are_told_to_enter_their_key(setup):
    s = setup; uri = s['uri']
    rpc(uri, 'observer_set_team_model_mode', 'relay', role='authenticated', user=s['user'])
    assert env(uri, s['user'])['relay_key_missing'] is True
    save(uri, s['user'], 'OPENAI_API_KEY')
    assert env(uri, s['user'])['relay_key_missing'] is False


def test_pilot_teams_get_team_egress_before_the_global_switch(setup):
    s = setup; uri = s['uri']
    run, _, _ = session(s)
    assert rpc(uri, 'observer_run_team_egress', run)['enabled'] is False
    assert env(uri, s['user'])['enabled'] is False
    query(uri, 'update private.observer_hardening set team_egress_teams=array[%s]::uuid[] where id', (s['team'],))
    assert rpc(uri, 'observer_run_team_egress', run)['enabled'] is True
    assert env(uri, s['user'])['enabled'] is True
    query(uri, "update private.observer_hardening set team_egress_teams='{}', team_egress=true where id")
    assert rpc(uri, 'observer_run_team_egress', run)['enabled'] is True


def test_open_egress_switch_and_pilot_teams(setup):
    s = setup; uri = s['uri']
    run, _, _ = session(s)
    assert rpc(uri, 'observer_run_team_egress', run)['open'] is False
    assert env(uri, s['user'])['open'] is False
    query(uri, 'update private.observer_hardening set open_egress_teams=array[%s]::uuid[] where id', (s['team'],))
    assert rpc(uri, 'observer_run_team_egress', run)['open'] is True
    assert env(uri, s['user'])['open'] is True
    query(uri, "update private.observer_hardening set open_egress_teams='{}', open_egress=true where id")
    assert rpc(uri, 'observer_run_team_egress', run)['open'] is True
    assert rpc(uri, 'observer_hardening')['open_egress'] is True
    # Rollback: one statement.
    query(uri, "update private.observer_hardening set open_egress=false, open_egress_teams='{}' where id")
    assert rpc(uri, 'observer_run_team_egress', run)['open'] is False


def test_job_receipts_store_the_egress_record_for_organizers_and_the_team(setup):
    s = setup; uri = s['uri']
    teammate, _ = identity(uri, team=s['team'])
    outsider, _ = identity(uri)
    run, _, _ = session(s)
    job = uuid.uuid4()
    query(uri, """insert into private.observer_installations(organization,organization_id,installation_id,repository_id,
        approved_sha) values('AGENTIC-OBSERVER26-runner-36','1','1','1',%s) on conflict do nothing""", ('a' * 40,))
    query(uri, """insert into private.observer_jobs(id,kind,run_id,organization,repository_id,organization_id,
        workflow_sha,nonce_hash,encrypted_nonce,encrypted_input,status)
        select %s,'engine',%s,organization,repository_id,organization_id,approved_sha,'h','n','i','claimed'
        from private.observer_installations where organization='AGENTIC-OBSERVER26-runner-36'""", (job, run))
    assert query(uri, 'select count(*) from private.observer_jobs where id=%s', (job,)) == [(1,)]
    import json
    egress = [{"host": "api.kimi.com", "port": 443, "connections": 3, "refused": 0, "bytes_up": 100,
               "bytes_down": 900, "first": "2026-10-04T01:00:00Z", "last": "2026-10-04T01:02:00Z"},
              {"host": "localtest.me", "port": 443, "connections": 0, "refused": 2, "bytes_up": 0, "bytes_down": 0,
               "first": "2026-10-04T01:00:00Z", "last": "2026-10-04T01:00:01Z"},
              {"host": "bad host; drop", "port": 1}, {"host": "x.com", "port": "nan"}, "junk"]
    query(uri, "update private.observer_jobs set result=%s::jsonb, status='succeeded' where id=%s",
          (json.dumps({"run_id": str(run), "egress": egress}), job))
    rows = query(uri, 'select host,port,connections,refused,bytes_up,bytes_down from private.observer_run_egress '
                      'where run_id=%s order by host', (run,))
    assert rows == [('api.kimi.com', 443, 3, 0, 100, 900), ('localtest.me', 443, 0, 2, 0, 0)]
    log = rpc(uri, 'observer_run_egress_log', run, role='authenticated', user=teammate)
    assert [(e['host'], e['bytes_down']) for e in log] == [('api.kimi.com', 900), ('localtest.me', 0)]
    assert rpc(uri, 'observer_run_egress_log', run, role='authenticated', user=outsider) == []
    with pytest.raises(psycopg.Error, match='permission denied'):
        query(uri, 'select * from private.observer_run_egress', role='authenticated', user=s['user'])
    # A receipt without an egress record changes nothing.
    query(uri, "update private.observer_jobs set result='{\"x\":1}'::jsonb where id=%s", (job,))
    assert len(query(uri, 'select 1 from private.observer_run_egress where run_id=%s', (run,))) == 2

"""Evaluations without a model and switched-off team variables (migration 20261005071000)."""
import uuid

import psycopg
import pytest

from test_project_database import database, identity, query, rpc, setup  # noqa: F401
from test_project_eval_ux import revision

CIPHER = 'v1.' + 'A' * 16 + '.' + 'B' * 40 + '=='


def save(uri, user, name, *, secret=True, model=None):
    args = [user, uuid.uuid4(), name, secret, CIPHER if secret else None, None if secret else 'plain-' + name,
            'wxyz' if secret else '']
    if model is not None:
        args.append(model)
    rpc(uri, 'observer_save_team_variable', *args)


def env(uri, user):
    return {v['name']: v for v in rpc(uri, 'observer_team_environment', role='authenticated', user=user)['variables']}


def flags(uri, user, name, model=None, disabled=None):
    return query(uri, 'select public.observer_set_team_variable_flags(%s,%s,%s)', (name, model, disabled),
                 role='authenticated', user=user)[0][0]


def evaluate(s, rev, no_model=None, confirm=True):
    args = (s['phase'], rev, confirm) + (() if no_model is None else (no_model,))
    holes = ','.join(['%s'] * len(args))
    return query(s['uri'], f'select public.observer_create_batch({holes})', args, role='authenticated',
                 user=s['user'])[0][0]


def run_of(uri, batch):
    return query(uri, 'select id from public.observer_runs where batch_id=%s limit 1', (batch,))[0][0]


def names(uri, run):
    return sorted(v['name'] for v in rpc(uri, 'observer_run_team_egress', run)['variables'])


@pytest.fixture
def team(setup):
    s = setup; uri = s['uri']
    for name in ('OPENAI_API_KEY', 'KIMI_API_KEY', 'TELEMETRY_TOKEN', 'GLM_5_3_FLASH_BASE_URL'):
        save(uri, s['user'], name)
    save(uri, s['user'], 'OPENAI_MODEL', secret=False)
    save(uri, s['user'], 'AGENT_LLM_REASONING_EFFORT', secret=False)
    save(uri, s['user'], 'MY_PROVIDER_TOKEN', model=True)      # 「添加模型服务」 / explicit tag
    save(uri, s['user'], 'STRATEGY', secret=False)
    query(uri, 'update public.observer_phase_settings set daily_batches=50,max_active_evaluations=10 where phase_id=%s',
          (s['phase'],))
    return s


def test_model_tag_is_automatic_by_name_explicit_and_kept_on_save(team):
    s = team; uri = s['uri']
    view = env(uri, s['user'])
    tagged = sorted(n for n, v in view.items() if v['model'])
    assert tagged == ['AGENT_LLM_REASONING_EFFORT', 'GLM_5_3_FLASH_BASE_URL', 'KIMI_API_KEY', 'MY_PROVIDER_TOKEN',
                      'OPENAI_API_KEY', 'OPENAI_MODEL']
    assert not any(v['disabled'] for v in view.values())
    # The team changes the tag; saving a new value keeps it (and the switch).
    flags(uri, s['user'], 'TELEMETRY_TOKEN', model=True, disabled=True)
    flags(uri, s['user'], 'OPENAI_MODEL', model=False)
    save(uri, s['user'], 'TELEMETRY_TOKEN')
    save(uri, s['user'], 'OPENAI_MODEL', secret=False)
    view = env(uri, s['user'])
    assert view['TELEMETRY_TOKEN']['model'] and view['TELEMETRY_TOKEN']['disabled']
    assert not view['OPENAI_MODEL']['model']
    # An explicit tag on save wins.
    save(uri, s['user'], 'OPENAI_MODEL', secret=False, model=True)
    assert env(uri, s['user'])['OPENAI_MODEL']['model']


def test_flags_are_the_teams_own(team):
    s = team; uri = s['uri']
    teammate, _ = identity(uri, team=s['team'])
    outsider, _ = identity(uri)
    view = flags(uri, teammate, 'STRATEGY', disabled=True)
    assert [v['disabled'] for v in view['variables'] if v['name'] == 'STRATEGY'] == [True]
    with pytest.raises(psycopg.Error, match='team_variable_not_found'):
        flags(uri, outsider, 'STRATEGY', disabled=False)
    with pytest.raises(psycopg.Error, match='team_variable_not_found'):
        flags(uri, s['user'], 'NOPE', disabled=False)
    with pytest.raises(psycopg.Error, match='permission denied'):
        query(uri, 'select public.observer_set_team_variable_flags(%s,%s,%s)', ('STRATEGY', None, False), role='anon')
    with pytest.raises(psycopg.Error, match='permission denied'):
        query(uri, 'select model,disabled from private.observer_team_variables', role='authenticated', user=s['user'])
    assert query(uri, "select count(*) from public.audit_log where action='observer.team_variable_flags'")[0][0] >= 1


def test_runs_without_a_model_never_get_model_variables_and_disabled_ones_never(team):
    s = team; uri = s['uri']
    rev = revision(s)
    everything = ['AGENT_LLM_REASONING_EFFORT', 'GLM_5_3_FLASH_BASE_URL', 'KIMI_API_KEY', 'MY_PROVIDER_TOKEN',
                  'OPENAI_API_KEY', 'OPENAI_MODEL', 'STRATEGY', 'TELEMETRY_TOKEN']
    normal = run_of(uri, evaluate(s, rev))                       # older callers: 3 arguments
    off = run_of(uri, evaluate(s, rev, no_model=True))
    assert names(uri, normal) == everything
    assert rpc(uri, 'observer_run_team_egress', normal)['model_disabled'] is False
    got = rpc(uri, 'observer_run_team_egress', off)
    assert got['model_disabled'] is True
    assert sorted(v['name'] for v in got['variables']) == ['STRATEGY', 'TELEMETRY_TOKEN']
    assert 'v1.' not in str([v for v in got['variables'] if v['name'] != 'TELEMETRY_TOKEN'])
    # A switched-off variable reaches no run (nor preparation); switching it on again restores it.
    flags(uri, s['user'], 'TELEMETRY_TOKEN', disabled=True)
    flags(uri, s['user'], 'OPENAI_API_KEY', disabled=True)
    assert names(uri, off) == ['STRATEGY']
    assert 'OPENAI_API_KEY' not in names(uri, normal) and 'TELEMETRY_TOKEN' not in names(uri, normal)
    prep = rpc(uri, 'observer_preparation_team_egress', rev)['variables']
    assert {'OPENAI_API_KEY', 'TELEMETRY_TOKEN'}.isdisjoint(v['name'] for v in prep)
    assert 'KIMI_API_KEY' in [v['name'] for v in prep]          # preparation is not an evaluation without a model
    flags(uri, s['user'], 'OPENAI_API_KEY', disabled=False)
    assert 'OPENAI_API_KEY' in names(uri, normal)
    # The values are kept (encrypted) while switched off.
    assert query(uri, "select encrypted_value from private.observer_team_variables where name='TELEMETRY_TOKEN'"
                 " and team_id=%s", (s['team'],)) == [(CIPHER,)]
    # The flag is on the evaluation; the platform model proxy refuses such runs.
    assert query(uri, 'select model_disabled from public.observer_batches b join public.observer_runs r on r.batch_id=b.id'
                 ' where r.id=%s', (off,), role='authenticated', user=s['user']) == [(True,)]
    participant = 'p' * 43
    rpc(uri, 'observer_open_session', off, participant, 'e' * 43)
    with pytest.raises(psycopg.Error, match='model_disabled'):
        rpc(uri, 'observer_model_route', off, participant)


def test_self_check_without_a_model_flags_all_three(team):
    s = team; uri = s['uri']
    rev = revision(s)
    got = query(uri, 'select public.observer_create_repeat_batches(%s,%s,%s,%s)', (s['phase'], rev, True, True),
                role='authenticated', user=s['user'])[0][0]
    flagged = query(uri, 'select bool_and(model_disabled),count(*) from public.observer_batches where repeat_group=%s',
                    (got['repeat_group'],))
    assert flagged == [(True, 3)]
    query(uri, "update public.observer_batches set status='cancelled',finished_at=now() where repeat_group=%s", (got['repeat_group'],))
    plain = query(uri, 'select public.observer_create_repeat_batches(%s,%s,%s)', (s['phase'], rev, True),
                  role='authenticated', user=s['user'])
    assert plain  # older callers (3 arguments) still work


def test_not_for_local_sessions_or_sealed_phases(team):
    s = team; uri = s['uri']
    rev = revision(s)
    with pytest.raises(psycopg.Error, match='no_model_not_available'):
        evaluate(s, None, no_model=True)
    query(uri, 'update public.observer_phase_settings set sealed=true where phase_id=%s', (s['phase'],))
    try:
        with pytest.raises(psycopg.Error, match='no_model_not_available'):
            evaluate(s, rev, no_model=True)
    finally:
        query(uri, 'update public.observer_phase_settings set sealed=false where phase_id=%s', (s['phase'],))


def test_stats_rows_carry_the_flag(team):
    s = team; uri = s['uri']
    rev = revision(s)
    evaluate(s, rev); evaluate(s, rev, no_model=True)
    query(uri, 'select private.stats_refresh()')
    rows = query(uri, 'select model_disabled,sum(runs) from private.stats_llm_usage_daily where team_id=%s group by 1'
                 ' order by 1', (s['team'],))
    assert [r[0] for r in rows] == [False, True]

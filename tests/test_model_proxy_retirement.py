"""Retiring the model proxy (migration 20261004080000) and direct model access for
preparation's adaptation and the local runner."""
import io
import json
import urllib.error
import uuid

import psycopg
import pytest

from project_platform.manifest import ProjectError
from project_platform.model_client import DirectModelClient, team_model_client
from test_project_database import database, identity, query, rpc, session, setup  # noqa: F401
from test_project_preparation import preparation  # noqa: F401

CIPHER = 'v1.' + 'A' * 16 + '.' + 'B' * 40 + '=='


@pytest.fixture(autouse=True)
def switches_off(database):
    # One database is shared by the tests: every test starts and ends with all switches off.
    reset = ("update private.observer_hardening set team_egress=false,team_egress_teams='{}',"
             "prepare_direct_model=false,model_proxy_retired=false where id")
    query(database, reset)
    yield
    query(database, reset)


def save(uri, user, name, *, secret=True, value=None):
    rpc(uri, 'observer_save_team_variable', user, uuid.uuid4(), name, secret,
        CIPHER if secret else None, None if secret else value, 'wxyz' if secret else '')


# --- database -------------------------------------------------------------------------------

def test_retired_proxy_refuses_team_egress_teams_and_reopens_on_egress_rollback(setup):
    s = setup; uri = s['uri']
    run, participant, _ = session(s)
    assert rpc(uri, 'observer_model_route', run, participant) == {'personal': False}
    # Retired, but the team is not on team egress: the proxy still serves it.
    rpc(uri, 'observer_set_model_proxy', None, True)
    assert rpc(uri, 'observer_model_route', run, participant) == {'personal': False}
    query(uri, 'update private.observer_hardening set team_egress=true where id')
    with pytest.raises(psycopg.Error, match='model_proxy_retired'):
        rpc(uri, 'observer_model_route', run, participant)
    # The egress rollback alone reopens the proxy.
    query(uri, 'update private.observer_hardening set team_egress=false where id')
    assert rpc(uri, 'observer_model_route', run, participant) == {'personal': False}
    # And the retirement rollback.
    query(uri, 'update private.observer_hardening set team_egress=true where id')
    assert rpc(uri, 'observer_set_model_proxy', None, False)['model_proxy_retired'] is False
    assert rpc(uri, 'observer_model_route', run, participant) == {'personal': False}
    with pytest.raises(psycopg.Error, match='permission denied'):
        rpc(uri, 'observer_set_model_proxy', None, True, role='authenticated', user=s['user'])


def test_preparation_gets_the_teams_variables_only_with_direct_access_and_team_egress(preparation):
    s = preparation; uri = s['uri']
    save(uri, s['user'], 'OPENAI_API_KEY')
    save(uri, s['user'], 'OPENAI_MODEL', secret=False, value='k3')
    rpc(uri, 'observer_set_team_domains', s['user'], ['api.kimi.com'])
    value = rpc(uri, 'observer_preparation_team_egress', s['revision'])
    assert value['enabled'] is False
    assert [(v['name'], v['encrypted_value'], v['plain_value']) for v in value['variables']] == [
        ('OPENAI_API_KEY', CIPHER, None), ('OPENAI_MODEL', None, 'k3')]
    assert value['domains'] == ['api.kimi.com']
    assert rpc(uri, 'observer_set_model_proxy', True, None)['prepare_direct_model'] is True
    assert rpc(uri, 'observer_preparation_team_egress', s['revision'])['enabled'] is False  # team egress off
    query(uri, 'update private.observer_hardening set team_egress=true where id')
    assert rpc(uri, 'observer_preparation_team_egress', s['revision'])['enabled'] is True
    rpc(uri, 'observer_set_model_proxy', False, None)
    assert rpc(uri, 'observer_preparation_team_egress', s['revision'])['enabled'] is False
    with pytest.raises(psycopg.Error, match='permission denied'):
        rpc(uri, 'observer_preparation_team_egress', s['revision'], role='authenticated', user=s['user'])


def test_organizer_paid_route_is_switched_off_by_the_migration(database):
    # Re-applying the migration is harmless and switches off organizer providers added since.
    import pathlib
    query(database, (pathlib.Path(__file__).parents[1] / 'supabase/migrations/20261004080000_model_proxy_retirement.sql').read_text())
    assert query(database, 'select count(*) from private.observer_providers where team_id is null and enabled') == [(0,)]


# --- direct model client ----------------------------------------------------------------------

class Opener:
    def __init__(self, *answers):
        self.answers, self.requests = list(answers), []

    def open(self, request, timeout):
        self.requests.append(request)
        answer = self.answers.pop(0)
        if isinstance(answer, int):
            raise urllib.error.HTTPError(request.full_url, answer, 'x', {}, io.BytesIO(b'{"error":"provider text sk-secret"}'))
        return io.BytesIO(json.dumps(answer).encode())


TEAM = {'environment': {'OPENAI_API_KEY': 'sk-team', 'OPENAI_BASE_URL': 'https://api.kimi.com/coding/v1',
                        'OPENAI_MODEL': 'kimi-for-coding'}, 'secrets': ['OPENAI_API_KEY'], 'domains': ['api.kimi.com']}
REQUEST = {'model': 'kimi-for-coding', 'temperature': 0, 'max_tokens': 99,
           'messages': [{'role': 'system', 'content': 'S'}, {'role': 'user', 'content': 'U'}]}


def test_openai_variables_call_the_provider_directly_without_temperature():
    client, model = team_model_client(TEAM)
    assert model == 'kimi-for-coding' and client.url == 'https://api.kimi.com/coding/v1/chat/completions'
    client.opener = Opener({'choices': [{'message': {'content': '{}'}}]})
    assert client(REQUEST)['choices'][0]['message']['content'] == '{}'
    request = client.opener.requests[0]
    assert request.get_header('Authorization') == 'Bearer sk-team'
    assert 'temperature' not in json.loads(request.data)


def test_anthropic_variables_use_messages_and_answer_in_chat_shape():
    team = {'environment': {'ANTHROPIC_API_KEY': 'sk-ant', 'ANTHROPIC_BASE_URL': 'https://api.kimi.com/coding',
                            'ANTHROPIC_MODEL': 'kimi-for-coding'}, 'secrets': [], 'domains': ['api.kimi.com']}
    client, model = team_model_client(team)
    assert client.url == 'https://api.kimi.com/coding/v1/messages'
    client.opener = Opener({'content': [{'type': 'thinking', 'thinking': 'x'}, {'type': 'text', 'text': '{"a":1}'}]})
    assert client(REQUEST) == {'choices': [{'message': {'role': 'assistant', 'content': '{"a":1}'}}]}
    request = client.opener.requests[0]
    assert request.get_header('X-api-key') == 'sk-ant'
    assert json.loads(request.data) == {'model': 'kimi-for-coding', 'max_tokens': 99, 'system': 'S',
                                        'messages': [{'role': 'user', 'content': 'U'}]}


@pytest.mark.parametrize('change,message', [
    ({'OPENAI_MODEL': ''}, 'OPENAI_MODEL'),
    ({'OPENAI_BASE_URL': 'https://169.254.169.254/v1'}, 'public https'),
    ({'OPENAI_BASE_URL': 'http://api.kimi.com/v1'}, 'public https'),
    ({'OPENAI_BASE_URL': 'https://api.other.com/v1'}, 'public https'),
    ({'OPENAI_API_KEY': ''}, 'OPENAI_API_KEY'),
])
def test_direct_access_needs_a_model_and_an_allowed_https_base(change, message):
    team = {**TEAM, 'environment': {**TEAM['environment'], **change}}
    with pytest.raises(ProjectError, match=message):
        team_model_client(team)


def test_open_egress_allows_any_public_https_name_for_direct_access():
    for base in ('https://api.other.com/v1', 'https://api.kimi.com/coding/v1'):
        team = {**TEAM, 'domains': [], 'open': True, 'environment': {**TEAM['environment'], 'OPENAI_BASE_URL': base}}
        client, _ = team_model_client(team)
        assert client.url == base + '/chat/completions'
    for base in ('https://169.254.169.254/v1', 'https://10.0.0.1/v1', 'https://svc.internal/v1', 'http://api.other.com/v1',
                 'https://localhost/v1', 'https://api.other.com:8443/v1'):
        team = {**TEAM, 'domains': [], 'open': True, 'environment': {**TEAM['environment'], 'OPENAI_BASE_URL': base}}
        with pytest.raises(ProjectError, match='public https'):
            team_model_client(team)


def test_provider_errors_never_carry_provider_text_and_transient_ones_retry_once(monkeypatch):
    monkeypatch.setattr('project_platform.model_client.PROVIDER_RETRY_DELAY', 0)
    client = DirectModelClient('openai', 'https://api.kimi.com/coding/v1', 'sk-team')
    client.opener = Opener(503, {'choices': [{'message': {'content': 'ok'}}]})
    assert client(REQUEST)['choices'][0]['message']['content'] == 'ok'
    client.opener = Opener(401)
    with pytest.raises(ProjectError) as error:
        client(REQUEST)
    assert 'HTTP 401' in str(error.value) and 'sk-' not in str(error.value)


def test_preparation_uses_team_variables_instead_of_the_proxy(monkeypatch):
    import project_platform.preparation as prep
    seen = {}

    def propose(files, model, completion, gameplay):
        seen.update(model=model, client=completion)
        raise ProjectError('stop')
    monkeypatch.setattr(prep, 'download_project', lambda *a: ())
    monkeypatch.setattr(prep, 'SnapshotRepository', lambda *a: type('S', (), {'store_revision': lambda self, r, s: ('d' * 40, 'c' * 64)})())
    monkeypatch.setattr(prep, 'read_manifest', lambda source: None)
    monkeypatch.setattr(prep, 'propose_adapter', propose)
    payload = {'archive_url': 'https://x.test/a.zip', 'repository': {'full_name': 'o/r', 'token': 't'},
               'revision_id': str(uuid.uuid4()), 'team_egress': TEAM}
    with pytest.raises(ProjectError, match='stop'):
        prep.prepare_project(payload, None)
    assert seen['model'] == 'kimi-for-coding' and isinstance(seen['client'], DirectModelClient)
    with pytest.raises(Exception, match='project_interface_required'):
        prep.prepare_project({k: v for k, v in payload.items() if k != 'team_egress'}, None)


# --- local runner -----------------------------------------------------------------------------

def test_local_runner_passes_the_participants_own_variables_instead_of_the_proxy(monkeypatch, tmp_path):
    import project_platform.local as local
    project = tmp_path / 'project'
    project.mkdir()
    (project / 'observer.project.json').write_text(json.dumps(
        {'schema_version': 'observer-project-v1', 'image': 'debian:bookworm-slim', 'run': ['./agent']}))
    env_file = tmp_path / '.env'
    env_file.write_text('# mine\nexport KIMI_API_KEY="sk-kimi"\nKIMI_MODEL=k3\n')
    captured = {}

    def fake_execute(runtime, client, environment):
        runtime.start(environment)
        captured.update(environment=environment, runtime=runtime.runtime.transport)
        raise RuntimeError('stop')

    class Transport:
        def __init__(self, argv, cwd, environment, redactions):
            captured.update(child=environment, redactions=redactions)

    monkeypatch.setattr(local, 'execute', fake_execute)
    monkeypatch.setattr(local, 'JsonlTransport', Transport)
    monkeypatch.setenv('OPENAI_API_KEY', 'sk-openai')
    monkeypatch.setenv('OPENAI_MODEL', 'gpt')
    run = str(uuid.uuid4())
    credential = f'obs_{run}.' + 'c' * 43
    with pytest.raises(RuntimeError, match='stop'):
        local.run_local(project, 'http://127.0.0.1:1/s', credential, None, tmp_path / 'out.csv', native=True,
                        env_file=env_file)
    assert set(captured['environment']) == {'OBSERVER_API_URL', 'OBSERVER_RUN_ID', 'OBSERVER_RUN_TOKEN'}
    child = captured['child']
    assert (child['OPENAI_API_KEY'], child['OPENAI_MODEL'], child['KIMI_API_KEY'], child['KIMI_MODEL']) == (
        'sk-openai', 'gpt', 'sk-kimi', 'k3')
    assert 'OPENAI_BASE_URL' not in child or 'observer-model' not in child['OPENAI_BASE_URL']
    assert {'sk-openai', 'sk-kimi', credential} <= set(captured['redactions'])


@pytest.mark.parametrize('line', ['OBSERVER_RUN_TOKEN=x', 'HTTPS_PROXY=x', 'lower=x', 'NOEQUALS'])
def test_local_env_file_refuses_platform_and_invalid_names(tmp_path, line):
    import project_platform.local as local
    env_file = tmp_path / '.env'
    env_file.write_text(line + '\n')
    with pytest.raises(ProjectError, match='Line 1'):
        local.own_environment(env_file, environ={})


KIMI_ONLY = {'environment': {'KIMI_API_KEY': 'sk-kimi', 'KIMI_BASE_URL': 'https://api.kimi.com/coding/v1',
                             'KIMI_MODEL': 'kimi-for-coding'}, 'secrets': ['KIMI_API_KEY'], 'domains': ['api.kimi.com']}


def test_a_complete_prefixed_trio_is_used_when_no_default_trio_exists():
    client, model = team_model_client(KIMI_ONLY)
    assert model == 'kimi-for-coding' and client.protocol == 'openai'
    assert client.url == 'https://api.kimi.com/coding/v1/chat/completions'
    assert client.source == 'KIMI_API_KEY, KIMI_BASE_URL, KIMI_MODEL (openai protocol)'
    client.opener = Opener({'choices': [{'message': {'content': '{}'}}]})
    client(REQUEST)
    assert client.opener.requests[0].get_header('Authorization') == 'Bearer sk-kimi'


def test_prefixed_trio_choice_is_deterministic_and_the_protocol_is_inferred_or_stated():
    env = {**KIMI_ONLY['environment'],
           'ACME_API_KEY': 'a', 'ACME_BASE_URL': 'https://api.acme.com/v1', 'ACME_MODEL': 'm',
           'ZED_API_KEY': 'z', 'ZED_BASE_URL': 'https://api.zed.com/v1', 'ZED_MODEL': 'z1'}
    team = {'environment': env, 'domains': ['api.kimi.com', 'api.acme.com', 'api.zed.com']}
    assert team_model_client(team)[0].variables == 'KIMI'
    del env['KIMI_MODEL']
    assert team_model_client(team)[0].variables == 'ACME'   # KIMI incomplete: alphabetical
    # A default trio, when complete, still comes first.
    env.update({'OPENAI_API_KEY': 'o', 'OPENAI_MODEL': 'gpt', 'OPENAI_BASE_URL': 'https://api.zed.com/v1'})
    assert team_model_client(team)[0].variables == 'OPENAI'
    for base, protocol in (('https://api.kimi.com/coding', 'anthropic'), ('https://api.kimi.com/coding/v1', 'openai'),
                           ('https://api.anthropic.com', 'anthropic'), ('https://gw.example.com/anthropic', 'anthropic'),
                           ('https://api.deepseek.com', 'openai')):
        host = base.split('/')[2]
        t = {'environment': {'X_API_KEY': 'k', 'X_BASE_URL': base, 'X_MODEL': 'm'}, 'domains': [host]}
        assert team_model_client(t)[0].protocol == protocol, base
    stated = {'environment': {'X_API_KEY': 'k', 'X_BASE_URL': 'https://api.deepseek.com', 'X_MODEL': 'm',
                              'X_PROTOCOL': 'Anthropic'}, 'domains': ['api.deepseek.com']}
    client, _ = team_model_client(stated)
    assert client.protocol == 'anthropic' and client.url == 'https://api.deepseek.com/v1/messages'


def test_the_error_lists_what_was_found_and_missing_and_suggests_a_manifest():
    team = {'environment': {'KIMI_API_KEY': 'sk-kimi', 'KIMI_BASE_URL': 'https://api.kimi.com/coding/v1'},
            'domains': ['api.kimi.com']}
    with pytest.raises(ProjectError) as error:
        team_model_client(team)
    text = str(error.value)
    assert 'KIMI_*: missing KIMI_MODEL' in text and 'observer.project.json' in text and 'sk-kimi' not in text
    with pytest.raises(ProjectError, match='No model variables were found'):
        team_model_client({'environment': {}, 'domains': []})


def test_a_provider_404_names_the_model_and_base_url_variables(monkeypatch):
    client, _ = team_model_client(KIMI_ONLY)
    client.opener = Opener(404)
    with pytest.raises(ProjectError) as error:
        client(REQUEST)
    assert '模型名或接口地址在服务商处不存在' in str(error.value) and 'KIMI_MODEL and KIMI_BASE_URL' in str(error.value)

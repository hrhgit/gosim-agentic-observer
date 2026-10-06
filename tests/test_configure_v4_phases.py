"""scripts/configure-v4-phases.py against the local PostgreSQL harness: the v3 -> v4
switch, its refusals, hidden-card privacy and an exact reverse."""
import importlib.util
import json
import secrets
import uuid
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
import pytest

from test_project_database import database, identity, query, rpc  # noqa: F401
from test_publish_v4_card_pages import *  # noqa: F401,F403  (the A-D card page publisher, run in the same CI step)

ROOT = Path(__file__).resolve().parents[1]
ORG = 'AGENTIC-OBSERVER26-runner-1'


def load(uri):
    spec = importlib.util.spec_from_file_location('configure_v4_phases', ROOT / 'scripts/configure-v4-phases.py')
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)

    def plain(value):
        if isinstance(value, uuid.UUID): return str(value)
        if isinstance(value, dict): return {k: plain(v) for k, v in value.items()}
        if isinstance(value, list): return [plain(v) for v in value]
        return value

    def local_query(statement):   # the shape of the management API: rows of the last statement as dicts
        with psycopg.connect(uri, autocommit=True, row_factory=dict_row) as conn:
            cur = conn.execute(statement)
            rows = cur.fetchall() if cur.description else []
            while cur.nextset():
                rows = cur.fetchall() if cur.description else []
            return plain(rows)
    mod.query = local_query
    return mod


def run(mod, *argv, capsys):
    code = mod.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


V3 = 'challenge-score-v3'


def scenario(uri, slug, *, bundle=True, public=False, contract='v4-score-v1', wallclock=900):
    sid = uuid.uuid4()
    query(uri, "insert into public.scenarios(id,slug,name,weather_public,forecasts_public,events_public,global_wallclock_seconds,contract)"
               " values(%s,%s,%s,%s,%s,%s,%s,%s)", (sid, slug, 'Name of ' + slug, public, public, public, wallclock, contract))
    if bundle: query(uri, "insert into private.observer_scenario_bundles values(%s,%s,%s)", (sid, f'{sid}/b.zip', secrets.token_hex(32)))
    return sid


def new_phase(uri, slug, *, settings=None, **cols):
    pid = uuid.uuid4()
    names = ['id', 'slug', 'name_en', 'name_zh'] + list(cols)
    query(uri, f"insert into public.phases({','.join(names)}) values({','.join(['%s'] * len(names))})",
          (pid, slug, slug, slug, *cols.values()))
    if settings is not None:
        keys = ['phase_id'] + list(settings)
        query(uri, f"insert into public.observer_phase_settings({','.join(keys)}) values({','.join(['%s'] * len(keys))})",
              (pid, *settings.values()))
    return pid


def links(uri, pid, *sids):
    for sid in sids: query(uri, 'insert into public.phase_scenarios(phase_id,scenario_id) values(%s,%s)', (pid, sid))


@pytest.fixture(scope='module')
def world(database):
    """The production phase layout of 2026-09-28 plus registered v4 cards in sealed staging phases."""
    uri = database
    dev = [scenario(uri, s, contract=V3) for s in ('dev-fortnight', 'dev-reference')]
    formal = [scenario(uri, f'formal-{c}', contract=V3) for c in 'abc']
    final = scenario(uri, 'eval-final', contract=V3)
    alpha, beta = scenario(uri, 'v4-alpha', public=True), scenario(uri, 'v4-beta', public=True)
    public_test = scenario(uri, 'v4-public-test', public=True, wallclock=300)  # the dedicated public test card
    a_d = [scenario(uri, f'v4-{c}') for c in 'abcd']
    e_h = [scenario(uri, f'v4-{c}') for c in 'efgh']
    project = dict(projects_enabled=True, local_sessions_enabled=False, colocated=True,
                   model_token_limit=1000, model_call_limit=10, model_concurrency=4)
    practice = new_phase(uri, 'practice', sort_order=49, daily_limit=50)
    playground = new_phase(uri, 'practice-projects', sort_order=50, daily_limit=5, starts_at='2026-09-25T15:17:29Z',
                           settings=dict(project, runtime_seconds=18000, daily_batches=5))
    online = new_phase(uri, 'online', sort_order=1, daily_limit=10, counts_for_final=True, starts_at='2026-10-04T16:00:00Z',
                       ends_at='2026-10-07T15:59:00Z', settings=dict(project, runtime_seconds=3600, daily_batches=10, colocated=False))
    hidden = new_phase(uri, 'final-hidden', sort_order=0, daily_limit=1, counts_for_final=True, leaderboard_mode='hidden',
                       ends_at='2026-10-17T15:59:00Z', settings=dict(project, runtime_seconds=18000, daily_batches=1, sealed=True,
                                                                     colocated=False))
    user, team = identity(uri)
    rehearsal = new_phase(uri, 'rehearsal-formal', sort_order=10200, leaderboard_mode='hidden',
                          settings=dict(project, runtime_seconds=3600, daily_batches=20, access_team_id=team))
    staging_practice = new_phase(uri, 'v4-staging-practice', sort_order=10300, leaderboard_mode='hidden', counts_for_final=True,
                                 settings=dict(project, runtime_seconds=900, daily_batches=20, sealed=True))
    # Sealed staging phases count for the final: a sealed phase that does not is 'started' for
    # public.observer_scenario_listed, which would name its scenarios to participants.
    staging_formal = new_phase(uri, 'v4-staging-formal', sort_order=10301, leaderboard_mode='hidden', counts_for_final=True,
                               settings=dict(project, runtime_seconds=900, daily_batches=20, sealed=True))
    staging_final = new_phase(uri, 'v4-staging-final', sort_order=10302, leaderboard_mode='hidden', counts_for_final=True,
                              settings=dict(project, runtime_seconds=900, daily_batches=20, sealed=True))
    links(uri, practice, *dev); links(uri, playground, *dev); links(uri, online, *formal); links(uri, hidden, final)
    links(uri, rehearsal, *formal); links(uri, staging_practice, alpha, beta)
    links(uri, staging_formal, *a_d); links(uri, staging_final, *e_h)
    query(uri, "insert into private.observer_preparation_config values(true,%s,%s,'test-model',true)", (online, dev[0]))
    # Files in the public 'scenarios' bucket, as registered by scenario-work (W2).
    query(uri, "insert into storage.buckets(id,name) values('scenarios','scenarios') on conflict do nothing")
    for name in CARD_FILES:
        query(uri, "insert into storage.objects(bucket_id,name) values('scenarios',%s)", (name,))
    query(uri, "insert into private.observer_installations(organization,organization_id,installation_id,repository_id,approved_sha,enabled)"
               " values(%s,'101',202,'303',%s,true) on conflict(organization) do nothing", (ORG, 'a' * 40))
    return {'uri': uri, 'user': user, 'team': team, 'online': online, 'hidden': hidden, 'playground': playground,
            'practice': practice, 'rehearsal': rehearsal, 'formal': formal, 'final': final, 'e_h': e_h, 'a_d': a_d,
            'alpha': alpha, 'beta': beta, 'public_test': public_test, 'staging_formal': staging_formal, 'staging_final': staging_final}


def state(uri):
    parking = "(select id from public.phases where slug='scenario-parking')"
    return {
        'links': query(uri, 'select phase_id,scenario_id from public.phase_scenarios where phase_id is distinct from '
                            + parking + ' order by 1,2'),
        'settings': query(uri, 'select to_jsonb(s)::text from public.observer_phase_settings s where phase_id is distinct from '
                               + parking + ' order by phase_id'),
        'phases': query(uri, "select to_jsonb(p)::text from public.phases p where slug<>'scenario-parking' order by id"),
        'preparation': query(uri, 'select to_jsonb(c)::text from private.observer_preparation_config c'),
    }


def slugs_of(uri, phase_slug):
    return [r[0] for r in query(uri, 'select s.slug from public.phase_scenarios ps join public.scenarios s on s.id=ps.scenario_id'
                                     ' join public.phases p on p.id=ps.phase_id where p.slug=%s order by 1', (phase_slug,))]


def listed(uri, sid, role='anon', user=None):
    return query(uri, 'select id from public.scenarios where id=%s', (sid,), role=role, user=user) != []


PRACTICE_FILES = [f'v4-{c}/{p}' for c in ('alpha', 'beta') for p in (
    'config/v4_scenario.json', 'config/v4_score_config.json', 'public/targets.csv', 'public/v4_bulletins.jsonl',
    'truth/v4_weather_truth.csv', 'truth/v4_events.csv')]
FORMAL_FILES = [f'v4-{c}/{p}' for c in 'abcd' for p in (
    'config/v4_fiber_config.json', 'config/v4_score_config.json', 'public/footprint.csv', 'public/targets.csv',
    'public/v4_night_calendar.csv')]
V3_FILES = ['formal-a/config/scenario_config.json', 'eval-final/config/scenario_config.json', 'dev-fortnight/config/scenario_config.json']
CARD_FILES = PRACTICE_FILES + FORMAL_FILES + V3_FILES


def readable(uri, role='anon', user=None):
    return {r[0] for r in query(uri, "select name from storage.objects where bucket_id='scenarios'", role=role, user=user)}


FORWARD = ('--practice', 'v4-alpha,v4-beta', '--formal', 'v4-a,v4-b,v4-c,v4-d', '--final', 'v4-e,v4-f,v4-g,v4-h')


def test_dry_run_and_refusals_change_nothing(world, capsys):
    uri = world['uri']; mod = load(uri); before = state(uri)
    code, out = run(mod, *FORWARD, capsys=capsys)
    assert code == 0 and not out['applied'] and out['problems'] == []
    assert out['planned']['online']['scenarios'] == ['v4-a', 'v4-b', 'v4-c', 'v4-d']
    assert out['planned']['online']['runtime_seconds'] == 900 and out['planned']['online']['daily_batches'] == 4
    assert out['parked'] == ['eval-final', 'formal-a', 'formal-b', 'formal-c']
    assert any('preview' in w for w in out['warnings'])
    assert 'Name of' not in json.dumps(out)
    assert state(uri) == before
    # A hidden card with a public file, or one listed as formal and final, is refused.
    query(uri, "insert into storage.objects(bucket_id,name) values('scenarios','v4-e/config/leak.json')")
    code, out = run(mod, *FORWARD, '--apply', capsys=capsys)
    assert code == 2 and not out['applied'] and 'v4-e has files in the public scenario bucket' in out['problems']
    query(uri, "delete from storage.objects where bucket_id='scenarios' and name='v4-e/config/leak.json'")
    code, out = run(mod, '--practice', 'v4-alpha', '--formal', 'v4-a,v4-b,v4-c,v4-e', '--final', 'v4-e,v4-f,v4-g,v4-h', '--apply', capsys=capsys)
    assert code == 2 and 'a card is listed in more than one group' in out['problems']
    code, out = run(mod, '--practice', 'v4-alpha', '--formal', 'eval-final', '--final', 'v4-e', '--apply', capsys=capsys)
    assert code == 2 and 'eval-final belongs to the hidden final' in out['problems']
    code, out = run(mod, '--practice', 'formal-a', '--formal', 'v4-a', '--final', 'v4-e', '--apply', capsys=capsys)
    assert code == 2 and 'formal-a is formal material; refusing to use it for practice' in out['problems']
    assert state(uri) == before
    # Reverse without a switch is refused.
    code, out = run(mod, '--reverse', '--apply', capsys=capsys)
    assert code == 2 and out['problems'] == ['no unrestored snapshot to restore']
    assert state(uri) == before


def test_active_jobs_block_the_switch_even_after_planning(world, capsys):
    uri = world['uri']; mod = load(uri)
    phase = new_phase(uri, 'job-phase-' + secrets.token_hex(3), settings=dict(projects_enabled=True, local_sessions_enabled=True,
                      model_token_limit=1000, model_call_limit=10, model_concurrency=4))
    scen = scenario(uri, 'job-scenario-' + secrets.token_hex(3)); links(uri, phase, scen)
    batch = rpc(uri, 'observer_create_batch', phase, None, role='authenticated', user=world['user'])
    run_id = query(uri, 'select id from public.observer_runs where batch_id=%s', (batch,))[0][0]
    before = state(uri)
    # Planned while idle, then a job appears before the transaction runs: the in-transaction guard refuses.
    args = mod.argparse.Namespace(practice=['v4-alpha', 'v4-beta'], formal=['v4-a', 'v4-b', 'v4-c', 'v4-d'],
                                  final=['v4-e', 'v4-f', 'v4-g', 'v4-h'], practice_mode='replace', runtime=900,
                                  practice_runtime=900, daily=4, preview=None)
    _, sql = mod.plan_forward(args)
    rpc(uri, 'observer_enqueue_job', uuid.uuid4(), 'engine', run_id, None, ORG, secrets.token_urlsafe(32), 'payload', 'nonce')
    with pytest.raises(psycopg.Error, match='queued, dispatched or claimed'):
        mod.query(sql)
    code, out = run(mod, *FORWARD, '--apply', capsys=capsys)
    assert code == 2 and any('job(s) queued' in p for p in out['problems'])
    assert state(uri) == before
    query(uri, "update private.observer_jobs set status='succeeded' where run_id=%s", (run_id,))


def test_switch_to_v4_and_back_restores_v3_exactly(world, capsys):
    uri = world['uri']; mod = load(uri); before = state(uri)
    code, out = run(mod, *FORWARD, '--preview', 'v4-alpha', capsys=capsys)
    assert code == 2 and any('dedicated public test card' in p for p in out['problems'])  # never a practice card
    code, out = run(mod, *FORWARD, '--preview', 'v4-public-test', '--apply', capsys=capsys)
    assert code == 0 and out['applied'], out
    assert slugs_of(uri, 'practice-projects') == ['v4-alpha', 'v4-beta']
    assert slugs_of(uri, 'practice') == ['dev-fortnight', 'dev-reference']
    assert slugs_of(uri, 'online') == ['v4-a', 'v4-b', 'v4-c', 'v4-d']
    assert slugs_of(uri, 'final-hidden') == ['v4-e', 'v4-f', 'v4-g', 'v4-h']
    assert slugs_of(uri, 'scenario-parking') == ['eval-final', 'formal-a', 'formal-b', 'formal-c']
    assert slugs_of(uri, 'rehearsal-formal') == [] and slugs_of(uri, 'v4-staging-final') == []
    settings = {r[0]: r[1:] for r in query(uri, 'select p.slug,s.board_layout,s.runtime_seconds,s.daily_batches,p.daily_limit,s.sealed,'
                                               's.colocated from public.observer_phase_settings s join public.phases p on p.id=s.phase_id')}
    # v4 runs are colocated only: every phase with v4 cards is switched to colocated (restored on reverse).
    assert settings['online'] == ('cards_overall', 900, 4, 4, False, True)
    assert settings['final-hidden'] == ('cards_overall', 900, 1, 1, True, True)
    assert settings['practice-projects'] == ('cards', 900, 5, 5, False, True)
    assert settings['scenario-parking'][0] == 'overall' and settings['scenario-parking'][4] is True
    assert query(uri, 'select scenario_id from private.observer_preparation_config') == [(world['public_test'],)]
    # Hidden cards and parked v3 scenarios stay unnamed for anonymous and signed-in participants.
    for sid in world['e_h'] + world['formal'] + [world['final']]:
        assert not listed(uri, sid) and not listed(uri, sid, 'authenticated', world['user'])
    assert listed(uri, world['alpha'])
    assert query(uri, "select slug from public.phases where slug in ('final-hidden','scenario-parking')", role='anon') == []
    board = rpc(uri, 'observer_card_board', world['hidden'], None, 100, role='anon')
    assert board == {'layout': 'cards_overall', 'cards': [], 'scenario': None, 'rows': []}
    # Running forward again is refused until reversed.
    code, out = run(mod, *FORWARD, '--apply', capsys=capsys)
    assert code == 2 and any('already switched' in p for p in out['problems'])

    code, out = run(mod, '--reverse', capsys=capsys)
    assert code == 0 and not out['applied'] and out['restore']['online'] == ['formal-a', 'formal-b', 'formal-c']
    code, out = run(mod, '--reverse', '--apply', capsys=capsys)
    assert code == 0 and out['applied'], out
    assert state(uri) == before
    # The v4 cards that came from sealed staging phases return there; nothing is left exposed.
    assert slugs_of(uri, 'v4-staging-final') == ['v4-e', 'v4-f', 'v4-g', 'v4-h']
    assert slugs_of(uri, 'scenario-parking') == []
    for sid in world['e_h'] + world['a_d'] + [world['final']]:
        assert not listed(uri, sid)

    # A second round trip (e.g. rehearsal, then the real switch) behaves the same.
    assert run(mod, *FORWARD, '--practice-mode', 'add', '--apply', capsys=capsys)[0] == 0
    assert slugs_of(uri, 'practice-projects') == ['dev-fortnight', 'dev-reference', 'v4-alpha', 'v4-beta']
    assert query(uri, "select runtime_seconds from public.observer_phase_settings where phase_id=%s", (world['playground'],)) == [(18000,)]
    assert run(mod, '--reverse', '--apply', capsys=capsys)[0] == 0
    assert state(uri) == before
    assert query(uri, "select s.colocated from public.observer_phase_settings s join public.phases p on p.id=s.phase_id"
                      " where p.slug in ('online','final-hidden')") == [(False,), (False,)]
    assert query(uri, 'select count(*) from private.observer_phase_config_snapshots where restored_at is null') == [(0,)]


def test_cards_left_without_a_phase_are_parked_on_reverse(world, capsys):
    """Cards registered without any phase link must not become public when the switch is reversed."""
    uri = world['uri']; mod = load(uri)
    query(uri, 'delete from public.phase_scenarios where phase_id=%s', (world['staging_final'],))
    for sid in world['e_h']:
        assert listed(uri, sid)          # already readable before the script runs: it warns
    before = state(uri)
    code, out = run(mod, *FORWARD, capsys=capsys)
    assert code == 0 and 'v4-e is readable by participants now (not in a private phase)' in out['warnings']
    assert run(mod, *FORWARD, '--apply', capsys=capsys)[0] == 0
    assert run(mod, '--reverse', '--apply', capsys=capsys)[0] == 0
    assert state(uri) == before
    assert slugs_of(uri, 'scenario-parking') == ['v4-e', 'v4-f', 'v4-g', 'v4-h']
    for sid in world['e_h']:
        assert not listed(uri, sid)
    query(uri, "delete from public.phase_scenarios where phase_id=(select id from public.phases where slug='scenario-parking')")
    links(uri, world['staging_final'], *world['e_h'])


def test_card_files_open_for_practice_at_once_and_for_formal_cards_at_the_competition_start(world, capsys):
    uri = world['uri']; mod = load(uri)
    participant, _ = identity(uri)
    before = readable(uri)
    assert not any(n.startswith('v4-') for n in before)            # sealed staging: nothing
    assert 'formal-a/config/scenario_config.json' not in before and 'dev-fortnight/config/scenario_config.json' in before
    code, out = run(mod, *FORWARD, capsys=capsys)
    assert out['released_files']['v4-alpha'] == {'release': 'practice', 'files': sorted(f.split('/', 1)[1] for f in PRACTICE_FILES if f.startswith('v4-alpha/'))}
    assert out['released_files']['v4-a']['release'] == 'competition' and len(out['released_files']['v4-a']['files']) == 5
    assert 'v4-e' not in out['released_files']
    assert run(mod, *FORWARD, '--apply', capsys=capsys)[0] == 0
    for role, user in (('anon', None), ('authenticated', participant)):
        names = readable(uri, role, user)
        assert set(PRACTICE_FILES) <= names                          # alpha/beta: full public set
        assert not any(n.startswith(('v4-a/', 'v4-b/', 'v4-c/', 'v4-d/')) for n in names)   # before the competition
        assert 'formal-a/config/scenario_config.json' not in names and 'eval-final/config/scenario_config.json' not in names
    online = world['online']
    # The phase has started but the site is still in practice mode: still closed.
    query(uri, "update public.phases set starts_at=now()-interval '1 minute' where id=%s", (online,))
    assert not any(n.startswith('v4-a/') for n in readable(uri))
    # The automatic switch at the start: competition mode -> the 5 public inputs of A-D open, nothing else.
    query(uri, "update private.observer_site_mode set mode='competition',phase_id=%s", (online,))
    names = readable(uri)
    assert set(FORMAL_FILES) <= names
    query(uri, "insert into storage.objects(bucket_id,name) values('scenarios','v4-a/truth/v4_weather_truth.csv'),"
               "('scenarios','v4-e/config/v4_score_config.json')")
    names = readable(uri)
    assert 'v4-a/truth/v4_weather_truth.csv' not in names and 'v4-e/config/v4_score_config.json' not in names
    assert not any(n.startswith(('v4-e/', 'v4-f/', 'v4-g/', 'v4-h/', 'formal-', 'eval-final/')) for n in names)
    query(uri, "delete from storage.objects where name in ('v4-a/truth/v4_weather_truth.csv','v4-e/config/v4_score_config.json')")
    # Back to practice mode and reverse: everything v4 is private again.
    query(uri, "update private.observer_site_mode set mode='practice',phase_id=null")
    query(uri, "update public.phases set starts_at='2026-10-04T16:00:00Z' where id=%s", (online,))
    assert run(mod, '--reverse', '--apply', capsys=capsys)[0] == 0
    assert readable(uri) == before
    assert query(uri, 'select count(*) from private.observer_scenario_public_files') == [(0,)]


def test_timeline_files_are_never_released_for_formal_cards(world, capsys):
    uri = world['uri']; mod = load(uri)
    for name in ('v4_bulletins.jsonl', 'v4_forecasts.jsonl', 'public/v4_weather_truth.csv', 'config/bulletins.jsonl',
                 'public/V4_Forecasts.JSONL', 'public/v4_events.csv', 'public/v4_stress_events.csv'):
        assert mod.timeline_file(name), name
    for name in FORMAL_FILES:
        assert not mod.timeline_file(name), name
    code, out = run(mod, *FORWARD, capsys=capsys)
    assert code == 0 and not out['problems']
    for c in 'abcd':      # exactly the five public inputs, nothing else
        assert out['released_files'][f'v4-{c}']['files'] == sorted(f.split('/', 1)[1] for f in FORMAL_FILES if f.startswith(f'v4-{c}/'))
    leaked = ['v4-a/public/v4_bulletins.jsonl', 'v4-b/public/v4_forecasts.jsonl', 'v4-d/config/v4_forecasts.jsonl']
    for name in leaked:
        query(uri, "insert into storage.objects(bucket_id,name) values('scenarios',%s)", (name,))
    try:
        code, out = run(mod, *FORWARD, capsys=capsys)
        assert code == 2
        for slug in ('v4-a', 'v4-b', 'v4-d'):
            assert f'{slug} has weather or event timeline files in the public scenario bucket (never released)' in out['problems']
        released = {f'{s}/{f}' for s, r in out['released_files'].items() for f in r['files']}
        assert not released & set(leaked)
        assert not any(mod.timeline_file(f) for s, r in out['released_files'].items() if r['release'] == 'competition' for f in r['files'])
        before = state(uri)
        code, out = run(mod, *FORWARD, '--apply', capsys=capsys)
        assert code == 2 and not out['applied'] and state(uri) == before
        assert query(uri, 'select count(*) from private.observer_scenario_public_files') == [(0,)]
    finally:
        query(uri, 'delete from storage.objects where name = any(%s)', (leaked,))


def test_practice_card_with_private_weather_keeps_its_timeline_closed(world, capsys):
    uri = world['uri']; mod = load(uri)
    query(uri, 'update public.scenarios set forecasts_public=false where slug=%s', ('v4-beta',))
    try:
        code, out = run(mod, *FORWARD, capsys=capsys)
        beta = out['released_files']['v4-beta']['files']
        assert 'public/v4_bulletins.jsonl' not in beta and not any(f.startswith('truth/') for f in beta)
        assert 'config/v4_scenario.json' in beta and 'public/targets.csv' in beta
        assert 'public/v4_bulletins.jsonl' in out['released_files']['v4-alpha']['files']    # fully public practice card
    finally:
        query(uri, 'update public.scenarios set forecasts_public=true where slug=%s', ('v4-beta',))


def test_the_switch_refuses_in_the_database_to_release_a_formal_timeline_file(world, capsys):
    uri = world['uri']; mod = load(uri)
    real = mod.released_files
    def leaky(cards, args, problems):
        out = real(cards, args, problems)
        out['v4-a'] = ('competition', out['v4-a'][1] + ['public/v4_forecasts.jsonl'])
        return out
    mod.released_files = leaky
    before = state(uri)
    with pytest.raises(Exception, match='weather or event timeline file'):
        mod.main(list(FORWARD) + ['--apply'])
    capsys.readouterr()
    assert state(uri) == before
    assert query(uri, 'select count(*) from private.observer_scenario_public_files') == [(0,)]


def test_dry_run_reports_missing_migrations_instead_of_failing(world, capsys):
    uri = world['uri']; mod = load(uri)
    real = mod.query
    def without_tables(statement):
        return [{k: (False if k.startswith('private.observer_') else v) for k, v in row.items()} for row in real(statement)] \
            if 'to_regclass' in statement else real(statement)
    mod.query = without_tables
    code, out = run(mod, *FORWARD, capsys=capsys)
    assert code == 2 and 'migration 20260928004100_card_boards is not applied' in out['problems']
    assert 'migration 20260928004400_v4_public_card_files is not applied' in out['problems']
    assert out['planned']['online']['scenarios'] == ['v4-a', 'v4-b', 'v4-c', 'v4-d']
    code, out = run(mod, '--reverse', capsys=capsys)
    assert code == 2 and len(out['problems']) == 2

"""Team registration closes at the configured number of visible teams (organizer decision 2026-09-27)."""
import concurrent.futures
import uuid

import psycopg
import pytest

from test_project_database import database, query, rpc  # noqa: F401


def account(uri, *, admin=False):
    user = uuid.uuid4()
    query(uri, 'insert into auth.users(id,email) values(%s,%s)', (user, f'{user}@example.test'))
    if admin:
        query(uri, 'update public.profiles set is_admin=true where id=%s', (user,))
    return user


def create(uri, user, name=None):
    return rpc(uri, 'create_team', name or f'Cap {uuid.uuid4().hex[:10]}', 3, '', '', role='authenticated', user=user)


def visible_teams(uri):
    return query(uri, 'select count(*) from public.teams where not is_hidden')[0][0]


def set_limit(uri, value):
    query(uri, "insert into public.site_settings(key,value) values('team_limit',%s::jsonb) "
               "on conflict(key) do update set value=excluded.value", (value,))


def capacity(uri):
    return rpc(uri, 'team_capacity', role='anon')


def test_default_limit_is_150_and_public(database):
    uri = database
    assert query(uri, "select value from public.site_settings where key='team_limit'") == []
    cap = capacity(uri)
    assert cap['limit'] == 150 and cap['teams'] == visible_teams(uri)
    assert cap['remaining'] == 150 - cap['teams'] and cap['full'] is False
    set_limit(uri, '120')
    assert query(uri, "select value from public.site_settings where key='team_limit'", role='anon') == [(120,)]
    assert capacity(uri)['limit'] == 120
    set_limit(uri, '150')


def test_creation_stops_at_the_limit_hidden_teams_do_not_count_and_joining_still_works(database):
    uri = database
    set_limit(uri, str(visible_teams(uri) + 1))
    first = account(uri)
    team = create(uri, first)
    assert capacity(uri)['full'] is True and capacity(uri)['remaining'] == 0
    late = account(uri)
    with pytest.raises(psycopg.Error, match='team_limit_reached'):
        create(uri, late)
    assert query(uri, 'select team_id from public.profiles where id=%s', (late,)) == [(None,)]
    # Joining an existing team is not a new team.
    code = query(uri, 'select invite_code from public.teams where id=%s', (team,))[0][0]
    assert rpc(uri, 'join_team', code, role='authenticated', user=late) == team
    # Admins bypass the cap.
    admin = account(uri, admin=True)
    admin_team = create(uri, admin)
    # Hiding a team (test / organizer teams) frees its place.
    query(uri, 'update public.teams set is_hidden=true where id in (%s,%s)', (team, admin_team))
    assert capacity(uri)['full'] is False
    create(uri, account(uri))
    with pytest.raises(psycopg.Error, match='team_limit_reached'):
        create(uri, account(uri))
    set_limit(uri, '150')


def test_malformed_limit_falls_back_to_150(database):
    uri = database
    set_limit(uri, '"many"')
    assert capacity(uri)['limit'] == 150
    set_limit(uri, '"3"')
    assert capacity(uri)['limit'] == 3
    set_limit(uri, '150')


def test_concurrent_creations_cannot_overshoot_the_limit(database):
    uri = database
    set_limit(uri, str(visible_teams(uri) + 2))
    users = [account(uri) for _ in range(6)]

    def attempt(user):
        try:
            create(uri, user)
            return 'created'
        except psycopg.Error as exc:
            assert 'team_limit_reached' in str(exc)
            return 'full'

    with concurrent.futures.ThreadPoolExecutor(len(users)) as pool:
        outcomes = list(pool.map(attempt, users))
    assert outcomes.count('created') == 2 and outcomes.count('full') == 4
    assert capacity(uri)['full'] is True
    set_limit(uri, '150')


def test_banned_accounts_cannot_create_teams(database):
    uri = database
    user = account(uri)
    query(uri, 'update public.profiles set is_banned=true where id=%s', (user,))
    with pytest.raises(psycopg.Error, match='banned'):
        create(uri, user)

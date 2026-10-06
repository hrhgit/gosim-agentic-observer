"""Popups seen once per person across devices (migration 20261004233000): each user reads and adds only their own keys."""
import uuid

import psycopg
import pytest

from test_project_database import database, query  # noqa: F401


def register(uri):
    user = uuid.uuid4()
    query(uri, "insert into auth.users(id,email) values(%s,%s)", (user, f"{user}@example.test"))
    return user


def mine(uri, user):
    return query(uri, "select public.my_seen_popups()", role='authenticated', user=user)[0][0]


def mark(uri, user, keys):
    query(uri, "select public.mark_popups_seen(%s::text[])", (keys,), role='authenticated', user=user)


def test_keys_are_per_user_and_idempotent(database):
    uri = database
    a, b = register(uri), register(uri)
    assert mine(uri, a) == []
    mark(uri, a, ['ann:1:abcd', 'kimi:t:captain'])
    mark(uri, a, ['ann:1:abcd', '', 'x' * 201])
    assert sorted(mine(uri, a)) == ['ann:1:abcd', 'kimi:t:captain']
    assert mine(uri, b) == []


def test_limits_and_access(database):
    uri = database
    a = register(uri)
    with pytest.raises(psycopg.Error, match='too_many_keys'):
        mark(uri, a, [f'k{i}' for i in range(51)])
    for start in range(0, 520, 40):
        mark(uri, a, [f'k{i}' for i in range(start, start + 40)])
    assert len(mine(uri, a)) == 500
    with pytest.raises(psycopg.Error, match='permission denied'):
        query(uri, "select public.my_seen_popups()", role='anon')
    with pytest.raises(psycopg.Error, match='permission denied'):
        query(uri, "select key from private.popup_seen", role='authenticated', user=a)

"""Permanent 9-digit UIDs (migration 20261004180000): backfilled in registration order, the next number for
every new profile, visible only to the user themselves through me()."""
import uuid
from pathlib import Path

import psycopg
import pytest

from test_project_database import database, query, rpc  # noqa: F401
from pg import start

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = '20261004180000_profile_uid.sql'


def register(uri, created_at=None):
    user = uuid.uuid4()
    query(uri, "insert into auth.users(id,email) values(%s,%s)", (user, f"{user}@example.test"))
    if created_at:
        query(uri, "update public.profiles set created_at=%s where id=%s", (created_at, user))
    return user


def uid_of(uri, user):
    rows = query(uri, "select uid from private.profile_uids where profile_id=%s", (user,))
    return rows[0][0] if rows else None


def test_backfill_follows_registration_order_and_new_users_continue():
    server, uri = start(apply_migrations=False)
    try:
        query(uri, (ROOT / "tests/supabase/auth_stub.sql").read_text())
        migrations = sorted((ROOT / "supabase/migrations").glob("*.sql"))
        for path in migrations:
            if path.name < MIGRATION:
                query(uri, path.read_text())
        # Registered out of insertion order; two share a timestamp (tie broken by id).
        late = register(uri, '2026-09-20T00:00:00Z')
        early = register(uri, '2026-09-15T00:00:00Z')
        tie = sorted([register(uri, '2026-09-18T00:00:00Z'), register(uri, '2026-09-18T00:00:00Z')], key=str)
        profiles_before = query(uri, "select to_jsonb(p)::text from public.profiles p order by id")
        for path in migrations:
            if path.name >= MIGRATION:
                query(uri, path.read_text())
        assert [uid_of(uri, u) for u in [early, *tie, late]] == [100000001, 100000002, 100000003, 100000004]
        # Nothing else about a profile changed.
        assert query(uri, "select to_jsonb(p)::text from public.profiles p order by id") == profiles_before
        # The trigger hands out the next number, permanently; a deleted user's number is never reused.
        newcomer = register(uri)
        assert uid_of(uri, newcomer) == 100000005
        query(uri, "delete from auth.users where id=%s", (newcomer,))
        assert uid_of(uri, register(uri)) == 100000006
        # Re-running the migration is a no-op for existing numbers.
        query(uri, (ROOT / "supabase/migrations" / MIGRATION).read_text())
        assert uid_of(uri, early) == 100000001
        assert uid_of(uri, register(uri)) == 100000007
    finally:
        server.cleanup()


def test_me_returns_only_the_callers_own_uid(database):
    uri = database
    me = register(uri)
    other = register(uri)
    mine = query(uri, "select public.me()", role='authenticated', user=me)[0][0]
    assert mine['uid'] == uid_of(uri, me) and 100000001 <= mine['uid'] <= 999999999
    assert mine['uid'] != uid_of(uri, other)
    assert query(uri, "select public.me()", role='anon')[0][0] is None


def test_uids_are_not_readable_directly(database):
    uri = database
    register(uri)
    for role in ('anon', 'authenticated'):
        with pytest.raises(psycopg.Error, match='permission denied'):
            query(uri, "select uid from private.profile_uids", role=role, user=uuid.uuid4())
        with pytest.raises(psycopg.Error, match='permission denied'):
            query(uri, "select nextval('private.profile_uid_seq')", role=role, user=uuid.uuid4())

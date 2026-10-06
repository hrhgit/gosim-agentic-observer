"""Personal API tokens for the survey26 CLI (20261004130000): real PostgreSQL."""
import hashlib
import re

import psycopg
import pytest

from test_project_database import database, identity, query, rpc  # noqa: F401


def switch(uri, value):
    query(uri, "update private.cli_config set enabled=%s::jsonb", (value,))


def sha(token):
    return hashlib.sha256(token.encode()).hexdigest()


def authenticate(uri, token, limit=120):
    return query(uri, "select public.cli_token_authenticate(%s,%s)", (sha(token), limit), role="service_role")[0][0]


@pytest.fixture
def users(database):
    switch(database, "true")
    query(database, "delete from private.cli_rate")
    return database, identity(database)[0], identity(database)[0]


def test_switch_defaults_off_and_can_target_single_accounts(database):
    user, _ = identity(database)
    other, _ = identity(database)
    switch(database, "false")
    assert rpc(database, "my_cli_tokens", role="authenticated", user=user)["enabled"] is False
    with pytest.raises(psycopg.Error, match="cli_tokens_disabled"):
        rpc(database, "create_cli_token", "laptop", role="authenticated", user=user)
    switch(database, '{"users": ["%s"]}' % user)
    token = rpc(database, "create_cli_token", "laptop", role="authenticated", user=user)["token"]
    with pytest.raises(psycopg.Error, match="cli_tokens_disabled"):
        rpc(database, "create_cli_token", "laptop", role="authenticated", user=other)
    assert authenticate(database, token)["user_id"] == str(user)
    # One-line rollback: every token stops working at once.
    switch(database, "false")
    with pytest.raises(psycopg.Error, match="cli_tokens_disabled"):
        authenticate(database, token)


def test_token_shown_once_only_hash_stored_and_use_recorded(users):
    uri, user, _ = users
    created = rpc(uri, "create_cli_token", "  agent box  ", role="authenticated", user=user)
    token = created["token"]
    assert re.fullmatch(r"s26_[0-9a-f]{64}", token) and created["name"] == "agent box" and created["hint"] == token[-4:]
    stored = query(uri, "select token_hash, token_hint from private.cli_tokens where id=%s", (created["id"],))[0]
    assert stored == (sha(token), token[-4:])
    assert not query(uri, "select 1 from private.cli_tokens where token_hash=%s or name=%s", (token, token))
    listed = rpc(uri, "my_cli_tokens", role="authenticated", user=user)
    assert listed["enabled"] is True and listed["tokens"][0]["last_used_at"] is None
    assert set(listed["tokens"][0]) == {"id", "name", "hint", "created_at", "last_used_at"}
    who = authenticate(uri, token)
    assert who["user_id"] == str(user) and who["token_id"] == created["id"]
    authenticate(uri, token)
    assert query(uri, "select use_count, last_used_at is not null from private.cli_tokens where id=%s",
                 (created["id"],))[0] == (2, True)
    assert rpc(uri, "my_cli_tokens", role="authenticated", user=user)["tokens"][0]["last_used_at"]
    actions = [r[0] for r in query(uri, "select action from public.audit_log where user_id=%s", (user,))]
    assert "cli_token_created" in actions


def test_revoke_is_owner_only_and_immediate(users):
    uri, user, other = users
    created = rpc(uri, "create_cli_token", "ci", role="authenticated", user=user)
    with pytest.raises(psycopg.Error, match="token_not_found"):
        rpc(uri, "revoke_cli_token", created["id"], role="authenticated", user=other)
    authenticate(uri, created["token"])
    query(uri, "select public.cli_session_put(%s,'v1.x.y',now()+interval '1 hour')", (user,), role="service_role")
    assert rpc(uri, "revoke_cli_token", created["id"], role="authenticated", user=user) is True
    with pytest.raises(psycopg.Error, match="invalid_token"):
        authenticate(uri, created["token"])
    assert rpc(uri, "my_cli_tokens", role="authenticated", user=user)["tokens"] == []
    # The cached server session goes with the last active token.
    assert not query(uri, "select 1 from private.cli_sessions where user_id=%s", (user,))
    with pytest.raises(psycopg.Error, match="token_not_found"):
        rpc(uri, "revoke_cli_token", created["id"], role="authenticated", user=user)


def test_unknown_tokens_limits_names_and_bans(users):
    uri, user, _ = users
    with pytest.raises(psycopg.Error, match="invalid_token"):
        authenticate(uri, "s26_" + "0" * 64)
    for bad in ("", "   ", "x" * 61):
        with pytest.raises(psycopg.Error, match="invalid_token_name"):
            rpc(uri, "create_cli_token", bad, role="authenticated", user=user)
    tokens = [rpc(uri, "create_cli_token", f"t{i}", role="authenticated", user=user)["token"] for i in range(5)]
    with pytest.raises(psycopg.Error, match="token_limit"):
        rpc(uri, "create_cli_token", "sixth", role="authenticated", user=user)
    query(uri, "update public.profiles set is_banned=true where id=%s", (user,))
    with pytest.raises(psycopg.Error, match="account_banned"):
        authenticate(uri, tokens[0])
    with pytest.raises(psycopg.Error, match="account_banned"):
        rpc(uri, "create_cli_token", "x", role="authenticated", user=user)
    query(uri, "update public.profiles set is_banned=false where id=%s", (user,))


def test_rate_limit_is_per_user_across_tokens(users):
    uri, user, other = users
    a = rpc(uri, "create_cli_token", "a", role="authenticated", user=user)["token"]
    b = rpc(uri, "create_cli_token", "b", role="authenticated", user=user)["token"]
    c = rpc(uri, "create_cli_token", "c", role="authenticated", user=other)["token"]
    authenticate(uri, a, 3)
    authenticate(uri, b, 3)
    authenticate(uri, a, 3)
    with pytest.raises(psycopg.Error, match="rate_limited"):
        authenticate(uri, b, 3)
    assert authenticate(uri, c, 3)["user_id"] == str(other)
    # A new minute starts a new window.
    query(uri, "update private.cli_rate set window_start=window_start-interval '1 minute' where user_id=%s", (user,))
    assert authenticate(uri, a, 3)["user_id"] == str(user)


def test_server_functions_and_tables_are_closed_to_users(users):
    uri, user, _ = users
    token = rpc(uri, "create_cli_token", "x", role="authenticated", user=user)["token"]
    for role in ("authenticated", "anon"):
        for statement, args in (("select public.cli_token_authenticate(%s)", (sha(token),)),
                                ("select public.cli_session_get(%s)", (user,)),
                                ("select public.cli_session_put(%s,'x',now())", (user,)),
                                ("select * from private.cli_tokens", ()),
                                ("select * from private.cli_sessions", ())):
            with pytest.raises(psycopg.Error, match="permission denied"):
                query(uri, statement, args, role=role, user=user)
    with pytest.raises(psycopg.Error, match="permission denied"):
        query(uri, "select public.create_cli_token('x')", role="anon")

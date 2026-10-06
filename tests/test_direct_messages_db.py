"""Direct messages between friends (migration 20261005050000): only friends can send, blocking stops delivery both
ways, per-sender limits, participants-only reads, unread counts, and report -> organizer delete/dismiss."""
import uuid

import psycopg
import pytest

from test_project_database import database, query, rpc  # noqa: F401


def person(uri, admin=False):
    user = uuid.uuid4()
    query(uri, "insert into auth.users(id,email) values(%s,%s)", (user, f"{user}@example.test"))
    query(uri, "update public.profiles set name=%s, is_admin=%s where id=%s", (f"P-{str(user)[:6]}", admin, user))
    uid = query(uri, "select uid from private.profile_uids where profile_id=%s", (user,))[0][0]
    return {"id": user, "uid": uid}


def call(uri, user, name, *args):
    return rpc(uri, name, *args, role="authenticated", user=user["id"])


def befriend(uri, a, b):
    call(uri, a, "send_friend_request", b["uid"])
    assert call(uri, b, "send_friend_request", a["uid"]) == {"status": "accepted"}


def send(uri, a, b, text):
    return call(uri, a, "dm_send", str(b["id"]), text)


def bodies(uri, viewer, other, after=0):
    return [(m["from_me"], m["body"]) for m in call(uri, viewer, "dm_thread", str(other["id"]), after)["messages"]]


def people(uri, viewer):
    return {p["user_id"]: p for p in call(uri, viewer, "dm_overview")["people"]}


def test_friends_send_read_and_unread(database):
    uri = database
    a, b = person(uri), person(uri)
    assert send(uri, a, b, "hi") == {"error": "not_friends"}       # strangers cannot
    befriend(uri, a, b)
    first = send(uri, a, b, "  hello https://example.com  ")
    assert isinstance(first["id"], int)
    send(uri, a, b, "second")
    assert call(uri, b, "social_counts") == {"requests": 0, "unread": 2}
    row = people(uri, b)[str(a["id"])]
    assert (row["unread"], row["last_body"], row["last_from_me"], row["is_friend"], row["can_send"]) == (2, "second", False, True, True)
    # Opening the thread marks it read; the text is stored trimmed, oldest first.
    assert bodies(uri, b, a) == [(False, "hello https://example.com"), (False, "second")]
    assert call(uri, b, "social_counts")["unread"] == 0
    reply = send(uri, b, a, "yo")
    assert people(uri, a)[str(b["id"])]["unread"] == 1
    # Friend requests count on the same badge.
    c = person(uri)
    call(uri, c, "send_friend_request", a["uid"])
    assert call(uri, a, "social_counts") == {"requests": 1, "unread": 1}
    assert bodies(uri, a, b, first["id"]) == [(True, "second"), (False, "yo")]  # polling: only newer ones
    assert call(uri, a, "social_counts") == {"requests": 1, "unread": 0} and reply["id"] > first["id"]


def test_validation(database):
    uri = database
    a, b = person(uri), person(uri)
    befriend(uri, a, b)
    assert send(uri, a, b, "   \n ") == {"error": "empty"}
    assert send(uri, a, b, "x" * 1001) == {"error": "too_long"}
    assert "id" in send(uri, a, b, "字" * 1000)
    assert call(uri, a, "dm_send", str(a["id"]), "me") == {"error": "not_friends"}


def test_only_participants_can_read(database):
    uri = database
    a, b, eve = person(uri), person(uri), person(uri)
    befriend(uri, a, b)
    send(uri, a, b, "secret")
    # A third person sees nothing of it, even asking for either side's thread.
    assert call(uri, eve, "dm_thread", str(a["id"]), 0)["messages"] == []
    assert call(uri, eve, "dm_overview")["people"] == []
    assert call(uri, eve, "social_counts")["unread"] == 0
    for role in ("authenticated", "anon"):
        with pytest.raises(psycopg.Error, match="permission denied"):
            query(uri, "select body from private.direct_messages", role=role, user=eve["id"])
    with pytest.raises(psycopg.Error, match="permission denied"):
        rpc(uri, "dm_overview", role="anon")


def test_unfriend_and_block_stop_delivery_both_ways(database):
    uri = database
    a, b = person(uri), person(uri)
    befriend(uri, a, b)
    send(uri, a, b, "before")
    call(uri, a, "remove_friend", str(b["id"]))
    assert send(uri, a, b, "x") == {"error": "not_friends"} and send(uri, b, a, "x") == {"error": "not_friends"}
    # History stays readable; sending is off.
    thread = call(uri, b, "dm_thread", str(a["id"]), 0)
    assert thread["can_send"] is False and [m["body"] for m in thread["messages"]] == ["before"]
    befriend(uri, a, b)
    send(uri, a, b, "unread one")
    call(uri, b, "block_user", str(a["id"]))
    assert send(uri, a, b, "x") == {"error": "not_friends"} and send(uri, b, a, "x") == {"error": "not_friends"}
    # The blocker no longer sees the conversation or its unread messages.
    assert call(uri, b, "social_counts")["unread"] == 0 and str(a["id"]) not in people(uri, b)
    with pytest.raises(psycopg.Error, match="user_not_found"):
        call(uri, b, "dm_thread", str(a["id"]), 0)
    # The blocked side is not told: same answer as for a non-friend.
    assert people(uri, a)[str(b["id"])]["can_send"] is False
    # Even a friendship made again (e.g. a stale path) does not deliver while a block stands.
    query(uri, "insert into private.friendships(user_a,user_b) values(least(%s::uuid,%s::uuid),greatest(%s::uuid,%s::uuid))",
          (a["id"], b["id"], a["id"], b["id"]))
    assert send(uri, a, b, "x") == {"error": "not_friends"}


def test_rate_limits(database):
    uri = database
    a, b = person(uri), person(uri)
    befriend(uri, a, b)
    for i in range(10):
        assert "id" in send(uri, a, b, f"m{i}")
    assert send(uri, a, b, "eleven") == {"error": "rate_minute"}
    # Age those ten past the minute window: then the daily limit (200 / 24 h) applies.
    query(uri, "update private.direct_messages set created_at = clock_timestamp() - interval '2 minutes' where sender_id=%s", (a["id"],))
    query(uri, "insert into private.direct_messages(sender_id,recipient_id,body,created_at) "
               "select %s,%s,'old',clock_timestamp() - interval '1 hour' from generate_series(1,190)", (a["id"], b["id"]))
    assert send(uri, a, b, "too many") == {"error": "rate_day"}
    assert "id" in send(uri, b, a, "the other side is not limited")


def test_report_delete_and_dismiss(database):
    uri = database
    a, b, admin = person(uri), person(uri), person(uri, admin=True)
    befriend(uri, a, b)
    bad = send(uri, a, b, "bad words")["id"]
    meh = send(uri, a, b, "meh")["id"]
    with pytest.raises(psycopg.Error, match="message_not_found"):
        call(uri, a, "dm_report", bad, "own message")                 # only the recipient reports
    call(uri, b, "dm_report", bad, "rude")
    call(uri, b, "dm_report", bad, "again")                           # once per person
    call(uri, b, "dm_report", meh, "")
    with pytest.raises(psycopg.Error, match="admin_only"):
        call(uri, b, "admin_dm_reports")
    reports = {r["message_id"]: r for r in call(uri, admin, "admin_dm_reports")}
    assert reports[bad]["body"] == "bad words" and reports[bad]["reports"] == 1 and reports[bad]["reasons"] == ["rude"]
    assert reports[bad]["sender_id"] == str(a["id"]) and reports[bad]["recipient_id"] == str(b["id"])
    with pytest.raises(psycopg.Error, match="admin_only"):
        call(uri, b, "admin_delete_dm", bad)
    call(uri, admin, "admin_delete_dm", bad)
    call(uri, admin, "admin_dismiss_dm_reports", meh)
    assert call(uri, admin, "admin_dm_reports") == []
    for viewer, other in ((a, b), (b, a)):
        msgs = {m["id"]: m for m in call(uri, viewer, "dm_thread", str(other["id"]), 0)["messages"]}
        assert msgs[bad]["deleted"] is True and msgs[bad]["body"] is None and msgs[meh]["body"] == "meh"
    assert people(uri, b)[str(a["id"])]["last_body"] == "meh"
    with pytest.raises(psycopg.Error, match="message_not_found"):
        call(uri, b, "dm_report", bad, "")

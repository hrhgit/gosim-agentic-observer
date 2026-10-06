"""Friends and team invitations by UID (migration 20261004190000): own data only, uniform answers for unknown
UIDs, a daily limit on every by-UID action, blocking, and invitations through the existing team flow."""
import uuid

import psycopg
import pytest

from test_project_database import database, identity, query, rpc  # noqa: F401


def person(uri, *, team=False, wall=False):
    user = uuid.uuid4()
    query(uri, "insert into auth.users(id,email) values(%s,%s)", (user, f"{user}@example.test"))
    query(uri, "update public.profiles set name=%s, show_on_wall=%s where id=%s", (f"P-{str(user)[:6]}", wall, user))
    team_id = rpc(uri, "create_team", f"Team {str(user)[:8]}", 3, "", "", role="authenticated", user=user) if team else None
    uid = query(uri, "select uid from private.profile_uids where profile_id=%s", (user,))[0][0]
    return {"id": user, "uid": uid, "team": team_id}


def call(uri, user, name, *args):
    return rpc(uri, name, *args, role="authenticated", user=user["id"])


def friends(uri, user):
    return call(uri, user, "my_friends")


def test_request_accept_list_and_remove(database):
    uri = database
    a, b = person(uri), person(uri, team=True)
    sent = call(uri, a, "send_friend_request", b["uid"])
    assert sent["status"] == "sent"
    # The sender sees only the UID they typed while the request is pending.
    out = friends(uri, a)["outgoing"]
    assert out == [{"id": sent["request_id"], "uid": b["uid"], "created_at": out[0]["created_at"]}]
    incoming = friends(uri, b)["incoming"]
    assert [(r["id"], r["user_id"], r["name"]) for r in incoming] == [(sent["request_id"], str(a["id"]), f"P-{str(a['id'])[:6]}")]
    # Repeating is idempotent; nobody else can answer it.
    assert call(uri, a, "send_friend_request", b["uid"])["request_id"] == sent["request_id"]
    with pytest.raises(psycopg.Error, match="request_not_found"):
        call(uri, a, "respond_friend_request", sent["request_id"], True)
    assert call(uri, b, "respond_friend_request", sent["request_id"], True) == "accepted"
    mine = friends(uri, a)
    assert mine["outgoing"] == [] and [f["uid"] for f in mine["friends"]] == [b["uid"]]
    assert mine["friends"][0]["team_name"] == f"Team {str(b['id'])[:8]}" and mine["friends"][0]["in_team"] is True
    assert call(uri, a, "send_friend_request", b["uid"]) == {"status": "already_friends"}
    call(uri, b, "remove_friend", str(a["id"]))
    assert friends(uri, a)["friends"] == [] and friends(uri, b)["friends"] == []
    with pytest.raises(psycopg.Error, match="not_friends"):
        call(uri, a, "remove_friend", str(b["id"]))


def test_crossing_requests_become_a_friendship_and_cancel_decline_work(database):
    uri = database
    a, b, c = person(uri), person(uri), person(uri)
    call(uri, a, "send_friend_request", b["uid"])
    assert call(uri, b, "send_friend_request", a["uid"]) == {"status": "accepted"}
    assert friends(uri, a)["outgoing"] == [] and len(friends(uri, a)["friends"]) == 1
    r = call(uri, a, "send_friend_request", c["uid"])["request_id"]
    call(uri, a, "cancel_friend_request", r)
    assert friends(uri, c)["incoming"] == []
    with pytest.raises(psycopg.Error, match="request_finished"):
        call(uri, a, "cancel_friend_request", r)
    r = call(uri, c, "send_friend_request", a["uid"])["request_id"]
    assert call(uri, a, "respond_friend_request", r, False) == "declined"
    assert friends(uri, c)["outgoing"] == [] and friends(uri, c)["friends"] == []


def test_unknown_banned_and_self_uids(database):
    uri = database
    a, banned = person(uri), person(uri)
    query(uri, "update public.profiles set is_banned=true where id=%s", (banned["id"],))
    assert call(uri, a, "send_friend_request", a["uid"]) == {"error": "self"}
    # An unknown UID and a banned user's UID look exactly the same.
    assert call(uri, a, "send_friend_request", 999999998) == {"error": "uid_unavailable"}
    assert call(uri, a, "send_friend_request", banned["uid"]) == {"error": "uid_unavailable"}


def test_every_by_uid_action_counts_towards_the_daily_limit(database):
    uri = database
    a, b = person(uri), person(uri)
    for _ in range(20):
        assert call(uri, a, "send_friend_request", 999999997) == {"error": "uid_unavailable"}
    assert call(uri, a, "send_friend_request", b["uid"]) == {"error": "daily_limit"}
    assert friends(uri, b)["incoming"] == []
    # Yesterday's actions no longer count.
    query(uri, "update private.uid_actions set created_at=created_at-interval '25 hours' where actor_id=%s", (a["id"],))
    assert call(uri, a, "send_friend_request", b["uid"])["status"] == "sent"


def test_blocking_hides_requests_silently(database):
    uri = database
    a, b = person(uri), person(uri)
    first = call(uri, a, "send_friend_request", b["uid"])["request_id"]
    call(uri, b, "block_user", str(a["id"]))
    assert friends(uri, b)["incoming"] == [] and [x["user_id"] for x in friends(uri, b)["blocked"]] == [str(a["id"])]
    # The blocked sender is not told: their request still reads as pending, and a new one is accepted silently.
    assert [r["id"] for r in friends(uri, a)["outgoing"]] == [first]
    with pytest.raises(psycopg.Error, match="request_not_found"):
        call(uri, b, "respond_friend_request", first, True)
    assert call(uri, b, "send_friend_request", a["uid"]) == {"error": "blocked_by_you"}
    call(uri, b, "unblock_user", str(a["id"]))
    assert call(uri, b, "send_friend_request", a["uid"]) == {"status": "sent", "request_id": call(uri, b, "my_friends")["outgoing"][0]["id"]}


def test_data_is_private(database):
    uri = database
    a = person(uri)
    for table in ("friend_requests", "friendships", "user_blocks", "uid_actions"):
        for role in ("anon", "authenticated"):
            with pytest.raises(psycopg.Error, match="permission denied"):
                query(uri, f"select * from private.{table}", role=role, user=a["id"])
    for fn in ("public.my_friends()", "public.send_friend_request(1)", "public.send_team_invite_by_uid(1)"):
        with pytest.raises(psycopg.Error, match="permission denied"):
            query(uri, f"select {fn}", role="anon")


def test_captain_invites_by_uid_through_the_existing_flow(database):
    uri = database
    captain, member, free, other_team = person(uri, team=True), person(uri), person(uri), person(uri, team=True)
    query(uri, "update public.profiles set team_id=%s where id=%s", (captain["team"], member["id"]))
    assert call(uri, free, "send_team_invite_by_uid", captain["uid"]) == {"error": "not_in_team"}
    assert call(uri, member, "send_team_invite_by_uid", free["uid"]) == {"error": "leader_only"}
    assert call(uri, captain, "send_team_invite_by_uid", captain["uid"]) == {"error": "self"}
    assert call(uri, captain, "send_team_invite_by_uid", 999999996) == {"error": "uid_not_found"}
    assert call(uri, captain, "send_team_invite_by_uid", other_team["uid"]) == {"error": "recipient_in_team"}
    sent = call(uri, captain, "send_team_invite_by_uid", free["uid"])
    assert sent["status"] == "sent"
    # The recipient doesn't need to be on Find teammates; it is an ordinary invitation they accept as usual.
    received = [i for i in call(uri, free, "my_team_invitations") if i["direction"] == "received"]
    assert [(i["id"], i["kind"], i["status"]) for i in received] == [(sent["invitation_id"], "invite", "pending")]
    assert call(uri, free, "respond_team_invite", sent["invitation_id"], True) == "accepted"
    assert query(uri, "select team_id from public.profiles where id=%s", (free["id"],)) == [(captain["team"],)]
    # The team is now full (3): further invitations are refused before any lookup.
    late = person(uri)
    assert call(uri, captain, "send_team_invite_by_uid", late["uid"]) == {"error": "full"}
    query(uri, "update public.profiles set team_id=null where id=%s", (member["id"],))
    query(uri, "update public.teams set is_locked=true where id=%s", (captain["team"],))
    assert call(uri, captain, "send_team_invite_by_uid", late["uid"]) == {"error": "locked"}
    query(uri, "update public.teams set is_locked=false where id=%s", (captain["team"],))
    withdrawn = call(uri, captain, "send_team_invite_by_uid", late["uid"])["invitation_id"]
    call(uri, captain, "cancel_team_invite", withdrawn)
    with pytest.raises(psycopg.Error, match="invitation_finished"):
        call(uri, late, "respond_team_invite", withdrawn, True)


def test_invite_by_uid_has_its_own_daily_limit_and_respects_blocks(database):
    uri = database
    captain, free = person(uri, team=True), person(uri)
    call(uri, free, "block_user", str(captain["id"]))
    assert call(uri, captain, "send_team_invite_by_uid", free["uid"]) == {"status": "sent"}
    assert call(uri, free, "my_team_invitations") == []
    for _ in range(19):
        call(uri, captain, "send_team_invite_by_uid", 999999995)
    assert call(uri, captain, "send_team_invite_by_uid", free["uid"]) == {"error": "daily_limit"}
    # The friend-request allowance is separate.
    assert call(uri, captain, "send_friend_request", 999999995) == {"error": "uid_unavailable"}


def test_badge_counts_requests_waiting_for_an_answer(database):
    uri = database
    a, b, c, d = person(uri), person(uri), person(uri), person(uri)
    count = lambda u: call(uri, u, "friend_request_count")  # noqa: E731
    assert count(a) == 0
    r1 = call(uri, b, "send_friend_request", a["uid"])["request_id"]
    call(uri, c, "send_friend_request", a["uid"])
    call(uri, d, "send_friend_request", a["uid"])
    assert count(a) == 3 and count(b) == 0
    # Looking at the list does not clear it; answering, blocking or a banned sender does.
    friends(uri, a)
    assert count(a) == 3
    call(uri, a, "respond_friend_request", r1, True)
    call(uri, a, "block_user", str(c["id"]))
    query(uri, "update public.profiles set is_banned=true where id=%s", (d["id"],))
    assert count(a) == 0
    with pytest.raises(psycopg.Error, match="permission denied"):
        query(uri, "select public.friend_request_count()", role="anon")


def test_find_by_uid_shows_only_wall_fields_and_is_uniform(database):
    uri = database
    me, other, banned, captain = person(uri), person(uri, team=True), person(uri), person(uri, team=True)
    query(uri, "update public.profiles set email='x@private.test', name='Real Name', nickname='Nick', city='Hidden City', contact='wx:secret' where id=%s", (other["id"],))
    query(uri, "update public.profiles set is_banned=true where id=%s", (banned["id"],))
    card = call(uri, me, "find_by_uid", other["uid"])
    assert card["name"] == "Nick" and card["uid"] == other["uid"] and card["in_team"] is True and card["self"] is False
    assert card["city"] is None and card["contact"] is None and "email" not in card and "Real Name" not in str(card)
    # On the wall, the wall fields appear (as they already do publicly there).
    query(uri, "update public.profiles set show_on_wall=true where id=%s", (other["id"],))
    assert call(uri, me, "find_by_uid", other["uid"])["city"] == "Hidden City"
    # Unknown, banned and out-of-range UIDs look the same.
    assert call(uri, me, "find_by_uid", 999999990) == {"error": "not_found"}
    assert call(uri, me, "find_by_uid", banned["uid"]) == {"error": "not_found"}
    assert call(uri, me, "find_by_uid", 42) == {"error": "not_found"}
    assert call(uri, me, "find_by_uid", me["uid"])["self"] is True
    with pytest.raises(psycopg.Error, match="permission denied"):
        query(uri, "select public.find_by_uid(100000001)", role="anon")


def test_every_lookup_counts_towards_its_daily_limit(database):
    uri = database
    me, other = person(uri), person(uri)
    for _ in range(19):
        call(uri, me, "find_by_uid", 999999991)
    assert call(uri, me, "find_by_uid", other["uid"])["id"] == str(other["id"])   # the 20th
    assert call(uri, me, "find_by_uid", other["uid"]) == {"error": "daily_limit"}
    # Friend requests have their own allowance.
    assert call(uri, me, "send_friend_request", other["uid"])["status"] == "sent"

"""friend_teams (migration 20261005060000): friends' visible teams with join-ability and the caller's pending request."""
import uuid

from test_project_database import database, query, rpc  # noqa: F401


def person(uri, team_size=None):
    user = uuid.uuid4()
    query(uri, "insert into auth.users(id,email) values(%s,%s)", (user, f"{user}@example.test"))
    query(uri, "update public.profiles set name=%s where id=%s", (f"P-{str(user)[:6]}", user))
    team = rpc(uri, "create_team", f"Team {str(user)[:8]}", team_size, "", "", role="authenticated", user=user) if team_size else None
    uid = query(uri, "select uid from private.profile_uids where profile_id=%s", (user,))[0][0]
    return {"id": user, "uid": uid, "team": team}


def call(uri, user, name, *args):
    return rpc(uri, name, *args, role="authenticated", user=user["id"])


def befriend(uri, a, b):
    call(uri, a, "send_friend_request", b["uid"])
    call(uri, b, "send_friend_request", a["uid"])


def teams(uri, user):
    return {r["user_id"]: r for r in call(uri, user, "friend_teams")}


def test_friend_teams(database):
    uri = database
    me, cap, solo, hidden, stranger = person(uri), person(uri, 3), person(uri), person(uri, 3), person(uri, 3)
    for f in (cap, solo, hidden):
        befriend(uri, me, f)
    query(uri, "update public.teams set is_hidden=true where id=%s", (hidden["team"],))
    rows = teams(uri, me)
    # Only friends in visible teams; strangers and hidden teams are not there.
    assert set(rows) == {str(cap["id"])}
    r = rows[str(cap["id"])]
    assert (r["team_id"], r["member_count"], r["max_size"], r["is_locked"], r["requested"]) == (str(cap["team"]), 1, 3, False, False)
    call(uri, me, "request_team_join", cap["team"])
    assert teams(uri, me)[str(cap["id"])]["requested"] is True
    query(uri, "update public.teams set is_locked=true where id=%s", (cap["team"],))
    assert teams(uri, me)[str(cap["id"])]["is_locked"] is True
    assert call(uri, stranger, "friend_teams") == []

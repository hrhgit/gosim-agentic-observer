"""Profile cards for pending team requests/invitations (migration 20261005001000): who may open which card, and
that only wall-type fields come back (never email, real name, or contact unless the person is on the wall)."""
import uuid

import psycopg
import pytest

from test_project_database import database, identity, query, rpc  # noqa: F401


def person(uri, on_wall=False):
    user = uuid.uuid4()
    query(uri, "insert into auth.users(id,email) values(%s,%s)", (user, f"{user}@example.test"))
    query(uri, "update public.profiles set name='Real Name', nickname=%s, blurb='I map galaxies', github='octo', contact='wx: secret',"
               " affiliation='Uni', show_on_wall=%s, seeking='ai', seeking_count=1, astro_level=2 where id=%s",
          (f"N-{str(user)[:6]}", on_wall, user))
    return user


def card(uri, viewer, target):
    return rpc(uri, "person_card", target, role="authenticated", user=viewer)


def team_card(uri, viewer, team):
    return rpc(uri, "team_card", team, role="authenticated", user=viewer)


def test_captain_sees_applicant_card_only_while_the_request_relates_them(database):
    uri = database
    leader, team = identity(uri)
    applicant, stranger = person(uri), person(uri)
    with pytest.raises(psycopg.Error, match="card_not_available"):
        card(uri, leader, applicant)
    invitation = rpc(uri, "request_team_join", team, role="authenticated", user=applicant)
    c = card(uri, leader, applicant)
    assert c["name"].startswith("N-") and c["blurb"] == "I map galaxies" and c["github"] == "octo"
    assert c["seeking"] == "ai" and c["astro_level"] == 2 and c["wechat_qr"] is None
    # Not on the wall: no contact, affiliation; never email or real name anywhere.
    assert c["contact"] is None and c["affiliation"] is None
    assert "email" not in c and "Real Name" not in str(c) and "example.test" not in str(c)
    # The applicant sees the team and its members; an unrelated person sees neither.
    tc = team_card(uri, applicant, team)
    assert tc["members"] and tc["members"][0]["is_leader"] and "email" not in str(tc)
    assert card(uri, applicant, leader)["id"] == str(leader)
    for fn, arg in ((card, applicant), (team_card, team)):
        with pytest.raises(psycopg.Error, match="card_not_available"):
            fn(uri, stranger, arg)
    with pytest.raises(psycopg.Error, match="permission denied"):
        query(uri, "select public.person_card(%s)", (applicant,), role="anon")
    # Once declined the relationship is gone.
    rpc(uri, "respond_team_invite", invitation, False, role="authenticated", user=leader)
    with pytest.raises(psycopg.Error, match="card_not_available"):
        card(uri, leader, applicant)
    with pytest.raises(psycopg.Error, match="card_not_available"):
        team_card(uri, applicant, team)


def test_wall_people_are_visible_to_anyone_signed_in_and_qr_follows_its_visibility(database):
    uri = database
    owner, viewer = person(uri, on_wall=True), person(uri)
    c = card(uri, viewer, owner)
    assert c["contact"] == "wx: secret" and c["affiliation"] == "Uni"
    path = f"{owner}/{uuid.uuid4()}.png"
    query(uri, "insert into storage.objects(bucket_id,name) values('wechat-qr',%s)", (path,))
    rpc(uri, "wechat_qr_store", str(owner), path, role="service_role")
    assert card(uri, viewer, owner)["wechat_qr"] is None  # friends only by default
    rpc(uri, "set_wechat_qr_visibility", "all", role="authenticated", user=owner)
    assert card(uri, viewer, owner)["wechat_qr"] == path
    query(uri, "update public.profiles set is_banned=true where id=%s", (owner,))
    with pytest.raises(psycopg.Error, match="card_not_available"):
        card(uri, viewer, owner)


def test_invited_person_sees_the_inviting_team(database):
    uri = database
    leader, team = identity(uri)
    guest = person(uri, on_wall=True)  # invitations go to people on the wall
    rpc(uri, "send_team_invite", guest, role="authenticated", user=leader)
    tc = team_card(uri, guest, team)
    assert tc["id"] == str(team) and [m["id"] for m in tc["members"]] == [str(leader)]
    assert card(uri, leader, guest)["blurb"] == "I map galaxies"

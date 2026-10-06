"""Nicknames persist independently of account names and respect existing visibility rules."""
from __future__ import annotations

import json
import uuid

import pytest

from conftest import Client


def register(hs, **metadata):
    email = f"nickname-{uuid.uuid4().hex}@test.org"
    status, session = Client(hs.url).call(
        "POST", "/auth/v1/signup",
        {"email": email, "password": "password123", "data": {"name": "Account Name", **metadata}},
    )
    assert status == 200, session
    client = Client(hs.url, session["access_token"])
    status, profile = client.rpc("me")
    assert status == 200
    return client, profile


def update_profile(client, user_id, **values):
    return client.call(
        "PATCH", f"/rest/v1/profiles?id=eq.{user_id}", values,
        headers={"Prefer": "return=representation"},
    )


def wall_entry(hs, user_id):
    status, rows = Client(hs.url).rpc("participants_wall", {"p_limit": 200})
    assert status == 200
    return next((row for row in rows if row["id"] == user_id), None)


def test_signup_and_edit_preserve_the_account_name(hs):
    client, profile = register(
        hs, nickname="  星际探索者 🐈  ", github="octocat", astro_level="2", ai_level="3",
        seeking="astro", seeking_count="2", show_on_wall="true", contact="Private Contact",
    )
    assert profile["name"] == "Account Name"
    assert profile["nickname"] == "星际探索者 🐈"
    assert (profile["astro_level"], profile["ai_level"], profile["seeking_count"]) == (2, 3, 2)
    assert profile["seeking"] == "astro" and profile["looking_for_team"]
    assert profile["github"] == "octocat" and profile["contact"] == "Private Contact"

    status, rows = update_profile(client, profile["id"], nickname="  Changed Nickname  ")
    assert status == 200, rows
    assert rows[0]["nickname"] == "Changed Nickname" and rows[0]["name"] == "Account Name"
    status, refreshed = client.rpc("me")
    assert status == 200 and refreshed["nickname"] == "Changed Nickname"


def test_public_wall_exposes_only_display_name_and_keeps_visibility_rules(hs):
    client, profile = register(hs, nickname="Public Nickname", show_on_wall="true")
    entry = wall_entry(hs, profile["id"])
    assert entry["name"] == "Public Nickname"
    assert "Account Name" not in json.dumps(entry)
    assert "email" not in entry and "contact" not in entry and "nickname" not in entry

    status, rows = Client(hs.url).select("profiles", f"select=name,nickname&id=eq.{profile['id']}")
    assert status in (401, 403) or (status == 200 and rows == [])
    assert update_profile(client, profile["id"], show_on_wall=False)[0] == 200
    assert wall_entry(hs, profile["id"]) is None
    assert update_profile(client, profile["id"], show_on_wall=True)[0] == 200
    hs.sql("update public.profiles set is_banned = true where id = %s", (profile["id"],))
    assert wall_entry(hs, profile["id"]) is None


def test_omitted_and_cleared_nickname_fall_back_to_existing_name(hs):
    client, profile = register(hs, show_on_wall="true")
    assert profile["nickname"] == ""
    assert wall_entry(hs, profile["id"])["name"] == "Account Name"
    assert update_profile(client, profile["id"], nickname="Nickname")[0] == 200
    assert wall_entry(hs, profile["id"])["name"] == "Nickname"
    for blank in ("", " \t\n\u00a0\u3000\ufeff ", None):
        status, rows = update_profile(client, profile["id"], nickname=blank)
        assert status == 200 and rows[0]["nickname"] == "", rows
        assert wall_entry(hs, profile["id"])["name"] == "Account Name"


@pytest.mark.parametrize("character", ["界", "🐈"])
def test_length_counts_unicode_code_points_after_trimming(hs, character):
    client, profile = register(hs)
    value = character * 40
    status, rows = update_profile(client, profile["id"], nickname=f"\ufeff\u00a0\t{value}\n\u3000")
    assert status == 200 and rows[0]["nickname"] == value, rows
    status, error = update_profile(client, profile["id"], nickname=character * 41)
    assert status == 400 and error["code"] == "23514", error
    assert "profiles_nickname_length" in error["message"]
    assert client.rpc("me")[1]["nickname"] == value


def test_signup_rejects_oversized_nickname_and_still_enforces_registration_deadline(hs):
    anon = Client(hs.url)
    email = f"nickname-invalid-{uuid.uuid4().hex}@test.org"
    status, _ = anon.call("POST", "/auth/v1/signup", {
        "email": email, "password": "password123", "data": {"name": "Account Name", "nickname": "🐈" * 41},
    })
    assert status >= 400
    assert hs.sql("select id from auth.users where email = %s", (email,)) == []

    previous = hs.sql("select value from public.site_settings where key = 'registration_deadline'")[0][0]
    try:
        hs.sql("update public.site_settings set value = to_jsonb((now() - interval '1 day')::text) where key = 'registration_deadline'")
        status, _ = anon.call("POST", "/auth/v1/signup", {
            "email": email, "password": "password123", "data": {"name": "Account Name", "nickname": "Valid"},
        })
        assert status >= 400
        assert hs.sql("select id from auth.users where email = %s", (email,)) == []
    finally:
        hs.sql("update public.site_settings set value = %s where key = 'registration_deadline'", (json.dumps(previous),))


def test_participants_cannot_edit_others_or_expand_profile_permissions(hs):
    owner, owner_profile = register(hs, nickname="Owner")
    other, other_profile = register(hs, nickname="Other")
    status, rows = update_profile(other, owner_profile["id"], nickname="Unauthorized")
    assert status == 200 and rows == []
    assert owner.rpc("me")[1]["nickname"] == "Owner"
    status, _ = update_profile(other, other_profile["id"], nickname="Other", is_admin=True)
    assert status in (401, 403)
    status, rows = Client(hs.url).call("PATCH", f"/rest/v1/profiles?id=eq.{owner_profile['id']}", {"nickname": "Anonymous"})
    assert status in (401, 403) or (status in (200, 204) and not rows)
    assert owner.rpc("me")[1]["nickname"] == "Owner"


def test_team_members_use_nicknames_without_exposing_other_teams(hs):
    leader, leader_profile = register(hs, name="Leader Account", nickname="Captain")
    member, member_profile = register(hs, name="Member Account")
    outsider, _ = register(hs)
    status, team_id = leader.rpc("create_team", {"p_name": f"Nicknames {uuid.uuid4().hex[:8]}", "p_max_size": 2})
    assert status == 200, team_id
    status, invite = leader.rpc("regenerate_invite_code")
    assert status == 200
    status, _ = member.rpc("join_team", {"p_invite_code": invite})
    assert status == 200
    status, rows = member.rpc("team_members", {"p_team_id": team_id})
    assert status == 200
    by_id = {row["id"]: row for row in rows}
    assert by_id[leader_profile["id"]]["name"] == "Captain"
    assert by_id[member_profile["id"]]["name"] == "Member Account"
    assert "Leader Account" not in json.dumps(rows)
    assert outsider.rpc("team_members", {"p_team_id": team_id}) == (200, [])
    status, rows = update_profile(member, leader_profile["id"], nickname="Unauthorized")
    assert status == 200 and rows == []
    assert leader.rpc("me")[1]["nickname"] == "Captain"

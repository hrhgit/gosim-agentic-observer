"""Optional WeChat QR codes (migration 20261004235000): private bucket, owner-chosen visibility, reports and
organizer removal. Uploads/removals are the wechat-qr edge function's (service role) job."""
import uuid

import psycopg
import pytest

from test_project_database import database, query, rpc  # noqa: F401


def person(uri, admin=False):
    user = uuid.uuid4()
    query(uri, "insert into auth.users(id,email) values(%s,%s)", (user, f"{user}@example.test"))
    query(uri, "update public.profiles set name=%s, is_admin=%s where id=%s", (f"P-{str(user)[:6]}", admin, user))
    return user


def store(uri, user):
    path = f"{user}/{uuid.uuid4()}.png"
    query(uri, "insert into storage.objects(bucket_id,name) values('wechat-qr',%s)", (path,))
    old = rpc(uri, "wechat_qr_store", str(user), path, role="service_role")
    return path, old


def visible(uri, viewer, owners):
    return query(uri, "select public.wechat_qr_visible(%s::uuid[])", ([str(o) for o in owners],), role="authenticated", user=viewer)[0][0]


def signable(uri, viewer, path):
    """What the storage policy lets the viewer read (createSignedUrl needs this)."""
    return query(uri, "select name from storage.objects where bucket_id='wechat-qr' and name=%s", (path,),
                 role="authenticated", user=viewer)


def befriend(uri, a, b):
    query(uri, "insert into private.friendships(user_a,user_b) values(least(%s::uuid,%s::uuid),greatest(%s::uuid,%s::uuid))", (a, b, a, b))


def test_bucket_is_private_and_limited(database):
    assert query(database, "select public,file_size_limit,allowed_mime_types from storage.buckets where id='wechat-qr'") == \
        [(False, 1048576, ['image/png', 'image/jpeg', 'image/webp'])]


def test_friends_only_by_default_then_everyone_signed_in(database):
    uri = database
    owner, friend, stranger = person(uri), person(uri), person(uri)
    befriend(uri, owner, friend)
    path, old = store(uri, owner)
    assert old is None
    assert rpc(uri, "my_wechat_qr", role="authenticated", user=owner)["visibility"] == "friends"
    assert visible(uri, friend, [owner]) == {str(owner): path}
    assert visible(uri, stranger, [owner]) == {}
    assert visible(uri, owner, [owner]) == {str(owner): path}
    assert signable(uri, friend, path) == [(path,)] and signable(uri, stranger, path) == []
    rpc(uri, "set_wechat_qr_visibility", "all", role="authenticated", user=owner)
    assert visible(uri, stranger, [owner]) == {str(owner): path} and signable(uri, stranger, path) == [(path,)]
    # Never anonymous.
    assert query(uri, "select name from storage.objects where bucket_id='wechat-qr'", role="anon") == []
    with pytest.raises(psycopg.Error, match="permission denied"):
        query(uri, "select public.wechat_qr_visible(%s::uuid[])", ([str(owner)],), role="anon")
    # A banned owner's code is hidden from everyone else.
    query(uri, "update public.profiles set is_banned=true where id=%s", (owner,))
    assert visible(uri, stranger, [owner]) == {} and signable(uri, stranger, path) == []
    query(uri, "update public.profiles set is_banned=false where id=%s", (owner,))
    with pytest.raises(psycopg.Error, match="invalid_visibility"):
        rpc(uri, "set_wechat_qr_visibility", "public", role="authenticated", user=owner)


def test_replacing_returns_the_old_object_and_only_the_current_one_is_readable(database):
    uri = database
    owner, friend = person(uri), person(uri)
    befriend(uri, owner, friend)
    first, _ = store(uri, owner)
    second, old = store(uri, owner)
    assert old == first
    assert signable(uri, friend, first) == [] and signable(uri, friend, second) == [(second,)]


def test_only_the_service_role_stores_or_removes(database):
    uri = database
    owner = person(uri)
    for role in ("authenticated", "anon"):
        with pytest.raises(psycopg.Error, match="permission denied"):
            rpc(uri, "wechat_qr_store", str(owner), f"{owner}/{uuid.uuid4()}.png", role=role, user=owner)
        with pytest.raises(psycopg.Error, match="permission denied"):
            rpc(uri, "wechat_qr_remove", str(owner), str(owner), role=role, user=owner)
    for table in ("wechat_qr", "wechat_qr_reports"):
        with pytest.raises(psycopg.Error, match="permission denied"):
            query(uri, f"select * from private.{table}", role="authenticated", user=owner)


def test_report_and_organizer_removal(database):
    uri = database
    owner, viewer, other, admin = person(uri), person(uri), person(uri), person(uri, admin=True)
    path, _ = store(uri, owner)
    rpc(uri, "set_wechat_qr_visibility", "all", role="authenticated", user=owner)
    with pytest.raises(psycopg.Error, match="no_wechat_qr"):
        rpc(uri, "report_wechat_qr", str(owner), "self", role="authenticated", user=owner)
    rpc(uri, "report_wechat_qr", str(owner), "not a WeChat code", role="authenticated", user=viewer)
    rpc(uri, "report_wechat_qr", str(owner), "again", role="authenticated", user=viewer)  # counted once
    with pytest.raises(psycopg.Error, match="admin_only"):
        rpc(uri, "admin_wechat_qr_reports", role="authenticated", user=viewer)
    reports = [r for r in rpc(uri, "admin_wechat_qr_reports", role="authenticated", user=admin) if r["owner_id"] == str(owner)]
    assert [(r["path"], r["reports"], r["current"], r["reasons"]) for r in reports] == [(path, 1, True, ["not a WeChat code"])]
    assert signable(uri, admin, path) == [(path,)]
    # Someone else cannot remove it; an organizer can (the function then deletes the returned object).
    with pytest.raises(psycopg.Error, match="admin_only"):
        rpc(uri, "wechat_qr_remove", str(owner), str(other), role="service_role")
    assert rpc(uri, "wechat_qr_remove", str(owner), str(admin), role="service_role") == path
    assert visible(uri, viewer, [owner]) == {}
    assert [r for r in rpc(uri, "admin_wechat_qr_reports", role="authenticated", user=admin) if r["owner_id"] == str(owner)] == []
    # Owners remove their own.
    path2, _ = store(uri, owner)
    assert rpc(uri, "wechat_qr_remove", str(owner), str(owner), role="service_role") == path2
    assert rpc(uri, "my_wechat_qr", role="authenticated", user=owner) is None

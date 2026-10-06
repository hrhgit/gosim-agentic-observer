"""Repository submissions are pinned and preserved at submission time (20261004072000)."""
from __future__ import annotations

import uuid

import psycopg
import pytest

from test_project_database import database, identity, query, rpc, setup  # noqa: F401

COMMIT = "1" * 40


def submit(s):
    return rpc(s["uri"], "observer_create_project", "Agent", "repository", "https://github.com/example/agent",
               role="authenticated", user=s["user"])


def test_switch_defaults_off_and_is_service_role_only(setup):
    s = setup; uri = s["uri"]
    assert rpc(uri, "observer_source_snapshots_enabled") is False
    assert query(uri, "select public.observer_set_source_snapshots(true)", role="service_role") == [(True,)]
    for role in ("authenticated", "anon"):
        for statement in ("select public.observer_set_source_snapshots(false)",
                          "select public.observer_source_snapshots_enabled()"):
            with pytest.raises(psycopg.Error, match="permission denied"):
                query(uri, statement, role=role, user=s["user"])
    assert query(uri, "select public.observer_set_source_snapshots(false)", role="service_role") == [(False,)]
    assert query(uri, "select public from storage.buckets where id='observer-sources'") == [(False,)]


def test_snapshot_pins_the_revision_and_reaches_preparation(setup):
    s = setup; uri = s["uri"]
    rev = submit(s)
    path = f"{s['user']}/{COMMIT}-{uuid.uuid4()}.zip"
    rpc(uri, "observer_record_source_snapshot", rev, COMMIT, path, 1234, "a" * 64)
    assert query(uri, "select submitted_commit from public.observer_revisions where id=%s", (rev,)) == [(COMMIT,)]
    row = query(uri, "select source_location,source_commit,storage_path,bytes from private.observer_source_snapshots where revision_id=%s", (rev,))
    assert row == [("https://github.com/example/agent", COMMIT, path, 1234)]
    # A second record for the same revision is refused; the pinned commit never changes.
    with pytest.raises(psycopg.Error):
        rpc(uri, "observer_record_source_snapshot", rev, "2" * 40, f"{s['user']}/{'2' * 40}-{uuid.uuid4()}.zip", 1, "b" * 64)
    # The snapshot outlives the revision row.
    query(uri, "delete from public.observer_revisions where id=%s", (rev,))
    assert query(uri, "select count(*) from private.observer_source_snapshots where revision_id=%s", (rev,)) == [(1,)]


def test_invalid_snapshots_are_refused(setup):
    s = setup; uri = s["uri"]
    rev = submit(s)
    for commit, path in (("xyz", f"{s['user']}/xyz-{uuid.uuid4()}.zip"),
                         (COMMIT, f"../{COMMIT}.zip"),
                         (COMMIT, f"{s['user']}/{'2' * 40}-{uuid.uuid4()}.zip")):
        with pytest.raises(psycopg.Error, match="invalid_source_snapshot"):
            rpc(uri, "observer_record_source_snapshot", rev, commit, path, 1, "a" * 64)
    with pytest.raises(psycopg.Error, match="revision_not_found"):
        rpc(uri, "observer_record_source_snapshot", uuid.uuid4(), COMMIT, f"{s['user']}/{COMMIT}-{uuid.uuid4()}.zip", 1, "a" * 64)
    with pytest.raises(psycopg.Error, match="permission denied"):
        rpc(uri, "observer_record_source_snapshot", rev, COMMIT, f"{s['user']}/{COMMIT}-{uuid.uuid4()}.zip", 1, "a" * 64,
            role="authenticated", user=s["user"])


def test_branch_and_folder_are_recorded_and_preparation_reads_the_snapshot(setup):
    """20261004230000: a named branch/tag/commit and project folder (repository source options)."""
    s = setup; uri = s["uri"]
    rev = submit(s)
    assert rpc(uri, "observer_preparation_source", rev) is None
    path = f"{s['user']}/{COMMIT}-{uuid.uuid4()}.zip"
    rpc(uri, "observer_record_source_snapshot", rev, COMMIT, path, 10, "a" * 64, "feature/x", "apps/agent")
    assert query(uri, "select submitted_commit,source_ref,source_subdir,source_location from public.observer_revisions where id=%s",
                 (rev,)) == [(COMMIT, "feature/x", "apps/agent", "https://github.com/example/agent")]
    assert query(uri, "select source_ref,source_subdir from private.observer_source_snapshots where revision_id=%s",
                 (rev,)) == [("feature/x", "apps/agent")]
    assert rpc(uri, "observer_preparation_source", rev) == path
    # Participants see the ref and folder on their own revision but cannot call the service functions.
    assert query(uri, "select source_ref,source_subdir from public.observer_revisions where id=%s", (rev,),
                 role="authenticated", user=s["user"]) == [("feature/x", "apps/agent")]
    with pytest.raises(psycopg.Error, match="permission denied"):
        query(uri, "select public.observer_preparation_source(%s)", (rev,), role="authenticated", user=s["user"])
    # The five-argument call (edge functions deployed before this migration) still works.
    other = submit(s)
    rpc(uri, "observer_record_source_snapshot", other, COMMIT, f"{s['user']}/{COMMIT}-{uuid.uuid4()}.zip", 1, "a" * 64)
    assert query(uri, "select source_ref,source_subdir from public.observer_revisions where id=%s", (other,)) == [(None, None)]


def test_invalid_branch_or_folder_is_refused(setup):
    s = setup; uri = s["uri"]
    rev = submit(s)
    for ref, subdir in (("a b", None), ("x" * 201, None), (None, "../up"), (None, "a/./b"), (None, "/abs"), (None, "a//b")):
        with pytest.raises(psycopg.Error, match="invalid_source_snapshot"):
            rpc(uri, "observer_record_source_snapshot", rev, COMMIT, f"{s['user']}/{COMMIT}-{uuid.uuid4()}.zip", 1,
                "a" * 64, ref, subdir)

"""Finished runs keep only the committed CSV rows once their result is archived."""
from psycopg.types.json import Jsonb

from test_project_database import database, query, rpc, session, setup  # noqa: F401

ARCHIVE = "github:AGENTIC-OBSERVER26-runner-1/participant-" + "0" * 32 + "@" + "a" * 40


def _fill(uri, run):
    query(uri, "insert into private.observer_messages(run_id,sequence,observation,response,committed) values(%s,1,%s,%s,%s)",
          (run, Jsonb({"big": "x" * 1000}), Jsonb({"action": "wait"}), Jsonb({"rows": [{"decision_id": "D1"}]})))
    query(uri, "update private.observer_sessions set publication=%s where run_id=%s", (Jsonb({"catalog": "y" * 1000}), run))


def test_archived_scored_runs_are_compacted_and_running_ones_are_untouched(setup):
    s = setup; uri = s["uri"]
    done, _participant, _engine = session(s)
    _fill(uri, done)
    query(uri, "update public.observer_runs set status='scored',result_path=%s,finished_at=now()-interval '2 hours' where id=%s",
          (ARCHIVE, done))
    assert query(uri, "select private.observer_compact_finished_runs()")[0][0] == 1
    assert query(uri, "select observation,response,committed from private.observer_messages where run_id=%s", (done,)) == [
        ({}, None, {"rows": [{"decision_id": "D1"}]})]
    assert query(uri, "select publication from private.observer_sessions where run_id=%s", (done,)) == [(None,)]
    assert query(uri, "select private.observer_compact_finished_runs()")[0][0] == 0
    # A run that finished minutes ago is left alone.
    query(uri, "update public.observer_runs set finished_at=now() where id=%s", (done,))
    query(uri, "update private.observer_messages set observation=%s where run_id=%s", (Jsonb({"a": 1}), done))
    assert query(uri, "select private.observer_compact_finished_runs()")[0][0] == 0


def test_runs_without_an_archived_result_are_kept(setup):
    s = setup; uri = s["uri"]
    run, _participant, _engine = session(s)
    _fill(uri, run)
    for status, path in (("failed", None), ("cancelled", None), ("scored", None)):
        query(uri, "update public.observer_runs set status=%s,result_path=%s,finished_at=now()-interval '2 hours' where id=%s",
              (status, path, run))
        assert query(uri, "select private.observer_compact_finished_runs()")[0][0] == 0
    assert query(uri, "select observation,response from private.observer_messages where run_id=%s", (run,)) == [
        ({"big": "x" * 1000}, {"action": "wait"})]
    assert query(uri, "select publication is not null from private.observer_sessions where run_id=%s", (run,)) == [(True,)]

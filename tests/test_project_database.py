"""Real PostgreSQL authorization, atomic quota and sequential-session tests."""
from __future__ import annotations

import concurrent.futures
import json
import secrets
import sys
import uuid
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests" / "supabase"))
from pg import start  # noqa: E402


@pytest.fixture(scope="module")
def database():
    server, uri = start()
    yield uri
    server.cleanup()


def query(uri, statement, args=(), *, role=None, user=None):
    with psycopg.connect(uri) as conn:
        if role:
            conn.execute(sql.SQL("set local role {}").format(sql.Identifier(role)))
            conn.execute("select set_config('request.jwt.claims', %s, true)",
                         (json.dumps({"role": role, "sub": str(user) if user else None}),))
        cur = conn.execute(statement, args or None)
        return cur.fetchall() if cur.description else []


def rpc(uri, name, *args, role="service_role", user=None):
    return query(uri, sql.SQL("select public.{}({})").format(
        sql.Identifier(name), sql.SQL(",").join(sql.Placeholder() for _ in args)),
        tuple(Jsonb(a) if isinstance(a, (dict, list)) else a for a in args), role=role, user=user)[0][0]


def identity(uri, *, team=None):
    user = uuid.uuid4()
    query(uri, "insert into auth.users(id,email) values(%s,%s)", (user, f"{user}@example.test"))
    if team is None:
        team = rpc(uri, "create_team", f"Project {str(user)[:8]}", 3, "", "",
                   role="authenticated", user=user)
    else:
        query(uri, "update public.profiles set team_id=%s where id=%s", (team, user))
    return user, team


@pytest.fixture
def setup(database):
    user, team = identity(database)
    phase, scenario, provider = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    query(database, "insert into public.phases(id,slug,name_en,name_zh,daily_limit) values(%s,%s,'Test','Test',10)",
          (phase, str(phase)))
    query(database, """insert into public.observer_phase_settings
          (phase_id,projects_enabled,local_sessions_enabled,model_token_limit,model_call_limit,model_concurrency)
          values(%s,true,true,1000,10,4)""", (phase,))
    query(database, "insert into public.scenarios(id,slug,name) values(%s,%s,'Test')", (scenario, str(scenario)))
    query(database, "insert into public.phase_scenarios values(%s,%s)", (phase, scenario))
    query(database, """insert into private.observer_providers
          (id,name,base_url,encrypted_key,models,enabled,daily_token_limit)
          values(%s,'Test','https://models.example.test/v1','ciphertext',array['test-model'],true,10000)""", (provider,))
    return {"uri": database, "user": user, "team": team, "phase": phase, "scenario": scenario, "provider": provider}


def session(s):
    uri = s["uri"]
    batch = rpc(uri, "observer_create_batch", s["phase"], None, role="authenticated", user=s["user"])
    run = query(uri, "select id from public.observer_runs where batch_id=%s", (batch,))[0][0]
    participant, engine = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    rpc(uri, "observer_open_session", run, participant, engine)
    return run, participant, engine


def test_old_phase_scores_and_permissions_are_not_modified():
    server, uri = start(apply_migrations=False)
    try:
        query(uri, (ROOT / "tests/supabase/auth_stub.sql").read_text())
        migrations = sorted((ROOT / "supabase/migrations").glob("*.sql"))
        for path in migrations:
            if path.name < "20260925000100":
                query(uri, path.read_text())
        user, team = identity(uri)
        phase, scenario = uuid.uuid4(), uuid.uuid4()
        query(uri, "insert into public.phases(id,slug,name_en,name_zh,allow_agents) values(%s,'legacy','Legacy','Legacy',false)", (phase,))
        query(uri, "insert into public.scenarios(id,slug,name) values(%s,'legacy','Legacy')", (scenario,))
        query(uri, "insert into public.phase_scenarios values(%s,%s)", (phase,scenario))
        query(uri, """insert into public.submissions(team_id,user_id,phase_id,scenario_id,kind,storage_path,
              status,score,science_score,completion,uniformity,finished_at) values(%s,%s,%s,%s,'results',
              'historic-decisions.csv','scored',21085.3,19000,0.9,0.8,now())""", (team,user,phase,scenario))
        tables = [r[0] for r in query(uri, "select tablename from pg_tables where schemaname='public' order by tablename")]
        def snapshot():
            return {table: query(uri, sql.SQL("select to_jsonb(t)::text from public.{} t order by 1")
                                .format(sql.Identifier(table))) for table in tables}
        before = snapshot()
        functions = query(uri, """select p.oid::regprocedure::text,p.proacl::text from pg_proc p
                          join pg_namespace n on n.oid=p.pronamespace where n.nspname in ('public','private')""")
        for path in migrations:
            if path.name >= "20260925000100":
                query(uri, path.read_text())
        assert snapshot() == before
        assert query(uri, "select count(*) from public.observer_phase_settings") == [(0,)]
        for name, permissions in functions:
            assert query(uri, "select proacl::text from pg_proc where oid=%s::regprocedure", (name,)) == [(permissions,)]
        with pytest.raises(psycopg.Error, match="phase_closed"):
            rpc(uri, "observer_create_batch", phase, None, role="authenticated", user=user)
    finally:
        server.cleanup()


def test_team_reads_and_approval_are_isolated_and_immutable(setup):
    s=setup; uri=s["uri"]
    outsider, _ = identity(uri)
    teammate, _ = identity(uri, team=s["team"])
    rev=rpc(uri,"observer_create_project","Rust project","repository","https://github.com/example/agent",
            role="authenticated",user=s["user"])
    assert query(uri,"select id from public.observer_revisions where id=%s",(rev,),
                 role="authenticated",user=outsider)==[]
    assert query(uri,"select id from public.observer_revisions where id=%s",(rev,),
                 role="authenticated",user=teammate)==[(rev,)]
    with pytest.raises(psycopg.Error,match="permission denied"):
        query(uri,"update public.observer_revisions set status='approved' where id=%s",(rev,),
              role="authenticated",user=s["user"])
    with pytest.raises(psycopg.Error,match="revision_not_found"):
        rpc(uri,"observer_approve_revision",rev,"a"*64,role="authenticated",user=outsider)
    with pytest.raises(psycopg.Error,match="check constraint"):
        query(uri,"update public.observer_revisions set status='reviewable',source_digest=%s,approval_digest=%s,manifest='{}' where id=%s",
              ("a"*64,"b"*64,rev))
    query(uri,"""update public.observer_revisions set status='reviewable',source_digest=%s,approval_digest=%s,
          manifest=%s,public_test='{"passed":true}' where id=%s""",
          ("a"*64,"b"*64,Jsonb({"image":"python@sha256:"+"c"*64}),rev))
    with pytest.raises(psycopg.Error,match="stale_approval"):
        rpc(uri,"observer_approve_revision",rev,"a"*64,role="authenticated",user=s["user"])
    rpc(uri,"observer_approve_revision",rev,"b"*64,role="authenticated",user=teammate)
    rpc(uri,"observer_approve_revision",rev,"b"*64,role="authenticated",user=teammate)
    with pytest.raises(psycopg.Error,match="approved_revision_immutable"):
        query(uri,"update public.observer_revisions set manifest='{}' where id=%s",(rev,))
    query(uri,"update public.profiles set is_banned=true where id=%s",(teammate,))
    assert query(uri,"select id from public.observer_revisions where id=%s",(rev,),
                 role="authenticated",user=teammate)==[]


def test_only_bulk_transfer_rpcs_receive_longer_database_budget(setup):
    rows=query(setup['uri'],"""select proname from pg_proc where pronamespace='public'::regnamespace
        and 'statement_timeout=60s'=any(proconfig) order by proname""")
    assert rows==[('observer_poll',),('observer_publish_initial',),('observer_publish_step',)]


def test_batch_freezes_all_scenarios_and_admission_is_atomic(setup):
    s=setup; uri=s["uri"]
    extra=uuid.uuid4()
    query(uri,"insert into public.scenarios(id,slug,name) values(%s,%s,'Extra')",(extra,str(extra)))
    query(uri,"insert into public.phase_scenarios values(%s,%s)",(s["phase"],extra))
    # One evaluation in flight at a time here, so concurrent admission must create exactly one.
    query(uri,"update public.observer_phase_settings set max_active_evaluations=1 where phase_id=%s",(s["phase"],))
    def create(_):
        try:
            return rpc(uri,"observer_create_batch",s["phase"],None,role="authenticated",user=s["user"])
        except psycopg.Error as exc:
            assert "batch_already_active" in str(exc)
            return None
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        results=list(pool.map(create,range(6)))
    assert len([r for r in results if r])==1
    batch=next(r for r in results if r)
    assert {r[0] for r in query(uri,"select scenario_id from public.observer_runs where batch_id=%s",(batch,))}=={s["scenario"],extra}


def test_protocol_rejects_future_data_forgery_and_conflicting_retries(setup):
    s=setup; uri=s["uri"]; run,participant,engine=session(s)
    with pytest.raises(psycopg.Error,match="invalid_or_expired_capability"):
        rpc(uri,"observer_publish_initial",run,participant,{"hidden":"forged"})
    with pytest.raises(psycopg.Error,match="permission denied"):
        rpc(uri,"observer_publish_initial",run,engine,{},role="authenticated",user=s["user"])
    with pytest.raises(psycopg.Error,match="permission denied"):
        query(uri,"select * from private.observer_sessions",role="authenticated",user=s["user"])
    rpc(uri,"observer_publish_initial",run,engine,{"tiles":["public"]})
    assert rpc(uri,"observer_poll",run,participant)["publication"]=={"tiles":["public"]}
    with pytest.raises(psycopg.Error,match="participant_not_ready"):
        rpc(uri,"observer_begin",run,engine)
    rpc(uri,"observer_ready",run,participant)
    rpc(uri,"observer_begin",run,engine)
    action={"protocol_version":"participant-agent-protocol-v2","message_type":"decision_response",
            "decision_sequence":1,"action":"wait"}
    with pytest.raises(psycopg.Error,match="step_not_published"):
        rpc(uri,"observer_respond",run,participant,1,action)
    rpc(uri,"observer_publish_step",run,engine,1,{"night":0,"decision_sequence":1})
    assert rpc(uri,"observer_poll",run,participant)["observation"]=={"night":0,"decision_sequence":1}
    with pytest.raises(psycopg.Error,match="step_out_of_order"):
        rpc(uri,"observer_publish_step",run,engine,2,{"night":1})
    rpc(uri,"observer_respond",run,participant,1,action)
    rpc(uri,"observer_respond",run,participant,1,action)
    with pytest.raises(psycopg.Error,match="response_conflict"):
        rpc(uri,"observer_respond",run,participant,1,{**action,"action":"observe"})
    assert rpc(uri,"observer_poll",run,engine,"engine")["response"]==action
    with pytest.raises(psycopg.Error,match="invalid_or_expired_capability"):
        rpc(uri,"observer_commit_step",run,participant,1,{"score":99999})
    rpc(uri,"observer_commit_step",run,engine,1,{"action":"wait"})
    rpc(uri,"observer_commit_step",run,engine,1,{"action":"wait"})
    rpc(uri,"observer_respond",run,participant,1,action)
    assert rpc(uri,"observer_poll",run,participant)["observation"] is None
    query(uri,"update private.observer_sessions set expires_at=now()-interval '1 second' where run_id=%s",(run,))
    with pytest.raises(psycopg.Error,match="invalid_or_expired_capability"):
        rpc(uri,"observer_poll",run,participant)


def reserve(s, run, token, call=None, tokens=300):
    return rpc(s["uri"],"observer_reserve_model",run,token,call or uuid.uuid4(),s["provider"],
               "test-model","d"*64,tokens)


def test_concurrent_model_calls_cannot_exceed_run_quota(setup):
    s=setup; uri=s["uri"]; run,participant,_=session(s)
    def attempt(_):
        try:
            return reserve(s,run,participant)
        except psycopg.Error as exc:
            assert "run_model_quota" in str(exc)
            return None
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results=list(pool.map(attempt,range(8)))
    assert len([r for r in results if r])==3
    assert query(uri,"select tokens_reserved,calls_used,calls_active from private.observer_sessions where run_id=%s",(run,))==[(900,3,3)]


def test_proxy_reservation_is_idempotent_and_settlement_cannot_double_refund(setup):
    s=setup; uri=s["uri"]; run,participant,_=session(s); call=uuid.uuid4()
    assert reserve(s,run,participant,call)["reserved"]
    assert reserve(s,run,participant,call)=={"reserved":False,"status":"reserved"}
    with pytest.raises(psycopg.Error,match="request_id_conflict"):
        reserve(s,run,participant,call,tokens=301)
    with pytest.raises(psycopg.Error,match="permission denied"):
        rpc(uri,"observer_settle_model",call,0,role="authenticated",user=s["user"])
    rpc(uri,"observer_settle_model",call,120)
    rpc(uri,"observer_settle_model",call,0)
    assert query(uri,"select tokens_used,tokens_reserved,calls_used,calls_active from private.observer_sessions where run_id=%s",(run,))==[(120,0,1,0)]
    assert reserve(s,run,participant,call)=={"reserved":False,"status":"settled"}
    unknown=uuid.uuid4()
    reserve(s,run,participant,unknown)
    rpc(uri,"observer_settle_model",unknown,None)
    assert query(uri,"select tokens_used from private.observer_sessions where run_id=%s",(run,))==[(420,)]


def test_sponsor_limit_shared_between_runs_and_team_provider_cannot_be_stolen(setup):
    s=setup; uri=s["uri"]; run,participant,_=session(s)
    other_user,other_team=identity(uri)
    other={**s,"user":other_user,"team":other_team}
    other_run,other_token,_=session(other)
    query(uri,"update private.observer_providers set daily_token_limit=500 where id=%s",(s["provider"],))
    reserve(s,run,participant)
    with pytest.raises(psycopg.Error,match="provider_model_quota"):
        reserve(other,other_run,other_token)
    query(uri,"update private.observer_providers set team_id=%s,daily_token_limit=10000 where id=%s",(s["team"],s["provider"]))
    with pytest.raises(psycopg.Error,match="model_not_available"):
        reserve(other,other_run,other_token)
    query(uri,"update public.observer_runs set status='failed' where id=%s",(run,))
    with pytest.raises(psycopg.Error,match="invalid_or_expired_capability"):
        reserve(s,run,participant)


def test_trusted_finish_requires_matching_csv_and_keeps_partial_batches_off_board(setup):
    s=setup; uri=s["uri"]; run,participant,engine=session(s)
    rpc(uri,"observer_publish_initial",run,engine,{})
    rpc(uri,"observer_ready",run,participant)
    rpc(uri,"observer_begin",run,engine)
    summary={"score":{"total":100.5},"termination_reason":"survey_complete"}
    with pytest.raises(psycopg.Error,match="invalid_or_expired_capability"):
        rpc(uri,"observer_finish_run",run,participant,summary,"f"*64,"private/artifact")
    rpc(uri,"observer_finish_run",run,engine,summary,"f"*64,"private/artifact")
    rpc(uri,"observer_finish_run",run,engine,summary,"f"*64,"private/artifact")
    status=rpc(uri,"observer_run_status",run,participant)
    assert status["status"]=="awaiting_csv"
    # The execute host reads the reason for the graceful finish message from here.
    assert status["termination_reason"]=="survey_complete"
    assert query(uri,"select * from public.observer_leaderboard(%s)",(s["phase"],),role="anon")==[]
    with pytest.raises(psycopg.Error,match="csv_does_not_match_session"):
        rpc(uri,"observer_accept_csv",run,s["user"],"e"*64)
    outsider,_=identity(uri)
    with pytest.raises(psycopg.Error,match="run_not_found"):
        rpc(uri,"observer_accept_csv",run,outsider,"f"*64)
    with pytest.raises(psycopg.Error,match="permission denied"):
        rpc(uri,"observer_accept_csv",run,s["user"],"f"*64,role="authenticated",user=s["user"])
    rpc(uri,"observer_accept_csv",run,s["user"],"f"*64)
    rows=query(uri,"select team_id,score from public.observer_leaderboard(%s)",(s["phase"],),role="anon")
    assert rows==[(s["team"],100.5)]
    with pytest.raises(psycopg.Error,match="result_conflict"):
        rpc(uri,"observer_finish_run",run,engine,{"score":{"total":999}},"f"*64,"private/artifact")
    with pytest.raises(psycopg.Error,match="invalid_or_expired_capability"):
        reserve(s,run,participant)


def test_reconciler_settles_unknown_usage_and_expires_missing_jobs(setup):
    s=setup; uri=s["uri"]; run,participant,_=session(s)
    call=uuid.uuid4()
    reserve(s,run,participant,call)
    query(uri,"update private.observer_model_calls set created_at=now()-interval '6 minutes' where id=%s",(call,))
    query(uri,"update private.observer_sessions set expires_at=now()-interval '1 second' where run_id=%s",(run,))
    assert rpc(uri,"observer_reconcile_sessions")>=2
    assert query(uri,"select status,actual_tokens from private.observer_model_calls where id=%s",(call,))==[("settled",300)]
    assert rpc(uri,"observer_run_status",run,participant)=={"status":"failed","expired":True,"termination_reason":None}
    assert rpc(uri,"observer_reconcile_sessions")==0


def test_poll_does_not_wait_for_a_session_row_lock(setup):
    # The engine holds the session row while it stores a multi-MB catalog; the
    # executor's read-only poll must not hit the lock timeout meanwhile.
    run, participant, engine = session(setup)
    with psycopg.connect(setup["uri"]) as holder:
        holder.execute("select 1 from private.observer_sessions where run_id=%s for update", (run,))
        with psycopg.connect(setup["uri"]) as conn:
            conn.execute("set lock_timeout='200ms'")
            conn.execute("set local role service_role")
            polled = conn.execute("select public.observer_poll(%s,%s)", (run, participant)).fetchone()[0]
            state = conn.execute("select public.observer_step_state(%s,%s,'engine')", (run, engine)).fetchone()[0]
    assert polled["sequence"] == state["sequence"] and state["answered"] is False
    with pytest.raises(psycopg.Error, match="invalid_or_expired_capability"):
        rpc(setup["uri"], "observer_poll", run, "wrong-token-" + "x" * 40)

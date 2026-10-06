"""Rescore jobs in the public repositories, and rescore sampling (20261004120000)."""
from __future__ import annotations

import secrets
import uuid

import psycopg
import pytest

from test_project_database import database, identity, query, rpc, setup  # noqa: F401
from test_public_runner_pool_db import HOME, POOL, pool, project_job, set_pool  # noqa: F401


def score_job(s, run):
    job = uuid.uuid4()
    rpc(s["uri"], "observer_enqueue_job", job, "score", run, None, HOME, secrets.token_urlsafe(32), "enc", "nonce")
    return job


def pending(s, job):
    return [j for j in rpc(s["uri"], "observer_pending_jobs", 20) if j["id"] == str(job)]


def test_score_jobs_use_a_healthy_public_repository_only_when_switched_on(pool):
    s = pool; uri = s["uri"]
    set_pool(s, mode="overflow")
    _, _, run = project_job(s)
    job = score_job(s, run)
    assert pending(s, job)[0]["public"] is False
    # Staged: a drill user's rescore goes public before the switch is on for everyone.
    set_pool(s, drill_users=[str(s["user"])])
    query(uri, "update private.observer_jobs set last_dispatch_at=null,dispatch_count=0 where id=%s", (job,))
    assert pending(s, job)[0]["public"] is True
    set_pool(s, drill_users=[])
    query(uri, "select public.observer_set_public_score_jobs(true)", role="service_role")
    query(uri, "update private.observer_jobs set last_dispatch_at=null,dispatch_count=0 where id=%s", (job,))
    assert pending(s, job)[0]["public"] is True
    target = rpc(uri, "observer_public_job", job)
    assert target["organization"] == POOL
    row = query(uri, "select runner,organization,home_organization from private.observer_jobs where id=%s", (job,))[0]
    assert row == ("public-hosted", POOL, HOME)
    # A public dispatch that does not start sends it home for good; it then runs privately.
    rpc(uri, "observer_public_return_job", job, "public_run_not_started")
    query(uri, "update private.observer_jobs set last_dispatch_at=null,dispatch_count=0 where id=%s", (job,))
    assert pending(s, job)[0]["public"] is False
    assert query(uri, "select runner,organization from private.observer_jobs where id=%s", (job,))[0] == ("github-hosted", HOME)
    # No healthy public repository: the score job stays private.
    other = score_job(s, project_job(s)[2])
    query(uri, "update private.observer_public_targets set cooldown_until=now()+interval '10 minutes'")
    assert pending(s, other)[0]["public"] is False
    query(uri, "update private.observer_public_targets set cooldown_until=null")
    query(uri, "select public.observer_set_public_score_jobs(false)", role="service_role")
    query(uri, "update private.observer_jobs set last_dispatch_at=null,dispatch_count=0 where id=%s", (other,))
    assert pending(s, other)[0]["public"] is False


def test_sealed_phase_rescore_needs_the_verified_sealed_transfer(pool):
    s = pool; uri = s["uri"]
    set_pool(s, mode="overflow", sealed_transfer_verified=False)
    query(uri, "select public.observer_set_public_score_jobs(true)", role="service_role")
    job = score_job(s, project_job(s)[2])
    query(uri, "update public.observer_phase_settings set sealed=true where phase_id=%s", (s["phase"],))
    try:
        query(uri, "update private.observer_jobs set last_dispatch_at=null,dispatch_count=0 where id=%s", (job,))
        assert pending(s, job)[0]["public"] is False
        set_pool(s, sealed_transfer_verified=True)
        query(uri, "update private.observer_jobs set last_dispatch_at=null,dispatch_count=0 where id=%s", (job,))
        assert pending(s, job)[0]["public"] is True
    finally:
        query(uri, "update public.observer_phase_settings set sealed=false where phase_id=%s", (s["phase"],))
        query(uri, "select public.observer_set_public_score_jobs(false)", role="service_role")


def scored_run(s, score):
    uri = s["uri"]
    batch, run = uuid.uuid4(), uuid.uuid4()
    query(uri, "insert into public.observer_batches(id,team_id,user_id,phase_id,mode) values(%s,%s,%s,%s,'local')",
          (batch, s["team"], s["user"], s["phase"]))
    query(uri, "insert into public.observer_runs(id,batch_id,scenario_id,status) values(%s,%s,%s,'queued')",
          (run, batch, s["scenario"]))
    query(uri, "insert into private.observer_scenario_bundles values(%s,%s,%s) on conflict do nothing",
          (s["scenario"], f"{s['scenario']}/bundle.zip", "b" * 64))
    query(uri, """update public.observer_runs set status='scored',score=%s,score_check='pending',finished_at=now(),
        result_path='github:x',decisions_digest=%s where id=%s""", (score, "d" * 64, run))
    return run


def test_sampling_keeps_each_teams_best_run_and_a_stable_share(pool):
    s = pool; uri = s["uri"]
    query(uri, "insert into private.observer_hardening(id,rescore) values(true,true) on conflict(id) do update set rescore=true")
    query(uri, "delete from private.observer_jobs where kind='score'")
    query(uri, "update public.observer_runs set score_check='verified' where score_check='pending'")
    runs = [scored_run(s, float(n)) for n in range(12)]
    listed = lambda: {r["id"] for r in rpc(uri, "observer_pending_score_runs", 20)}  # noqa: E731
    assert listed() == {str(r) for r in runs}
    # auto: plenty of minutes left, nothing sampled.
    assert query(uri, "select public.observer_set_rescore_sampling(p_mode=>'auto',p_threshold_minutes=>0)",
                 role="service_role")[0][0]["active"] is False
    # Below the threshold: only the best run (score 11) and the stable share.
    out = query(uri, "select public.observer_set_rescore_sampling(p_threshold_minutes=>1000000)", role="service_role")[0][0]
    assert out["active"] is True
    sampled = listed()
    assert str(runs[-1]) in sampled and len(sampled) < len(runs)
    share = {str(r) for r in runs[:-1] if query(uri, "select abs(hashtextextended(%s::text,26))%%100<20", (str(r),))[0][0]}
    assert sampled == share | {str(runs[-1])}
    assert query(uri, "select count(*) from public.audit_log where action='observer.rescore_sampling'")[0][0] >= 1
    # Sampling ends: everything left pending is rescored after all.
    query(uri, "select public.observer_set_rescore_sampling(p_mode=>'off')", role="service_role")
    assert listed() == {str(r) for r in runs}
    query(uri, "select public.observer_set_rescore_sampling(p_mode=>'auto',p_threshold_minutes=>3000)", role="service_role")


def test_switches_are_service_role_only(pool):
    s = pool
    for role in ("authenticated", "anon"):
        for statement in ("select public.observer_set_public_score_jobs(true)",
                          "select public.observer_set_rescore_sampling(p_mode=>'on')",
                          "select public.observer_rescore_policy_reconcile()"):
            with pytest.raises(psycopg.Error, match="permission denied"):
                query(s["uri"], statement, role=role, user=s["user"])

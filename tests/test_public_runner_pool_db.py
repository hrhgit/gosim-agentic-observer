"""Public-repository runner pool: routing switches, run-bound claims, returns."""
from __future__ import annotations

import secrets
import uuid

import psycopg
import pytest
from psycopg.types.json import Jsonb

from test_project_database import database, identity, query, rpc, setup  # noqa: F401

HOME = "AGENTIC-OBSERVER26-runner-3"
POOL = "AGENTIC-OBSERVER26-runner-12"
POOL_SHA = "b" * 40


@pytest.fixture
def pool(setup):
    s = setup
    uri = s["uri"]
    for org, n in ((HOME, 3), (POOL, 12)):
        query(uri, """insert into private.observer_installations
            (organization,organization_id,installation_id,repository_id,approved_sha,enabled)
            values(%s,%s,%s,%s,%s,true) on conflict(organization) do update set enabled=true,
            monthly_minute_limit=1800""", (org, str(100 + n), 200 + n, str(300 + n), "a" * 40))
    query(uri, "delete from private.observer_public_pool")
    query(uri, """insert into private.observer_public_pool(organization,repository_id,organization_id,approved_sha)
        values(%s,'4242',%s,%s)""", (POOL, "112", POOL_SHA))
    query(uri, "delete from private.observer_public_targets")
    query(uri, "delete from private.observer_public_events")
    query(uri, """insert into private.observer_public_targets(organization,repository_id,organization_id,approved_sha,enabled)
        values(%s,'4242',%s,%s,true)""", (POOL, "112", POOL_SHA))
    query(uri, "update public.observer_phase_settings set colocated=true where phase_id=%s", (s["phase"],))
    query(uri, "update public.phases set slug=%s where id=%s", ("phase-" + s["phase"].hex, s["phase"]))
    s["slug"] = "phase-" + s["phase"].hex
    yield s
    query(uri, "update private.observer_jobs set status='failed' where status in ('queued','dispatched','claimed')")


def set_pool(s, **values):
    """observer_set_public_pool with named arguments (drill_users as uuid[])."""
    names = ", ".join(f"p_{k}=>%s" + ("::uuid[]" if k == "drill_users" else "") for k in values)
    args = tuple([str(u) for u in v] if k == "drill_users" else v for k, v in values.items())
    return query(s["uri"], f"select public.observer_set_public_pool({names})", args, role="service_role")[0][0]


def project_job(s):
    """A queued colocated engine job of a project run in the test phase."""
    uri = s["uri"]
    rev = rpc(uri, "observer_create_project", "Agent", "repository", "https://github.com/example/agent",
              role="authenticated", user=s["user"])
    query(uri, """update public.observer_revisions set status='reviewable',source_digest=%s,approval_digest=%s,
        manifest=%s,public_test='{"passed":true}' where id=%s""",
          ("c" * 64, "d" * 64, Jsonb({"image": "python@sha256:" + "e" * 64}), rev))
    rpc(uri, "observer_approve_revision", rev, "d" * 64, role="authenticated", user=s["user"])
    # One active batch per team: earlier test batches leave the queue (their jobs stay).
    query(uri, "update public.observer_batches set status='failed' where user_id=%s and status in ('queued','running')",
          (s["user"],))
    batch = rpc(uri, "observer_create_batch", s["phase"], rev, role="authenticated", user=s["user"])
    run = query(uri, "select id from public.observer_runs where batch_id=%s", (batch,))[0][0]
    rpc(uri, "observer_open_session", run, secrets.token_urlsafe(32), secrets.token_urlsafe(32))
    job, nonce = uuid.uuid4(), secrets.token_urlsafe(32)
    rpc(uri, "observer_enqueue_job", job, "engine", run, None, HOME, nonce, "encrypted input", "encrypted nonce")
    return job, nonce, run


def pending(s, job):
    return [j for j in rpc(s["uri"], "observer_pending_jobs", 20) if j["id"] == str(job)]


def job_row(s, job):
    return query(s["uri"], """select organization,runner,status,repository_id,workflow_sha,public_run_id,
        home_organization from private.observer_jobs where id=%s""", (job,))[0]


def test_default_off_and_switches_are_service_role_only(pool):
    s = pool
    job, _, _ = project_job(s)
    assert pending(s, job)[0]["public"] is False
    with pytest.raises(psycopg.Error):
        rpc(s["uri"], "observer_public_job", job)
    for name, args in (("observer_set_public_pool", ("overflow",)), ("observer_set_public_pool_phase", (s["slug"], True)),
                       ("observer_public_pool", ())):
        with pytest.raises(psycopg.Error, match="permission denied"):
            rpc(s["uri"], name, *args, role="authenticated", user=s["user"])
    assert rpc(s["uri"], "observer_public_pool")["mode"] == "off"


def test_drill_routes_only_drill_users_of_enabled_phases(pool):
    s = pool
    set_pool(s, mode="drill", drill_users=[str(s["user"])])
    job, _, _ = project_job(s)
    assert pending(s, job)[0]["public"] is False  # phase not enabled yet
    rpc(s["uri"], "observer_set_public_pool_phase", s["slug"], True)
    query(s["uri"], "update private.observer_jobs set last_dispatch_at=null where id=%s", (job,))
    assert pending(s, job)[0]["public"] is True
    other, _ = identity(s["uri"])
    set_pool(s, drill_users=[str(other)])
    query(s["uri"], "update private.observer_jobs set last_dispatch_at=null where id=%s", (job,))
    assert pending(s, job)[0]["public"] is False


def test_overflow_triggers_on_near_limit_or_busy_organizations(pool):
    s = pool
    set_pool(s, mode="overflow")
    rpc(s["uri"], "observer_set_public_pool_phase", s["slug"], True)
    job, _, _ = project_job(s)
    assert pending(s, job)[0]["public"] is False  # the home organization has minutes left
    query(s["uri"], "update private.observer_installations set monthly_minute_limit=0 where organization=%s", (HOME,))
    query(s["uri"], "update private.observer_jobs set last_dispatch_at=null where id=%s", (job,))
    assert pending(s, job)[0]["public"] is True
    query(s["uri"], "update private.observer_installations set monthly_minute_limit=1800 where organization=%s", (HOME,))
    set_pool(s, busy_jobs=1)  # busy at one active job
    query(s["uri"], "update private.observer_jobs set last_dispatch_at=null where id=%s", (job,))
    project_job(s)  # another active job on the home organization
    assert pending(s, job)[0]["public"] is True


def test_hidden_phases_need_the_verified_sealed_transfer_and_capacity_is_capped(pool):
    s = pool
    uri = s["uri"]
    set_pool(s, mode="drill", max_active=1, drill_users=[str(s["user"])])
    rpc(uri, "observer_set_public_pool_phase", s["slug"], True)
    job, _, _ = project_job(s)
    second, _, _ = project_job(s)
    # A sealed (hidden) phase: runs exist before it is sealed.
    query(uri, "update public.observer_phase_settings set sealed=true where phase_id=%s", (s["phase"],))
    assert pending(s, job)[0]["public"] is False
    set_pool(s, sealed_transfer_verified=True)
    query(uri, "update private.observer_jobs set last_dispatch_at=null where id=%s", (job,))
    assert pending(s, job)[0]["public"] is True
    rpc(uri, "observer_public_job", job)
    query(uri, "update private.observer_jobs set last_dispatch_at=null where id=%s", (second,))
    assert pending(s, second)[0]["public"] is False  # max_active=1
    with pytest.raises(psycopg.Error, match="public_pool_unavailable"):
        rpc(uri, "observer_public_job", second)


def test_a_verified_sealed_phase_prefers_the_pool_in_overflow_mode(pool):
    s = pool
    uri = s["uri"]
    set_pool(s, mode="overflow")
    rpc(uri, "observer_set_public_pool_phase", s["slug"], True)
    job, _, _ = project_job(s)
    # Unsealed: only when the home organization is near its minutes or busy (it is not).
    assert pending(s, job)[0]["public"] is False
    query(uri, "update public.observer_phase_settings set sealed=true where phase_id=%s", (s["phase"],))
    query(uri, "update private.observer_jobs set last_dispatch_at=null,dispatch_count=0 where id=%s", (job,))
    assert pending(s, job)[0]["public"] is False  # sealed, transfer not verified
    set_pool(s, sealed_transfer_verified=True)
    query(uri, "update private.observer_jobs set last_dispatch_at=null,dispatch_count=0 where id=%s", (job,))
    assert pending(s, job)[0]["public"] is True  # preferred although the home organization has minutes
    set_pool(s, mode="off")
    query(uri, "update private.observer_jobs set last_dispatch_at=null,dispatch_count=0 where id=%s", (job,))
    assert pending(s, job)[0]["public"] is False
    set_pool(s, mode="overflow")
    rpc(uri, "observer_set_public_pool_phase", s["slug"], False)
    query(uri, "update private.observer_jobs set last_dispatch_at=null,dispatch_count=0 where id=%s", (job,))
    assert pending(s, job)[0]["public"] is False  # the phase must be switched on


def test_a_public_job_is_claimed_only_by_its_dispatched_run_without_a_nonce(pool):
    s = pool
    uri = s["uri"]
    set_pool(s, mode="drill", drill_users=[str(s["user"])])
    rpc(uri, "observer_set_public_pool_phase", s["slug"], True)
    job, nonce, _ = project_job(s)
    target = rpc(uri, "observer_public_job", job)
    assert target == {"organization": POOL, "approved_sha": POOL_SHA, "repository_id": "4242"}
    assert job_row(s, job) == (POOL, "public-hosted", "queued", "4242", POOL_SHA, None, HOME)
    identity_row = rpc(uri, "observer_job_identity", job)
    assert identity_row["repository"] == "observer-public" and identity_row["visibility"] == "public"
    assert identity_row["repositoryId"] == "4242" and identity_row["organizationId"] == "112"
    # Not dispatched yet: nothing can claim it.
    with pytest.raises(psycopg.Error, match="job_identity_mismatch"):
        rpc(uri, "observer_claim_job", job, None, "555", "1", "4242", "112", POOL_SHA)
    rpc(uri, "observer_mark_public_dispatched", job, "555")
    # Already accepted by GitHub: never re-dispatched (its claim is bound to run 555).
    assert pending(s, job) == []
    for args in ((nonce, "555"), (None, "556")):
        with pytest.raises(psycopg.Error, match="job_identity_mismatch"):
            rpc(uri, "observer_claim_job", job, args[0], args[1], "1", "4242", "112", POOL_SHA)
    with pytest.raises(psycopg.Error, match="job_identity_mismatch"):
        rpc(uri, "observer_claim_job", job, None, "555", "1", "303", "103", "a" * 40)
    assert rpc(uri, "observer_claim_job", job, None, "555", "1", "4242", "112", POOL_SHA) == "encrypted input"
    # Public jobs never count as private-organization load.
    load = {r["organization"]: r for r in query(uri, "select to_jsonb(o) from public.observer_organizations_by_load() o")
            for r in [r[0]]}
    assert load[POOL]["active_jobs"] == 0


def test_unclaimed_or_switched_off_public_jobs_return_home_and_private_claims_still_need_the_nonce(pool):
    s = pool
    uri = s["uri"]
    set_pool(s, mode="drill", drill_users=[str(s["user"])])
    rpc(uri, "observer_set_public_pool_phase", s["slug"], True)
    job, nonce, _ = project_job(s)
    rpc(uri, "observer_public_job", job)
    rpc(uri, "observer_mark_public_dispatched", job, "777")
    assert rpc(uri, "observer_public_pool_reconcile") == 0
    query(uri, "update private.observer_jobs set last_dispatch_at=now()-interval '11 minutes' where id=%s", (job,))
    assert rpc(uri, "observer_public_pool_reconcile") == 1
    assert job_row(s, job) == (HOME, "github-hosted", "queued", "303", "a" * 40, None, None)
    # The abandoned public run can no longer claim; the private dispatch can, with the nonce.
    with pytest.raises(psycopg.Error, match="job_identity_mismatch"):
        rpc(uri, "observer_claim_job", job, None, "777", "1", "4242", "112", POOL_SHA)
    assert rpc(uri, "observer_claim_job", job, nonce, "888", "1", "303", "103", "a" * 40) == "encrypted input"
    # A queued public job returns at once when the pool is switched off.
    second, _, _ = project_job(s)
    rpc(uri, "observer_public_job", second)
    set_pool(s, mode="off")
    assert rpc(uri, "observer_public_target", second) is None
    assert rpc(uri, "observer_public_pool_reconcile") == 1
    assert job_row(s, second)[1] == "github-hosted"


def test_a_returned_job_is_never_offered_again_and_stranded_queued_jobs_return(pool):
    s = pool
    uri = s["uri"]
    set_pool(s, mode="drill", drill_users=[str(s["user"])])
    rpc(uri, "observer_set_public_pool_phase", s["slug"], True)
    job, _, run = project_job(s)
    count = query(uri, "select dispatch_count from private.observer_jobs where id=%s", (job,))[0][0]
    rpc(uri, "observer_public_job", job)
    rpc(uri, "observer_public_return_job", job, "github_request_failed")
    assert query(uri, "select dispatch_count,error from private.observer_jobs where id=%s", (job,))[0] == (
        count, "github_request_failed")
    query(uri, "update private.observer_jobs set last_dispatch_at=null where id=%s", (job,))
    assert pending(s, job)[0]["public"] is False
    with pytest.raises(psycopg.Error, match="public_pool_unavailable"):
        rpc(uri, "observer_public_job", job)
    # A queued public job left behind by an interrupted dispatcher goes home after five minutes.
    second, _, _ = project_job(s)
    rpc(uri, "observer_public_job", second)
    assert rpc(uri, "observer_public_pool_reconcile") == 0
    query(uri, "update private.observer_jobs set public_moved_at=now()-interval '6 minutes' where id=%s", (second,))
    assert rpc(uri, "observer_public_pool_reconcile") == 1
    assert job_row(s, second)[1] == "github-hosted"


def test_runs_evaluated_in_the_public_pool_are_rescored_in_their_own_organization(pool):
    s = pool
    uri = s["uri"]
    set_pool(s, mode="drill", drill_users=[str(s["user"])])
    rpc(uri, "observer_set_public_pool_phase", s["slug"], True)
    job, _, run = project_job(s)
    rpc(uri, "observer_public_job", job)
    query(uri, "insert into private.observer_scenario_bundles values(%s,%s,%s) on conflict do nothing",
          (s["scenario"], f"{s['scenario']}/bundle.zip", "b" * 64))
    query(uri, """update public.observer_runs set status='scored',score_check='pending',finished_at=now(),
        result_path='github:x',decisions_digest=%s where id=%s""", ("d" * 64, run))
    query(uri, "insert into private.observer_hardening(id,rescore) values(true,true) on conflict(id) do update set rescore=true")
    rows = [r for r in rpc(uri, "observer_pending_score_runs", 20) if r["id"] == str(run)]
    assert rows and rows[0]["organization"] == HOME


def test_split_runs_and_other_job_kinds_never_use_the_public_pool(pool):
    s = pool
    uri = s["uri"]
    set_pool(s, mode="drill", drill_users=[str(s["user"])])
    rpc(uri, "observer_set_public_pool_phase", s["slug"], True)
    job, _, run = project_job(s)
    rpc(uri, "observer_enqueue_job", uuid.uuid4(), "execute", run, None, HOME, secrets.token_urlsafe(32), "x", "y")
    assert pending(s, job)[0]["public"] is False
    score, _, run2 = project_job(s)
    query(uri, "update private.observer_jobs set kind='score' where id=%s", (score,))
    # Score jobs use the public repositories only for drill users or with score_jobs on
    # (tests/test_public_rescore_db.py).
    set_pool(s, drill_users=[])
    assert pending(s, score)[0]["public"] is False


def test_finished_public_jobs_are_listed_once_for_sealed_object_cleanup(pool):
    s = pool
    uri = s["uri"]
    set_pool(s, mode="drill", drill_users=[str(s["user"])])
    rpc(uri, "observer_set_public_pool_phase", s["slug"], True)
    job, _, _ = project_job(s)
    rpc(uri, "observer_public_job", job)
    rpc(uri, "observer_mark_public_dispatched", job, "901")
    rpc(uri, "observer_claim_job", job, None, "901", "1", "4242", "112", POOL_SHA)
    assert str(job) not in rpc(uri, "observer_public_sealed_pending", 100)
    query(uri, "update private.observer_jobs set status='succeeded',finished_at=now() where id=%s", (job,))
    assert str(job) in rpc(uri, "observer_public_sealed_pending", 100)
    rpc(uri, "observer_public_sealed_cleaned", job)
    assert str(job) not in rpc(uri, "observer_public_sealed_pending", 100)


def add_target(s, org, n, **values):
    query(s["uri"], """insert into private.observer_installations
        (organization,organization_id,installation_id,repository_id,approved_sha,enabled)
        values(%s,%s,%s,%s,%s,true) on conflict(organization) do update set enabled=true""",
          (org, str(100 + n), 200 + n, str(300 + n), "a" * 40))
    return rpc(s["uri"], "observer_set_public_target", org, values.get("enabled", True), values.get("max_active", 15),
               POOL_SHA, str(5000 + n), str(600 + n))


def test_jobs_spread_over_healthy_public_repositories_with_cooldown_and_kill_switch(pool):
    s = pool
    uri = s["uri"]
    other = "AGENTIC-OBSERVER26-runner-7"
    query(uri, "update public.observer_phase_settings set daily_batches=100 where phase_id=%s", (s["phase"],))
    query(uri, "update public.phases set daily_limit=100 where id=%s", (s["phase"],))
    add_target(s, other, 7)
    query(uri, "update private.observer_public_targets set max_active=1")
    set_pool(s, mode="drill", max_active=20, drill_users=[str(s["user"])])
    rpc(uri, "observer_set_public_pool_phase", s["slug"], True)
    first, _, _ = project_job(s)
    second, _, _ = project_job(s)
    third, _, _ = project_job(s)
    orgs = {rpc(uri, "observer_public_job", job)["organization"] for job in (first, second)}
    assert orgs == {POOL, other}  # one slot each: both repositories are used
    with pytest.raises(psycopg.Error, match="public_pool_unavailable"):
        rpc(uri, "observer_public_job", third)  # every slot taken
    # A claim must come from the repository the job was sent to.
    status = {t["organization"]: t for t in rpc(uri, "observer_public_targets_status")}
    assert status[POOL]["active"] == 1 and status[other]["active"] == 1
    # Three failed starts within 15 minutes cool a repository down; its load goes elsewhere.
    query(uri, "update private.observer_public_targets set max_active=5")
    for _ in range(3):
        query(uri, "select private.observer_public_note(%s,null,'returned','public_run_not_started')", (other,))
    status = {t["organization"]: t for t in rpc(uri, "observer_public_targets_status")}
    assert status[other]["healthy"] is False and status[POOL]["healthy"] is True
    for _ in range(3):
        assert rpc(uri, "observer_public_job", project_job(s)[0])["organization"] == POOL
    # Kill switch per repository; queued jobs there go home on the next reconcile.
    rpc(uri, "observer_set_public_target", other, True, None, None, None, None, True)  # clear cooldown
    rpc(uri, "observer_set_public_target", POOL, False)
    job = project_job(s)[0]
    assert rpc(uri, "observer_public_job", job)["organization"] == other
    queued = query(uri, "select id from private.observer_jobs where runner='public-hosted' and organization=%s and status='queued'", (POOL,))
    assert queued and rpc(uri, "observer_public_pool_reconcile") >= len(queued)
    assert query(uri, "select count(*) from private.observer_jobs where runner='public-hosted' and organization=%s and status='queued'", (POOL,)) == [(0,)]


def test_primary_mode_prefers_public_repositories_for_the_rollout_share(pool):
    s = pool
    uri = s["uri"]
    set_pool(s, mode="primary", max_active=20, rollout_percent=0)
    rpc(uri, "observer_set_public_pool_phase", s["slug"], True)
    job, _, _ = project_job(s)
    assert pending(s, job)[0]["public"] is False  # 0 %: overflow rules (the home organization has minutes)
    set_pool(s, rollout_percent=100)
    query(uri, "update private.observer_jobs set last_dispatch_at=null,dispatch_count=0 where id=%s", (job,))
    assert pending(s, job)[0]["public"] is True
    team = query(uri, "select team_id from public.profiles where id=%s", (s["user"],))[0][0]
    share = query(uri, "select abs(hashtextextended(%s::text,0)) %% 100", (str(team),))[0][0]
    set_pool(s, rollout_percent=share)  # this team is just outside the share
    query(uri, "update private.observer_jobs set last_dispatch_at=null,dispatch_count=0 where id=%s", (job,))
    assert pending(s, job)[0]["public"] is False
    set_pool(s, mode="off")
    assert rpc(uri, "observer_public_pool")["mode"] == "off"

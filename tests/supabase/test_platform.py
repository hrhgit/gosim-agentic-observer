"""Integration tests (challenge v3): migrations + RLS + RPCs through real PostgREST, worker evaluation, storage rules."""
from __future__ import annotations

import io
import json
import shutil
import time
import zipfile
from pathlib import Path

import pytest
import psycopg

from conftest import Client, ROOT, signup
from harness import service_key

CH = ROOT / "challenge"
FIXTURES = ROOT / "tests" / "fixtures"


def agent_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted((CH / "participant_agent").glob("*.py")):
            zf.write(p, p.name)
        zf.write(CH / "scoring_preview.py", "scoring_preview.py")
        zf.writestr(".env", "MODEL_PROVIDER=deterministic\n")
    return buf.getvalue()


@pytest.fixture(scope="session")
def seeded(hs, service):
    from worker import main as wm
    wm.seed(wm.client())
    rows = service.select("scenarios", "select=slug,weather_public,events_public,n_nights,global_wallclock_seconds")[1]
    by = {r["slug"]: r for r in rows}
    assert set(by) == {"demo-week", "dev-reference", "dev-fortnight", "eval-a", "eval-b"}
    assert by["dev-fortnight"]["n_nights"] == 14 and by["eval-a"]["events_public"] is False and by["dev-reference"]["events_public"] is True
    assert by["demo-week"]["n_nights"] == 7 and by["demo-week"]["events_public"] is True
    hs.sql("update public.phases set starts_at = now() - interval '1 hour', ends_at = now() + interval '1 day' where slug = 'online'")
    hs.sql("update public.scenarios set global_wallclock_seconds = 120")  # keep tests fast
    return by


@pytest.fixture(scope="session")
def fortnight_decisions(hs, seeded):
    """A decisions.csv for dev-fortnight produced by the minimal agent through the platform runner (also saved as a fixture for the web e2e)."""
    from worker import main as wm, challenge_runner as cr
    sb = wm.client()
    scn = seeded["dev-fortnight"]
    root = wm.fetch_scenario(sb, "dev-fortnight", "")
    pkg = hs.dir / "agent-pkg"
    shutil.rmtree(pkg, ignore_errors=True)
    pkg.mkdir()
    for p in (CH / "participant_agent").glob("*.py"):
        shutil.copyfile(p, pkg / p.name)
    shutil.copyfile(CH / "scoring_preview.py", pkg / "scoring_preview.py")
    out = hs.dir / "fortnight-run"
    res = cr.run_agent_package(pkg, root, out, wallclock_seconds=120, scenario_meta={"slug": "dev-fortnight"})
    assert res["termination_reason"] == "survey_complete"
    FIXTURES.mkdir(exist_ok=True)
    shutil.copyfile(out / "decisions.csv", FIXTURES / "dev-fortnight-decisions.csv")
    return out / "decisions.csv"


@pytest.fixture(scope="session")
def alice(hs, seeded):
    c = signup(hs, "alice@test.org", name="Alice")
    st, team_id = c.rpc("create_team", {"p_name": "Night Owls", "p_max_size": 2})
    assert st == 200, team_id
    c.team_id = team_id
    return c


@pytest.fixture(scope="session")
def bob(hs, seeded, alice):
    c = signup(hs, "bob@test.org", name="Bob")
    st, code = alice.rpc("regenerate_invite_code")
    st, res = c.rpc("join_team", {"p_invite_code": code})
    assert st == 200, res
    c.team_id = alice.team_id
    return c


@pytest.fixture(scope="session")
def mallory(hs, seeded):
    c = signup(hs, "mallory@test.org", name="Mallory")
    st, tid = c.rpc("create_team", {"p_name": "Other Team", "p_max_size": 1})
    assert st == 200
    c.team_id = tid
    return c


def _submit(client: Client, *, phase: str, kind: str, scenario: str | None, data: bytes, filename: str):
    name = f"{client.team_id}/{int(time.time() * 1000)}-{Path(filename).suffix}"
    st, res = client.upload("submissions", name, data, "text/csv" if filename.endswith(".csv") else "application/zip")
    assert st == 200, res
    st, sid = client.rpc("create_submission", {"p_phase_slug": phase, "p_kind": kind, "p_scenario_slug": scenario, "p_storage_path": name, "p_filename": filename, "p_sha256": "x", "p_title": "t", "p_notes": ""})
    return st, sid


def test_scenario_file_visibility(hs, seeded):
    anon = Client(hs.url)
    st, body = anon.download("scenarios", "dev-reference/outputs/reference/weather.csv")
    assert st == 200 and body.startswith(b"slot_id")
    assert anon.download("scenarios", "dev-reference/outputs/reference/weather_events.csv")[0] == 200  # public practice scenario
    assert anon.download("scenarios", "eval-a/outputs/reference/weather.csv")[0] == 400
    assert anon.download("scenarios", "eval-a/outputs/reference/weather_forecasts.csv")[0] == 400
    assert anon.download("scenarios", "eval-a/outputs/reference/weather_events.csv")[0] == 400
    assert anon.download("scenarios", "eval-a/outputs/reference/tiles.csv")[0] == 200
    assert anon.download("scenarios", "eval-a/outputs/reference/targets.csv")[0] == 200
    assert anon.download("scenarios", "eval-a/config/score_config.json")[0] == 200
    assert anon.download("scenarios", "eval-a/outputs/reference/scenario_manifest.json")[0] == 200


def test_team_workflow_rules(hs, alice, bob, mallory):
    st, res = alice.rpc("create_team", {"p_name": "Second", "p_max_size": 2})
    assert st == 400 and res["message"] == "already_in_team"
    carol = signup(hs, "carol@test.org", name="Carol")
    st, res = carol.rpc("join_team", {"p_invite_code": "NOPE1234"})
    assert st == 400 and res["message"] == "bad_code"
    st, code = alice.rpc("regenerate_invite_code")
    st, res = carol.rpc("join_team", {"p_invite_code": code})
    assert st == 400 and res["message"] == "full"
    st, res = alice.rpc("leave_team")
    assert st == 400 and res["message"] == "leader_must_transfer"
    st, members = bob.rpc("team_members", {"p_team_id": alice.team_id})
    assert st == 200 and {m["name"] for m in members} == {"Alice", "Bob"}
    assert mallory.rpc("team_members", {"p_team_id": alice.team_id})[1] == []
    st, res = mallory.call("PATCH", f"/rest/v1/teams?id=eq.{alice.team_id}", {"name": "Hacked"})
    assert st in (401, 403, 404)


def test_results_submission_scored_by_worker(hs, alice, bob, fortnight_decisions):
    from worker import main as wm
    st, sid = _submit(alice, phase="practice", kind="results", scenario="dev-fortnight", data=fortnight_decisions.read_bytes(), filename="decisions.csv")
    assert st == 200, sid
    assert wm.run_loop(once=True) == 1
    st, rows = alice.select("submissions", f"select=*,evaluations(*,scenarios(slug))&id=eq.{sid}")
    sub = rows[0]
    assert sub["status"] == "scored", sub["error"]
    assert sub["score"] > 1000 and sub["base_science"] > 0 and sub["completed_tiles"] > 20
    ev = sub["evaluations"][0]
    assert ev["scenarios"]["slug"] == "dev-fortnight" and ev["termination_reason"] == "trace_complete"
    assert ev["summary"]["requests_total"] >= 1 and "penalties" in ev["summary"]
    st, body = alice.download("results", ev["report_path"])
    assert st == 200 and b'"schema_version": "score-report-v3"' in body
    if ev.get("replay_path"):
        st, html = alice.download("results", ev["replay_path"])
        assert st == 200 and b"<canvas" in html
    # the practice board ranks one scenario at a time
    st, board = Client(hs.url).rpc("leaderboard", {"p_phase_slug": "practice", "p_limit": 10, "p_scenario_slug": "dev-fortnight"})
    assert st == 200 and board[0]["team_name"] == "Night Owls" and board[0]["base_science"] > 0 and board[0]["required_missing"] is not None
    assert board[0]["scenario_slug"] == "dev-fortnight" and board[0]["best_submission_id"] == sid
    st, other = Client(hs.url).rpc("leaderboard", {"p_phase_slug": "practice", "p_limit": 10, "p_scenario_slug": "dev-reference"})
    assert st == 200 and all(e["best_submission_id"] != sid for e in other)


def test_rls_blocks_other_team(hs, alice, mallory):
    assert mallory.select("submissions", "select=id")[1] == []
    assert mallory.select("evaluations", "select=id")[1] == []
    st, evs = alice.select("evaluations", "select=report_path")
    assert mallory.download("results", evs[0]["report_path"])[0] == 400
    assert mallory.upload("submissions", f"{alice.team_id}/evil.csv", b"x", "text/csv")[0] == 400
    sid = hs.sql("select id from public.submissions where team_id = %s limit 1", (alice.team_id,))[0][0]
    st, res = mallory.rpc("cancel_submission", {"p_id": sid})
    assert st == 400 and res["message"] == "cannot_cancel"


def test_submission_validation_rules(hs, alice, mallory, fortnight_decisions):
    hs.sql("update public.phases set allow_results = false where slug = 'online'")
    st, res = _submit(alice, phase="online", kind="results", scenario="eval-a", data=fortnight_decisions.read_bytes(), filename="decisions.csv")
    assert st == 400 and res["message"] == "results_not_allowed"
    hs.sql("update public.phases set allow_results = true where slug = 'online'")
    st, res = _submit(alice, phase="practice", kind="results", scenario="eval-a", data=fortnight_decisions.read_bytes(), filename="decisions.csv")
    assert st == 400 and res["message"] == "bad_scenario"
    hs.sql("update public.phases set daily_limit = 0 where slug = 'practice'")
    st, res = _submit(alice, phase="practice", kind="results", scenario="dev-fortnight", data=fortnight_decisions.read_bytes(), filename="decisions.csv")
    assert st == 400 and res["message"] == "daily_limit"
    hs.sql("update public.phases set daily_limit = 50 where slug = 'practice'")
    from worker import main as wm
    bad = b"decision_id,slot_id,action,tile_id,program,request_id,reason\nD1,NOPE,observe,T00001,DARK,,x\nD1,NOPE,wait,,,,dup\n"
    st, sid = _submit(alice, phase="practice", kind="results", scenario="dev-fortnight", data=bad, filename="decisions.csv")
    assert st == 200
    wm.run_loop(once=True)
    st, rows = alice.select("submissions", f"select=status,error&id=eq.{sid}")
    assert rows[0]["status"] == "invalid" and "rejected" in rows[0]["error"]


def test_agent_submission_runs_on_hidden_weather(hs, alice):
    # The old worker remains useful for archived formats, but an old phase flag
    # cannot reopen legacy uploads for the project-only competition.
    hs.sql("update public.phases set allow_agents = true")
    st, rejected = _submit(alice, phase="online", kind="agent", scenario=None, data=agent_zip(), filename="agent.zip")
    assert st == 400 and rejected["message"] == "competition_project_required"
    archive_phase=hs.sql("insert into public.phases(slug,name_en,name_zh,allow_agents,counts_for_final) values('legacy-hidden-worker','Archive','旧格式测试',true,false) returning id")[0][0]
    hs.sql("insert into public.phase_scenarios select %s,scenario_id from public.phase_scenarios where phase_id=(select id from public.phases where slug='online')",(archive_phase,))
    from worker import main as wm
    st, sid = _submit(alice, phase="legacy-hidden-worker", kind="agent", scenario=None, data=agent_zip(), filename="agent.zip")
    assert st == 200, sid
    assert wm.run_loop(once=True) == 1
    st, rows = alice.select("submissions", f"select=*,evaluations(*,scenarios(slug))&id=eq.{sid}")
    sub = rows[0]
    assert sub["status"] == "scored", sub["error"]
    assert {e["scenarios"]["slug"] for e in sub["evaluations"]} == {"eval-a", "eval-b"}
    for e in sub["evaluations"]:
        assert e["termination_reason"] == "survey_complete", e["summary"]
        assert e["summary"]["committed_actions"] > 100 and e["accounted_wallclock_seconds"] < 120
        assert e["log_path"] and e["workflow_path"]
        st, log = alice.download("results", e["log_path"])
        assert st == 200 and b"provider=deterministic" in log and b"MODEL_PROVIDER=" not in log
    assert sub["score"] == pytest.approx(sum(e["score"] for e in sub["evaluations"]) / 2, abs=1e-6)
    st, board = Client(hs.url).rpc("leaderboard", {"p_phase_slug": "legacy-hidden-worker", "p_limit": 10})
    assert board[0]["team_name"] == "Night Owls" and board[0]["kind"] == "agent"


def test_crashing_and_slow_agents(hs, alice):
    hs.sql("update public.phases set allow_agents = true")
    from worker import main as wm
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("agent.py", "import sys\nsys.stdin.readline()\nraise SystemExit(3)\n")
    st, sid = _submit(alice, phase="practice", kind="agent", scenario=None, data=buf.getvalue(), filename="crash.zip")
    assert st == 200, sid
    wm.run_loop(once=True)
    st, rows = alice.select("submissions", f"select=status,error,score,evaluations(termination_reason,summary)&id=eq.{sid}")
    sub = rows[0]
    # package semantics: a crashed agent is scored on what it committed (nothing) plus terminal penalties
    assert sub["status"] == "scored" and sub["score"] < 0
    assert {e["termination_reason"] for e in sub["evaluations"]} <= {"agent_error", "agent_initialization_error"}
    assert "exited" in sub["error"] or "agent" in sub["error"]
    # an agent that never answers burns the wall clock and is scored the same way
    hs.sql("update public.scenarios set global_wallclock_seconds = 5")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("agent.py", "import sys, time\nfor line in sys.stdin:\n    time.sleep(60)\n")
    st, sid = _submit(alice, phase="practice", kind="agent", scenario=None, data=buf.getvalue(), filename="slow.zip")
    t0 = time.monotonic()
    wm.run_loop(once=True)
    assert time.monotonic() - t0 < 60
    st, rows = alice.select("submissions", f"select=status,evaluations(termination_reason,scenarios(slug))&id=eq.{sid}")
    assert rows[0]["status"] == "scored"
    assert any(e["termination_reason"] == "global_wallclock_expired" for e in rows[0]["evaluations"] if e["scenarios"]["slug"] == "dev-fortnight")
    hs.sql("update public.scenarios set global_wallclock_seconds = 120")


def test_admin_controls(hs, alice, mallory, service):
    admin = signup(hs, "admin@test.org", name="Admin")
    hs.sql("update public.profiles set is_admin = true where email = 'admin@test.org'")
    st, stats = admin.rpc("admin_stats")
    assert st == 200 and stats["users"] >= 4 and stats["scored"] >= 1
    assert alice.rpc("admin_users", {"p_query": None})[1] == []
    hs.sql("update public.phases set leaderboard_mode = 'hidden' where slug = 'practice'")
    assert Client(hs.url).rpc("leaderboard", {"p_phase_slug": "practice", "p_limit": 10})[1] == []
    assert admin.rpc("leaderboard", {"p_phase_slug": "practice", "p_limit": 10})[1] != []
    hs.sql("update public.phases set leaderboard_mode = 'live' where slug = 'practice'")
    st, res = admin.call("POST", "/rest/v1/announcements", {"title_en": "Scorer v3 frozen", "title_zh": "评分器 v3 冻结", "is_pinned": True, "is_published": True}, headers={"Prefer": "return=representation"})
    assert st == 201, res
    assert {r["title_en"] for r in Client(hs.url).select("announcements", "select=title_en")[1]} == {"Scorer v3 frozen"}
    from worker import main as wm
    sid = hs.sql("select id from public.submissions where status = 'scored' and kind = 'results' order by id limit 1")[0][0]
    st, _ = admin.rpc("admin_submission_action", {"p_id": sid, "p_action": "rescore"})
    assert st in (200, 204)
    wm.run_loop(once=True)
    assert hs.sql("select status from public.submissions where id = %s", (sid,))[0][0] == "scored"
    # admins can flip visibility flags; the storage rule follows
    hs.sql("update public.scenarios set events_public = true where slug = 'eval-a'")
    assert Client(hs.url).download("scenarios", "eval-a/outputs/reference/weather_events.csv")[0] == 200
    hs.sql("update public.scenarios set events_public = false where slug = 'eval-a'")


def test_stale_running_rows_are_requeued(hs, service):
    hs.sql("update public.submissions set status = 'running', started_at = now() - interval '2 hours' where status = 'scored'")
    st, n = service.rpc("requeue_stale", {"p_minutes": 30})
    assert st == 200 and n >= 1
    hs.sql("update public.submissions set status = 'scored' where status = 'queued'")


def test_redeem_codes_claim_flow(hs, alice, bob, mallory, service):
    admin = signup(hs, "admin2@test.org", name="Admin2")
    hs.sql("update public.profiles set is_admin = true where email = 'admin2@test.org'")
    st, n = admin.rpc("admin_import_redeem_codes", {"p_provider": "deepseek", "p_codes": "sk-aaa\nsk-bbb\n\nsk-aaa\n", "p_note": "500k tokens"})
    assert st == 200 and n == 2
    st, c1 = alice.rpc("claim_redeem_code", {"p_provider": "deepseek"})
    assert st == 200 and c1["code"] in ("sk-aaa", "sk-bbb")
    st, c2 = bob.rpc("claim_redeem_code", {"p_provider": "deepseek"})
    assert c2["code"] == c1["code"] and c2["already"] is True
    st, c3 = mallory.rpc("claim_redeem_code", {"p_provider": "deepseek"})
    assert st == 200 and c3["code"] != c1["code"]
    assert [r["code"] for r in mallory.select("redeem_codes", "select=code")[1]] == [c3["code"]]
    st, stats = admin.rpc("admin_redeem_stats")
    assert next(s for s in stats if s["provider"] == "deepseek")["assigned"] == 2


def test_results_only_refuses_agent_packages(hs, alice):
    hs.sql("update public.phases set allow_agents = false")  # the event default; earlier tests switch it on
    for phase in ("practice", "online"):
        st, res = _submit(alice, phase=phase, kind="agent", scenario=None, data=agent_zip(), filename="agent.zip")
        assert st == 400 and res["message"] == "agents_not_allowed", (phase, st, res)


def test_competition_weather_published_when_the_phase_opens(hs, seeded):
    anon = Client(hs.url)
    hs.sql("update public.scenarios set weather_public = false, forecasts_public = false, events_public = false where slug like 'eval-%'")
    try:
        hs.sql("update public.phases set starts_at = now() + interval '1 day' where slug = 'online'")
        hs.sql("select public.publish_open_phase_weather()")
        assert anon.download("scenarios", "eval-a/outputs/reference/weather.csv")[0] == 400  # not open yet
        hs.sql("update public.phases set starts_at = now() - interval '1 minute' where slug = 'online'")
        hs.sql("select public.publish_open_phase_weather()")
        for name in ("weather.csv", "weather_forecasts.csv", "weather_events.csv"):
            assert anon.download("scenarios", f"eval-a/outputs/reference/{name}")[0] == 200, name
        # the anomaly answer key is never downloadable
        assert anon.download("scenarios", "eval-a/outputs/reference/tile_anomalies.csv")[0] == 400
    finally:
        hs.sql("update public.scenarios set weather_public = false, forecasts_public = false, events_public = false where slug like 'eval-%'")
        hs.sql("update public.phases set starts_at = now() - interval '1 hour' where slug = 'online'")


def test_final_board_averages_the_best_score_on_each_scenario(hs, mallory):
    """Results files cover one scenario each: the final board takes a team's best score on A and on B,
    averages them, and lists the team only once both are scored. Historical
    scores keep that behavior even though new CSV uploads are now rejected."""
    uid = hs.sql("select id from public.profiles where email = 'mallory@test.org'")[0][0]
    phase = hs.sql("select id from public.phases where slug = 'online'")[0][0]

    def scored(scenario: str, score: float) -> int:
        scn = hs.sql("select id from public.scenarios where slug = %s", (scenario,))[0][0]
        # Seed an archived result from before the phase became project-only.
        # Restore the phase in the same local test transaction; never disable
        # the submission constraint or use this path in production.
        with psycopg.connect(hs.db_uri) as conn:
            conn.execute("update public.phases set slug='historical-seed',counts_for_final=false where id=%s",(phase,))
            sid=conn.execute("insert into public.submissions (team_id,user_id,phase_id,scenario_id,kind,storage_path,status,score,base_science,penalty_total) "
                "values (%s,%s,%s,%s,'results','x','scored',%s,%s,0) returning id",(mallory.team_id,uid,phase,scn,score,score)).fetchone()[0]
            conn.execute("update public.phases set slug='online',counts_for_final=true where id=%s",(phase,))
        hs.sql("insert into public.evaluations (submission_id, scenario_id, status, score, base_science, penalty_total, completed_tiles) "
               "values (%s, %s, 'scored', %s, %s, 0, 10)", (sid, scn, score, score))
        return sid

    def mine():
        st, board = Client(hs.url).rpc("leaderboard", {"p_phase_slug": "online", "p_limit": 100})
        assert st == 200
        return next((e for e in board if e["team_id"] == mallory.team_id), None)

    scored("eval-a", 90000.0)
    assert mine() is None  # scenario B not scored yet
    scored("eval-a", 1000.0)  # a worse A never lowers the best
    scored("eval-b", 3000.0)
    row = mine()
    assert row is not None and row["total_score"] == pytest.approx(46500.0) and row["scenario_slug"] is None
    assert row["submission_count"] == 3 and row["completed_tiles"] == 10
    st, only_a = Client(hs.url).rpc("leaderboard", {"p_phase_slug": "online", "p_limit": 100, "p_scenario_slug": "eval-a"})
    assert next(e for e in only_a if e["team_id"] == mallory.team_id)["total_score"] == pytest.approx(90000.0)
    hs.sql("delete from public.submissions where team_id = %s and phase_id = %s", (mallory.team_id, phase))

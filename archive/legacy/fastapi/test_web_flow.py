"""End-to-end HTTP flow: register -> team -> submit -> worker -> leaderboard -> admin."""
from __future__ import annotations

import io
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "starter_kit"
EX = KIT / "example"


def _register(client, csrf, email, name):
    token = csrf(client, "/register")
    r = client.post("/register", data={"csrf_token": token, "name": name, "email": email, "password": "password123", "password2": "password123", "agree": "on", "next": "/dashboard"}, follow_redirects=False)
    assert r.status_code == 303, r.text
    return r


def _csrf_of(client, path):
    html = client.get(path).text
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


def test_public_pages_render_both_locales(client):
    for path in ["/", "/brief", "/rules", "/docs", "/faq", "/resources", "/leaderboard", "/announcements"]:
        for lang in ("zh", "en"):
            r = client.get(path, params={"lang": lang})
            assert r.status_code == 200, (path, lang)
    assert "巡天智能体" in client.get("/?lang=zh").text
    assert "Agent Observer" in client.get("/?lang=en").text


def test_csrf_required(client):
    r = client.post("/login", data={"email": "x@y.z", "password": "nope"})
    assert r.status_code == 400


def test_register_login_team_submit_flow(client, csrf):
    # register two users
    _register(client, csrf, "alice@test.org", "Alice")
    me = client.get("/dashboard")
    assert me.status_code == 200 and "Alice" in me.text
    # duplicate email rejected
    token = _csrf_of(client, "/profile")
    r = client.post("/team/create", data={"csrf_token": token, "name": "Night Owls", "max_size": 3}, follow_redirects=False)
    assert r.status_code == 303
    team_page = client.get("/team").text
    code = re.search(r'class="token[^"]*"[^>]*>\s*([A-Z0-9]{8})\s*<', team_page).group(1)

    # results submission on the public scenario
    token = _csrf_of(client, "/submit")
    files = {"file": ("decisions.csv", (EX / "decisions.csv").read_bytes(), "text/csv")}
    r = client.post("/submit", data={"csrf_token": token, "phase": "practice", "kind": "results", "scenario": "dev-example", "title": "reference"}, files=files, follow_redirects=False)
    assert r.status_code == 303, r.text
    sid = int(r.headers["location"].rsplit("/", 1)[1])
    detail = client.get(f"/submissions/{sid}")
    assert detail.status_code == 200 and "Queued" in detail.text

    # run the worker synchronously
    from app.services import jobs
    assert jobs.run_pending() == 1
    api = client.get(f"/api/submissions/{sid}").json()
    assert api["status"] == "scored", api
    assert api["score"] == pytest.approx(10377.46553, abs=1e-3)
    detail = client.get(f"/submissions/{sid}").text
    assert "10377.47" in detail and "Region completion" in detail

    # leaderboard shows the team
    board = client.get("/api/leaderboard", params={"phase": "practice"}).json()
    assert board["entries"][0]["team_name"] == "Night Owls"
    assert board["entries"][0]["total_score"] == pytest.approx(10377.466, abs=1e-2)
    assert "Night Owls" in client.get("/leaderboard/practice").text

    # invalid results file -> invalid status
    token = _csrf_of(client, "/submit")
    files = {"file": ("decisions.csv", b"decision_id,slot_id,action,tile_id,program,reason\n0,N01-S001,observe,200069,BRIGHT,mismatch\n", "text/csv")}
    r = client.post("/submit", data={"csrf_token": token, "phase": "practice", "kind": "results", "scenario": "dev-example"}, files=files, follow_redirects=False)
    sid2 = int(r.headers["location"].rsplit("/", 1)[1])
    jobs.run_pending()
    api2 = client.get(f"/api/submissions/{sid2}").json()
    assert api2["status"] == "scored"  # operationally invalid actions still score (zero)
    assert api2["score"] < 0

    # a garbage file is rejected before queueing
    token = _csrf_of(client, "/submit")
    r = client.post("/submit", data={"csrf_token": token, "phase": "practice", "kind": "results", "scenario": "dev-example"}, files={"file": ("decisions.csv", b"nope", "text/csv")}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/submit"

    # second user joins with invite code
    from fastapi.testclient import TestClient
    from app.main import app
    bob = TestClient(app)
    _register(bob, csrf, "bob@test.org", "Bob")
    token = _csrf_of(bob, "/team")
    r = bob.post("/team/join", data={"csrf_token": token, "invite_code": code}, follow_redirects=False)
    assert r.status_code == 303
    assert "Night Owls" in bob.get("/team").text
    # bob sees the team's submissions
    assert f"/submissions/{sid}" in bob.get("/submissions").text


def test_agent_submission_runs_on_hidden_scenarios(client, csrf):
    from app.services import jobs
    token = _csrf_of(client, "/submit")
    files = {"file": ("agent.py", (KIT / "agent.py").read_bytes(), "text/x-python")}
    r = client.post("/submit", data={"csrf_token": token, "phase": "practice", "kind": "agent", "title": "baseline"}, files=files, follow_redirects=False)
    assert r.status_code == 303, r.text
    sid = int(r.headers["location"].rsplit("/", 1)[1])
    jobs.run_pending()
    api = client.get(f"/api/submissions/{sid}").json()
    assert api["status"] == "scored", api
    assert len(api["evaluations"]) == 2  # dev-example + dev-week
    assert {e["scenario"] for e in api["evaluations"]} == {"dev-example", "dev-week"}
    assert api["evaluations"][0]["summary"]["steps"] > 0
    page = client.get(f"/submissions/{sid}").text
    assert "dev-week" in page and "Agent log" in page
    # artifacts downloadable
    ev = api["evaluations"][0]
    assert client.get(f"/submissions/{sid}/eval/{ev['id']}/decisions.csv").status_code == 200
    assert client.get(f"/submissions/{sid}/eval/{ev['id']}/report.json").json()["status"] == "ok"


def test_api_token_submission_and_cli(client, csrf, tmp_path):
    import subprocess, sys
    from app.services import jobs
    prof = client.get("/profile").text
    token = re.search(r'id="tok">([0-9a-f]{48})<', prof).group(1)
    me = client.get("/api/me", headers={"Authorization": f"Bearer {token}"}).json()
    assert me["email"] == "alice@test.org" and me["team"]["name"] == "Night Owls"
    r = client.post("/api/submissions", headers={"Authorization": f"Bearer {token}"}, data={"phase": "practice", "kind": "results", "scenario": "dev-example"},
                    files={"file": ("decisions.csv", (EX / "decisions.csv").read_bytes(), "text/csv")})
    assert r.status_code == 200, r.text
    jobs.run_pending()
    assert client.get(f"/api/submissions/{r.json()['id']}", headers={"Authorization": f"Bearer {token}"}).json()["status"] == "scored"
    # bad token
    assert client.get("/api/me", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_daily_limit_and_phase_gating(client, csrf):
    from app.db import session_scope
    from app.models import Phase
    with session_scope() as db:
        p = db.query(Phase).filter_by(slug="practice").first()
        p.daily_limit = 0
    token = _csrf_of(client, "/submit")
    r = client.post("/submit", data={"csrf_token": token, "phase": "practice", "kind": "results", "scenario": "dev-example"}, files={"file": ("d.csv", (EX / "decisions.csv").read_bytes(), "text/csv")}, follow_redirects=False)
    assert r.headers["location"] == "/submit"
    with session_scope() as db:
        db.query(Phase).filter_by(slug="practice").first().daily_limit = 50
    # online phase is upcoming -> closed for non-admins
    token = _csrf_of(client, "/submit")
    r = client.post("/submit", data={"csrf_token": token, "phase": "online", "kind": "agent"}, files={"file": ("agent.py", b"print(1)", "text/x-python")}, follow_redirects=False)
    assert r.headers["location"] == "/submit"
    # hidden weather not downloadable
    assert client.get("/download/scenario/eval-a/weather.csv").status_code == 403
    assert client.get("/download/scenario/eval-a/tiles.csv").status_code == 200


def test_admin_pages_and_actions(admin_client, client):
    for path in ["/admin", "/admin/phases", "/admin/scenarios", "/admin/submissions", "/admin/users", "/admin/teams", "/admin/announcements", "/admin/settings", "/admin/jobs", "/admin/export/users.csv", "/admin/export/teams.csv", "/admin/export/submissions.csv", "/admin/export/leaderboard.csv?phase=practice"]:
        r = admin_client.get(path)
        assert r.status_code == 200, path
    # non-admin is forbidden
    assert client.get("/admin").status_code == 403
    # create an announcement and see it on the site
    token = _csrf_of(admin_client, "/admin/announcements")
    r = admin_client.post("/admin/announcements/save", data={"csrf_token": token, "title_en": "Scorer v1 frozen", "title_zh": "评分器 v1 冻结", "body_en": "Constants are final.", "level": "info", "is_pinned": "on", "is_published": "on"}, follow_redirects=False)
    assert r.status_code == 303
    assert "Scorer v1 frozen" in client.get("/?lang=en").text
    assert client.get("/api/announcements").json()["announcements"][0]["title_en"] == "Scorer v1 frozen"
    # generate a scenario
    token = _csrf_of(admin_client, "/admin/scenarios")
    r = admin_client.post("/admin/scenarios/generate", data={"csrf_token": token, "slug": "gen-test", "name": "Generated", "seed": 9, "n_nights": 1, "slots_per_night": 4, "n_tiles": 10, "tiles_public": "on"}, follow_redirects=False)
    assert r.status_code == 303
    assert "gen-test" in admin_client.get("/admin/scenarios").text
    # hide the board of practice and check it disappears for participants
    from app.db import session_scope
    from app.models import Phase
    with session_scope() as db:
        db.query(Phase).filter_by(slug="practice").first().leaderboard_mode = "hidden"
    assert client.get("/api/leaderboard?phase=practice").json()["visible"] is False
    assert admin_client.get("/api/leaderboard?phase=practice").json()["visible"] is True
    with session_scope() as db:
        db.query(Phase).filter_by(slug="practice").first().leaderboard_mode = "live"
    # rescore a submission
    from app.models import Submission
    with session_scope() as db:
        sid = db.query(Submission).filter_by(status="scored").first().id
    token = _csrf_of(admin_client, "/admin/submissions")
    r = admin_client.post(f"/admin/submissions/{sid}/action", data={"csrf_token": token, "action": "rescore"}, follow_redirects=False)
    assert r.status_code == 303
    from app.services import jobs
    jobs.run_pending()
    assert admin_client.get(f"/api/submissions/{sid}").json()["status"] == "scored"


def test_password_reset_flow(client, csrf):
    from app.services.mailer import outbox
    token = _csrf_of(client, "/forgot")
    r = client.post("/forgot", data={"csrf_token": token, "email": "alice@test.org"})
    assert r.status_code == 200
    link = re.search(r"http://testserver(/reset/[^\s]+)", outbox[-1]["body"]).group(1)
    token = _csrf_of(client, link)
    r = client.post(link, data={"csrf_token": token, "password": "newpassword9", "password2": "newpassword9"}, follow_redirects=False)
    assert r.status_code == 303
    # logout and log in with the new password
    token = _csrf_of(client, "/profile")
    client.post("/logout", data={"csrf_token": token}, follow_redirects=False)
    token = _csrf_of(client, "/login")
    r = client.post("/login", data={"csrf_token": token, "email": "alice@test.org", "password": "newpassword9"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/dashboard"


def test_starter_kit_zip_contents(client):
    import zipfile
    r = client.get("/download/starter-kit.zip")
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = set(z.namelist())
    for n in ("agent.py", "local_runner.py", "protocol.py", "scorer.py", "score_config.json", "generate_example_data.py", "sac_submit.py", "SKILL.md", "README.md", "example/weather.csv", "example/tiles.csv"):
        assert f"agent-observer-starter-kit/{n}" in names, n
    assert "agent-observer-starter-kit/example/decisions.csv" not in names

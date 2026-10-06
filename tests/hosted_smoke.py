#!/usr/bin/env python3
"""Smoke test against the HOSTED Supabase project (creates a throw-away user/team, submits, runs the worker once, cleans up).

  SUPABASE_URL=… SUPABASE_ANON_KEY=… SUPABASE_SERVICE_ROLE_KEY=… python tests/hosted_smoke.py

Verifies: sign-up trigger, team RPC, storage upload policy, create_submission, worker scoring, results download,
leaderboard, hidden-weather policy. Everything it creates is deleted at the end.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

URL = os.environ["SUPABASE_URL"].rstrip("/")
ANON = os.environ["SUPABASE_ANON_KEY"]
SVC = os.environ["SUPABASE_SERVICE_ROLE_KEY"]


def call(method, path, body=None, data=None, token=None, headers=None):
    h = {"apikey": ANON, "Authorization": f"Bearer {token or ANON}"}
    payload = None
    if body is not None:
        payload = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    elif data is not None:
        payload = data
    h.update(headers or {})
    req = urllib.request.Request(URL + path, data=payload, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            out = r.read()
            return r.status, (json.loads(out) if out and "json" in r.headers.get("Content-Type", "") else out)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:400]


def check(cond, msg):
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        raise SystemExit(1)


def main() -> int:
    os.environ.setdefault("SAC_DATA_DIR", "/tmp/sac-hosted-smoke")
    email = f"smoke-{secrets.token_hex(3)}@example.com"
    st, sess = call("POST", "/auth/v1/signup", {"email": email, "password": "smoke-password-1", "data": {"name": "Smoke", "locale": "en"}})
    check(st == 200, f"sign-up {email}")
    tok, uid = sess["access_token"], sess["user"]["id"]
    st, team = call("POST", "/rest/v1/rpc/create_team", {"p_name": f"Smoke {secrets.token_hex(2)}", "p_max_size": 1}, token=tok)
    check(st == 200, "create_team")
    ex = (ROOT / "tests" / "fixtures" / "dev-fortnight-decisions.csv").read_bytes()  # produced by the minimal agent on dev-fortnight (deterministic)
    path = f"{team}/{int(time.time())}-{secrets.token_hex(3)}.csv"
    st, _ = call("POST", f"/storage/v1/object/submissions/{urllib.parse.quote(path)}", data=ex, token=tok, headers={"Content-Type": "text/csv"})
    check(st == 200, "upload to own team folder")
    st, _ = call("POST", "/storage/v1/object/submissions/other-team/x.csv", data=b"x", token=tok, headers={"Content-Type": "text/csv"})
    check(st != 200, "upload to a foreign folder is rejected")
    st, sid = call("POST", "/rest/v1/rpc/create_submission", {"p_phase_slug": "practice", "p_kind": "results", "p_scenario_slug": "dev-fortnight", "p_storage_path": path, "p_filename": "decisions.csv", "p_sha256": hashlib.sha256(ex).hexdigest(), "p_title": "smoke", "p_notes": ""}, token=tok)
    check(st == 200, f"create_submission -> #{sid}")
    check(call("GET", "/storage/v1/object/scenarios/dev-reference/outputs/reference/weather.csv")[0] == 200, "anon can read public weather")
    check(call("GET", "/storage/v1/object/scenarios/dev-reference/outputs/reference/weather_events.csv")[0] == 200, "practice events are public")
    check(call("GET", "/storage/v1/object/scenarios/eval-a/outputs/reference/weather.csv", token=tok)[0] != 200, "hidden weather is not readable")
    check(call("GET", "/storage/v1/object/scenarios/eval-a/outputs/reference/weather_events.csv", token=tok)[0] != 200, "hidden events are not readable")
    check(call("GET", "/storage/v1/object/scenarios/eval-a/outputs/reference/tiles.csv", token=tok)[0] == 200, "hidden scenario tiles are readable")
    check(call("GET", "/storage/v1/object/scenarios/eval-a/config/score_config.json", token=tok)[0] == 200, "score config is public")
    from worker import main as wm
    from worker.config import reset_settings_cache
    reset_settings_cache()
    n = wm.run_loop(once=True)
    check(n >= 1, f"worker processed {n} submission(s)")
    st, rows = call("GET", f"/rest/v1/submissions?id=eq.{sid}&select=status,score,error,base_science,penalty_total,evaluations(report_path,decisions_path,replay_path)", token=tok)
    check(rows[0]["status"] == "scored" and rows[0]["score"] > 1000, f"scored {rows[0]['score']} (base {rows[0]['base_science']}, penalties {rows[0]['penalty_total']})")
    rp_html = rows[0]["evaluations"][0].get("replay_path")
    check(bool(rp_html) and call("GET", f"/storage/v1/object/results/{urllib.parse.quote(rp_html)}", token=tok)[0] == 200, "replay html stored and readable by the team")
    rp = rows[0]["evaluations"][0]["report_path"]
    check(call("GET", f"/storage/v1/object/results/{urllib.parse.quote(rp)}", token=tok)[0] == 200, "team can download its report")
    check(call("GET", f"/storage/v1/object/results/{urllib.parse.quote(rp)}")[0] != 200, "anon cannot download reports")
    st, board = call("POST", "/rest/v1/rpc/leaderboard", {"p_phase_slug": "practice", "p_limit": 50, "p_scenario_slug": "dev-fortnight"})
    check(any(e["best_submission_id"] == sid for e in board), "submission appears on the practice board")
    # cleanup with the service role
    def svc(method, p, body=None):
        req = urllib.request.Request(URL + p, data=json.dumps(body).encode() if body is not None else None, method=method, headers={"apikey": SVC, "Authorization": f"Bearer {SVC}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code
    svc("DELETE", f"/rest/v1/evaluations?submission_id=eq.{sid}")
    svc("DELETE", f"/rest/v1/submissions?id=eq.{sid}")
    svc("DELETE", "/storage/v1/object/submissions", {"prefixes": [path]})
    svc("DELETE", "/storage/v1/object/results", {"prefixes": [p for p in (rp, rows[0]["evaluations"][0]["decisions_path"], rp_html) if p]})
    svc("DELETE", f"/rest/v1/audit_log?user_id=eq.{uid}")
    svc("DELETE", f"/rest/v1/teams?id=eq.{team}")
    check(svc("DELETE", f"/auth/v1/admin/users/{uid}") == 200, "cleanup: user, team, submission, objects removed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

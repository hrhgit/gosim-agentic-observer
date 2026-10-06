#!/usr/bin/env python3
"""End-to-end smoke of the CLOUD evaluation path: queue a minimal-agent package on the hosted Supabase project,
dispatch the GitHub Actions worker (or wait for the cron), and wait until the run is scored.

  set -a; source .secrets/supabase.env; set +a
  python tests/hosted_agent_smoke.py [--dispatch] [--keep] [--timeout 1500]

Creates a throw-away user + team, uploads archive/starter_kit_v3/agent as a zip, calls create_submission(kind=agent, practice),
then polls the submission until it is scored/failed. Everything it created is deleted at the end unless --keep.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "archive" / "starter_kit_v3"))
from pack_agent import collect  # noqa: E402

URL = os.environ["SUPABASE_URL"].rstrip("/")
ANON = os.environ["SUPABASE_ANON_KEY"]
SVC = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
REPO = os.environ.get("SAC_GITHUB_REPO", "BH3GEI/agent-observer")


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
    print(("ok   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        raise SystemExit(1)


def build_zip() -> bytes:
    agent_dir = ROOT / "archive" / "starter_kit_v3" / "agent"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in collect(agent_dir, include_env=False):
            zf.write(path, str(path.relative_to(agent_dir)))
    return buf.getvalue()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dispatch", action="store_true", help="trigger the worker workflow with gh instead of waiting for the cron")
    ap.add_argument("--keep", action="store_true", help="leave the user/team/submission in place for inspection")
    ap.add_argument("--timeout", type=int, default=1500)
    args = ap.parse_args()

    email = f"cloud-smoke-{secrets.token_hex(3)}@example.com"
    st, sess = call("POST", "/auth/v1/signup", {"email": email, "password": "smoke-password-1", "data": {"name": "Cloud Smoke", "locale": "en"}})
    check(st == 200, f"sign-up {email}")
    tok, uid = sess["access_token"], sess["user"]["id"]
    st, team = call("POST", "/rest/v1/rpc/create_team", {"p_name": f"Cloud Smoke {secrets.token_hex(2)}", "p_max_size": 1}, token=tok)
    check(st == 200, "create_team")
    blob = build_zip()
    path = f"{team}/{int(time.time())}-{secrets.token_hex(3)}.zip"
    st, _ = call("POST", f"/storage/v1/object/submissions/{urllib.parse.quote(path)}", data=blob, token=tok, headers={"Content-Type": "application/zip"})
    check(st == 200, f"uploaded minimal agent package ({len(blob)} bytes)")
    st, sid = call("POST", "/rest/v1/rpc/create_submission", {"p_phase_slug": "practice", "p_kind": "agent", "p_scenario_slug": None, "p_storage_path": path,
                                                             "p_filename": "minimal-agent.zip", "p_sha256": hashlib.sha256(blob).hexdigest(), "p_title": "cloud smoke", "p_notes": ""}, token=tok)
    check(st == 200, f"create_submission -> #{sid}")
    if args.dispatch:
        r = subprocess.run(["gh", "workflow", "run", "worker.yml", "--repo", REPO], capture_output=True, text=True)
        check(r.returncode == 0, f"dispatched worker workflow {r.stderr.strip()}")
    t0 = time.time()
    last = None
    while time.time() - t0 < args.timeout:
        st, rows = call("GET", f"/rest/v1/submissions?id=eq.{sid}&select=status,score,error,base_science,program_bonus,request_reward,penalty_total,completed_tiles,claimed_by,evaluations(scenario_id,status,score,termination_reason,report_path,replay_path,workflow_path,log_path)", token=tok)
        row = rows[0] if st == 200 and rows else None
        state = row["status"] if row else "?"
        if state != last:
            print(f"     t+{int(time.time() - t0):4d}s status={state} worker={row.get('claimed_by') if row else None}", flush=True)
            last = state
        if state in ("scored", "failed", "invalid"):
            break
        time.sleep(15)
    check(row is not None and row["status"] == "scored", f"final status {row and row['status']} error={row and row.get('error')}")
    print(f"     score={row['score']} base={row['base_science']} bonus={row['program_bonus']} requests={row['request_reward']} penalties={row['penalty_total']} tiles={row['completed_tiles']}")
    for ev in row["evaluations"]:
        print(f"     scenario={ev['scenario_id']} status={ev['status']} score={ev['score']} termination={ev['termination_reason']} replay={'yes' if ev.get('replay_path') else 'no'}")
    check(all(ev["status"] == "scored" and ev.get("replay_path") for ev in row["evaluations"]), "every scenario scored with a replay")
    st, board = call("POST", "/rest/v1/rpc/leaderboard", {"p_phase_slug": "practice", "p_limit": 100})
    check(st == 200 and any(e["best_submission_id"] == sid for e in board), "submission appears on the practice board")
    if args.keep:
        print(f"     kept: user {email} / team {team} / submission #{sid}")
        return 0

    def svc(method, p, body=None):
        req = urllib.request.Request(URL + p, data=json.dumps(body).encode() if body is not None else None, method=method, headers={"apikey": SVC, "Authorization": f"Bearer {SVC}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()
    st, listing = svc("POST", "/storage/v1/object/list/results", {"prefix": f"{team}", "limit": 1000})
    names = []
    if st == 200:
        for entry in json.loads(listing or b"[]"):
            # list is not recursive: descend one level per submission folder
            st2, sub = svc("POST", "/storage/v1/object/list/results", {"prefix": f"{team}/{entry['name']}", "limit": 1000})
            for e2 in (json.loads(sub or b"[]") if st2 == 200 else []):
                if e2.get("id"):
                    names.append(f"{team}/{entry['name']}/{e2['name']}")
                else:
                    st3, sub3 = svc("POST", "/storage/v1/object/list/results", {"prefix": f"{team}/{entry['name']}/{e2['name']}", "limit": 1000})
                    names += [f"{team}/{entry['name']}/{e2['name']}/{e3['name']}" for e3 in (json.loads(sub3 or b"[]") if st3 == 200 else []) if e3.get("id")]
            if entry.get("id"):
                names.append(f"{team}/{entry['name']}")
    svc("DELETE", f"/rest/v1/evaluations?submission_id=eq.{sid}")
    svc("DELETE", f"/rest/v1/submissions?id=eq.{sid}")
    svc("DELETE", "/storage/v1/object/submissions", {"prefixes": [path]})
    if names:
        svc("DELETE", "/storage/v1/object/results", {"prefixes": names})
    svc("DELETE", f"/rest/v1/audit_log?user_id=eq.{uid}")
    svc("DELETE", f"/rest/v1/teams?id=eq.{team}")
    st, _ = svc("DELETE", f"/auth/v1/admin/users/{uid}")
    check(st == 200, f"cleanup: user, team, submission, {len(names)} result objects removed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

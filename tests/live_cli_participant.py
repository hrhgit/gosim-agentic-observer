#!/usr/bin/env python3
"""Participant walk-through from the command line against the LIVE platform, using the kit exactly as downloaded.

  set -a; source .secrets/supabase.env; set +a
  .venv/bin/python tests/live_cli_participant.py [--kit-url https://bh3gei.github.io/agent-observer/downloads/agent-observer-starter-kit.zip]

Steps: download + unzip the kit, fetch_scenario dev-fortnight, local_runner on it, score_decisions, pack_agent,
sac_submit results (--wait), sac_submit agent (--wait), then three "mistake" packages: a zip made the macOS way
(parent folder + __MACOSX), an agent that crashes on start, and an agent with a requirements.txt dependency.
A throw-away account/team is created through the API and removed at the end.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

URL = os.environ["SUPABASE_URL"].rstrip("/")
ANON = os.environ["SUPABASE_ANON_KEY"]
SVC = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
PY = sys.executable
PROBLEMS: list[str] = []


def ok(cond, msg):
    print(("ok   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        PROBLEMS.append(msg)


def call(method, path, body=None, data=None, token=None, headers=None):
    h = {"apikey": ANON, "Authorization": f"Bearer {token or ANON}"}
    payload = None
    if body is not None:
        payload = json.dumps(body).encode(); h["Content-Type"] = "application/json"
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


def svc(method, p, body=None):
    return call(method, p, body=body, token=SVC, headers={"apikey": SVC})


def run(cmd, cwd, env=None, timeout=900, check=True):
    r = subprocess.run(cmd, cwd=str(cwd), env={**os.environ, **(env or {})}, capture_output=True, text=True, timeout=timeout)
    if check and r.returncode != 0:
        print(r.stdout[-2000:], r.stderr[-2000:])
    return r


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kit-url", default="https://bh3gei.github.io/agent-observer/downloads/agent-observer-starter-kit.zip")
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()
    work = Path(tempfile.mkdtemp(prefix="sac-participant-"))
    print("workdir", work)
    with urllib.request.urlopen(args.kit_url, timeout=120) as r:
        blob = r.read()
    ok(zipfile.is_zipfile(io.BytesIO(blob)), f"kit downloaded ({len(blob)} bytes)")
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        zf.extractall(work)
    kit = work / "agent-observer-starter-kit"
    ok((kit / "local_runner.py").exists() and (kit / "fetch_scenario.py").exists() and (kit / "sac_submit.py").exists(), "kit layout")
    skill = (kit / "SKILL.md").read_text()
    ok("{{" not in skill and URL in skill, "SKILL.md has the real platform URL and key filled in")
    sub_src = (kit / "sac_submit.py").read_text()
    ok('DEFAULT_URL = "https://' in sub_src and 'DEFAULT_SITE_URL = "https://' in sub_src, "sac_submit.py defaults filled in")

    # account + team through the API (a participant does this on the website)
    email = f"cli-smoke-{secrets.token_hex(3)}@example.com"
    password = "cli-smoke-password-1"
    st, sess = call("POST", "/auth/v1/signup", {"email": email, "password": password, "data": {"name": "CLI Smoke", "locale": "en"}})
    ok(st == 200, f"account {email}")
    tok, uid = sess["access_token"], sess["user"]["id"]
    st, team = call("POST", "/rest/v1/rpc/create_team", {"p_name": f"CLI Smoke {secrets.token_hex(2)}", "p_max_size": 1}, token=tok)
    ok(st == 200, "team")
    env = {"SAC_EMAIL": email, "SAC_PASSWORD": password}
    sids: list[int] = []
    try:
        # 1. fetch a published scenario, run, score, pack
        r = run([PY, "fetch_scenario.py", "--list"], kit)
        ok(r.returncode == 0 and "dev-fortnight" in r.stdout and "eval-a" in r.stdout, "fetch_scenario --list")
        r = run([PY, "fetch_scenario.py", "dev-fortnight"], kit)
        ok(r.returncode == 0 and (kit / "scenarios/dev-fortnight/outputs/reference/weather_events.csv").exists(), "fetch_scenario dev-fortnight (checksums verified)")
        r = run([PY, "fetch_scenario.py", "eval-a", "--out", str(work / "eval-a")], kit)
        ok(r.returncode == 0 and not (work / "eval-a/outputs/reference/weather.csv").exists() and "not published" in r.stdout, "fetch_scenario eval-a skips hidden files and says so")
        r = run([PY, "local_runner.py", "--scenario", "scenarios/dev-fortnight", "--agent", "agent/minimal_agent.py", "--out", "run_fortnight", "--quiet"], kit)
        summary = json.loads(r.stdout) if r.stdout.strip().startswith("{") else {}
        ok(r.returncode == 0 and summary.get("termination_reason") == "survey_complete", f"local_runner dev-fortnight total={summary.get('total')}")
        r = run([PY, "score_decisions.py", "--scenario", "scenarios/dev-fortnight", "--decisions", "run_fortnight/decisions.csv"], kit)
        local_total = json.loads(r.stdout)["score"]["total"] if r.returncode == 0 else None
        ok(r.returncode == 0 and abs(local_total - summary.get("total", 0)) < 1e-6, f"score_decisions reproduces the run ({local_total})")
        r = run([PY, "pack_agent.py", "--agent", "agent", "--out", "my-agent.zip", "--no-env"], kit)
        ok(r.returncode == 0 and (kit / "my-agent.zip").exists(), "pack_agent")

        # 2. results file via CLI, wait for the platform
        r = run([PY, "sac_submit.py", "--phase", "practice", "--kind", "results", "--scenario", "dev-fortnight", "--file", "run_fortnight/decisions.csv", "--wait"], kit, env=env, timeout=1500, check=False)
        m = re.search(r"submission #(\d+)", r.stdout)
        sids.append(int(m.group(1)) if m else -1)
        body = json.loads(r.stdout[r.stdout.index("{"):r.stdout.index("}") + 1]) if "{" in r.stdout else {}
        ok(r.returncode == 0 and body.get("status") == "scored" and abs(float(body.get("score") or 0) - local_total) < 1e-3,
           f"sac_submit results --wait: platform score {body.get('score')} == local {local_total}; termination={body.get('termination_reason')}")
        ok("/submissions/" in r.stdout, "sac_submit prints the submission page link")
        # 3. agent package via CLI
        r = run([PY, "sac_submit.py", "--phase", "practice", "--kind", "agent", "--file", "my-agent.zip", "--wait"], kit, env=env, timeout=1800, check=False)
        m = re.search(r"submission #(\d+)", r.stdout)
        sids.append(int(m.group(1)) if m else -1)
        body = json.loads(r.stdout[r.stdout.index("{"):r.stdout.index("}") + 1]) if "{" in r.stdout else {}
        ok(r.returncode == 0 and body.get("status") == "scored" and body.get("required_missing") == 1, f"sac_submit agent --wait: score {body.get('score')} termination={body.get('termination_reason')}")
        ok("dev-fortnight: scored" in r.stdout and "dev-reference: scored" in r.stdout, "per-scenario lines printed")

        # 4. mistakes: macOS-style zip (parent folder + __MACOSX + .DS_Store), crash on start, requirements.txt
        mac = work / "mac.zip"
        with zipfile.ZipFile(mac, "w") as zf:
            for f in sorted((kit / "agent").glob("*.py")):
                zf.write(f, f"agent/{f.name}")
                zf.writestr(f"__MACOSX/agent/._{f.name}", b"\x00Mac OS X junk")
            zf.writestr("agent/.DS_Store", b"\x00\x00")
            zf.writestr("__MACOSX/._agent", b"\x00")
        crash = work / "crash.zip"
        with zipfile.ZipFile(crash, "w") as zf:
            zf.writestr("agent.py", "import sys\nprint('booting', file=sys.stderr)\nraise RuntimeError('I forgot to read the initialize message')\n")
        reqs = work / "reqs.zip"
        with zipfile.ZipFile(reqs, "w") as zf:
            for f in sorted((kit / "agent").glob("*.py")):
                src = f.read_text()
                if f.name == "minimal_agent.py":
                    src = src.replace("import json\n", "import json\nimport tabulate  # noqa: F401  (installed from requirements.txt by the platform)\nimport sys as _s; _s.stderr.write('tabulate imported ok\\n')\n", 1)
                zf.writestr(f.name, src)
            zf.writestr("requirements.txt", "tabulate==0.9.0\n")
        single = work / "my_strategy.py"
        single.write_text("def choose_action(candidates, snapshot, memory):\n    return candidates[0] if candidates else None\n")
        results = {}
        for label, path in (("mac", mac), ("crash", crash), ("reqs", reqs), ("single", single)):
            r = run([PY, "sac_submit.py", "--phase", "practice", "--kind", "agent", "--file", str(path), "--title", label], kit, env=env, check=False)
            m = re.search(r"submission #(\d+)", r.stdout)
            results[label] = int(m.group(1)) if m else -1
            sids.append(results[label])
            ok(m is not None, f"queued {label} package as #{results[label]}")
        t0 = time.time()
        rows = []
        while time.time() - t0 < 1800:
            st, rows = call("GET", f"/rest/v1/submissions?id=in.({','.join(str(s) for s in results.values())})&select=id,status,score,error,termination_reason,evaluations(status,termination_reason,error,log_path,scenarios(slug))", token=tok)
            if st == 200 and rows and all(r["status"] not in ("queued", "running") for r in rows):
                break
            time.sleep(15)
        by = {r["id"]: r for r in rows}
        macr = by.get(results["mac"], {})
        ok(macr.get("status") == "scored", f"macOS-style zip (parent folder + __MACOSX) is evaluated normally: {macr.get('status')} {macr.get('score')}")
        cr = by.get(results["crash"], {})
        ev_err = " | ".join(f"{e['scenarios']['slug']}: {e.get('termination_reason')} {e.get('error') or ''}" for e in cr.get("evaluations") or [])
        ok(cr.get("status") in ("failed", "scored") and "agent_initialization_error" in (cr.get("termination_reason") or "") + ev_err,
           f"crashing agent gets a clear result: status={cr.get('status')} error={cr.get('error')!r} / {ev_err}")
        if cr.get("evaluations"):
            lp = cr["evaluations"][0].get("log_path")
            st, log = call("GET", f"/storage/v1/object/results/{lp}", token=tok) if lp else (0, b"")
            ok(st == 200 and b"I forgot to read" in log, "agent.log of the crash is downloadable and contains the traceback")
        sg = by.get(results["single"], {})
        ok(sg.get("status") == "scored" and abs(float(sg.get("score") or 0) - 9400.099832) < 1e-3, f"bare my_strategy.py via sac_submit is completed by the platform and scores {sg.get('score')} error={sg.get('error')!r}")
        rq = by.get(results["reqs"], {})
        ok(rq.get("status") == "scored", f"requirements.txt package: status={rq.get('status')} error={rq.get('error')!r}")
        if rq.get("evaluations"):
            lp = rq["evaluations"][0].get("log_path")
            st, log = call("GET", f"/storage/v1/object/results/{lp}", token=tok) if lp else (0, b"")
            ok(st == 200 and b"tabulate imported ok" in log, "requirements were installed before the run (agent.log confirms the import)")
    finally:
        if args.keep:
            print("kept", email, team, sids)
        else:
            names = []
            def walk(prefix):
                st, l = svc("POST", "/storage/v1/object/list/results", {"prefix": prefix, "limit": 1000})
                for e in (l if st == 200 and isinstance(l, list) else []):
                    (names.append(f"{prefix}/{e['name']}") if e.get("id") else walk(f"{prefix}/{e['name']}"))
            walk(team)
            st, subs = svc("GET", f"/rest/v1/submissions?team_id=eq.{team}&select=id,storage_path")
            for s in (subs if st == 200 else []):
                svc("DELETE", f"/rest/v1/evaluations?submission_id=eq.{s['id']}"); svc("DELETE", f"/rest/v1/submissions?id=eq.{s['id']}")
            if st == 200 and subs:
                svc("DELETE", "/storage/v1/object/submissions", {"prefixes": [s["storage_path"] for s in subs]})
            if names:
                svc("DELETE", "/storage/v1/object/results", {"prefixes": names})
            svc("DELETE", f"/rest/v1/audit_log?user_id=eq.{uid}"); svc("DELETE", f"/rest/v1/teams?id=eq.{team}")
            st, _ = svc("DELETE", f"/auth/v1/admin/users/{uid}")
            ok(st == 200, f"cleanup: user, team, {len(subs) if isinstance(subs, list) else 0} submissions, {len(names)} result objects removed")
            shutil.rmtree(work, ignore_errors=True)
    print("\nPROBLEMS:", len(PROBLEMS))
    for p in PROBLEMS:
        print("  -", p)
    return 1 if PROBLEMS else 0


if __name__ == "__main__":
    sys.exit(main())

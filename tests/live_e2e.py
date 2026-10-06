#!/usr/bin/env python3
"""Browser end-to-end test against the LIVE site (GitHub Pages) and the HOSTED Supabase project.

  set -a; source .secrets/supabase.env; set +a
  .venv/bin/python tests/live_e2e.py [--base https://bh3gei.github.io/agent-observer] [--no-dispatch] [--keep]

What it does, in order (everything it creates is removed at the end unless --keep):
  1. public pages in English and Chinese, deep links, console errors and failed requests are collected
  2. storage policy sweep: every scenario file that should be public downloads, every hidden one is refused
  3. registers a throw-away user, creates a team, submits a results file (dev-fortnight) and the minimal agent zip
  4. dispatches the GitHub Actions worker (gh CLI) and waits until both submissions are scored
  5. submission pages: v3 breakdown, requests, waits, termination, agent panel, replay iframe, sky map, timeline, downloads
  6. leaderboard, dashboard in Chinese, mobile viewport
  7. admin pages with the throw-away user promoted to admin
Screenshots go to artifacts/live-e2e/.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import io
import json
import os
import re
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from playwright.sync_api import Page, expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "archive" / "starter_kit_v3"))
from challenge.scenario_builder import CONFIG_FILES, HIDDEN_BY_FLAG, PUBLIC_ALWAYS  # noqa: E402
from pack_agent import collect  # noqa: E402

URL = os.environ["SUPABASE_URL"].rstrip("/")
ANON = os.environ["SUPABASE_ANON_KEY"]
SVC = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
REPO = os.environ.get("SAC_GITHUB_REPO", "BH3GEI/agent-observer")
SHOTS = ROOT / "artifacts" / "live-e2e"
PASSWORD = "live-smoke-password-1"
PROBLEMS: list[str] = []
CONSOLE: list[str] = []
FAILED: list[str] = []


def call(method, path, body=None, data=None, token=None, headers=None, timeout=120):
    h = {"apikey": ANON, "Authorization": f"Bearer {token or ANON}"}
    payload = None
    if body is not None:
        payload = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    elif data is not None:
        payload = data
    h.update(headers or {})
    req = urllib.request.Request(URL + path, data=payload, method=method, headers=h)
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                out = r.read()
                return r.status, (json.loads(out) if out and "json" in r.headers.get("Content-Type", "") else out)
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")[:400]
        except (http.client.IncompleteRead, urllib.error.URLError, ConnectionError, TimeoutError) as e:  # transient transport errors
            if attempt == 3 or method not in ("GET", "POST") or (method == "POST" and not path.startswith("/storage/v1/object/list")):
                raise
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError("unreachable")


def svc(method, p, body=None):
    return call(method, p, body=body, token=SVC, headers={"apikey": SVC})


def ok(cond, msg):
    print(("ok   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        PROBLEMS.append(msg)


def shot(page: Page, name: str, full: bool = True):
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / f"{name}.png"), full_page=full)


def watch(page: Page):
    page.on("console", lambda m: CONSOLE.append(f"{page.url} :: {m.text}") if m.type == "error" and "status of 404" not in m.text else None)
    page.on("pageerror", lambda e: CONSOLE.append(f"{page.url} :: pageerror {e}"))
    # GitHub Pages answers deep links with 404.html (the SPA shell), so the document itself is a 404 by design
    page.on("response", lambda r: FAILED.append(f"{r.status} {r.url}") if r.status >= 400 and r.request.resource_type != "document" else None)
    page.on("console", lambda m: None)


def build_zip() -> bytes:
    agent_dir = ROOT / "archive" / "starter_kit_v3" / "agent"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in collect(agent_dir, include_env=False):
            zf.write(path, str(path.relative_to(agent_dir)))
        zf.writestr(".env", "MODEL_PROVIDER=deterministic\n")
    return buf.getvalue()


def public_pages(page: Page, base: str):
    checks = {
        "/": ("main", re.compile("Agent Observer|巡天", re.I)),
        "/brief": ("main", re.compile(".{200,}", re.S)),
        "/rules": ("article", re.compile("flexible_shortfall")),
        "/docs": ("article", re.compile("participant-agent-protocol-v2")),
        "/faq": ("main", re.compile("wall clock|时钟", re.I)),
        "/resources": ("[data-testid=resources-scenario-dev-reference]", re.compile("dev-reference")),
        "/leaderboard/practice": ("main", re.compile("Standings|榜")),
        "/leaderboard/online": ("main", re.compile("Standings|榜")),
        "/announcements": ("main", re.compile("Announcements|公告")),
        "/register": ("[data-testid=reg-email]", None),
        "/no-such-page": ("main", re.compile("404|not found", re.I)),
    }
    for path, (sel, pat) in checks.items():
        page.goto(base + path, wait_until="domcontentloaded")
        try:
            expect(page.locator(sel).first).to_be_visible(timeout=20000)
            if pat is not None:
                expect(page.locator(sel).first).to_contain_text(pat, timeout=20000)
            ok(True, f"page {path} (deep link)")
        except AssertionError as e:
            ok(False, f"page {path}: {str(e)[:200]}")
        page.wait_for_timeout(400)
        shot(page, "pub" + (path.replace('/', '_') or "_home"), full=path not in ("/",))
    # docs protocol explorer tabs
    page.goto(base + "/docs", wait_until="domcontentloaded")
    ex = page.locator("[data-testid=protocol-explorer]")
    expect(ex).to_be_visible(timeout=20000)
    for tab in ("initialize", "decision_request", "decision_response"):
        ex.locator(f"[data-testid=protocol-tab-{tab}]").click()
        expect(ex.locator("pre")).to_contain_text(f'"message_type": "{tab}"', timeout=5000)
    ok(True, "docs protocol explorer tabs switch")
    # Chinese pass
    page.goto(base + "/", wait_until="domcontentloaded")
    page.click("[data-testid=lang-toggle]")
    expect(page.locator("html")).to_have_attribute("lang", re.compile("zh"), timeout=10000)
    for path, pat in (("/", "巡天"), ("/docs", "challenge-score-v3"), ("/rules", "惩罚|罚"), ("/resources", "天气"), ("/leaderboard/practice", "榜|排名"), ("/faq", "时钟")):
        page.goto(base + path, wait_until="domcontentloaded")
        try:
            expect(page.locator("main")).to_contain_text(re.compile(pat), timeout=20000)
            ok(True, f"zh page {path}")
        except AssertionError as e:
            ok(False, f"zh page {path}: {str(e)[:200]}")
        shot(page, "zh" + (path.replace('/', '_') or "_home"), full=path != "/")
    page.click("[data-testid=lang-toggle]")


def storage_sweep():
    st, rows = call("GET", "/rest/v1/scenarios?select=slug,weather_public,forecasts_public,events_public,is_active&order=slug")
    ok(st == 200 and len(rows) >= 4, f"scenarios listed anonymously ({len(rows) if st == 200 else st})")
    for s in rows:
        files = [(f"config/{n}", True) for n in CONFIG_FILES] + [(f"outputs/reference/{n}", True) for n in PUBLIC_ALWAYS]
        files += [(f"outputs/reference/{n}", bool(s[flag])) for n, flag in HIDDEN_BY_FLAG.items()]
        bad = []
        for rel, visible in files:
            code, _ = call("GET", f"/storage/v1/object/scenarios/{s['slug']}/{rel}", timeout=60)
            if (code == 200) != visible:
                bad.append(f"{rel}={code}")
        ok(not bad, f"storage policy {s['slug']} (weather={s['weather_public']} forecasts={s['forecasts_public']} events={s['events_public']}) {' '.join(bad)}")


def register_and_submit(page: Page, base: str, email: str, results_csv: Path, zip_path: Path):
    page.goto(base + "/register", wait_until="domcontentloaded")
    page.fill("[data-testid=reg-name]", "Live Smoke")
    page.fill("[data-testid=reg-email]", email)
    page.fill("[data-testid=reg-password]", PASSWORD)
    page.fill("[data-testid=reg-password2]", PASSWORD)
    page.check("[data-testid=reg-agree]")
    page.click("[data-testid=reg-submit]")
    expect(page).to_have_url(re.compile(r"/dashboard"), timeout=30000)
    ok(True, f"registered {email} and landed on the dashboard")
    shot(page, "10-dashboard-empty", full=False)
    page.goto(base + "/team", wait_until="domcontentloaded")
    page.fill("[data-testid=team-name-input]", f"Live Smoke {secrets.token_hex(2)}")
    page.click("[data-testid=team-create]")
    expect(page.locator("[data-testid=team-invite-code]")).to_be_visible(timeout=20000)
    ok(True, "team created")
    shot(page, "11-team")
    # results file
    page.goto(base + "/submit", wait_until="domcontentloaded")
    page.select_option("[data-testid=submit-phase]", "practice")
    page.check("[data-testid=submit-kind-results]")
    page.select_option("[data-testid=submit-scenario]", "dev-fortnight")
    expect(page.locator("[data-testid=submit-wallclock]")).to_contain_text("dev-fortnight", timeout=10000)
    page.set_input_files("[data-testid=submit-file]", str(results_csv))
    page.fill("[data-testid=submit-title]", "live smoke results")
    shot(page, "12-submit-results", full=False)
    page.click("[data-testid=submit-button]")
    expect(page).to_have_url(re.compile(r"/submissions/\d+"), timeout=30000)
    sid_results = int(page.url.rsplit("/", 1)[1])
    expect(page.locator("[data-testid=sub-status]")).to_contain_text(re.compile("queued|running|scored|排队|运行中|已评分", re.I), timeout=20000)  # the evaluator may already have picked it up
    ok(True, f"results submission #{sid_results} queued")
    # agent package
    page.goto(base + "/submit", wait_until="domcontentloaded")
    page.select_option("[data-testid=submit-phase]", "practice")
    page.check("[data-testid=submit-kind-agent]")
    expect(page.locator("[data-testid=agent-hints]")).to_contain_text("requirements.txt")
    expect(page.locator("[data-testid=submit-wallclock]")).to_contain_text("dev-reference")
    page.set_input_files("[data-testid=submit-file]", str(zip_path))
    page.fill("[data-testid=submit-title]", "live smoke agent")
    shot(page, "13-submit-agent", full=False)
    page.click("[data-testid=submit-button]")
    expect(page).to_have_url(re.compile(r"/submissions/\d+"), timeout=30000)
    sid_agent = int(page.url.rsplit("/", 1)[1])
    expect(page.locator("[data-testid=sub-status]")).to_contain_text(re.compile("queued|running|scored|排队|运行中|已评分", re.I), timeout=20000)  # the evaluator may already have picked it up
    ok(True, f"agent submission #{sid_agent} queued")
    # beginner path 1: a single my_strategy.py (the platform completes the package)
    strategy = SHOTS / "tmp" / "my_strategy.py"
    strategy.write_text("def choose_action(candidates, snapshot, memory):\n    if not candidates:\n        return None\n"
                        "    required = [c for c in candidates if c['scheduling_class'] == 'REQUIRED']\n    return required[0] if required else candidates[0]\n")
    page.goto(base + "/submit", wait_until="domcontentloaded")
    page.select_option("[data-testid=submit-phase]", "practice")
    page.check("[data-testid=submit-kind-agent]")
    page.set_input_files("[data-testid=submit-file]", str(strategy))
    page.fill("[data-testid=submit-title]", "live smoke single file")
    page.click("[data-testid=submit-button]")
    expect(page).to_have_url(re.compile(r"/submissions/\d+"), timeout=30000)
    sid_single = int(page.url.rsplit("/", 1)[1])
    ok(True, f"single-file my_strategy.py submission #{sid_single} queued")
    # beginner path 2: the whole agent folder, zipped in the browser
    folder = SHOTS / "tmp" / "agent"
    if folder.exists():
        import shutil
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    for f in (ROOT / "archive" / "starter_kit_v3" / "agent").glob("*.py"):
        (folder / f.name).write_bytes(f.read_bytes())
    (folder / "__pycache__").mkdir()
    (folder / "__pycache__" / "junk.pyc").write_bytes(b"\x00")
    (folder / ".env").write_text("MODEL_PROVIDER=deterministic\n")
    page.goto(base + "/submit", wait_until="domcontentloaded")
    page.select_option("[data-testid=submit-phase]", "practice")
    page.check("[data-testid=submit-kind-agent]")
    page.set_input_files("[data-testid=submit-folder]", str(folder))
    expect(page.locator("[data-testid=packed-summary]")).to_contain_text(re.compile(r"\d+"), timeout=20000)
    summary = page.locator("[data-testid=packed-summary]").inner_text()
    ok("my_strategy.py" in summary and "junk" not in summary, f"folder packed in the browser: {summary[:90]}")
    page.fill("[data-testid=submit-title]", "live smoke folder")
    shot(page, "14-submit-folder", full=False)
    page.click("[data-testid=submit-button]")
    expect(page).to_have_url(re.compile(r"/submissions/\d+"), timeout=30000)
    sid_folder = int(page.url.rsplit("/", 1)[1])
    ok(True, f"browser-packed folder submission #{sid_folder} queued")
    return sid_results, sid_agent, sid_single, sid_folder


def wait_scored(token: str, ids: list[int], timeout: int):
    t0 = time.time()
    last = {}
    while time.time() - t0 < timeout:
        st, rows = call("GET", f"/rest/v1/submissions?id=in.({','.join(map(str, ids))})&select=id,status,score,error,claimed_by", token=token)
        states = {r["id"]: r["status"] for r in rows} if st == 200 else {}
        if states != last:
            print(f"     t+{int(time.time() - t0):4d}s {states}", flush=True)
            last = states
        if states and all(s in ("scored", "failed", "invalid") for s in states.values()):
            return rows
        time.sleep(15)
    return rows if st == 200 else []


def submission_pages(page: Page, base: str, token: str, sid_results: int, sid_agent: int):
    st, rows = call("GET", f"/rest/v1/submissions?id=in.({sid_results},{sid_agent})&select=id,kind,status,score,base_science,program_bonus,request_reward,penalty_total,completed_tiles,required_missing,termination_reason,evaluations(status,score,termination_reason,report_path,decisions_path,replay_path,workflow_path,log_path,scenarios(slug))", token=token)
    by_id = {r["id"]: r for r in rows}
    for sid in (sid_results, sid_agent):
        r = by_id[sid]
        ok(r["status"] == "scored", f"#{sid} {r['kind']} scored={r['score']} base={r['base_science']} penalties={r['penalty_total']} tiles={r['completed_tiles']} error={r.get('error')}")
        for ev in r["evaluations"]:
            paths = [ev.get(k) for k in ("report_path", "decisions_path", "replay_path", "workflow_path", "log_path") if ev.get(k)]
            codes = {p: call("GET", f"/storage/v1/object/results/{urllib.parse.quote(p)}", token=token, timeout=60)[0] for p in paths}
            anon = {p: call("GET", f"/storage/v1/object/results/{urllib.parse.quote(p)}", timeout=60)[0] for p in paths[:1]}
            ok(all(c == 200 for c in codes.values()) and all(c != 200 for c in anon.values()),
               f"#{sid} {ev['scenarios']['slug']} {ev['termination_reason']} score={ev['score']} artefacts={len(paths)} team-readable, anon refused")
            expected = {"report_path", "decisions_path", "replay_path"} | ({"workflow_path", "log_path"} if r["kind"] == "agent" else set())
            ok(all(ev.get(k) for k in expected), f"#{sid} {ev['scenarios']['slug']} has {sorted(expected)}")
    # browser: agent submission page
    page.goto(base + f"/submissions/{sid_agent}", wait_until="domcontentloaded")
    expect(page.locator("[data-testid=score-card]")).to_be_visible(timeout=30000)
    expect(page.locator("[data-testid=sub-status]")).to_contain_text(re.compile("scored|Scored|已评分"), timeout=30000)
    page.wait_for_timeout(800)
    shot(page, "20-submission-agent-top", full=False)
    for slug, budget in (("dev-fortnight", "/ 1800 s"), ("dev-reference", "/ 7200 s")):
        ev = page.locator(f"[data-testid=evaluation-{slug}]")
        try:
            expect(ev).to_be_visible(timeout=15000)
            expect(ev.locator("[data-testid=penalty-table]")).to_be_visible()
            expect(ev.locator("[data-testid=region-grid]")).to_contain_text("R00")
            expect(ev.locator("[data-testid=request-table]")).to_be_visible()
            expect(ev.locator("[data-testid=wait-seconds]")).to_be_visible()
            expect(ev.locator("[data-testid=termination-pill]")).to_contain_text(re.compile("survey complete|巡天完成", re.I))
            expect(ev.locator("[data-testid=agent-panel]")).to_contain_text(budget)
            expect(ev.locator("[data-testid=action-timeline]")).to_be_visible(timeout=30000)
            expect(ev.locator("[data-testid=observed-sky] canvas")).to_be_visible(timeout=30000)
            ok(True, f"agent page panel {slug}: penalties, regions, requests, waits, termination, agent panel {budget.strip()}, timeline, sky map")
        except AssertionError as e:
            ok(False, f"agent page panel {slug}: {str(e)[:300]}")
    ev = page.locator("[data-testid=evaluation-dev-fortnight]")
    ev.locator("[data-testid=replay-open]").click()
    try:
        expect(ev.locator("[data-testid=replay-frame]")).to_be_visible(timeout=45000)
        frame = ev.locator("[data-testid=replay-frame]").element_handle().content_frame()
        expect(frame.locator("body")).to_contain_text(re.compile("ASTRA|SURVEY", re.I), timeout=30000)
        expect(frame.locator("body")).to_contain_text(re.compile("N20261005|night", re.I), timeout=30000)
        ok(True, "replay iframe loads from the results bucket and renders the ASTRA // SURVEY page")
        page.wait_for_timeout(1000)
        ev.locator("[data-testid=replay-frame]").scroll_into_view_if_needed()
        ev.locator("[data-testid=replay-frame]").screenshot(path=str(SHOTS / "21-replay-frame.png"))
    except AssertionError as e:
        ok(False, f"replay iframe: {str(e)[:300]}")
    shot(page, "22-submission-agent-full")
    # downloads (report / decisions / workflow / log / replay) through the page buttons
    for label in ("score_report|report|报告", "decisions", "workflow", "agent.log|log|日志"):
        btn = ev.get_by_role("button", name=re.compile(label, re.I)).first
        if btn.count() == 0:
            ok(False, f"download button {label} missing")
            continue
        try:
            with page.expect_download(timeout=30000) as dl:
                btn.click()
            ok(dl.value.suggested_filename != "", f"download {label} -> {dl.value.suggested_filename}")
        except Exception as e:  # noqa: BLE001
            ok(False, f"download {label}: {str(e)[:200]}")
    # results submission page
    page.goto(base + f"/submissions/{sid_results}", wait_until="domcontentloaded")
    expect(page.locator("[data-testid=score-card]")).to_be_visible(timeout=30000)
    ev = page.locator("[data-testid=evaluation-dev-fortnight]")
    try:
        expect(ev.locator("[data-testid=termination-pill]")).to_contain_text(re.compile("trace complete|轨迹回放完成", re.I), timeout=15000)
        expect(ev.locator("[data-testid=penalty-table]")).to_be_visible()
        assert ev.locator("[data-testid=agent-panel]").count() == 0, "results file must not show an agent panel"
        ok(True, "results page: trace_complete pill, penalties, no agent panel")
    except AssertionError as e:
        ok(False, f"results page: {str(e)[:300]}")
    shot(page, "23-submission-results-top", full=False)
    # submissions list
    page.goto(base + "/submissions", wait_until="domcontentloaded")
    expect(page.locator("table tbody tr")).to_have_count(4, timeout=20000)
    head = page.locator("table thead").first
    expect(head).to_contain_text(re.compile("Base science|基础科学分"))
    ok(True, "submissions list shows all four rows with v3 columns")
    shot(page, "24-submissions-list", full=False)
    # dashboard, credits panel, Chinese
    page.goto(base + "/dashboard", wait_until="domcontentloaded")
    expect(page.locator("main")).to_contain_text(re.compile("live smoke", re.I), timeout=20000)
    shot(page, "25-dashboard")
    page.click("[data-testid=lang-toggle]")
    expect(page.locator("html")).to_have_attribute("lang", re.compile("zh"), timeout=10000)
    page.goto(base + f"/submissions/{sid_agent}", wait_until="domcontentloaded")
    expect(page.locator("[data-testid=sub-status]")).to_contain_text("已评分", timeout=30000)
    expect(page.locator("[data-testid=evaluation-dev-fortnight] [data-testid=termination-pill]")).to_contain_text("巡天完成")
    ok(True, "submission page in Chinese")
    shot(page, "26-submission-agent-zh", full=False)
    page.click("[data-testid=lang-toggle]")


def beginner_submissions(token: str, sid_single: int, sid_folder: int):
    st, rows = call("GET", f"/rest/v1/submissions?id=in.({sid_single},{sid_folder})&select=id,status,score,required_missing,error,evaluations(status,log_path,scenarios(slug))", token=token)
    by = {r["id"]: r for r in rows} if st == 200 else {}
    single = by.get(sid_single, {})
    ok(single.get("status") == "scored" and single.get("required_missing") in (0, 1), f"single my_strategy.py scored {single.get('score')} (REQUIRED missing {single.get('required_missing')}) error={single.get('error')!r}")
    lp = next((e.get("log_path") for e in single.get("evaluations", []) if e.get("log_path")), None)
    st, log = call("GET", f"/storage/v1/object/results/{lp}", token=token) if lp else (0, b"")
    ok(st == 200 and b"completed this package" in log, "agent.log says the platform completed the package")
    folder = by.get(sid_folder, {})
    ok(folder.get("status") == "scored" and abs(float(folder.get("score") or 0) - 9400.099832) < 1e-3, f"browser-packed folder scored {folder.get('score')} error={folder.get('error')!r}")


def leaderboard(page: Page, base: str, team_pat: str):
    page.goto(base + "/leaderboard/practice", wait_until="domcontentloaded")
    expect(page.locator("[data-testid=lb-row]").first).to_be_visible(timeout=30000)
    try:
        expect(page.locator("table")).to_contain_text(re.compile(team_pat), timeout=15000)
        expect(page.locator("[data-testid=score-bars]")).to_be_visible()
        ok(True, "practice leaderboard lists the new team with stacked bars")
    except AssertionError as e:
        ok(False, f"leaderboard: {str(e)[:300]}")
    shot(page, "30-leaderboard")


def mobile(browser, base: str, email: str, sid_agent: int):
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, locale="en-US", is_mobile=True, has_touch=True)
    page = ctx.new_page()
    watch(page)
    page.goto(base + "/", wait_until="domcontentloaded")
    expect(page.locator("main")).to_be_visible(timeout=20000)
    shot(page, "40-mobile-home", full=False)
    page.goto(base + "/leaderboard/practice", wait_until="domcontentloaded")
    expect(page.locator("[data-testid=lb-row]").first).to_be_visible(timeout=30000)
    shot(page, "41-mobile-leaderboard")
    page.goto(base + "/register?mode=login", wait_until="domcontentloaded")
    page.fill("[data-testid=login-email]", email)
    page.fill("[data-testid=login-password]", PASSWORD)
    page.click("[data-testid=login-submit]")
    expect(page).to_have_url(re.compile(r"/dashboard"), timeout=30000)
    page.goto(base + f"/submissions/{sid_agent}", wait_until="domcontentloaded")
    expect(page.locator("[data-testid=score-card]")).to_be_visible(timeout=30000)
    expect(page.locator("[data-testid=evaluation-dev-fortnight] [data-testid=action-timeline]")).to_be_visible(timeout=30000)
    w = page.evaluate("document.documentElement.scrollWidth")
    ok(w <= 400, f"mobile submission page has no horizontal overflow (scrollWidth={w})")
    shot(page, "42-mobile-submission")
    ctx.close()


def admin(page: Page, base: str, uid: str):
    st, _ = svc("PATCH", f"/rest/v1/profiles?id=eq.{uid}", {"is_admin": True})
    ok(st in (200, 204), "promoted the throw-away user to admin")
    page.goto(base + "/dashboard", wait_until="domcontentloaded")
    page.reload()
    pages = ["/admin", "/admin/phases", "/admin/scenarios", "/admin/submissions", "/admin/users", "/admin/teams", "/admin/announcements", "/admin/credits", "/admin/settings"]
    for i, path in enumerate(pages):
        page.goto(base + path, wait_until="domcontentloaded")
        try:
            expect(page).to_have_url(re.compile(re.escape(path) + "$"), timeout=20000)
            expect(page.locator("main")).to_be_visible(timeout=20000)
            page.wait_for_timeout(1500)
            assert page.locator("text=/denied|拒绝/").count() == 0
            ok(True, f"admin page {path}")
        except AssertionError as e:
            ok(False, f"admin page {path}: {str(e)[:200]}")
        shot(page, f"5{i}-admin{path.replace('/', '_')}")
    table = page.locator("[data-testid=admin-scenarios]")
    page.goto(base + "/admin/scenarios", wait_until="domcontentloaded")
    try:
        expect(table).to_contain_text("eval-a", timeout=20000)
        expect(table).to_contain_text("eval-b")
        expect(table.locator("[data-testid=wallclock-eval-a]")).to_have_value("3600")
        expect(table.locator("[data-testid=wallclock-dev-reference]")).to_have_value("7200")
        boxes = table.locator("input[type=checkbox]")
        assert boxes.count() >= 20, f"expected the visibility checkboxes, found {boxes.count()}"
        assert table.locator("input[type=file]").count() == 0, "the old upload form must be gone"
        ok(True, "admin scenarios table shows v3 wall clocks and visibility flags")
    except AssertionError as e:
        ok(False, f"admin scenarios: {str(e)[:200]}")
    page.goto(base + "/admin/submissions", wait_until="domcontentloaded")
    try:
        expect(page.locator("main")).to_contain_text(re.compile("live smoke", re.I), timeout=20000)
        ok(True, "admin submissions lists the smoke submissions")
    except AssertionError as e:
        ok(False, f"admin submissions: {str(e)[:200]}")


def cleanup(uid: str, team: str, sids: list[int], sub_paths: list[str]):
    names = []
    def walk(prefix):
        st, l = svc("POST", "/storage/v1/object/list/results", {"prefix": prefix, "limit": 1000})
        for e in (l if st == 200 and isinstance(l, list) else []):
            if e.get("id"):
                names.append(f"{prefix}/{e['name']}")
            else:
                walk(f"{prefix}/{e['name']}")
    walk(team)
    for sid in sids:
        svc("DELETE", f"/rest/v1/evaluations?submission_id=eq.{sid}")
        svc("DELETE", f"/rest/v1/submissions?id=eq.{sid}")
    if sub_paths:
        svc("DELETE", "/storage/v1/object/submissions", {"prefixes": sub_paths})
    if names:
        svc("DELETE", "/storage/v1/object/results", {"prefixes": names})
    svc("DELETE", f"/rest/v1/audit_log?user_id=eq.{uid}")
    svc("DELETE", f"/rest/v1/teams?id=eq.{team}")
    st, _ = svc("DELETE", f"/auth/v1/admin/users/{uid}")
    ok(st == 200, f"cleanup: user, team, {len(sids)} submissions, {len(names)} result objects removed")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="https://bh3gei.github.io/agent-observer")
    ap.add_argument("--no-dispatch", action="store_true")
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--timeout", type=int, default=1500)
    args = ap.parse_args()
    base = args.base.rstrip("/")
    SHOTS.mkdir(parents=True, exist_ok=True)
    tmp = SHOTS / "tmp"
    tmp.mkdir(exist_ok=True)
    zip_path = tmp / "minimal-agent.zip"
    zip_path.write_bytes(build_zip())
    results_csv = ROOT / "tests" / "fixtures" / "dev-fortnight-decisions.csv"
    email = f"live-smoke-{secrets.token_hex(3)}@example.com"
    uid = team = None
    sids: list[int] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 900}, locale="en-US", accept_downloads=True)
        page = ctx.new_page()
        watch(page)
        try:
            public_pages(page, base)
            storage_sweep()
            sid_results, sid_agent, sid_single, sid_folder = register_and_submit(page, base, email, results_csv, zip_path)
            sids = [sid_results, sid_agent, sid_single, sid_folder]
            st, sess = call("POST", "/auth/v1/token?grant_type=password", {"email": email, "password": PASSWORD})
            token, uid = sess["access_token"], sess["user"]["id"]
            st, team = call("POST", "/rest/v1/rpc/my_team_id", {}, token=token)
            ok(st == 200 and bool(team), f"team id resolved: {team}")
            if not args.no_dispatch:
                r = subprocess.run(["gh", "workflow", "run", "worker.yml", "--repo", REPO], capture_output=True, text=True)
                ok(r.returncode == 0, f"dispatched the Actions worker {r.stderr.strip()}")
            rows = wait_scored(token, sids, args.timeout)
            ok(bool(rows) and all(r["status"] == "scored" for r in rows), f"both submissions scored by {[r.get('claimed_by') for r in rows]}")
            submission_pages(page, base, token, sid_results, sid_agent)
            beginner_submissions(token, sid_single, sid_folder)
            leaderboard(page, base, "Live Smoke")
            mobile(browser, base, email, sid_agent)
            admin(page, base, uid)
        finally:
            noise = [f for f in FAILED if not re.search(r"/storage/v1/object/scenarios/eval-|/storage/v1/object/results/|favicon", f)]
            print("\nconsole errors:", len(CONSOLE))
            for c in CONSOLE[:40]:
                print("  ", c[:300])
            print("failed requests (unexpected):", len(noise))
            for f in noise[:40]:
                print("  ", f[:300])
            if args.keep:
                print(f"kept: {email} / team {team} / submissions {sids}")
            elif uid:
                st, subs = svc("GET", f"/rest/v1/submissions?id=in.({','.join(map(str, sids)) or '0'})&select=storage_path")
                cleanup(uid, team or "", sids, [s["storage_path"] for s in (subs if st == 200 else [])])
            browser.close()
    print("\nPROBLEMS:", len(PROBLEMS))
    for p in PROBLEMS:
        print("  -", p)
    return 1 if PROBLEMS else 0


if __name__ == "__main__":
    sys.exit(main())

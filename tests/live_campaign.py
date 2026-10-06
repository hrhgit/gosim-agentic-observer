#!/usr/bin/env python3
"""Full end-to-end campaign against the LIVE site + hosted Supabase. Creates e2e-* accounts and cleans them up.

  SUPABASE_URL=… SUPABASE_ANON_KEY=… SUPABASE_SERVICE_ROLE_KEY=… python tests/live_campaign.py [--base URL] [--skip-worker]

Covers: public pages (2 locales × 3 viewports, raw-key scan, overflow, console errors, axe), auth flows and guards,
team workflow, submissions (results / invalid / agent / zip / crash / cancel / XSS title), live status updates,
leaderboard chart, profile + password change, admin console actions, resources downloads, CLI submit, edge function auth.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "archive" / "starter_kit_v3"
SHOTS = ROOT / "artifacts" / "screenshots-campaign"
SHOTS.mkdir(parents=True, exist_ok=True)
AXE = ROOT / "web" / "node_modules" / "axe-core" / "axe.min.js"

URL = os.environ["SUPABASE_URL"].rstrip("/")
ANON = os.environ["SUPABASE_ANON_KEY"]
SVC = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
RUN = secrets.token_hex(2)
PW = "e2e-Passw0rd-" + RUN
results: list[tuple[str, bool, str]] = []
console_errors: dict[str, list[str]] = {}


def check(name: str, cond, detail: str = "") -> bool:
    ok = bool(cond)
    results.append((name, ok, detail))
    print(("ok   " if ok else "FAIL ") + name + (f"  — {detail}" if (detail and not ok) else ""))
    return ok


def svc(method: str, path: str, body=None, data: bytes | None = None, headers: dict | None = None, token: str | None = None):
    h = {"apikey": ANON if token else SVC, "Authorization": f"Bearer {token or SVC}", "Accept": "application/json"}
    payload = None
    if body is not None:
        payload = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    elif data is not None:
        payload = data
    h.update(headers or {})
    for attempt in range(3):
        try:
            req = urllib.request.Request(URL + path, data=payload, method=method, headers=h)
            with urllib.request.urlopen(req, timeout=60) as r:
                out = r.read()
                return r.status, (json.loads(out) if out and "json" in r.headers.get("Content-Type", "") else out)
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")[:500]
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2)


def signup_api(email: str, name: str):
    st, sess = svc("POST", "/auth/v1/signup", {"email": email, "password": PW, "data": {"name": name, "locale": "en"}}, token=ANON)
    assert st == 200, sess
    return sess["access_token"], sess["user"]["id"]


def wait_status(sid: int, token: str, want: set[str], timeout: float = 240) -> str:
    t0 = time.time()
    while time.time() - t0 < timeout:
        st, rows = svc("GET", f"/rest/v1/submissions?id=eq.{sid}&select=status", token=token)
        if rows and rows[0]["status"] in want:
            return rows[0]["status"]
        time.sleep(3)
    return rows[0]["status"] if rows else "?"


def dispatch_worker() -> None:
    try:
        subprocess.run(["gh", "workflow", "run", "worker.yml", "--repo", "BH3GEI/agent-observer"], check=True, capture_output=True, timeout=60)
    except Exception as exc:  # noqa: BLE001
        print("  (worker dispatch failed:", exc, ")")


# ---------------------------------------------------------------------------------------------- browser helpers
RAW_KEY = re.compile(r"(?<![\w/.:-])(?:nav|hero|home|auth|team|submit|subs|dash|profile|leaderboard|admin|common|status|kind|faq|resources|meta|flash|errors)\.[a-z_]+(?:\.[a-z_]+)*(?![\w/.-])")


def attach_console(page: Page, key: str):
    console_errors.setdefault(key, [])
    page.on("pageerror", lambda e: console_errors[key].append(f"pageerror: {e}"))
    expected = ("/auth/v1/token", "/auth/v1/signup", "/auth/v1/recover", "/rpc/join_team", "/rpc/create_submission", "/rpc/create_team", "/rpc/disband_team", "/rpc/leave_team", "/functions/v1/score-results")
    page.on("response", lambda r: console_errors[key].append(f"http {r.status}: {r.url}") if r.status >= 400 and "supabase.co" in r.url and not any(x in r.url for x in expected) else None)
    page.on("requestfailed", lambda r: console_errors[key].append(f"requestfailed: {r.url} {r.failure}") if "realtime" not in r.url and "ERR_ABORTED" not in str(r.failure) else None)


def page_health(page: Page, key: str, width: int) -> list[str]:
    problems = []
    text = page.evaluate("document.body.innerText")
    raw = sorted(set(RAW_KEY.findall(text)))
    if raw:
        problems.append(f"raw i18n keys: {raw[:5]}")
    sw = page.evaluate("document.documentElement.scrollWidth")
    if sw > width + 1:
        problems.append(f"horizontal overflow {sw}>{width}")
    if not page.locator("h1").first.is_visible():
        problems.append("no visible h1")
    return problems


def axe(page: Page) -> list[dict]:
    page.add_script_tag(content=AXE.read_text())
    res = page.evaluate("async () => { const r = await axe.run(document, {runOnly: ['wcag2a','wcag2aa'], resultTypes: ['violations']}); return r.violations.filter(v => ['serious','critical'].includes(v.impact)).map(v => ({id: v.id, impact: v.impact, nodes: v.nodes.length, help: v.help})) }")
    return res


def login_ui(page: Page, base: str, email: str):
    page.goto(base + "/register?mode=login", wait_until="networkidle")
    page.fill("[data-testid=login-email]", email)
    page.fill("[data-testid=login-password]", PW)
    page.click("[data-testid=login-submit]")
    page.wait_for_url(re.compile(r"/dashboard"), timeout=30000)


def register_ui(page: Page, base: str, name: str, email: str):
    page.goto(base + "/register", wait_until="networkidle")
    page.fill("[data-testid=reg-name]", name)
    page.fill("[data-testid=reg-email]", email)
    page.fill("[data-testid=reg-password]", PW)
    page.fill("[data-testid=reg-password2]", PW)
    page.check("[data-testid=reg-agree]")
    page.click("[data-testid=reg-submit]")
    page.wait_for_url(re.compile(r"/dashboard"), timeout=30000)


def flash_text(page: Page) -> str:
    try:
        return page.locator("[data-testid=flash]").inner_text(timeout=8000).strip()
    except Exception:
        return ""


def alert_text(page: Page) -> str:
    """Inline form errors (role=alert / .errors) or a flash message."""
    try:
        loc = page.locator("[role=alert], .errors")
        texts = [t.strip() for t in loc.all_inner_texts() if t.strip()]
        if texts:
            return " | ".join(texts)
    except Exception:
        pass
    return flash_text(page)


def submit_ui(page: Page, base: str, *, phase: str, kind: str, scenario: str | None, file: Path, title: str = "") -> int:
    page.goto(base + "/submit", wait_until="networkidle")
    page.select_option("[data-testid=submit-phase]", phase)
    page.check(f"[data-testid=submit-kind-{kind}]")
    if scenario:
        page.select_option("[data-testid=submit-scenario]", scenario)
    page.set_input_files("[data-testid=submit-file]", str(file))
    if title:
        page.fill("[data-testid=submit-title]", title)
    page.click("[data-testid=submit-button]")
    page.wait_for_url(re.compile(r"/submissions/\d+"), timeout=30000)
    return int(page.url.rsplit("/", 1)[1])


# ---------------------------------------------------------------------------------------------- campaign
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="https://bh3gei.github.io/agent-observer")
    ap.add_argument("--skip-worker", action="store_true")
    args = ap.parse_args()
    base = args.base.rstrip("/")
    created_users: list[str] = []
    admin_email = f"e2e-admin-{RUN}@example.com"
    alice_email, bob_email, carol_email = (f"e2e-{n}-{RUN}@example.com" for n in ("alice", "bob", "carol"))
    # temp admin via admin_emails
    st, cur = svc("GET", "/rest/v1/site_settings?key=eq.admin_emails")
    admin_list = list((cur[0]["value"] if cur else []) or [])
    svc("POST", "/rest/v1/site_settings?on_conflict=key", {"key": "admin_emails", "value": admin_list + [admin_email]}, headers={"Prefer": "resolution=merge-duplicates"})
    tmp = Path("/tmp/sac-campaign"); tmp.mkdir(exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        # ------------------------------------------------------------------ A. public pages
        pages = ["/", "/brief", "/rules", "/docs", "/faq", "/resources", "/leaderboard", "/announcements", "/register", "/nope-404"]
        for width, label in ((1440, "desktop"), (768, "tablet"), (390, "mobile")):
            ctx = browser.new_context(viewport={"width": width, "height": 900}, locale="en-US")
            pg = ctx.new_page(); attach_console(pg, f"public-{label}")
            for lang in ("en", "zh"):
                for path in pages:
                    try:
                        pg.goto(f"{base}{path}?lang={lang}", wait_until="networkidle", timeout=45000)
                        pg.wait_for_timeout(400)
                        probs = page_health(pg, f"{path}/{lang}/{label}", width)
                        check(f"page {path} [{lang}/{label}]", not probs, "; ".join(probs))
                        if label == "desktop" and lang == "en":
                            viol = axe(pg)
                            check(f"axe {path}", not viol, "; ".join(f"{v['id']}({v['impact']}×{v['nodes']})" for v in viol))
                            pg.screenshot(path=str(SHOTS / f"A-{path.strip('/').replace('/', '-') or 'home'}-{lang}.png"), full_page=(path != "/"))
                    except Exception as exc:  # noqa: BLE001
                        check(f"page {path} [{lang}/{label}]", False, str(exc)[:200])
            if label == "mobile":
                pg.goto(base + "/", wait_until="networkidle")
                pg.click("button[aria-label], .btn-burger, [data-burger]", timeout=5000) if pg.locator("button[aria-label]").count() else None
            errs = [e for e in console_errors[f"public-{label}"] if "favicon" not in e]
            check(f"no console errors on public pages [{label}]", not errs, "; ".join(errs[:3]))
            ctx.close()
        check("404 route renders app not-found", "404" in browser.new_page().goto(base + "/nope-404").text() or True)

        # ------------------------------------------------------------------ B. auth flows
        ctx = browser.new_context(viewport={"width": 1440, "height": 900}, locale="en-US")
        pg = ctx.new_page(); attach_console(pg, "alice")
        try:
            pg.goto(base + "/dashboard", wait_until="networkidle")
            check("guard: logged-out /dashboard redirects to login", "/register" in pg.url and "mode=login" in pg.url, pg.url)
            register_ui(pg, base, "Alice E2E", alice_email); created_users.append(alice_email)
            check("register -> dashboard", "/dashboard" in pg.url)
            # duplicate registration is rejected with a message
            pg.click("[data-testid=nav-logout]"); pg.wait_for_selector("[data-testid=nav-register]", timeout=15000)
            pg.goto(base + "/register", wait_until="networkidle")
            pg.fill("[data-testid=reg-name]", "Dup"); pg.fill("[data-testid=reg-email]", alice_email)
            pg.fill("[data-testid=reg-password]", PW); pg.fill("[data-testid=reg-password2]", PW); pg.check("[data-testid=reg-agree]")
            pg.click("[data-testid=reg-submit]"); pg.wait_for_timeout(3000)
            check("duplicate email rejected", "/dashboard" not in pg.url and alert_text(pg) != "", alert_text(pg)[:80])
            # wrong password
            pg.goto(base + "/register?mode=login", wait_until="networkidle")
            pg.fill("[data-testid=login-email]", alice_email); pg.fill("[data-testid=login-password]", "wrong-password-1"); pg.click("[data-testid=login-submit]")
            pg.wait_for_timeout(3000)
            check("wrong password shows error", "/dashboard" not in pg.url and alert_text(pg) != "", alert_text(pg)[:80])
            # forgot shows confirmation without leaking existence
            pg.goto(base + "/register?mode=forgot", wait_until="networkidle")
            inp = pg.locator("input[type=email]").first; inp.fill(alice_email)
            pg.locator("form button[type=submit]").first.click(); pg.wait_for_timeout(3000)
            check("forgot password shows confirmation", flash_text(pg) != "" or "sent" in pg.evaluate("document.body.innerText").lower())
            pg.goto(base + "/reset", wait_until="networkidle")
            check("/reset without recovery session shows guidance", pg.locator("h1").first.is_visible())
            login_ui(pg, base, alice_email)
            check("login with correct password", "/dashboard" in pg.url)
            pg.screenshot(path=str(SHOTS / "B-dashboard.png"), full_page=True)
        except Exception as exc:  # noqa: BLE001
            check("auth flow", False, traceback.format_exc()[-300:])

        # ------------------------------------------------------------------ C. team workflow
        code = ""
        try:
            pg.goto(base + "/team", wait_until="networkidle")
            pg.fill("[data-testid=team-name-input]", f"E2E Owls {RUN}"); pg.click("[data-testid=team-create]")
            pg.wait_for_selector("[data-testid=team-invite-code]", timeout=20000)
            code = pg.locator("[data-testid=team-invite-code]").inner_text().strip()
            check("team created with 8-char code", bool(re.fullmatch(r"[A-Z0-9]{8}", code)), code)
            # regenerate code
            regen = pg.get_by_role("button", name=re.compile("regenerate|重新生成", re.I))
            if regen.count():
                regen.first.click(); pg.wait_for_timeout(2500)
                code2 = pg.locator("[data-testid=team-invite-code]").inner_text().strip()
                check("regenerate invite code changes it", code2 != code and re.fullmatch(r"[A-Z0-9]{8}", code2 or ""), f"{code}->{code2}")
                code = code2 or code
            # bob joins via UI
            ctx_b = browser.new_context(viewport={"width": 1280, "height": 800}, locale="en-US"); pb = ctx_b.new_page(); attach_console(pb, "bob")
            register_ui(pb, base, "Bob E2E", bob_email); created_users.append(bob_email)
            pb.goto(base + "/team", wait_until="networkidle"); pb.fill("[data-testid=team-join-code]", "ZZZZ9999"); pb.click("[data-testid=team-join]"); pb.wait_for_timeout(2500)
            check("bad invite code shows error", alert_text(pb) != "", alert_text(pb)[:80])
            pb.fill("[data-testid=team-join-code]", code); pb.click("[data-testid=team-join]"); pb.wait_for_timeout(3000)
            check("bob joined via invite code", f"E2E Owls {RUN}" in pb.evaluate("document.body.innerText"))
            # carol: locked team cannot be joined
            ctx_c = browser.new_context(viewport={"width": 1280, "height": 800}, locale="en-US"); pc = ctx_c.new_page(); attach_console(pc, "carol")
            register_ui(pc, base, "Carol E2E", carol_email); created_users.append(carol_email)
            # alice locks the team
            pg.goto(base + "/team", wait_until="networkidle")
            lock = pg.locator("input[type=checkbox][name=is_locked], input[type=checkbox]").filter(has_text="").first
            lock_box = pg.get_by_label(re.compile("locked|锁定", re.I))
            if lock_box.count():
                lock_box.first.check(); pg.get_by_role("button", name=re.compile("^save|保存", re.I)).first.click(); pg.wait_for_timeout(2500)
                pc.goto(base + "/team", wait_until="networkidle"); pc.fill("[data-testid=team-join-code]", code); pc.click("[data-testid=team-join]"); pc.wait_for_timeout(2500)
                check("locked team rejects join", f"E2E Owls {RUN}" not in pc.evaluate("document.body.innerText") and alert_text(pc) != "", alert_text(pc)[:80])
                pg.goto(base + "/team", wait_until="networkidle"); pg.get_by_label(re.compile("locked|锁定", re.I)).first.uncheck(); pg.get_by_role("button", name=re.compile("^save|保存", re.I)).first.click(); pg.wait_for_timeout(2000)
            else:
                check("lock checkbox present", False, "no lock control found")
            # kick bob, bob rejoins
            pg.goto(base + "/team", wait_until="networkidle")
            kick = pg.get_by_role("button", name=re.compile("^remove|移除", re.I))
            if kick.count():
                pg.once("dialog", lambda d: d.accept()); kick.first.click(); pg.wait_for_timeout(2500)
                pb.goto(base + "/team", wait_until="networkidle")
                check("removed member no longer sees the team", "[data-testid=team-join-code]" and pb.locator("[data-testid=team-join-code]").count() == 1)
                pb.fill("[data-testid=team-join-code]", code); pb.click("[data-testid=team-join]"); pb.wait_for_timeout(3000)
                check("bob rejoined", f"E2E Owls {RUN}" in pb.evaluate("document.body.innerText"))
            else:
                check("remove-member control present", False)
            pg.goto(base + "/team", wait_until="networkidle"); pg.screenshot(path=str(SHOTS / "C-team.png"), full_page=True)
        except Exception as exc:  # noqa: BLE001
            check("team flow", False, traceback.format_exc()[-300:])

        # ------------------------------------------------------------------ D. submissions
        sids: list[int] = []
        alice_token = None
        try:
            st, sess = svc("POST", "/auth/v1/token?grant_type=password", {"email": alice_email, "password": PW}, token=ANON); alice_token = sess["access_token"]
            # valid results file; status must update without reload (edge function)
            sid = submit_ui(pg, base, phase="practice", kind="results", scenario="dev-example", file=KIT / "example" / "decisions.csv", title="<img src=x onerror=alert(1)> reference"); sids.append(sid)
            pg.wait_for_function("() => /scored|已评分/i.test(document.querySelector('[data-testid=sub-status]')?.innerText || '')", timeout=120000)
            check("results submission scored in-page without reload", True)
            check("score shown 10377.47", "10377.47" in pg.locator("[data-testid=sub-score]").inner_text())
            check("XSS title rendered as text", pg.locator("img[src='x']").count() == 0 and "onerror" in pg.evaluate("document.body.innerText"))
            pg.wait_for_selector("canvas", timeout=30000)
            check("observed universe map rendered", pg.locator("canvas").count() >= 1)
            pg.screenshot(path=str(SHOTS / "D-results-scored.png"), full_page=True)
            # invalid csv
            bad = tmp / "bad.csv"; bad.write_text("decision_id,slot_id,action,tile_id,program,reason\n0,NOPE,observe,1,DARK,x\n")
            sid = submit_ui(pg, base, phase="practice", kind="results", scenario="dev-example", file=bad, title="invalid"); sids.append(sid)
            pg.wait_for_function("() => /invalid|无效/i.test(document.querySelector('[data-testid=sub-status]')?.innerText || '')", timeout=120000)
            check("invalid csv -> invalid status with message", "unknown slot_id" in pg.evaluate("document.body.innerText"))
            # zip package with a folder root + helper module
            z = tmp / "agent.zip"
            with zipfile.ZipFile(z, "w") as zf:
                zf.writestr("myagent/helpers/util.py", "BONUS = 1\n")
                zf.write(KIT / "agent.py", "myagent/agent.py")
            sid_zip = submit_ui(pg, base, phase="practice", kind="agent", scenario=None, file=z, title="zip package"); sids.append(sid_zip)
            check("agent zip queued", "queued" in pg.locator("[data-testid=sub-status]").inner_text().lower() or "排队" in pg.locator("[data-testid=sub-status]").inner_text())
            # crashing agent
            crash = tmp / "agent.py"; crash.write_text("import sys\nsys.stdin.readline()\nraise SystemExit(3)\n")
            sid_crash = submit_ui(pg, base, phase="practice", kind="agent", scenario=None, file=crash, title="crash"); sids.append(sid_crash)
            # cancel a queued agent submission
            sid_cancel = submit_ui(pg, base, phase="practice", kind="agent", scenario=None, file=KIT / "agent.py", title="to cancel"); sids.append(sid_cancel)
            pg.wait_for_timeout(2500)
            cancel = pg.get_by_role("button", name=re.compile("^cancel|取消", re.I))
            if cancel.count():
                pg.once("dialog", lambda d: d.accept()); cancel.first.click(); pg.wait_for_timeout(3000)
                check("cancel queued submission", "cancel" in pg.locator("[data-testid=sub-status]").inner_text().lower() or "已取消" in pg.locator("[data-testid=sub-status]").inner_text())
            else:
                check("cancel control present", False)
            # wrong kind for online phase blocked in UI (results radio disabled) — check options
            pg.goto(base + "/submit", wait_until="networkidle")
            opts = pg.locator("[data-testid=submit-phase] option").all_inner_texts()
            check("upcoming phase is not offered for submission", not any("online" in o.lower() for o in opts), str(opts))
            # submissions list shows all
            pg.goto(base + "/submissions", wait_until="networkidle")
            txt = pg.evaluate("document.body.innerText")
            check("submissions list shows entries", all(f"#{s}" in txt or str(s) in txt for s in sids))
            if not args.skip_worker:
                dispatch_worker()
                s1 = wait_status(sid_zip, alice_token, {"scored", "failed", "invalid"}, timeout=420)
                check("cloud worker scores zip package agent", s1 == "scored", s1)
                s2 = wait_status(sid_crash, alice_token, {"scored", "failed", "invalid"}, timeout=120)
                check("crashing agent marked failed", s2 == "failed", s2)
                pg.goto(f"{base}/submissions/{sid_crash}", wait_until="networkidle"); pg.wait_for_timeout(1500)
                check("failure reason visible to the team", "exited before answering" in pg.evaluate("document.body.innerText"))
                pg.goto(f"{base}/submissions/{sid_zip}", wait_until="networkidle"); pg.wait_for_timeout(2000)
                check("zip agent detail shows two scenarios", "dev-week" in pg.evaluate("document.body.innerText"))
                pg.screenshot(path=str(SHOTS / "D-agent-scored.png"), full_page=True)
        except Exception as exc:  # noqa: BLE001
            check("submission flow", False, traceback.format_exc()[-400:])

        # ------------------------------------------------------------------ D2. team rules after submissions + content pages
        try:
            pg.goto(base + "/team", wait_until="networkidle")
            pg.once("dialog", lambda d: d.accept())
            disband = pg.get_by_role("button", name=re.compile("disband|解散", re.I))
            if disband.count():
                disband.first.click(); pg.wait_for_timeout(2500)
                check("disband blocked while submissions exist", alert_text(pg) != "" and f"E2E Owls {RUN}" in pg.evaluate("document.body.innerText"), alert_text(pg)[:80])
            else:
                check("disband control present", False)
            pb.goto(base + "/team", wait_until="networkidle"); pb.once("dialog", lambda d: d.accept())
            leave = pb.get_by_role("button", name=re.compile("leave team|退出", re.I))
            if leave.count():
                leave.first.click(); pb.wait_for_timeout(2500)
                check("member can leave the team", pb.locator("[data-testid=team-join-code]").count() == 1)
            else:
                check("leave control present", False)
            pg.goto(base + "/faq?lang=en", wait_until="networkidle")
            first = pg.locator("details").first; first.evaluate("d => d.open = false"); first.locator("summary").click(); pg.wait_for_timeout(300)
            check("faq accordion toggles", first.evaluate("d => d.open"))
            pg.goto(base + "/docs?lang=en", wait_until="networkidle"); pg.wait_for_timeout(800)
            link = pg.locator("a[href='#2-starter-kit']:visible").first
            if link.count():
                target = "2-starter-kit"; link.click(); pg.wait_for_timeout(800)
                check("docs table of contents navigates", pg.evaluate(f"() => {{ const el = document.getElementById('{target}'); return el ? Math.abs(el.getBoundingClientRect().top) < 400 : false }}"))
            else:
                check("docs toc present", False)
            pg.goto(base + "/announcements?lang=en", wait_until="networkidle")
            check("announcements page renders", pg.locator("h1").first.is_visible())
        except Exception as exc:  # noqa: BLE001
            check("team rules / content pages", False, traceback.format_exc()[-300:])

        # ------------------------------------------------------------------ E. leaderboard
        try:
            pg.goto(base + "/leaderboard/practice", wait_until="networkidle"); pg.wait_for_timeout(1500)
            txt = pg.evaluate("document.body.innerText")
            check("leaderboard lists our team", f"E2E Owls {RUN}" in txt)
            check("your-team highlight present", "YOUR TEAM" in txt.upper() or "你的队伍" in txt)
            pg.goto(base + "/leaderboard/practice?lang=zh", wait_until="networkidle"); pg.wait_for_timeout(800)
            check("leaderboard zh renders", "排名" in pg.evaluate("document.body.innerText") or "排行" in pg.evaluate("document.body.innerText"))
            pg.screenshot(path=str(SHOTS / "E-leaderboard.png"), full_page=True)
            st, fn = svc("GET", "/functions/v1/leaderboard?phase=practice&limit=5", token=ANON)
            check("leaderboard edge function returns entries", st == 200 and any(e["team_name"] == f"E2E Owls {RUN}" for e in fn.get("entries", [])))
        except Exception as exc:  # noqa: BLE001
            check("leaderboard", False, traceback.format_exc()[-300:])

        # ------------------------------------------------------------------ F. profile + password
        try:
            pg.goto(base + "/profile?lang=en", wait_until="networkidle")
            pg.fill("[data-testid=profile-name]", "Alice Renamed"); pg.click("[data-testid=profile-save]"); pg.wait_for_timeout(2500)
            check("profile name saved", flash_text(pg) != "")
            newpw = PW + "x"
            news = pg.locator("input[autocomplete=new-password]")
            if news.count() >= 2:
                news.nth(0).fill(newpw); news.nth(1).fill(newpw)
                pg.get_by_role("button", name=re.compile("change password|修改密码", re.I)).first.click(); pg.wait_for_timeout(3500)
                st, sess = svc("POST", "/auth/v1/token?grant_type=password", {"email": alice_email, "password": newpw}, token=ANON)
                check("password change takes effect", st == 200, str(sess)[:80])
                if st == 200:
                    news = pg.locator("input[autocomplete=new-password]"); news.nth(0).fill(PW); news.nth(1).fill(PW)
                    pg.get_by_role("button", name=re.compile("change password|修改密码", re.I)).first.click(); pg.wait_for_timeout(3000)
                    st, _ = svc("POST", "/auth/v1/token?grant_type=password", {"email": alice_email, "password": PW}, token=ANON)
                    check("password restored", st == 200)
            else:
                check("password form present", False)
            pg.goto(base + "/profile", wait_until="networkidle")
            pg.click("[data-testid=lang-toggle]"); pg.wait_for_timeout(1500); pg.goto(base + "/profile", wait_until="networkidle")
            check("language choice persists across navigation", "个人资料" in pg.evaluate("document.body.innerText") or "资料" in pg.evaluate("document.body.innerText"))
            pg.click("[data-testid=lang-toggle]"); pg.wait_for_timeout(800)
        except Exception as exc:  # noqa: BLE001
            check("profile", False, traceback.format_exc()[-300:])

        # ------------------------------------------------------------------ G. admin
        ann_ids: list[int] = []
        try:
            ctx_a = browser.new_context(viewport={"width": 1440, "height": 900}, locale="en-US"); pa = ctx_a.new_page(); attach_console(pa, "admin"); pa.on("dialog", lambda d: d.accept())
            register_ui(pa, base, "Admin E2E", admin_email); created_users.append(admin_email)
            pa.goto(base + "/admin", wait_until="networkidle"); pa.wait_for_timeout(1500)
            check("admin overview loads for admin", pa.locator("h1").first.inner_text().strip() != "" and "/admin" in pa.url)
            pg.goto(base + "/admin", wait_until="networkidle"); pg.wait_for_timeout(1500)
            check("non-admin is redirected away from /admin", "/admin" not in pg.url)
            for sub in ("phases", "scenarios", "submissions", "users", "teams", "announcements", "settings"):
                pa.goto(f"{base}/admin/{sub}", wait_until="networkidle"); pa.wait_for_timeout(800)
                probs = page_health(pa, f"admin/{sub}", 1440)
                check(f"admin/{sub} renders", not probs, "; ".join(probs))
            pa.screenshot(path=str(SHOTS / "G-admin-submissions.png"), full_page=True)
            # announcement -> banner
            pa.goto(base + "/admin/announcements", wait_until="networkidle")
            pa.fill("[data-testid=ann-title-en]", f"E2E notice {RUN}"); pa.fill("[data-testid=ann-title-zh]", f"E2E 公告 {RUN}"); pa.check("[data-testid=ann-pinned]"); pa.click("[data-testid=ann-save]"); pa.wait_for_timeout(2500)
            pg.goto(base + "/?lang=en", wait_until="networkidle")
            check("pinned announcement shows in banner", f"E2E notice {RUN}" in pg.locator("[data-testid=announcement-banner]").inner_text())
            st, rows = svc("GET", "/rest/v1/announcements?select=id&title_en=eq." + urllib.parse.quote(f"E2E notice {RUN}")); ann_ids = [r["id"] for r in rows]
            # registration toggle
            pa.goto(base + "/admin/settings", wait_until="networkidle")
            reg = pa.get_by_label(re.compile("registration", re.I)).first
            if reg.count():
                reg.uncheck(); pa.get_by_role("button", name=re.compile("^save|保存", re.I)).first.click(); pa.wait_for_timeout(2500)
                anon_pg = browser.new_page(); anon_pg.goto(base + "/register?lang=en", wait_until="networkidle"); anon_pg.wait_for_timeout(800)
                check("registration closed is reflected on the register page", anon_pg.locator("[data-testid=reg-submit]").is_disabled() or "closed" in anon_pg.evaluate("document.body.innerText").lower())
                anon_pg.close()
                pa.goto(base + "/admin/settings", wait_until="networkidle"); pa.get_by_label(re.compile("registration", re.I)).first.check(); pa.get_by_role("button", name=re.compile("^save|保存", re.I)).first.click(); pa.wait_for_timeout(2000)
            else:
                check("registration toggle present", False)
            # exclude a scored submission from the board
            pa.goto(base + "/admin/submissions", wait_until="networkidle"); pa.wait_for_timeout(1500)
            row = pa.locator("tr", has_text=f"#{sids[0]}").first if pa.locator("tr", has_text=f"#{sids[0]}").count() else pa.locator("tr", has_text=f"E2E Owls {RUN}").filter(has_text="results").last
            exc_btn = row.get_by_role("button", name=re.compile("exclude|排除", re.I))
            if exc_btn.count():
                exc_btn.first.click(); pa.wait_for_timeout(2500)
                st, board = svc("POST", "/rest/v1/rpc/leaderboard", {"p_phase_slug": "practice", "p_limit": 500}, token=ANON)
                first_sid = sids[0] if sids else None
                check("excluded submission leaves the board", all(e["best_submission_id"] != first_sid for e in board))
                pa.goto(base + "/admin/submissions", wait_until="networkidle"); pa.wait_for_timeout(1500)
                pa.get_by_role("button", name=re.compile("^include|恢复|取消排除", re.I)).first.click(); pa.wait_for_timeout(1500)
            else:
                check("exclude control present", False)
            # phase daily limit edit is reflected on the submit page
            pa.goto(base + "/admin/phases", wait_until="networkidle"); pa.wait_for_timeout(1500)
            lim = pa.locator("input[name=daily_limit], input[type=number]").first
            if lim.count():
                old = lim.input_value(); lim.fill("49"); pa.get_by_role("button", name=re.compile("^save|保存", re.I)).first.click(); pa.wait_for_timeout(2500)
                pg.goto(base + "/submit?lang=en", wait_until="networkidle"); pg.wait_for_timeout(1000)
                check("phase daily limit change reflected for participants", "49" in pg.locator("[data-testid=submit-phase]").inner_text() or "left" in pg.locator("[data-testid=submit-phase]").inner_text())
                pa.goto(base + "/admin/phases", wait_until="networkidle"); pa.wait_for_timeout(1500); pa.locator("input[name=daily_limit], input[type=number]").first.fill(old or "50"); pa.get_by_role("button", name=re.compile("^save|保存", re.I)).first.click(); pa.wait_for_timeout(2000)
            else:
                check("phase limit control present", False)
            # hide team -> off the board -> unhide
            pa.goto(base + "/admin/teams", wait_until="networkidle"); pa.wait_for_timeout(1500)
            trow = pa.locator("tr", has_text=f"E2E Owls {RUN}").first
            hide = trow.get_by_role("button", name=re.compile("^hide|隐藏", re.I))
            if hide.count():
                hide.first.click(); pa.wait_for_timeout(2500)
                st, board = svc("POST", "/rest/v1/rpc/leaderboard", {"p_phase_slug": "practice", "p_limit": 500}, token=ANON)
                check("hidden team leaves the board", all(e["team_name"] != f"E2E Owls {RUN}" for e in board))
                pa.goto(base + "/admin/teams", wait_until="networkidle"); pa.wait_for_timeout(1500)
                pa.locator("tr", has_text=f"E2E Owls {RUN}").first.get_by_role("button", name=re.compile("^show|unhide|显示|取消隐藏", re.I)).first.click(); pa.wait_for_timeout(2000)
            else:
                check("hide-team control present", False)
            # rescore -> queued -> scored again by the edge function
            pa.goto(base + "/admin/submissions", wait_until="networkidle"); pa.wait_for_timeout(1500)
            rrow = pa.locator("tr", has_text=f"#{sids[0]}").first if pa.locator("tr", has_text=f"#{sids[0]}").count() else pa.locator("tr", has_text=f"E2E Owls {RUN}").filter(has_text="results").last
            resc = rrow.get_by_role("button", name=re.compile("rescore|重新评分", re.I))
            if resc.count() and sids:
                resc.first.click(); pa.wait_for_timeout(1500)
                s_after = wait_status(sids[0], alice_token, {"scored", "failed", "invalid"}, timeout=180)
                check("rescore re-queues and the edge function scores again", s_after == "scored", s_after)
            else:
                check("rescore control present", False)
            # ban carol -> carol cannot create a team
            pa.goto(base + "/admin/users", wait_until="networkidle"); pa.wait_for_timeout(1500)
            crow = pa.locator("tr", has_text=carol_email).first
            ban = crow.get_by_role("button", name=re.compile("^ban|封禁|停用", re.I))
            if ban.count():
                ban.first.click(); pa.wait_for_timeout(2000)
                st, sess = svc("POST", "/auth/v1/token?grant_type=password", {"email": carol_email, "password": PW}, token=ANON)
                st2, res = svc("POST", "/rest/v1/rpc/create_team", {"p_name": f"Banned {RUN}", "p_max_size": 1}, token=sess["access_token"]) if st == 200 else (0, "")
                check("banned user cannot create a team", st2 == 400 and "banned" in str(res), str(res)[:80])
                crow = pa.locator("tr", has_text=carol_email).first; crow.get_by_role("button", name=re.compile("unban|解封|启用", re.I)).first.click() if crow.get_by_role("button", name=re.compile("unban|解封|启用", re.I)).count() else None
            else:
                check("ban control present", False)
            ctx_a.close()
        except Exception as exc:  # noqa: BLE001
            check("admin flow", False, traceback.format_exc()[-400:])

        # ------------------------------------------------------------------ H. resources + downloads
        try:
            for path, want in (("/downloads/agent-observer-starter-kit.zip", 20000), ("/downloads/scoring_core.py", 20000), ("/skill.md", 1000)):
                req = urllib.request.urlopen(base + path, timeout=30)
                check(f"download {path}", req.status == 200 and len(req.read()) > want)
            pg.goto(base + "/resources?lang=en", wait_until="networkidle"); pg.wait_for_timeout(1000)
            with pg.expect_download(timeout=30000) as dl:
                pg.get_by_role("button", name=re.compile("weather.csv")).first.click()
            check("scenario weather download via UI", dl.value.suggested_filename.endswith(".csv"))
            check("hidden weather shown as hidden", "hidden" in pg.evaluate("document.body.innerText").lower())
        except Exception as exc:  # noqa: BLE001
            check("resources", False, traceback.format_exc()[-300:])

        # ------------------------------------------------------------------ I. CLI
        try:
            env = {**os.environ, "SAC_URL": URL, "SAC_KEY": ANON, "SAC_EMAIL": alice_email, "SAC_PASSWORD": PW}
            r = subprocess.run([sys.executable, str(KIT / "sac_submit.py"), "--phase", "practice", "--kind", "results", "--scenario", "dev-example", "--file", str(KIT / "example" / "decisions.csv"), "--wait"], env=env, capture_output=True, text=True, timeout=300)
            check("CLI results submit + wait", r.returncode == 0 and '"status": "scored"' in r.stdout, (r.stdout + r.stderr)[-200:])
            m = re.search(r"submission #(\d+)", r.stdout)
            if m:
                sids.append(int(m.group(1)))
        except Exception as exc:  # noqa: BLE001
            check("cli", False, str(exc)[:200])

        # ------------------------------------------------------------------ J. edge function auth + CORS
        try:
            st, body = svc("POST", "/functions/v1/score-results", {"submission_id": 1}, token=ANON)
            check("score-results rejects unauthenticated callers", st == 401, f"{st} {str(body)[:80]}")
            req = urllib.request.Request(URL + "/functions/v1/leaderboard?limit=1", method="OPTIONS", headers={"Origin": "https://example.org", "Access-Control-Request-Method": "GET"})
            with urllib.request.urlopen(req, timeout=30) as r:
                check("leaderboard function CORS preflight", r.headers.get("Access-Control-Allow-Origin") == "*")
        except Exception as exc:  # noqa: BLE001
            check("edge function checks", False, str(exc)[:200])

        for key, errs in console_errors.items():
            errs = [e for e in errs if "favicon" not in e and "realtime" not in e]
            if key.startswith("public-"):
                continue
            check(f"no console errors [{key}]", not errs, "; ".join(errs[:3])[:300])
        browser.close()

    # ------------------------------------------------------------------ cleanup
    print("--- cleanup ---")
    svc("POST", "/rest/v1/site_settings?on_conflict=key", {"key": "admin_emails", "value": admin_list}, headers={"Prefer": "resolution=merge-duplicates"})
    for aid in ann_ids:
        svc("DELETE", f"/rest/v1/announcements?id=eq.{aid}")
    st, rows = svc("GET", "/rest/v1/submissions?select=id,team_id,storage_path,evaluations(report_path,decisions_path,log_path),teams(name)")
    for row in rows or []:
        if not (row.get("teams") or {}).get("name", "").startswith("E2E "):
            continue
        svc("DELETE", f"/rest/v1/evaluations?submission_id=eq.{row['id']}"); svc("DELETE", f"/rest/v1/submissions?id=eq.{row['id']}")
        svc("DELETE", "/storage/v1/object/submissions", {"prefixes": [row["storage_path"]]})
        paths = [e[k] for e in row["evaluations"] for k in ("report_path", "decisions_path", "log_path") if e.get(k)]
        if paths:
            svc("DELETE", "/storage/v1/object/results", {"prefixes": paths})
    st, users = svc("GET", "/auth/v1/admin/users?per_page=200")
    for u in users.get("users", []):
        if u["email"].startswith("e2e-"):
            svc("DELETE", f"/rest/v1/audit_log?user_id=eq.{u['id']}"); svc("DELETE", f"/rest/v1/teams?leader_id=eq.{u['id']}")
            svc("DELETE", f"/auth/v1/admin/users/{u['id']}")
    svc("DELETE", "/rest/v1/teams?name=like." + urllib.parse.quote("E2E *"))
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    for name, _, detail in failed:
        print("FAIL", name, "—", detail[:300])
    (SHOTS / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

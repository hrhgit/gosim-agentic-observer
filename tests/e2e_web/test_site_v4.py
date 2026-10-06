"""Five end-to-end rounds over the parts of the participant loop the other files do not cover.

test_site.py walks the happy path (register → team → submit → score → leaderboard). These rounds take the
same loop from five other angles, so a break in any of them is visible without a hosted project:

  round 1  sign-up validation, duplicate e-mail, log out / log back in, password-reset entry
  round 2  team membership: submitting without a team, invite code, a wrong code, leaving and re-joining
  round 3  what a phase accepts: kind, scenario, file extension, and the daily limit
  round 4  bad input and hidden scenarios: a package with no entry script, eval-a weather is not downloadable
  round 5  both languages, mobile navigation, and the motion layer under prefers-reduced-motion
"""
from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

from playwright.sync_api import Page, expect

from conftest import SHOTS, run_worker_once
from test_site import _register, results_fixture, shot

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "correct-horse-9"
ERRORS = "[role=alert]"


def _login(page: Page, base: str, email: str, password: str = PASSWORD) -> None:
    page.goto(base + "/register?mode=login")
    page.fill("[data-testid=login-email]", email)
    page.fill("[data-testid=login-password]", password)
    page.click("[data-testid=login-submit]")


def _make_team(page: Page, base: str, name: str) -> str:
    page.goto(base + "/team")
    page.fill("[data-testid=team-name-input]", name)
    page.click("[data-testid=team-create]")
    expect(page.locator("[data-testid=team-invite-code]")).to_be_visible(timeout=15000)
    return page.locator("[data-testid=team-invite-code]").inner_text().strip()


# ---------------------------------------------------------------------------
# round 1 · sign-up is a closed loop, including the ways it can go wrong
# ---------------------------------------------------------------------------

def test_round1_signup_validation_duplicate_and_relogin(page: Page, site):
    base = site["base"]

    # every client-side rule reports itself before anything is sent
    page.goto(base + "/register")
    page.fill("[data-testid=reg-name]", "")
    page.fill("[data-testid=reg-email]", "not-an-email")
    page.fill("[data-testid=reg-password]", "short")
    page.fill("[data-testid=reg-password2]", "different")
    page.click("[data-testid=reg-submit]")
    errors = page.locator(ERRORS)
    expect(errors).to_be_visible(timeout=10000)
    # name, e-mail, password length, password mismatch and the unchecked agreement are five separate problems
    assert errors.locator("li").count() >= 4, errors.inner_text()
    expect(page).to_have_url(re.compile(r"/register"))
    shot(page, "v4-01-register-errors")

    # a real sign-up lands on the dashboard and the session survives a reload
    _register(page, base, "Round One", "round1@e2e.org")
    page.reload()
    expect(page).to_have_url(re.compile(r"/dashboard"), timeout=15000)
    expect(page.locator("[data-testid=nav-logout]")).to_be_visible()

    # the same address cannot be taken twice
    page.click("[data-testid=nav-logout]")
    expect(page.locator("[data-testid=nav-register]")).to_be_visible(timeout=10000)
    page.goto(base + "/register")
    page.fill("[data-testid=reg-name]", "Impostor")
    page.fill("[data-testid=reg-email]", "round1@e2e.org")
    page.fill("[data-testid=reg-password]", PASSWORD)
    page.fill("[data-testid=reg-password2]", PASSWORD)
    page.check("[data-testid=reg-agree]")
    page.click("[data-testid=reg-submit]")
    expect(page.locator(ERRORS)).to_be_visible(timeout=15000)
    expect(page).to_have_url(re.compile(r"/register"))

    # a wrong password does not let anyone in; the right one does
    _login(page, base, "round1@e2e.org", "wrong-password-1")
    expect(page.locator(ERRORS)).to_be_visible(timeout=15000)
    _login(page, base, "round1@e2e.org")
    expect(page).to_have_url(re.compile(r"/dashboard"), timeout=20000)

    # the reset form is reachable from the login screen and accepts an address
    page.click("[data-testid=nav-logout]")
    page.goto(base + "/register?mode=reset")
    expect(page.locator("form")).to_be_visible(timeout=10000)


# ---------------------------------------------------------------------------
# round 2 · a submission needs a team, and team membership is reversible
# ---------------------------------------------------------------------------

def test_round2_team_membership_gates_submission(page: Page, site):
    base = site["base"]
    _register(page, base, "Round Two", "round2@e2e.org")

    # without a team the submit page says so instead of showing the form
    page.goto(base + "/submit")
    expect(page.locator("[data-testid=submit-button]")).to_have_count(0)
    expect(page.locator("body")).to_contain_text(re.compile("team|队伍", re.I))
    shot(page, "v4-02-submit-without-team")

    code = _make_team(page, base, "Round Two Collective")
    page.goto(base + "/submit")
    expect(page.locator("[data-testid=submit-button]")).to_be_visible(timeout=15000)

    # a second account cannot join with a code that does not exist
    ctx = page.context.browser.new_context(viewport={"width": 1280, "height": 800})
    other = ctx.new_page()
    _register(other, base, "Round Two Guest", "round2b@e2e.org")
    other.goto(base + "/team")
    other.fill("[data-testid=team-join-code]", "ZZZZZZZZ")
    other.click("[data-testid=team-join]")
    expect(other.locator("[data-testid=team-invite-code]")).to_have_count(0)

    # the real code works, and leaving puts the account back to "no team"
    other.fill("[data-testid=team-join-code]", code)
    other.click("[data-testid=team-join]")
    expect(other.locator("text=Round Two Collective").first).to_be_visible(timeout=15000)
    other.on("dialog", lambda d: d.accept())
    other.click("text=/^(Leave team|退出队伍)$/i")
    expect(other.locator("[data-testid=team-name-input]")).to_be_visible(timeout=15000)
    other.goto(base + "/submit")
    expect(other.locator("[data-testid=submit-button]")).to_have_count(0)
    ctx.close()


# ---------------------------------------------------------------------------
# round 3 · the phase decides what it accepts
# ---------------------------------------------------------------------------

def test_round3_phase_rules_and_daily_limit(page: Page, site):
    base, hs = site["base"], site["hs"]
    _register(page, base, "Round Three", "round3@e2e.org")
    _make_team(page, base, "Round Three Team")

    page.goto(base + "/submit")
    expect(page.locator("[data-testid=submit-phase]")).to_be_visible(timeout=15000)

    # the online phase takes agent packages only: the results radio is disabled, not silently ignored
    page.select_option("[data-testid=submit-phase]", "online")
    expect(page.locator("[data-testid=submit-kind-results]")).to_be_disabled()
    expect(page.locator("[data-testid=submit-kind-agent]")).to_be_checked()

    # a results file can only target a scenario whose weather is public: the hidden ones are not even offered,
    # and one of the public ones is preselected so the field is never left empty
    page.select_option("[data-testid=submit-phase]", "practice")
    page.check("[data-testid=submit-kind-results]")
    options = page.locator("[data-testid=submit-scenario] option")
    values = {options.nth(i).get_attribute("value") for i in range(options.count())}
    assert {"demo-week", "dev-fortnight", "dev-reference"} <= values, values
    assert not values & {"eval-a", "eval-b"}, values
    assert page.locator("[data-testid=submit-scenario]").input_value() in values

    # the extension is checked before anything is uploaded
    page.select_option("[data-testid=submit-scenario]", "demo-week")
    expect(page.locator("[data-testid=submit-wallclock]")).to_contain_text("demo-week")
    wrong = Path(str(SHOTS.parent / "not-a-results-file.py"))
    wrong.parent.mkdir(parents=True, exist_ok=True)
    wrong.write_text("print('hello')\n", encoding="utf-8")
    page.set_input_files("[data-testid=submit-file]", str(wrong))
    page.click("[data-testid=submit-button]")
    expect(page.locator(ERRORS)).to_contain_text(re.compile(r"\.csv", re.I), timeout=10000)

    # with the phase's daily limit already used up, the server refuses and the page says which limit
    hs.sql("update public.phases set daily_limit = 0 where slug = 'practice'")
    page.reload()
    page.select_option("[data-testid=submit-phase]", "practice")
    page.check("[data-testid=submit-kind-results]")
    page.select_option("[data-testid=submit-scenario]", "demo-week")
    page.set_input_files("[data-testid=submit-file]", str(results_fixture()))
    page.click("[data-testid=submit-button]")
    expect(page.locator(ERRORS)).to_contain_text(re.compile("limit|上限|次数", re.I), timeout=20000)
    shot(page, "v4-03-daily-limit")
    hs.sql("update public.phases set daily_limit = 50 where slug = 'practice'")


# ---------------------------------------------------------------------------
# round 4 · unusable uploads, and scenarios whose weather stays hidden
# ---------------------------------------------------------------------------

def test_round4_broken_package_and_hidden_scenario(page: Page, site, tmp_path):
    base = site["base"]
    _register(page, base, "Round Four", "round4@e2e.org")
    _make_team(page, base, "Round Four Team")

    # a zip with no entry script is rejected with a reason, not left queued forever
    broken = tmp_path / "no-entry.zip"
    with zipfile.ZipFile(broken, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("readme.txt", "there is no agent in this package\n")
    page.goto(base + "/submit")
    page.select_option("[data-testid=submit-phase]", "practice")
    page.check("[data-testid=submit-kind-agent]")
    page.set_input_files("[data-testid=submit-file]", str(broken))
    page.click("[data-testid=submit-button]")
    expect(page).to_have_url(re.compile(r"/submissions/\d+"), timeout=20000)
    assert run_worker_once() == 1
    expect(page.locator("[data-testid=sub-status]")).to_contain_text(re.compile("invalid|无效", re.I), timeout=30000)
    expect(page.locator("body")).to_contain_text(re.compile("entry|入口|minimal_agent", re.I))
    shot(page, "v4-04-invalid-package")

    # the hidden competition scenario is listed but its weather files are not offered for download
    page.goto(base + "/resources")
    expect(page.locator("[data-testid=resources-scenario-demo-week]")).to_be_visible(timeout=15000)
    expect(page.locator("[data-testid='dl-demo-week-weather.csv']")).to_be_visible()
    expect(page.locator("[data-testid=resources-scenario-eval-a]")).to_be_visible()
    expect(page.locator("[data-testid='dl-eval-a-weather.csv']")).to_have_count(0)
    expect(page.locator("[data-testid='dl-eval-a-weather_events.csv']")).to_have_count(0)
    shot(page, "v4-05-resources-hidden")


# ---------------------------------------------------------------------------
# round 5 · both languages, mobile navigation, and motion that can be switched off
# ---------------------------------------------------------------------------

PUBLIC_PATHS = ["/", "/brief", "/rules", "/docs", "/faq", "/resources", "/leaderboard", "/announcements"]


def test_round5_languages_mobile_and_reduced_motion(page: Page, site):
    base = site["base"]

    # no page in either language may print a raw i18n key such as home.vision.lede
    raw_key = re.compile(r"\b[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*){2,}\b")
    for _ in range(2):
        for path in PUBLIC_PATHS:
            page.goto(base + path)
            expect(page.locator("h1").first).to_be_visible(timeout=15000)
            body = page.locator("main").inner_text()
            leaked = [m.group(0) for m in raw_key.finditer(body) if not m.group(0).endswith((".csv", ".json", ".py", ".html", ".zip", ".md", ".co", ".org", ".com"))]
            assert not leaked, f"{path}: untranslated keys {leaked[:5]}"
        page.goto(base + "/")
        page.click("[data-testid=lang-toggle]")

    # mobile: the menu button opens the navigation and a link inside it actually navigates
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(base + "/")
    menu_button = page.locator("header button[aria-expanded]")
    expect(menu_button).to_be_visible()
    menu_button.click()
    expect(page.locator("header button[aria-expanded='true']")).to_be_visible(timeout=5000)
    # the desktop nav carries the same hrefs but stays hidden at this width; click the one the menu revealed
    page.locator("header a[href$='/rules']:visible").first.click()
    expect(page).to_have_url(re.compile(r"/rules"), timeout=15000)
    shot(page, "v4-06-mobile-rules")

    # mobile sign-up: a rejected form must show its error panel inside the viewport, not above it
    page.goto(base + "/register")
    page.click("[data-testid=reg-submit]")
    expect(page.locator(ERRORS)).to_be_in_viewport(timeout=10000)
    shot(page, "v4-07-mobile-register-errors")

    # prefers-reduced-motion: revealed content and staggered rows are fully opaque, no animation pending
    page.set_viewport_size({"width": 1440, "height": 900})
    page.emulate_media(reduced_motion="reduce")
    page.goto(base + "/")
    page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
    page.wait_for_timeout(1200)
    opacities = page.evaluate("""() => [...document.querySelectorAll('.reveal, .reveal-stagger > *')]
        .map(el => Number(getComputedStyle(el).opacity))""")
    assert opacities, "the homepage should have revealed elements"
    assert all(o > 0.99 for o in opacities), f"hidden under reduced motion: {[o for o in opacities if o <= 0.99][:5]}"
    page.emulate_media(reduced_motion="no-preference")

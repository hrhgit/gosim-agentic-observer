from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from conftest import SHOTS, run_worker_once

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures"
RESULTS_FIXTURE = FIXTURES / "dev-fortnight-decisions.csv"
SCORE_RE = re.compile(r"^-?\d+\.\d{2}$")


def results_fixture() -> Path:
    """decisions.csv of the minimal agent on the harness scenario dev-fortnight (created by the harness task)."""
    if RESULTS_FIXTURE.exists():
        return RESULTS_FIXTURE
    pytest.skip(f"missing fixture {RESULTS_FIXTURE}")


def agent_package(tmp_path: Path) -> Path:
    """Zip of the minimal participant agent (entry minimal_agent.py + its modules + scoring_preview.py), deterministic mode."""
    src = ROOT / "challenge" / "participant_agent"
    out = tmp_path / "minimal-agent.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(src.glob("*.py")):
            zf.write(f, f.name)
        zf.write(ROOT / "challenge" / "scoring_preview.py", "scoring_preview.py")
        zf.writestr(".env", "MODEL_PROVIDER=deterministic\n")
    return out


def shot(page: Page, name: str, full: bool = True):
    page.screenshot(path=str(SHOTS / f"{name}.png"), full_page=full)


def _register(page: Page, base: str, name: str, email: str, password: str = "correct-horse-9"):
    page.goto(base + "/register")
    page.fill("[data-testid=reg-name]", name)
    page.fill("[data-testid=reg-email]", email)
    page.fill("[data-testid=reg-password]", password)
    page.fill("[data-testid=reg-password2]", password)
    page.check("[data-testid=reg-agree]")
    page.click("[data-testid=reg-submit]")
    expect(page).to_have_url(re.compile(r"/dashboard"), timeout=20000)


def test_public_pages_and_language(page: Page, site):
    base = site["base"]
    page.goto(base + "/")
    expect(page.locator("h1").first).to_contain_text(re.compile("巡天智能体|Agent Observer"))
    page.click("[data-testid=lang-toggle]")
    page.click("[data-testid=lang-toggle]")
    shot(page, "01-home")
    for path in ["/brief", "/rules", "/docs", "/faq", "/resources", "/leaderboard", "/announcements"]:
        page.goto(base + path)
        expect(page.locator("h1").first).to_be_visible()
        shot(page, "02-page" + path.replace("/", "-"))
    page.goto(base + "/resources")
    expect(page.locator("[data-testid=resources-scenario-dev-reference]")).to_be_visible(timeout=15000)
    expect(page.locator("[data-testid=resources-scenario-dev-fortnight]")).to_contain_text("dev-fortnight")
    expect(page.locator("[data-testid='dl-dev-fortnight-tiles.csv']")).to_be_visible()


def test_participant_journey(page: Page, site, tmp_path):
    base = site["base"]
    _register(page, base, "Ada Lovelace", "ada@e2e.org")
    shot(page, "10-dashboard")
    page.goto(base + "/team")
    page.fill("[data-testid=team-name-input]", "Analytical Engines")
    page.click("[data-testid=team-create]")
    expect(page.locator("[data-testid=team-invite-code]")).to_be_visible(timeout=15000)
    code = page.locator("[data-testid=team-invite-code]").inner_text().strip()
    assert re.fullmatch(r"[A-Z0-9]{8}", code), code
    shot(page, "11-team")

    page.goto(base + "/submit")
    page.select_option("[data-testid=submit-phase]", "practice")
    page.check("[data-testid=submit-kind-results]")
    page.select_option("[data-testid=submit-scenario]", "dev-fortnight")
    expect(page.locator("[data-testid=submit-wallclock]")).to_contain_text("dev-fortnight")
    page.set_input_files("[data-testid=submit-file]", str(results_fixture()))
    page.fill("[data-testid=submit-title]", "minimal agent trace")
    page.click("[data-testid=submit-button]")
    expect(page).to_have_url(re.compile(r"/submissions/\d+"), timeout=20000)
    expect(page.locator("[data-testid=sub-status]")).to_contain_text(re.compile("queued|Queued|排队"), timeout=15000)
    expect(page.locator("[data-testid=worker-status]")).to_be_visible(timeout=10000)  # evaluator liveness + queue position
    assert run_worker_once() == 1
    expect(page.locator("[data-testid=sub-status]")).to_contain_text(re.compile("scored|Scored|已评分"), timeout=30000)
    expect(page.locator("[data-testid=sub-score]")).to_have_text(SCORE_RE, timeout=15000)
    expect(page.locator("[data-testid=evaluation-dev-fortnight] [data-testid=termination-pill]")).to_contain_text(re.compile("trace complete|轨迹回放完成"))
    expect(page.locator("[data-testid=penalty-table]")).to_be_visible()
    shot(page, "12-submission-scored")

    page.goto(base + "/submit")
    page.select_option("[data-testid=submit-phase]", "practice")
    page.check("[data-testid=submit-kind-agent]")
    expect(page.locator("[data-testid=agent-hints]")).to_contain_text("requirements.txt")
    expect(page.locator("[data-testid=submit-wallclock]")).to_contain_text("dev-reference")
    page.set_input_files("[data-testid=submit-file]", str(agent_package(tmp_path)))
    page.click("[data-testid=submit-button]")
    expect(page).to_have_url(re.compile(r"/submissions/\d+"), timeout=20000)
    assert run_worker_once() == 1
    expect(page.locator("[data-testid=sub-status]")).to_contain_text(re.compile("scored|Scored|已评分"), timeout=90000)
    expect(page.locator("[data-testid=evaluation-dev-fortnight]")).to_be_visible()
    expect(page.locator("[data-testid=evaluation-dev-reference]")).to_be_visible()
    expect(page.locator("[data-testid=evaluation-dev-fortnight] [data-testid=termination-pill]")).to_contain_text(re.compile("survey complete|巡天完成"))
    expect(page.locator("[data-testid=evaluation-dev-fortnight] [data-testid=agent-panel]")).to_contain_text("/ 1800 s")
    shot(page, "13-agent-submission")

    page.goto(base + "/leaderboard/practice")
    expect(page.locator("[data-testid=lb-row]").first).to_contain_text("Analytical Engines", timeout=15000)
    shot(page, "14-leaderboard")

    ctx2 = page.context.browser.new_context(viewport={"width": 1280, "height": 800})
    p2 = ctx2.new_page()
    _register(p2, base, "Charles Babbage", "charles@e2e.org")
    p2.goto(base + "/team")
    p2.fill("[data-testid=team-join-code]", code)
    p2.click("[data-testid=team-join]")
    expect(p2.locator("text=Analytical Engines").first).to_be_visible(timeout=15000)
    ctx2.close()

    page.goto(base + "/profile")
    page.fill("[data-testid=profile-name]", "Ada K. Lovelace")
    page.click("[data-testid=profile-save]")
    expect(page.locator("[data-testid=flash]")).to_be_visible(timeout=10000)
    page.click("[data-testid=nav-logout]")
    expect(page.locator("[data-testid=nav-register]")).to_be_visible(timeout=10000)


def test_admin_journey(page: Page, site):
    base = site["base"]
    _register(page, base, "Admin", "admin@e2e.org")
    page.goto(base + "/admin")
    expect(page.locator("h1").first).to_be_visible(timeout=15000)
    shot(page, "20-admin")
    page.goto(base + "/admin/announcements")
    page.fill("[data-testid=ann-title-en]", "Online competition opens Oct 5 00:00 (UTC+8)")
    page.fill("[data-testid=ann-title-zh]", "线上比赛 10 月 5 日 0 点开放")
    page.check("[data-testid=ann-pinned]")
    page.click("[data-testid=ann-save]")
    page.goto(base + "/")
    expect(page.locator("[data-testid=announcement-banner]")).to_contain_text("Online competition opens", timeout=15000)
    shot(page, "21-home-banner")


def test_mobile_layout(page: Page, site):
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(site["base"] + "/")
    expect(page.locator("h1").first).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    shot(page, "30-mobile", full=False)

"""Challenge v3 contract: score-report-v3 submission page with replay iframe, leaderboard breakdown, per-scenario file matrix, docs, admin scenario flags."""
from __future__ import annotations

import re
from pathlib import Path

from playwright.sync_api import Page, expect

from test_site_v2 import _login

ROOT = Path(__file__).resolve().parents[2]
SHOTS_V3 = ROOT / "artifacts" / "screenshots-web-v3"


def shot(page: Page, name: str, full: bool = True):
    SHOTS_V3.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS_V3 / f"{name}.png"), full_page=full)


def test_submission_detail_v3_and_replay(page: Page, site):
    """The results submission of team Analytical Engines (test_site.py) shows the v3 report and loads the replay iframe."""
    base = site["base"]
    _login(page, base, "ada@e2e.org")
    page.goto(base + "/submissions")
    expect(page.locator("table tbody tr").first).to_be_visible(timeout=15000)
    # the first (oldest) row is the results file; open the agent run (newest) first
    page.locator("table tbody tr").first.locator("a").first.click()
    expect(page).to_have_url(re.compile(r"/submissions/\d+"), timeout=15000)
    expect(page.locator("[data-testid=score-card]")).to_be_visible(timeout=15000)
    page.wait_for_timeout(600)
    shot(page, "00-submission-v3-top", full=False)
    ev = page.locator("[data-testid=evaluation-dev-fortnight]")
    expect(ev).to_be_visible()
    expect(ev.locator("[data-testid=penalty-table]")).to_be_visible()
    expect(ev.locator("[data-testid=region-grid]")).to_contain_text("R00")
    expect(ev.locator("[data-testid=wait-seconds]")).to_be_visible()
    expect(ev.locator("[data-testid=action-timeline]")).to_be_visible(timeout=20000)
    expect(ev.locator("[data-testid=observed-sky] canvas")).to_be_visible(timeout=20000)
    # agent-run panel for the agent submission
    if ev.locator("[data-testid=agent-panel]").count():
        expect(ev.locator("[data-testid=agent-panel]")).to_contain_text(re.compile(r"\d+\.\d s / \d+ s"))
    # replay: loaded from the private results bucket into a sandboxed iframe
    ev.locator("[data-testid=replay-open]").click()
    expect(ev.locator("[data-testid=replay-frame]")).to_be_visible(timeout=30000)
    frame = ev.locator("[data-testid=replay-frame]").element_handle().content_frame()
    assert frame is not None
    expect(frame.locator("body")).to_contain_text(re.compile("ASTRA|SURVEY|replay", re.I), timeout=20000)
    expect(ev.locator("[data-testid=replay-new-tab]")).to_be_visible()
    page.wait_for_timeout(800)
    shot(page, "01-submission-v3-replay")
    ev.scroll_into_view_if_needed()
    shot(page, "01a-submission-v3-scenario", full=False)
    ev.locator("[data-testid=replay-frame]").scroll_into_view_if_needed()
    ev.locator("[data-testid=replay-frame]").screenshot(path=str(SHOTS_V3 / "01b-replay-frame.png"))
    page.click("[data-testid=nav-logout]")
    expect(page.locator("[data-testid=nav-register]")).to_be_visible(timeout=10000)


def test_leaderboard_v3(page: Page, site):
    base = site["base"]
    page.goto(base + "/leaderboard/practice")
    expect(page.locator("[data-testid=score-bars]")).to_be_visible(timeout=15000)
    expect(page.locator("[data-testid=score-bar]").first).to_be_visible()
    head = page.locator("table thead").first
    expect(head).to_contain_text(re.compile("Base science|基础科学分"))
    expect(head).to_contain_text(re.compile("Penalties|惩罚"))
    expect(head).to_contain_text(re.compile("REQ missing|REQ 缺失"))
    expect(page.locator("[data-testid=lb-row]").first).to_contain_text("Analytical Engines")
    shot(page, "02-leaderboard-v3")


def test_resources_docs_rules_v3(page: Page, site):
    base = site["base"]
    page.goto(base + "/resources")
    for slug in ("dev-reference", "dev-fortnight", "eval-a", "eval-b"):
        expect(page.locator(f"[data-testid=resources-scenario-{slug}]")).to_be_visible(timeout=15000)
    dev = page.locator("[data-testid=resources-scenario-dev-fortnight]")
    expect(dev).to_contain_text(re.compile("weather public|天气公开"))
    expect(dev.locator("[data-testid='dl-dev-fortnight-weather_events.csv']")).to_be_visible()
    expect(dev.locator("[data-testid='dl-dev-fortnight-score_config.json']")).to_be_visible()
    hidden = page.locator("[data-testid=resources-scenario-eval-a]")
    expect(hidden).to_contain_text(re.compile("weather hidden|天气隐藏"))
    assert hidden.locator("[data-testid='dl-eval-a-weather.csv']").count() == 0
    expect(hidden.locator("[data-testid='dl-eval-a-tiles.csv']")).to_be_visible()
    # a public file actually downloads through the storage API
    with page.expect_download(timeout=20000) as dl:
        dev.locator("[data-testid='dl-dev-fortnight-tiles.csv']").click()
    assert dl.value.suggested_filename == "dev-fortnight-tiles.csv"
    shot(page, "03-resources-v3")

    page.goto(base + "/docs")
    expect(page.locator("[data-testid=protocol-explorer]")).to_be_visible()
    expect(page.locator("article")).to_contain_text("participant-agent-protocol-v2")
    expect(page.locator("article")).to_contain_text("global_wallclock_seconds")
    expect(page.locator("article")).to_contain_text("request_id,reason")
    page.click("[data-testid=lang-toggle]")
    expect(page.locator("article")).to_contain_text("challenge-score-v3")
    page.click("[data-testid=lang-toggle]")
    shot(page, "04-docs-v3")
    page.goto(base + "/rules")
    expect(page.locator("article")).to_contain_text("flexible_shortfall")
    shot(page, "05-rules-v3")
    page.goto(base + "/")
    expect(page.locator("#evaluation")).to_contain_text("request_reward")
    page.locator("#evaluation").scroll_into_view_if_needed()
    page.wait_for_timeout(500)
    shot(page, "06-home-evaluation-v3", full=False)


def test_admin_scenarios_v3(page: Page, site):
    base = site["base"]
    _login(page, base, "admin@e2e.org")
    page.goto(base + "/admin/scenarios")
    table = page.locator("[data-testid=admin-scenarios]")
    expect(table).to_be_visible(timeout=15000)
    expect(table).to_contain_text("eval-a")
    expect(table).to_contain_text(re.compile("forecasts public|预报公开"))
    assert page.locator("input[type=file]").count() == 0, "the old scenario upload form must be gone"
    page.fill("[data-testid=wallclock-dev-fortnight]", "2400")
    page.click("[data-testid=save-dev-fortnight]")
    expect(page.locator("[data-testid=flash]")).to_be_visible(timeout=10000)
    page.reload()
    expect(page.locator("[data-testid=wallclock-dev-fortnight]")).to_have_value("2400", timeout=15000)
    rows = site["hs"].sql("select global_wallclock_seconds from public.scenarios where slug = 'dev-fortnight'")
    assert rows[0][0] == 2400
    page.fill("[data-testid=wallclock-dev-fortnight]", "1800")
    page.click("[data-testid=save-dev-fortnight]")
    expect(page.locator("[data-testid=flash]")).to_be_visible(timeout=10000)
    shot(page, "07-admin-scenarios-v3")
    page.click("[data-testid=nav-logout]")
    expect(page.locator("[data-testid=nav-register]")).to_be_visible(timeout=10000)

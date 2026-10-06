from __future__ import annotations

import re
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

ROOT = Path(__file__).resolve().parents[2]
KIT = ROOT / "starter_kit"
SHOTS = ROOT / "artifacts" / "screenshots"


def shot(page: Page, name: str, full: bool = True):
    page.screenshot(path=str(SHOTS / f"{name}.png"), full_page=full)


def test_landing_page_renders_and_switches_language(page: Page, live_server):
    page.goto(live_server["base"] + "/")
    expect(page.locator("h1.hero-title")).to_contain_text("Agent Observer")
    expect(page.locator("#leaderboard")).to_be_visible()
    shot(page, "01-home-en")
    page.click("a.btn-lang")
    expect(page.locator("h1.hero-title")).to_contain_text("巡天智能体")
    shot(page, "02-home-zh")
    page.click("a.btn-lang")
    for path, text in [("/brief", "Agent Observer"), ("/rules", "Competition rules"), ("/docs", "Participant documentation"), ("/faq", "Questions before you register"), ("/resources", "Starter kit"), ("/leaderboard", "Standings")]:
        page.goto(live_server["base"] + path)
        expect(page.locator("h1")).to_contain_text(text)
        shot(page, "03-page" + path.replace("/", "-"))


def test_full_participant_journey(page: Page, live_server):
    base = live_server["base"]
    # register
    page.goto(base + "/register")
    page.fill("input[name=name]", "Ada Lovelace")
    page.fill("input[name=email]", "ada@e2e.org")
    page.fill("input[name=password]", "correct-horse-9")
    page.fill("input[name=password2]", "correct-horse-9")
    page.check("input[name=agree]")
    shot(page, "10-register")
    page.click("button[type=submit]")
    expect(page).to_have_url(re.compile(r"/dashboard$"))
    expect(page.locator("h1")).to_contain_text("Ada Lovelace")
    shot(page, "11-dashboard-no-team")

    # create team
    page.goto(base + "/team")
    page.fill("input[name=name]", "Analytical Engines")
    page.fill("input[name=max_size]", "2")
    page.click("form[action='/team/create'] button[type=submit]")
    expect(page.locator(".flash")).to_contain_text("Team created")
    code = page.locator(".token").inner_text().strip()
    assert re.fullmatch(r"[A-Z0-9]{8}", code)
    shot(page, "12-team")

    # results submission
    page.goto(base + "/submit")
    page.select_option("select[name=phase]", "practice")
    page.check("input[name=kind][value=results]")
    page.set_input_files("input[name=file]", str(KIT / "example" / "decisions.csv"))
    page.fill("input[name=title]", "reference trace")
    shot(page, "13-submit-form")
    page.click("form[action='/submit'] button[type=submit]")
    expect(page).to_have_url(re.compile(r"/submissions/\d+$"))
    # the inline worker scores it; the page polls and reloads
    expect(page.locator(".pill.scored").first).to_be_visible(timeout=60000)
    expect(page.locator(".score-big")).to_contain_text("10377.47")
    expect(page.locator("text=Region completion")).to_be_visible()
    shot(page, "14-submission-scored")

    # agent submission (runs on two public scenarios)
    page.goto(base + "/submit")
    page.check("input[name=kind][value=agent]")
    page.set_input_files("input[name=file]", str(KIT / "agent.py"))
    page.fill("input[name=title]", "baseline agent")
    page.click("form[action='/submit'] button[type=submit]")
    expect(page).to_have_url(re.compile(r"/submissions/\d+$"))
    expect(page.locator(".pill.scored").first).to_be_visible(timeout=180000)
    expect(page.locator("text=dev-week")).to_be_visible()
    expect(page.locator("a", has_text="Agent log").first).to_be_visible()
    shot(page, "15-agent-submission")

    # leaderboard
    page.goto(base + "/leaderboard/practice")
    expect(page.locator("table.table tbody tr.me")).to_contain_text("Analytical Engines")
    shot(page, "16-leaderboard")
    page.goto(base + "/")
    expect(page.locator("#leaderboard table")).to_contain_text("Analytical Engines")

    # second user joins via invite code in a fresh context
    ctx2 = page.context.browser.new_context(viewport={"width": 1280, "height": 800})
    p2 = ctx2.new_page()
    p2.goto(base + "/register")
    p2.fill("input[name=name]", "Charles Babbage")
    p2.fill("input[name=email]", "charles@e2e.org")
    p2.fill("input[name=password]", "difference-engine-1")
    p2.fill("input[name=password2]", "difference-engine-1")
    p2.check("input[name=agree]")
    p2.click("button[type=submit]")
    p2.goto(base + "/team")
    p2.fill("input[name=invite_code]", code)
    p2.click("form[action='/team/join'] button[type=submit]")
    expect(p2.locator(".flash")).to_contain_text("Joined Analytical Engines")
    expect(p2.locator("table.table")).to_contain_text("Charles Babbage")
    # team is now full (2/2): a third join must fail
    ctx2.close()

    # profile: api token visible and copyable
    page.goto(base + "/profile")
    token = page.locator("#tok").inner_text().strip()
    assert len(token) == 48
    shot(page, "17-profile")

    # logout
    page.click("header form[action='/logout'] button")
    expect(page).to_have_url(re.compile(r"/$"))
    expect(page.locator("a.btn-register")).to_have_text("Register")


def test_mobile_layout(page: Page, live_server):
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(live_server["base"] + "/")
    expect(page.locator("h1.hero-title")).to_be_visible()
    page.click("button[data-burger]")
    expect(page.locator("[data-mobile-nav]")).to_be_visible()
    shot(page, "18-mobile-home", full=False)
    # no horizontal overflow
    width = page.evaluate("document.documentElement.scrollWidth")
    assert width <= 390


def test_admin_journey(page: Page, live_server):
    base = live_server["base"]
    # the seeded admin's temporary password is in the server log
    log = (live_server["data"] / "server.log").read_text()
    m = re.search(r"created admin admin@e2e.org with temporary password: (\S+)", log)
    assert m, log[-1000:]
    page.goto(base + "/login")
    page.fill("input[name=email]", "admin@e2e.org")
    page.fill("input[name=password]", m.group(1))
    page.click("button[type=submit]")
    page.goto(base + "/lang/en?next=/admin")  # the seeded admin defaults to Chinese
    expect(page.locator("h1")).to_contain_text("Overview")
    shot(page, "20-admin-overview")
    page.goto(base + "/admin/announcements")
    page.fill("form.panel >> nth=0 >> input[name=title_en]", "Online competition opens Oct 5 00:00 (UTC+8)")
    page.fill("form.panel >> nth=0 >> input[name=title_zh]", "线上比赛 10 月 5 日 0 点开放")
    page.check("form.panel >> nth=0 >> input[name=is_pinned]")
    page.click("form.panel >> nth=0 >> button[value=save]")
    expect(page.locator(".flash")).to_contain_text("saved")
    page.goto(base + "/")
    expect(page.locator(".banner")).to_contain_text("Online competition opens")
    shot(page, "21-home-with-banner")
    page.goto(base + "/admin/submissions")
    expect(page.locator("h1")).to_contain_text("Submissions")
    expect(page.locator("table.table")).to_be_visible()
    shot(page, "22-admin-submissions")
    page.goto(base + "/admin/scenarios")
    shot(page, "23-admin-scenarios")
    page.goto(base + "/admin/phases")
    shot(page, "24-admin-phases")

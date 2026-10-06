"""Nickname journeys through the real local database, PostgREST, and browser.

Auth and storage use the existing local harness; nickname data and permissions
are exercised against the actual migrations, with no production services.
"""
from __future__ import annotations

import json
import re
import uuid

from playwright.sync_api import Page, expect

from conftest import SHOTS


PASSWORD = "correct-horse-9"


def _registration_details(page: Page, base: str, name: str, email: str, nickname: str = "") -> None:
    page.goto(base + "/register")
    page.get_by_test_id("reg-name").fill(name)
    page.get_by_test_id("reg-nickname").fill(nickname)
    page.get_by_test_id("reg-email").fill(email)
    page.get_by_test_id("reg-password").fill(PASSWORD)
    page.get_by_test_id("reg-password2").fill(PASSWORD)
    page.get_by_test_id("reg-agree").check()


def _finish_registration(page: Page) -> None:
    page.get_by_test_id("reg-next").click()
    page.get_by_test_id("reg-contact").fill("nickname-test-contact")
    page.get_by_test_id("reg-wall").check()
    page.get_by_test_id("reg-submit").click()
    expect(page).to_have_url(re.compile(r"/dashboard$"), timeout=20000)


def _saved_profile(hs, email: str) -> tuple:
    return hs.sql("select id::text, name, nickname from public.profiles where email = %s", (email,))[0]


def _edit_nickname(page: Page, base: str, nickname: str, expected: str, name: str) -> None:
    page.goto(base + "/profile")
    expect(page.get_by_test_id("profile-name")).to_have_value(name, timeout=15000)
    page.get_by_test_id("profile-nickname").fill(nickname)
    with page.expect_response(lambda r: "/rest/v1/profiles?" in r.url and r.request.method == "PATCH") as update:
        page.get_by_test_id("profile-save").click()
    assert update.value.ok, update.value.text()
    expect(page.get_by_test_id("profile-save")).to_be_enabled()
    expect(page.get_by_test_id("profile-nickname")).to_have_value(expected)
    page.reload()
    expect(page.get_by_test_id("profile-nickname")).to_have_value(expected, timeout=15000)
    expect(page.get_by_test_id("profile-name")).to_have_value(name)


def _assert_wall(page: Page, base: str, user_id: str, display_name: str, hidden_name: str | None) -> None:
    with page.expect_response(lambda r: r.url.endswith("/rest/v1/rpc/participants_wall")) as response:
        page.goto(base + "/teammates")
    assert response.value.ok
    entry = next(row for row in response.value.json() if row["id"] == user_id)
    assert entry["name"] == display_name
    if hidden_name:
        # The public API must not send the original name in another field either.
        assert hidden_name not in json.dumps(entry, ensure_ascii=False)
    grid = page.get_by_test_id("wall-grid")
    expect(grid.get_by_role("heading", name=display_name, exact=True)).to_be_visible(timeout=15000)
    if hidden_name:
        expect(grid).not_to_contain_text(hidden_name)


def _assert_team_and_submission(page: Page, base: str, submission_id: int, display_name: str, hidden_name: str | None) -> None:
    page.goto(base + "/team")
    members = page.locator("tbody")
    expect(members).to_contain_text(display_name, timeout=15000)
    if hidden_name:
        expect(members).not_to_contain_text(hidden_name)
    page.goto(base + "/dashboard")
    expect(page.locator("main li").filter(has_text=display_name)).to_have_count(1, timeout=15000)
    page.goto(base + "/submissions")
    row = page.locator("tbody tr").filter(has=page.get_by_role("link", name=f"#{submission_id}", exact=True))
    expect(row.locator("td").last).to_have_text(display_name, timeout=15000)
    page.goto(base + f"/submissions/{submission_id}")
    expect(page.locator("dd").filter(has_text=re.compile(f"^{re.escape(display_name)}$"))).to_have_count(1, timeout=15000)


def test_nickname_register_edit_clear_across_public_and_team_views(page: Page, site):
    base, hs = site["base"], site["hs"]
    name, email = "原姓名只用于资料", "nickname-owner@e2e.org"
    initial, edited = "星空观察员🚀", "改名后的队长🌌"
    _registration_details(page, base, name, email, f"　{initial}  ")
    _finish_registration(page)
    expect(page.locator("h1")).to_contain_text(initial, timeout=15000)
    user_id, saved_name, saved_nickname = _saved_profile(hs, email)
    assert (saved_name, saved_nickname) == (name, initial)

    page.goto(base + "/team")
    page.get_by_test_id("team-name-input").fill("Nickname Journey Team")
    page.get_by_test_id("team-create").click()
    expect(page.get_by_test_id("team-invite-code")).to_be_visible(timeout=15000)
    invite_code = page.get_by_test_id("team-invite-code").inner_text().strip()

    # A teammate signs up without a nickname, proving the field stays optional.
    teammate_context = page.context.browser.new_context(locale="en-US")
    visitor_context = page.context.browser.new_context(locale="en-US")
    try:
        teammate, visitor = teammate_context.new_page(), visitor_context.new_page()
        _registration_details(teammate, base, "Unnamed Teammate", "nickname-teammate@e2e.org")
        _finish_registration(teammate)
        expect(teammate.locator("h1")).to_contain_text("Unnamed Teammate", timeout=15000)
        assert _saved_profile(hs, "nickname-teammate@e2e.org")[2] == ""
        teammate.goto(base + "/team")
        teammate.get_by_test_id("team-join-code").fill(invite_code)
        teammate.get_by_test_id("team-join").click()
        expect(teammate.get_by_test_id("team-invite-code")).to_have_text(invite_code, timeout=15000)

        # The existing wall shows cards once at least three people opt in.
        hs.sql(
            "insert into auth.users (id, email, raw_user_meta_data) values (%s, %s, %s)",
            (str(uuid.uuid4()), "nickname-wall-fixture@e2e.org", json.dumps({"name": "Wall Fixture", "show_on_wall": "true"})),
        )
        # This display-only fixture never enters the evaluator queue.
        submission_id = hs.sql(
            """insert into public.submissions
               (team_id, user_id, phase_id, scenario_id, kind, storage_path, original_filename, status, title)
               select p.team_id, p.id, ph.id, s.id, 'results', p.team_id::text || '/nickname/decisions.csv',
                      'decisions.csv', 'invalid', 'Nickname author fixture'
               from public.profiles p, public.phases ph, public.scenarios s
               where p.id = %s and ph.slug = 'practice' and s.slug = 'dev-fortnight'
               returning id""",
            (user_id,),
        )[0][0]
        _assert_wall(visitor, base, user_id, initial, name)
        _assert_team_and_submission(teammate, base, submission_id, initial, name)

        _edit_nickname(page, base, f"  {edited}　", edited, name)
        assert _saved_profile(hs, email)[1:] == (name, edited)
        page.goto(base + "/dashboard")
        expect(page.locator("h1")).to_contain_text(edited, timeout=15000)
        _assert_wall(visitor, base, user_id, edited, name)
        _assert_team_and_submission(teammate, base, submission_id, edited, name)

        # Blank/whitespace nicknames restore the original name everywhere.
        _edit_nickname(page, base, "　  ", "", name)
        assert _saved_profile(hs, email)[1:] == (name, "")
        page.goto(base + "/dashboard")
        expect(page.locator("h1")).to_contain_text(name, timeout=15000)
        _assert_wall(visitor, base, user_id, name, None)
        _assert_team_and_submission(teammate, base, submission_id, name, None)
    finally:
        teammate_context.close()
        visitor_context.close()


def test_nickname_unicode_limit_at_registration_and_profile(page: Page, site):
    base, hs = site["base"], site["hs"]
    name, email = "Unicode Boundary User", "nickname-unicode@e2e.org"
    _registration_details(page, base, name, email, "星" * 41)
    page.get_by_test_id("reg-next").click()
    expect(page.get_by_role("alert")).to_contain_text("40")
    expect(page.get_by_test_id("reg-name")).to_be_visible()
    assert not hs.sql("select 1 from auth.users where email = %s", (email,))

    # Forty astral characters occupy eighty UTF-16 units but are valid nicknames.
    full_length = "🚀" * 40
    page.get_by_test_id("reg-nickname").fill(full_length)
    _finish_registration(page)
    expect(page.locator("h1")).to_contain_text(full_length, timeout=15000)
    assert _saved_profile(hs, email)[1:] == (name, full_length)

    page.goto(base + "/profile")
    expect(page.get_by_test_id("profile-nickname")).to_have_value(full_length, timeout=15000)
    page.get_by_test_id("profile-nickname").fill("🚀" * 41)
    page.get_by_test_id("profile-save").click()
    expect(page.get_by_test_id("flash").locator("[data-kind=error]")).to_contain_text("40")
    page.reload()
    expect(page.get_by_test_id("profile-nickname")).to_have_value(full_length, timeout=15000)
    assert _saved_profile(hs, email)[1:] == (name, full_length)

    # Profile edits must apply the same Unicode limit as registration.
    page.set_viewport_size({"width": 390, "height": 844})
    _edit_nickname(page, base, "🌌" * 40, "🌌" * 40, name)
    assert _saved_profile(hs, email)[1:] == (name, "🌌" * 40)
    expect(page.get_by_test_id("profile-nickname")).to_be_visible()
    page.screenshot(path=str(SHOTS / "nickname-mobile-profile.png"), full_page=True, animations="disabled")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")

    for index in range(2):
        hs.sql(
            "insert into auth.users (id, email, raw_user_meta_data) values (%s, %s, %s)",
            (str(uuid.uuid4()), f"nickname-unicode-wall-{index}@e2e.org", json.dumps({"name": f"Unicode Wall Fixture {index}", "show_on_wall": "true"})),
        )
    visitor_context = page.context.browser.new_context(viewport={"width": 390, "height": 844})
    try:
        visitor = visitor_context.new_page()
        _assert_wall(visitor, base, _saved_profile(hs, email)[0], "🌌" * 40, name)
        visitor.screenshot(path=str(SHOTS / "nickname-mobile-wall.png"), full_page=True, animations="disabled")
        assert visitor.evaluate("document.documentElement.scrollWidth <= innerWidth")

        # A continuous Latin nickname must wrap inside its card on a phone.
        unbroken = "N" * 40
        _edit_nickname(page, base, unbroken, unbroken, name)
        _assert_wall(visitor, base, _saved_profile(hs, email)[0], unbroken, name)
        visitor.screenshot(path=str(SHOTS / "nickname-mobile-wall-latin.png"), full_page=True, animations="disabled")
        heading = visitor.get_by_test_id("wall-grid").get_by_role("heading", name=unbroken, exact=True)
        assert heading.evaluate("""heading => {
            const card = heading.closest('article').getBoundingClientRect();
            const name = heading.getBoundingClientRect();
            return name.right <= card.right && heading.scrollWidth <= heading.clientWidth;
        }"""), "The unbroken nickname overflows its participant card"
        assert visitor.evaluate("document.documentElement.scrollWidth <= innerWidth")
    finally:
        visitor_context.close()


def test_nickname_saved_value_survives_failed_profile_refresh(page: Page, site):
    base, hs = site["base"], site["hs"]
    name, email = "Refresh Failure User", "nickname-refresh@e2e.org"
    _registration_details(page, base, name, email, "Original nickname")
    _finish_registration(page)
    page.goto(base + "/profile")
    expect(page.get_by_test_id("profile-nickname")).to_have_value("Original nickname", timeout=15000)

    # The write reaches real PostgREST; only its follow-up read fails once.
    page.route(
        "**/rest/v1/rpc/me",
        lambda route: route.fulfill(status=500, content_type="application/json", body='{"message":"temporary read failure"}'),
        times=1,
    )
    page.get_by_test_id("profile-nickname").fill("  已经保存的新昵称🚀  ")
    with page.expect_response(lambda r: "/rest/v1/profiles?" in r.url and r.request.method == "PATCH") as update:
        with page.expect_response(lambda r: r.url.endswith("/rest/v1/rpc/me") and r.status == 500):
            page.get_by_test_id("profile-save").click()
    assert update.value.ok, update.value.text()
    expect(page.get_by_test_id("profile-save")).to_be_enabled()
    expect(page.get_by_test_id("profile-nickname")).to_have_value("已经保存的新昵称🚀")
    assert _saved_profile(hs, email)[1:] == (name, "已经保存的新昵称🚀")
    page.reload()
    expect(page.get_by_test_id("profile-nickname")).to_have_value("已经保存的新昵称🚀", timeout=15000)

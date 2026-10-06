"""Card boards in the browser: Overall + one tab per card labelled with the scenario name, on the
leaderboard page and the home section; the hidden final's cards appear only once published; the
Playground card board keeps the stage-separation rules."""
import os
import re
import secrets
import uuid

import pytest
from playwright.sync_api import sync_playwright, expect

from test_project_http import edge_stack  # noqa: F401
from test_project_portal_browser import portal_site  # noqa: F401
from test_project_database import identity, query
from test_card_board import batch, card

pytestmark = pytest.mark.skipif(not all(os.environ.get(k) for k in ('OBSERVER_DENO_BIN', 'SAC_POSTGREST_BIN', 'SAC_NODE_BIN')),
                                reason='Local browser toolchain required')


def card_phase(uri, slug, layout, *, names, counts_for_final=False):
    phase = uuid.uuid4()
    query(uri, "insert into public.phases(id,slug,name_en,name_zh,counts_for_final) values(%s,%s,%s,%s,%s)",
          (phase, slug, 'Board ' + slug, 'Board ' + slug, counts_for_final))
    query(uri, """insert into public.observer_phase_settings(phase_id,projects_enabled,local_sessions_enabled,daily_batches,
                  model_token_limit,model_call_limit,board_layout) values(%s,true,true,5,1000,10,%s)""", (phase, layout))
    cards = [card(uri, phase, f'{slug}-{i}', name) for i, name in enumerate(names)]
    return phase, cards


def team_named(uri, name):
    user, team = identity(uri)
    query(uri, 'update public.teams set name=%s where id=%s', (name, team))
    return user


def texts(locator):
    return [t.strip() for t in locator.all_inner_texts()]


def test_formal_card_board_overall_and_card_tabs(portal_site, edge_stack):
    uri = edge_stack['harness'].db_uri; tag = secrets.token_hex(3)
    slug = 'cards-' + tag
    phase, (a, b) = card_phase(uri, slug, 'cards_overall', names=['Card Alpha ' + tag, 'Card Beta ' + tag])
    first, second = team_named(uri, 'Steady ' + tag), team_named(uri, 'Spiky ' + tag)
    batch(uri, phase, first, {a: 40, b: 40})     # overall 40
    batch(uri, phase, second, {a: 10, b: 60})    # overall 35, best on Beta
    query(uri, "update private.observer_site_mode set mode='competition',phase_id=%s", (phase,))
    # Formal card names are listed once the phase has started (public.observer_scenario_listed).
    query(uri, "update public.phases set counts_for_final=(id=%s),starts_at=case when id=%s then now()-interval '1 hour' else starts_at end",
          (phase, phase))
    final, (e, f) = card_phase(uri, 'final-' + tag, 'cards_overall', names=['Card Echo ' + tag, 'Card Fox ' + tag])
    batch(uri, final, first, {e: 7, f: 9})
    # Batches are created while the phases do not count (CSV-free local batches); then they become formal.
    query(uri, "update public.phases set leaderboard_mode='hidden',counts_for_final=true where id=%s", (final,))
    query(uri, 'update public.observer_phase_settings set sealed=true where phase_id=%s', (final,))
    errors = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel=os.environ.get('OBSERVER_BROWSER_CHANNEL'))
        page = browser.new_page(viewport={'width': 1365, 'height': 950})
        page.on('pageerror', lambda err: errors.append(str(err)))
        page.goto(portal_site + '/leaderboard/' + slug + '?lang=en')
        tabs = page.get_by_test_id('board-cards')
        expect(tabs).to_be_visible(timeout=15000)
        assert texts(tabs.locator('button')) == ['OVERALL', 'Card Alpha ' + tag, 'Card Beta ' + tag]
        expect(page.get_by_test_id('board-card-overall')).to_have_attribute('aria-pressed', 'true')
        rows = page.get_by_test_id('lb-row')
        expect(rows).to_have_count(2)
        expect(rows.nth(0)).to_contain_text('Steady ' + tag)
        expect(page.get_by_test_id(f'card-col-{slug}-1')).to_have_text('Card Beta ' + tag)
        expect(rows.nth(1).locator('td').nth(2)).to_contain_text('35')
        page.get_by_test_id(f'board-card-{slug}-1').click()
        expect(page).to_have_url(re.compile(r'scenario=' + slug + '-1'))
        expect(page.get_by_test_id(f'board-card-{slug}-1')).to_have_attribute('aria-pressed', 'true')
        expect(rows.nth(0)).to_contain_text('Spiky ' + tag)
        expect(rows.nth(0).locator('td').nth(2)).to_contain_text('60')
        rows.nth(0).click()
        dialog = page.get_by_test_id('team-detail')
        expect(dialog).to_contain_text('Board: Card Beta ' + tag)
        expect(dialog).to_contain_text('Best scores')
        page.keyboard.press('Escape')
        # Home section: the same tabs, overall first.
        page.goto(portal_site + '/?lang=en')
        home = page.locator('#board')
        expect(home.get_by_test_id('board-card-overall')).to_be_visible(timeout=15000)
        expect(home.get_by_test_id('lb-row').first).to_contain_text('Steady ' + tag)
        home.get_by_test_id(f'board-card-{slug}-0').click()
        expect(home.get_by_test_id('lb-row').first).to_contain_text('Steady ' + tag)
        expect(home.get_by_test_id('lb-row').nth(1)).to_contain_text('10')
        # The sealed final: no tab, no names before publication.
        page.goto(portal_site + '/leaderboard?lang=en')
        expect(page.locator('.tabs')).to_be_visible(timeout=15000)
        assert 'Card Echo' not in page.content() and 'final-' + tag not in page.content()
        query(uri, "update public.phases set leaderboard_mode='published' where id=%s", (final,))
        page.goto(portal_site + '/leaderboard/final-' + tag + '?lang=en')
        expect(page.get_by_test_id('board-cards')).to_be_visible(timeout=15000)
        assert texts(page.get_by_test_id('board-cards').locator('button')) == ['OVERALL', 'Card Echo ' + tag, 'Card Fox ' + tag]
        expect(page.get_by_test_id('lb-row').first.locator('td').nth(2)).to_contain_text('8')
        browser.close()
    query(uri, "update public.phases set is_active=false where id in (%s,%s)", (phase, final))
    query(uri, "update private.observer_site_mode set mode='practice',phase_id=null")
    assert not errors, errors


def test_playground_card_board_has_card_tabs_only_and_keeps_practice_wording(portal_site, edge_stack):
    uri = edge_stack['harness'].db_uri; tag = secrets.token_hex(3)
    phase, (alpha, beta) = card_phase(uri, 'practice-projects', 'cards', names=['Alpha ' + tag, 'Beta ' + tag])
    query(uri, "update private.observer_site_mode set mode='practice',phase_id=null")
    user = team_named(uri, 'Practice ' + tag)
    batch(uri, phase, user, {alpha: 12, beta: 3})
    errors, problems = [], []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel=os.environ.get('OBSERVER_BROWSER_CHANNEL'))
        page = browser.new_page(viewport={'width': 1365, 'height': 950})
        page.on('pageerror', lambda err: errors.append(str(err)))
        for language in ('en', 'zh'):
            page.goto(portal_site + '/?lang=' + language)
            home = page.locator('#board')
            expect(home.get_by_test_id('board-cards')).to_be_visible(timeout=15000)
            assert texts(home.get_by_test_id('board-cards').locator('button')) == ['Alpha ' + tag, 'Beta ' + tag]
            expect(home.get_by_test_id('board-card-overall')).to_have_count(0)
            expect(home.get_by_test_id('lb-row').first).to_contain_text('12')
            page.goto(portal_site + '/leaderboard/practice-projects?lang=' + language)
            expect(page.get_by_test_id('board-card-practice-projects-1')).to_be_visible(timeout=15000)
            page.get_by_test_id('board-card-practice-projects-1').click()
            expect(page.get_by_test_id('lb-row').first.locator('td').nth(2)).to_contain_text('3')
            text = page.locator('main').inner_text()
            match = re.search(r'正式赛|正式比赛|线上比赛|online competition|finals-preview|competition scenarios', text, re.I)
            if match: problems.append((language, text[max(0, match.start() - 40):match.end() + 80]))
        browser.close()
    query(uri, "update public.phases set is_active=false,slug='practice-projects-old-'||%s where id=%s", (tag, phase))
    assert not errors, errors
    assert not problems, problems

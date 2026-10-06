"""Task cards in the browser: the practice cards are readable now; the hackathon cards A-D stay locked until
the scenarios bucket releases their files (the bucket policy of migration 20260928004400, emulated here
by routing the storage API); the hidden cards E-H never appear. The rules and resources pages point at them."""
import io
import json
import os
import zipfile

import pytest
from playwright.sync_api import sync_playwright, expect

from test_project_http import edge_stack  # noqa: F401
from test_project_portal_browser import portal_site  # noqa: F401
from test_project_database import query

pytestmark = pytest.mark.skipif(not all(os.environ.get(k) for k in ('OBSERVER_DENO_BIN', 'SAC_POSTGREST_BIN', 'SAC_NODE_BIN')),
                                reason='Local browser toolchain required')

RELEASED = {
    'v4-practice-alpha/config': ['v4_scenario.json', 'v4_score_config.json'],
    'v4-practice-alpha/public': ['targets.csv'],
    'v4-practice-alpha/truth': ['v4_weather_truth.csv'],
    'v4-a/public': ['targets.csv', 'taskcard.en.md', 'taskcard.zh.md'],
}
CARD_A = {'en': '# Task card A: released for the start\n\n## At a glance\n\nReleased text.\n',
          'zh': '# 任务卡 A：开赛公开\n\n## 一览\n\n公开内容。\n'}


def storage(released, requests):
    """Route the Storage API like the bucket policy: listing shows only released files, downloads serve them."""
    def handle(route):
        url = route.request.url
        requests.append(url)
        if '/object/list/scenarios' in url:
            prefix = json.loads(route.request.post_data or '{}').get('prefix', '').strip('/')
            names = released.get(prefix, [])
            return route.fulfill(json=[{'name': n, 'id': f'{prefix}/{n}', 'metadata': {'size': 3}} for n in names])
        path = url.split('/object/scenarios/', 1)[-1].split('?')[0]
        folder, _, name = path.rpartition('/')
        if name not in released.get(folder, []):
            return route.fulfill(status=400, json={'statusCode': '404', 'error': 'not_found', 'message': 'Object not found'})
        language = name.split('.')[1] if name.startswith('taskcard.') else None
        return route.fulfill(body=CARD_A[language] if language else f'{path}\n', content_type='text/plain')
    return handle


def test_practice_cards_now_formal_cards_at_the_start_hidden_cards_never(portal_site, edge_stack):
    uri = edge_stack['harness'].db_uri
    query(uri, "update private.observer_site_mode set mode='practice'")
    errors, requests = [], []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel=os.environ.get('OBSERVER_BROWSER_CHANNEL'))
        page = browser.new_page(viewport={'width': 1365, 'height': 950}, accept_downloads=True)
        page.on('pageerror', lambda e: errors.append(str(e)))
        released = {}
        page.route('**/storage/v1/object/**', storage(released, requests))

        # Before any release: the practice pages ship with the site, A-D are locked, E-H do not exist.
        page.goto(portal_site + '/cards?lang=zh', wait_until='domcontentloaded')
        expect(page.get_by_test_id('card-tab-alpha')).to_have_attribute('aria-current', 'page')
        expect(page.locator('main h1').last).to_have_text('任务卡 α（alpha）：初见星光')
        expect(page.get_by_test_id('card-files')).to_contain_text('卡片文件尚未上线')
        for card in ('alpha', 'beta', 'a', 'b', 'c', 'd'):
            expect(page.get_by_test_id(f'card-tab-{card}')).to_be_visible()
        for card in ('e', 'f', 'g', 'h'):
            expect(page.get_by_test_id(f'card-tab-{card}')).to_have_count(0)
        page.get_by_test_id('card-tab-a').click()
        expect(page.get_by_test_id('card-locked')).to_contain_text('10 月 5 日 00:00')
        expect(page.get_by_test_id('card-zip-a')).to_have_count(0)
        page.goto(portal_site + '/cards/e?lang=en', wait_until='domcontentloaded')
        expect(page).to_have_url(portal_site + '/cards?lang=en')
        expect(page.locator('main h1').last).to_have_text('Task card α (alpha): First light')
        assert not [u for u in requests if 'v4-e' in u or 'v4-f' in u or 'v4-g' in u or 'v4-h' in u]

        # Resources: the v4 kit, the cards and (kept for the CSV warm-up) the v3 kit.
        page.goto(portal_site + '/resources?lang=en', wait_until='domcontentloaded')
        expect(page.get_by_test_id('kit-v4')).to_have_attribute('href', '/downloads/agent-observer-starter-kit-v4.zip')
        expect(page.locator('a[download][href$="agent-observer-starter-kit.zip"]')).to_be_visible()
        expect(page.get_by_test_id('resources-card-alpha')).to_contain_text('files not online yet')
        expect(page.get_by_test_id('resources-card-a')).to_contain_text('published at the start')
        expect(page.get_by_test_id('resources-card-e')).to_have_count(0)
        kit = page.request.get(portal_site + '/downloads/agent-observer-starter-kit-v4.zip')
        assert kit.ok and 'agent-observer-starter-kit-v4/local_runner.py' in zipfile.ZipFile(io.BytesIO(kit.body())).namelist()

        # The bucket releases the practice files and, at the competition start, A's public files.
        released.update(RELEASED)
        page.goto(portal_site + '/cards/alpha?lang=en', wait_until='domcontentloaded')
        with page.expect_download() as download:
            page.get_by_test_id('card-zip-alpha').click()
        assert download.value.suggested_filename == 'taskcard-alpha.zip'
        names = zipfile.ZipFile(download.value.path()).namelist()
        assert sorted(names) == ['alpha/config/v4_scenario.json', 'alpha/config/v4_score_config.json',
                                 'alpha/public/targets.csv', 'alpha/truth/v4_weather_truth.csv']
        page.get_by_test_id('card-tab-a').click()
        expect(page.locator('main h1').last).to_have_text('Task card A: released for the start')
        expect(page.get_by_test_id('card-zip-a')).to_be_visible()
        page.goto(portal_site + '/cards/a?lang=zh', wait_until='domcontentloaded')
        expect(page.locator('main h1').last).to_have_text('任务卡 A：开赛公开')
        page.goto(portal_site + '/resources?lang=zh', wait_until='domcontentloaded')
        expect(page.get_by_test_id('card-zip-alpha')).to_be_visible()
        expect(page.get_by_test_id('card-zip-a')).to_be_visible()
        expect(page.get_by_test_id('card-zip-b')).to_have_count(0)

        # The rules describe the v4 cards.
        page.goto(portal_site + '/rules?lang=zh', wait_until='domcontentloaded')
        expect(page.locator('main')).to_contain_text('黑客松任务卡 A、B、C、D')
        expect(page.locator('main')).to_contain_text('DARK 1.20、BRIGHT 1.12、BACKUP 1.06')
        expect(page.locator('main')).not_to_contain_text('三个正式场景')
        page.goto(portal_site + '/rules?lang=en', wait_until='domcontentloaded')
        expect(page.locator('main')).to_contain_text('Hidden cards E, F, G, H')
        browser.close()
    assert not errors, errors

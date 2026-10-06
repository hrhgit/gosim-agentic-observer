"""Newcomer journey: next public stage, dashboard quest, one-click solo team, team directory and page layout."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import hashlib
import os
import re
import uuid

from playwright.sync_api import sync_playwright, expect
from psycopg.types.json import Jsonb
import pytest

from test_project_http import edge_stack  # noqa: F401
from test_project_portal_browser import portal_site  # noqa: F401
from test_project_database import query, identity

pytestmark = pytest.mark.skipif(not all(os.environ.get(key) for key in
    ('OBSERVER_DENO_BIN', 'SAC_POSTGREST_BIN', 'SAC_NODE_BIN')), reason='Local browser toolchain required')

PASSWORD = 'local-ux-browser-password-31'
SHOTS = os.environ.get('OBSERVER_UX_SHOTS')
DESKTOP = {'width': 1365, 'height': 950}
MOBILE = {'width': 390, 'height': 844}


def shot(page, name):
    """Optional review screenshots; set OBSERVER_UX_SHOTS to a directory to keep them."""
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(SHOTS) / f'{name}.png'), full_page=False)


def person(uri, name, nickname=''):
    user = uuid.uuid4()
    query(uri, 'insert into auth.users(id,email,raw_user_meta_data) values(%s,%s,%s)',
          (user, f'{user}@example.test', Jsonb({'name': name, 'nickname': nickname,
                                                'password_hash': hashlib.sha256(PASSWORD.encode()).hexdigest()})))
    return user


def login(page, base, user, path='/dashboard', lang='zh'):
    page.goto(f'{base}/login?next={path}&lang={lang}')
    page.get_by_test_id('login-email').fill(f'{user}@example.test')
    page.get_by_test_id('login-password').fill(PASSWORD)
    page.get_by_test_id('login-submit').click()
    expect(page.get_by_test_id('team-notifications')).to_be_visible(timeout=15000)


@pytest.fixture(scope='module')
def schedule(edge_stack):
    """Playground now; the public formal competition in nine days; a team-only test phase sooner."""
    uri = edge_stack['harness'].db_uri
    practice, online, beta = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    starts = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=9, hours=6)
    query(uri, "insert into public.phases(id,slug,name_en,name_zh) values(%s,'practice','Practice','练习赛')", (practice,))
    query(uri, "insert into public.phases(id,slug,name_en,name_zh,starts_at,sort_order) values(%s,'online','Online Competition','线上比赛',%s,10)",
          (online, starts))
    query(uri, "insert into public.phases(id,slug,name_en,name_zh,starts_at,sort_order) values(%s,'beta-acceptance','Beta acceptance','内测验收',%s,5)",
          (beta, starts - timedelta(days=5)))
    tester, access_team = identity(uri)
    query(uri, 'update auth.users set raw_user_meta_data=raw_user_meta_data || %s where id=%s',
          (Jsonb({'password_hash': hashlib.sha256(PASSWORD.encode()).hexdigest()}), tester))
    query(uri, 'insert into public.observer_phase_settings(phase_id,access_team_id) values(%s,%s)', (beta, access_team))
    query(uri, "update private.observer_site_mode set mode='practice',phase_id=%s", (practice,))
    return {'uri': uri, 'practice': practice, 'online': online, 'starts': starts, 'tester': tester}


def test_visitor_sees_next_public_stage_aligned_pages_and_mobile_register_bar(portal_site, schedule):
    local = schedule['starts'].astimezone(ZoneInfo('Asia/Shanghai'))
    errors = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel=os.environ.get('OBSERVER_BROWSER_CHANNEL'))
        # The start day is always given in Beijing time, whatever the visitor's own time zone.
        desktop = browser.new_context(viewport=DESKTOP, timezone_id='America/Los_Angeles').new_page()
        desktop.on('pageerror', lambda error: errors.append(str(error)))
        desktop.goto(portal_site + '/?lang=zh')
        stage = desktop.get_by_test_id('phase-next')
        expect(stage).to_contain_text('线上比赛', timeout=15000)
        expect(stage).to_contain_text(f'{local.month}月{local.day}日开赛（北京时间）')
        expect(stage).to_contain_text('还有 9 天')
        expect(stage.locator('.phase-countdown')).to_be_visible()
        expect(stage).not_to_contain_text('内测验收')
        expect(desktop.get_by_test_id('phase-strip')).to_contain_text('练习赛')
        expect(desktop.get_by_test_id('phase-pill')).to_have_attribute('title', re.compile('下一阶段：线上比赛'))
        # The first-visit walkthrough shades only the sky map, never the hero title beside it.
        expect(desktop.locator('.sky-tour-shade')).to_be_visible()
        clipped = desktop.evaluate("""() => {
          const shade = document.querySelector('.sky-tour-shade').getBoundingClientRect()
          const consoleBox = document.querySelector('.sky-console').getBoundingClientRect()
          const title = document.querySelector('.hero-title').getBoundingClientRect()
          return getComputedStyle(document.querySelector('.sky-tour-shade')).overflow === 'hidden'
            && shade.left >= consoleBox.left && shade.right <= consoleBox.right && shade.left > title.right }""")
        assert clipped
        shot(desktop, 'home-next-stage.zh.desktop')
        desktop.goto(portal_site + '/?lang=en')
        expect(desktop.get_by_test_id('phase-next')).to_contain_text('Online Competition', timeout=15000)
        expect(desktop.get_by_test_id('phase-next')).to_contain_text(f"starts {local.strftime('%B')} {local.day} (Beijing time)")
        expect(desktop.get_by_test_id('phase-next')).to_contain_text('9 days to go')
        # The header already offers registration from md up: no floating button covers the board.
        assert desktop.get_by_test_id('register-float').count() == 0
        for path in ('/leaderboard', '/rules'):
            desktop.goto(portal_site + path + '?lang=zh')
            expect(desktop.locator('.page-head .poster-kicker')).to_be_visible(timeout=15000)
            offsets = desktop.evaluate("""() => {
              const kicker = document.querySelector('.page-head .poster-kicker').getBoundingClientRect().left
              const wrap = document.querySelector('main .section .wrap, main .section .wrap-narrow')
              return [kicker, wrap.getBoundingClientRect().left + parseFloat(getComputedStyle(wrap).paddingLeft)] }""")
            assert offsets[0] > 20 and abs(offsets[0] - offsets[1]) <= 1, (path, offsets)
        shot(desktop, 'leaderboard.zh.desktop')
        assert desktop.get_by_test_id('register-float').count() == 0

        mobile = browser.new_context(viewport=MOBILE, timezone_id='Asia/Shanghai').new_page()
        mobile.on('pageerror', lambda error: errors.append(str(error)))
        mobile.goto(portal_site + '/leaderboard?lang=zh')
        bar = mobile.get_by_test_id('register-float')
        expect(bar).to_be_visible(timeout=15000)
        box = bar.bounding_box()
        assert box['width'] >= MOBILE['width'] - 40 and box['y'] + box['height'] <= MOBILE['height']
        # Let the board finish loading, then jump (html scrolls smoothly) to the very end of the page.
        mobile.wait_for_load_state('networkidle')
        for _ in range(3):
            mobile.evaluate("window.scrollTo({ top: document.documentElement.scrollHeight, behavior: 'instant' })")
            mobile.wait_for_timeout(250)
        end = mobile.evaluate("""() => {
          const footer = document.querySelector('footer').getBoundingClientRect()
          const bar = document.querySelector('.register-bar').getBoundingClientRect()
          return { gap: bar.top - footer.bottom, left: document.documentElement.scrollHeight - innerHeight - scrollY } }""")
        # Scrolled to the end, the footer sits fully above the bar: nothing is left underneath it.
        assert abs(end['left']) < 2 and end['gap'] >= -1, end
        shot(mobile, 'leaderboard-register-bar.zh.mobile')
        bar.click()
        expect(mobile).to_have_url(re.compile(r'/register'), timeout=15000)
        expect(mobile.get_by_test_id('register-float')).to_have_count(0)
        expect(mobile.locator('.register-bar-spacer')).to_have_count(0)
        mobile.goto(portal_site + '/?lang=zh')
        mobile.get_by_role('button', name='菜单').click()
        expect(mobile.get_by_test_id('menu-next-phase')).to_contain_text('下一阶段：线上比赛')
        # The access team can read its restricted test phase, yet it is never announced as the next stage.
        assert query(schedule['uri'], "select slug from public.phases where slug='beta-acceptance'",
                     role='authenticated', user=schedule['tester']) == [('beta-acceptance',)]
        tester = browser.new_context(viewport=DESKTOP).new_page()
        tester.on('pageerror', lambda error: errors.append(str(error)))
        login(tester, portal_site, schedule['tester'], '/dashboard')
        tester.goto(portal_site + '/?lang=zh')
        expect(tester.get_by_test_id('phase-next')).to_contain_text('线上比赛', timeout=15000)
        expect(tester.get_by_test_id('phase-next')).not_to_contain_text('内测验收')
        browser.close()
    assert not errors, errors


def test_dashboard_quest_and_one_click_solo_team(portal_site, schedule):
    uri = schedule['uri']
    newcomer = person(uri, 'Quest Newcomer', 'Nova 单人')
    holder = person(uri, 'Name Holder')
    # Someone already owns the nickname as a team name: the solo team retries with a suffix.
    query(uri, "select public.create_team('nova 单人',3,'','')", role='authenticated', user=holder)
    mobile_user = person(uri, 'Solo Mobile')
    errors = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel=os.environ.get('OBSERVER_BROWSER_CHANNEL'))
        page = browser.new_context(viewport=DESKTOP, accept_downloads=True).new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        login(page, portal_site, newcomer)
        quest = page.get_by_test_id('dash-quest')
        expect(quest.get_by_test_id('quest-progress')).to_have_text('进度 0/4', timeout=15000)
        expect(quest.locator('[data-state=current]')).to_have_count(1)
        expect(quest.locator('[data-step=team]')).to_have_attribute('data-state', 'current')
        expect(quest.get_by_test_id('quest-next')).to_have_count(1)
        expect(quest.get_by_test_id('quest-next')).to_have_class(re.compile(r'\bprimary\b'))
        expect(quest.get_by_test_id('quest-next')).to_contain_text('下一步：组队')
        # The quest leads the page: the phase table no longer comes first, and no other primary competes.
        phases = page.locator('.panel', has_text='阶段').first
        assert quest.bounding_box()['y'] < phases.bounding_box()['y']
        assert page.locator('main .btn.primary').count() == 1
        shot(page, 'dashboard-quest-start.zh.desktop')
        page.goto(portal_site + '/compete')
        expect(page.get_by_test_id('solo-team')).to_be_visible(timeout=15000)
        page.goto(portal_site + '/team')
        expect(page.get_by_test_id('solo-callout').get_by_test_id('solo-team')).to_have_text('一键单人参赛', timeout=15000)
        shot(page, 'team-solo.zh.desktop')
        page.goto(portal_site + '/dashboard')
        page.get_by_test_id('dash-quest').get_by_test_id('solo-team').click()
        expect(page).to_have_url(portal_site + '/compete', timeout=15000)
        expect(page.get_by_test_id('flash')).to_contain_text('已创建单人队伍「Nova 单人-')
        rows = query(uri, 'select t.name,t.max_size from public.teams t join public.profiles p on p.team_id=t.id where p.id=%s', (newcomer,))
        assert len(rows) == 1 and re.fullmatch(r'Nova 单人-[a-z0-9]{4}', rows[0][0]) and rows[0][1] == 1, rows

        page.goto(portal_site + '/dashboard')
        expect(quest.get_by_test_id('quest-progress')).to_have_text('进度 1/4', timeout=15000)
        expect(quest.locator('[data-step=team]')).to_have_attribute('data-state', 'done')
        expect(quest.locator('[data-step=prepare]')).to_have_attribute('data-state', 'current')
        with page.expect_download() as download:
            quest.get_by_test_id('quest-next').click()
        assert download.value.suggested_filename == 'agent-observer-starter-kit.zip'
        expect(quest.get_by_test_id('quest-progress')).to_have_text('进度 2/4')
        expect(quest.get_by_test_id('quest-next')).to_have_attribute('href', '/compete')

        team = query(uri, 'select team_id from public.profiles where id=%s', (newcomer,))[0][0]
        submission = query(uri, """insert into public.submissions(team_id,user_id,phase_id,kind,storage_path,status,score)
          values(%s,%s,%s,'results','quest.csv','scored',1234.5) returning id""", (team, newcomer, schedule['practice']))[0][0]
        page.reload()
        expect(quest.get_by_test_id('quest-progress')).to_have_text('进度 3/4', timeout=15000)
        expect(quest.locator('[data-step=review]')).to_have_attribute('data-state', 'current')
        quest.get_by_test_id('quest-next').click()
        expect(page).to_have_url(f'{portal_site}/submissions/{submission}', timeout=15000)
        page.goto(portal_site + '/dashboard')
        done = page.locator('[data-testid=dash-quest][data-finished=true]')
        expect(done).to_contain_text('新手任务全部完成', timeout=15000)
        expect(done).to_contain_text('4/4')
        shot(page, 'dashboard-quest-done.zh.desktop')

        # On a phone, from the no-team state of Participate: the team appears and the page continues.
        phone = browser.new_context(viewport=MOBILE).new_page()
        phone.on('pageerror', lambda error: errors.append(str(error)))
        login(phone, portal_site, mobile_user, '/compete')
        assert phone.get_by_test_id('register-float').count() == 0
        phone.get_by_test_id('solo-team').click()
        expect(phone.locator('input[type=file]')).to_be_attached(timeout=15000)
        assert phone.url.startswith(portal_site + '/compete')
        assert query(uri, 'select t.name,t.max_size from public.teams t join public.profiles p on p.team_id=t.id where p.id=%s',
                     (mobile_user,)) == [('Solo Mobile', 1)]
        browser.close()
    assert not errors, errors


def test_team_directory_recruiting_filter_search_and_one_line_rows(portal_site, schedule):
    uri = schedule['uri']
    names = {'open': 'Directory Alpha', 'full': 'Directory Full', 'paused': 'Directory Paused',
             'long': 'Directory-long-' + 'x' * 45}
    for key, name in names.items():
        _, team = identity(uri)
        query(uri, 'update public.teams set name=%s,max_size=%s,is_locked=%s where id=%s',
              (name, 1 if key == 'full' else 3, key == 'paused', team))
    total = query(uri, 'select count(*) from public.teams where not is_hidden')[0][0]
    errors = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel=os.environ.get('OBSERVER_BROWSER_CHANNEL'))
        for viewport in (DESKTOP, MOBILE):
            page = browser.new_context(viewport=viewport).new_page()
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(portal_site + '/teammates?lang=zh')
            directory = page.get_by_test_id('team-directory')
            expect(directory.locator('li', has_text=names['open'])).to_be_visible(timeout=15000)
            expect(directory.locator('li', has_text=names['long'])).to_be_visible()
            expect(directory.locator('li', has_text=names['full'])).to_have_count(0)
            expect(directory.locator('li', has_text=names['paused'])).to_have_count(0)
            layout = page.evaluate("""() => [...document.querySelectorAll('[data-testid=team-directory] li.team-dir-row')].map(row => {
              const box = row.getBoundingClientRect(), action = row.querySelector('.team-dir-action').getBoundingClientRect()
              const name = row.querySelector('.team-dir-name > :first-child')
              return { text: name.textContent, height: box.height, gap: box.right - action.right, top: action.top - box.top,
                       cut: name.scrollWidth > name.clientWidth, ellipsis: getComputedStyle(name).textOverflow }
            })""")
            assert layout and all(abs(row['gap']) <= 1 and row['height'] < 70 and row['top'] < 20 for row in layout), layout
            long_row = next(row for row in layout if row['text'] == names['long'])
            assert long_row['ellipsis'] == 'ellipsis' and (long_row['cut'] or viewport is DESKTOP), long_row
            toggle = directory.get_by_test_id('team-directory-all')
            expect(directory.locator('label', has=page.get_by_test_id('team-directory-all'))).to_contain_text(f'显示全部（{total}）')
            toggle.check()
            expect(directory.locator('li', has_text=names['full'])).to_contain_text('已满员')
            expect(directory.locator('li', has_text=names['paused'])).to_contain_text('暂停招募')
            directory.get_by_test_id('team-directory-search').fill('DIRECTORY al')
            expect(directory.locator('li.team-dir-row')).to_have_count(1)
            expect(directory.locator('li.team-dir-row')).to_contain_text(names['open'])
            directory.get_by_test_id('team-directory-search').fill('no team is called this')
            empty = directory.get_by_test_id('team-directory-empty')
            expect(empty).to_contain_text('没有匹配的队伍')
            expect(empty.get_by_role('link')).to_contain_text('创建自己的队伍 / 单人参赛')
            directory.get_by_test_id('team-directory-search').fill('')
            toggle.uncheck()
            shot(page, 'teammates-directory.zh.' + ('desktop' if viewport is DESKTOP else 'mobile'))
            page.context.close()
        seeker = person(uri, 'Directory Seeker')
        page = browser.new_context(viewport=DESKTOP).new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        login(page, portal_site, seeker, '/team')
        directory = page.get_by_test_id('team-directory')
        expect(directory.locator('li', has_text=names['open']).get_by_role('button', name='申请加入')).to_be_visible(timeout=15000)
        directory.get_by_test_id('team-directory-search').fill('nothing matches here')
        expect(directory.get_by_test_id('team-directory-empty').get_by_test_id('solo-team')).to_be_visible()
        shot(page, 'team-directory-empty.zh.desktop')
        browser.close()
    assert not errors, errors


def test_log_out_clears_the_stored_session(portal_site, schedule):
    """Log out forgets the session in this browser at once, also after a reload."""
    user = person(schedule['uri'], 'Log Out Tester')
    errors = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel=os.environ.get('OBSERVER_BROWSER_CHANNEL'))
        page = browser.new_context(viewport=DESKTOP).new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        login(page, portal_site, user, lang='en')
        stored = "() => Object.keys(localStorage).filter(key => key.startsWith('sb-') && key.endsWith('-auth-token'))"
        assert page.evaluate(stored)
        page.get_by_test_id('nav-logout').click()
        expect(page.get_by_test_id('nav-logout')).to_have_count(0, timeout=10000)
        assert page.evaluate(stored) == []
        page.reload()
        expect(page.get_by_role('link', name='Register').first).to_be_visible(timeout=15000)
        expect(page.get_by_test_id('nav-logout')).to_have_count(0)
        browser.close()
    assert not errors, errors


def test_simple_header_and_three_step_start_page(portal_site, schedule):
    """The header leads with four destinations, the rest sits under More, and /start is three steps."""
    errors = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel=os.environ.get('OBSERVER_BROWSER_CHANNEL'))
        page = browser.new_context(viewport=DESKTOP).new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(portal_site + '/?lang=zh')
        header = page.locator('header')
        for label in ('上手', '规则', '排行榜'):
            expect(header.get_by_role('link', name=label, exact=True)).to_be_visible(timeout=15000)
        expect(page.get_by_test_id('primary-submit')).to_have_text('参赛')
        expect(page.get_by_test_id('primary-submit')).to_have_attribute('href', '/compete')
        # The other public pages live behind the More dropdown instead of the nav row.
        assert header.get_by_role('link', name='完整赛事说明', exact=True).count() == 0 or \
            not header.get_by_role('link', name='完整赛事说明', exact=True).first.is_visible()
        page.get_by_test_id('nav-more').hover()
        for label, path in (('完整赛事说明', '/brief'), ('文档', '/docs'), ('资源', '/resources'),
                            ('常见问题', '/faq'), ('公告', '/announcements'), ('找队友', '/teammates')):
            expect(header.get_by_role('link', name=label, exact=True)).to_have_attribute('href', path)
        header.get_by_role('link', name='常见问题', exact=True).click()
        expect(page).to_have_url(re.compile(r'/faq'), timeout=15000)
        # The hero leads with the three-step start and the leaderboard.
        page.goto(portal_site + '/?lang=zh')
        expect(page.get_by_test_id('hero-cta-start')).to_have_attribute('href', '/start')
        expect(page.get_by_test_id('hero-cta-start')).to_contain_text('3 步上手')
        expect(page.get_by_test_id('hero-cta-board')).to_have_attribute('href', '/leaderboard')
        page.get_by_test_id('hero-cta-start').click()
        expect(page).to_have_url(re.compile(r'/start'), timeout=15000)
        shot(page, 'simple-start.zh.desktop')
        expect(page.locator('h1').first).to_have_text('3 步上手')
        for step in ('start-step-1', 'start-step-2', 'start-step-3'):
            expect(page.get_by_test_id(step)).to_be_visible()
        expect(page.get_by_test_id('start-kit-download')).to_have_attribute('href', re.compile(r'agent-observer-starter-kit\.zip$'))
        expect(page.get_by_test_id('start-step-2')).to_contain_text('python3 local_runner.py')
        expect(page.get_by_test_id('start-step-3')).to_contain_text('50 次')
        expect(page.get_by_test_id('start-step-3')).to_contain_text('8 次')
        expect(page.get_by_test_id('start-go-compete')).to_have_attribute('href', '/compete')
        page.goto(portal_site + '/start?lang=en')
        expect(page.locator('h1').first).to_have_text('Get started in 3 steps')
        expect(page.get_by_test_id('start-step-3')).to_contain_text('50 per team per day')
        header_en = page.locator('header')
        for label in ('Get started', 'Rules', 'Leaderboard'):
            expect(header_en.get_by_role('link', name=label, exact=True)).to_be_visible()
        expect(page.get_by_test_id('primary-submit')).to_have_text('Participate')
        shot(page, 'simple-start.en.desktop')
        page.context.close()

        mobile = browser.new_context(viewport=MOBILE).new_page()
        mobile.on('pageerror', lambda error: errors.append(str(error)))
        mobile.goto(portal_site + '/?lang=zh')
        mobile.get_by_role('button', name='菜单').click()
        menu = mobile.get_by_test_id('mobile-menu')
        expect(menu).to_be_visible(timeout=5000)
        for label in ('上手', '规则', '排行榜', '参赛'):
            expect(menu.get_by_role('link', name=label, exact=True)).to_be_visible()
        # The More pages close the menu, below the session controls, with tap targets of at least 24 px.
        more_label = mobile.get_by_test_id('mobile-more')
        expect(more_label).to_have_text('更多')
        layout = mobile.evaluate("""() => {
          const menu = document.querySelector('[data-testid=mobile-menu]')
          const more = document.querySelector('[data-testid=mobile-more]').getBoundingClientRect()
          const auth = [...menu.querySelectorAll('a, button')].filter(el => /报名|登录/.test(el.textContent))
          const links = [...menu.querySelectorAll('a')].map(el => el.getBoundingClientRect().height)
          return { moreTop: more.top, authBottom: Math.max(...auth.map(el => el.getBoundingClientRect().bottom)),
                   minLink: Math.min(...links), width: document.documentElement.scrollWidth } }""")
        assert layout['moreTop'] >= layout['authBottom'], layout
        assert layout['minLink'] >= 24, layout
        assert layout['width'] <= MOBILE['width'] + 1, layout
        menu.get_by_role('link', name='找队友', exact=True).click()
        expect(mobile).to_have_url(re.compile(r'/teammates'), timeout=15000)
        mobile.goto(portal_site + '/start?lang=zh')
        expect(mobile.locator('h1').first).to_have_text('3 步上手')
        assert mobile.evaluate("() => document.documentElement.scrollWidth") <= MOBILE['width'] + 1
        shot(mobile, 'simple-start.zh.mobile')
        browser.close()
    assert not errors, errors

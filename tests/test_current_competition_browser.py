"""Public pages carry one unified copy that covers both the Playground and the competition."""
import hashlib
from psycopg.types.json import Jsonb
import os
import re
import uuid
import pytest
from playwright.sync_api import sync_playwright, expect, Error as PlaywrightError
from test_project_http import edge_stack  # noqa: F401
from test_project_portal_browser import portal_site  # noqa: F401
from test_project_database import query, identity
from test_scenario_instance_database import configure

def goto(page,url):
    # /submit client-redirects to /compete; a fast in-app navigation can abort the initial load.
    try: page.goto(url)
    except PlaywrightError as error:
        if 'ERR_ABORTED' not in str(error): raise

pytestmark=pytest.mark.skipif(not all(os.environ.get(k) for k in ('OBSERVER_DENO_BIN','SAC_POSTGREST_BIN','SAC_NODE_BIN')),
    reason='Local browser toolchain required')


def test_public_playground_pages_never_ask_participants_to_choose_a_stage(portal_site,edge_stack):
    uri=edge_stack['harness'].db_uri
    phase=uuid.uuid4()
    query(uri,"insert into public.phases(id,slug,name_en,name_zh) values(%s,'practice','Practice','练习赛')",(phase,))
    query(uri,"update private.observer_site_mode set mode='practice',phase_id=%s",(phase,))
    problems=[];errors=[];private_requests=[]
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel=os.environ.get('OBSERVER_BROWSER_CHANNEL'))
        page=browser.new_page(viewport={'width':1365,'height':950})
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.on('request',lambda request: private_requests.append(request.url)
                if '/rpc/champion_' in request.url or '/object/' in request.url and '/results/' in request.url else None)
        for language in ('zh','en','ja','fr'):
            for path in ('/','/start','/brief','/rules','/docs','/resources','/faq','/leaderboard','/teammates','/announcements'):
                page.goto(portal_site+path+'?lang='+language)
                page.locator('main').wait_for()
                if path=='/':
                    expect(page.get_by_test_id('sky-console')).to_have_attribute('data-replay-source','demo')
                    expect(page.locator('.sky-live-title')).to_contain_text({
                        'zh':'官方示例回放','en':'Official example replay',
                        'ja':'公式サンプルのリプレイ','fr':'Rejeu de l’exemple officiel'}[language])
                text=page.locator('main').text_content()
                # The single copy mentions both stages where relevant; retired wording must not come back.
                match=re.search(r'官方本地会话|official local-session|智能体项目页|Agent projects page',text,re.I)
                if match:problems.append((language,path,text[max(0,match.start()-40):match.end()+100]))
        # One copy, both stages: the unified answers are present in practice mode too.
        page.goto(portal_site+'/faq?lang=zh')
        expect(page.locator('main')).to_contain_text('黑客松正式比赛实现语言不限')
        expect(page.locator('main')).to_contain_text('练习赛 decisions.csv 每队每天 50 次')
        page.goto(portal_site+'/docs?lang=zh')
        expect(page.locator('main')).to_contain_text('练习赛：本地运行与 decisions.csv')
        expect(page.locator('main')).to_contain_text('确认版本并评测')
        page.goto(portal_site+'/faq?lang=en')
        expect(page.locator('main')).to_contain_text('Submit your complete project as a ZIP file and it runs in the cloud.')
        browser.close()
    assert not errors,errors
    assert not private_requests,private_requests
    assert not problems,'\n'.join(map(str,problems))


def test_admin_switch_updates_submission_resources_and_public_instructions(portal_site,edge_stack):
    uri=edge_stack['harness'].db_uri
    practice=query(uri,"select id from public.phases where slug='practice'")[0][0]
    phase,scenario=uuid.uuid4(),uuid.uuid4()
    query(uri,"insert into public.phases(id,slug,name_en,name_zh) values(%s,'online','Competition','正式比赛')",(phase,))
    query(uri,"insert into public.scenarios(id,slug,name) values(%s,%s,'Private competition scenario')",(scenario,str(scenario)))
    query(uri,'insert into public.phase_scenarios values(%s,%s)',(phase,scenario))
    admin,_=identity(uri);password='local-stage-switch-password'
    query(uri,'update public.profiles set is_admin=true where id=%s',(admin,))
    query(uri,'update auth.users set raw_user_meta_data=raw_user_meta_data || %s where id=%s',
        (Jsonb({'password_hash':hashlib.sha256(password.encode()).hexdigest()}),admin))
    errors=[];problems=[]
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel=os.environ.get('OBSERVER_BROWSER_CHANNEL'))
        page=browser.new_page(viewport={'width':1365,'height':950})
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.route('**/functions/v1/observer-portal',lambda route:route.fulfill(
            response=route.fetch(url=edge_stack['urls']['observer-portal'])))
        page.goto(portal_site+'/login?next=/admin/settings&lang=en')
        page.get_by_test_id('login-email').fill(f'{admin}@example.test')
        page.get_by_test_id('login-password').fill(password)
        page.get_by_test_id('login-submit').click()
        switch=page.get_by_test_id('competition-mode-switch')
        expect(switch).to_have_text('Switch to competition',timeout=15000)
        switch.click()
        expect(page.get_by_role('alert')).to_contain_text('not ready')
        assert query(uri,'select mode from private.observer_site_mode')[0][0]=='practice'
        query(uri,'insert into public.observer_phase_settings(phase_id,projects_enabled,local_sessions_enabled) values(%s,true,true)',(phase,))
        configure({'uri':uri,'phase':phase,'scenario':scenario})
        # A successful switch reloads the page; wait for that reload, or it aborts the next goto.
        with page.expect_navigation(timeout=15000):
            switch.click()
        expect(page.get_by_test_id('competition-mode-switch')).to_have_text('Switch to Practice',timeout=15000)
        goto(page,portal_site+'/submit?lang=en')
        expect(page.get_by_test_id('project-title')).to_be_visible(timeout=15000)
        assert page.locator('a[href="/projects"],a[href="/submit"]').count()==0
        expect(page.get_by_test_id('primary-submit')).to_have_attribute('href','/compete')
        expect(page.get_by_role('button',name='Start local CSV session')).to_have_count(0)
        page.goto(portal_site+'/resources?lang=en')
        # Stage-neutral resources: the starter kit and the complete-project example are always offered.
        expect(page.locator('a[download][href$="agent-observer-starter-kit.zip"]')).to_be_visible()
        expect(page.locator('a[href="https://github.com/BH3GEI/observer-project-example"]')).to_be_visible()
        expect(page.locator('[data-testid^="dl-"]')).to_have_count(0)
        expect(page.get_by_text("Competition data is supplied round by round during evaluation. Source bundles cannot be downloaded.").first).to_be_visible()
        for language in ('zh','en','ja','fr'):
            for path in ('/','/start','/brief','/rules','/docs','/resources','/faq','/leaderboard'):
                page.goto(portal_site+path+'?lang='+language)
                page.locator('main').wait_for()
                body=page.locator('main').text_content()
                # The official demo truthfully identifies its public practice data.
                if path=='/':
                    demo_title=page.locator('.demo-title')
                    expect(demo_title).to_be_visible()
                    body=body.replace(demo_title.inner_text(),'',1)
                # One copy, both stages: no stage-specific variant is swapped in.
                match=re.search(r'官方本地会话|official local-session|智能体项目页|Agent projects page',body,re.I)
                if match:problems.append((language,path,body[max(0,match.start()-40):match.end()+100]))
        page.goto(portal_site+'/faq?lang=en')
        expect(page.locator('main')).to_contain_text('Submit your complete project as a ZIP file and it runs in the cloud.')
        page.goto(portal_site+'/admin/settings?lang=en')
        with page.expect_navigation(timeout=15000):
            page.get_by_test_id('competition-mode-switch').click()
        expect(page.get_by_test_id('competition-mode-switch')).to_have_text('Switch to competition',timeout=15000)
        goto(page,portal_site+'/submit?lang=en')
        expect(page.locator('input[type=file]')).to_be_visible(timeout=15000)
        assert query(uri,'select phase_id from private.observer_site_mode')[0][0]==practice
        browser.close()
    assert not errors,errors
    assert not problems,'\n'.join(map(str,problems))

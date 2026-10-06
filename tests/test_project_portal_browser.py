"""Browser portal journeys against real Edge handlers and PostgreSQL.

Auth/storage use the local Supabase harness. The prepared revision fixture stands
in for a completed public test; GitHub scheduling is exercised separately.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from pathlib import Path
import subprocess
import time
import urllib.request
import uuid

import pytest
from playwright.sync_api import sync_playwright, expect
from psycopg.types.json import Jsonb

from test_project_http import edge_stack, run_setup, free_port, ROOT, anon_key  # noqa: F401
from test_project_database import identity, query
from test_project_database import rpc
from project_platform.artifacts import pack_files
from project_platform.package import ProjectFile

pytestmark=pytest.mark.skipif(not os.environ.get('OBSERVER_DENO_BIN') or not os.environ.get('SAC_POSTGREST_BIN') or
    not os.environ.get('SAC_NODE_BIN'),reason='Explicit local browser test toolchain required')


def ensure_model_panel(page):
    """The model API panel starts collapsed for teams that never configured a key; open it when closed."""
    panel = page.get_by_test_id('model-api-settings')
    if panel.get_attribute('open') is None:
        panel.get_by_test_id('model-api-toggle').click()


@pytest.fixture(scope='module')
def portal_site(edge_stack):
    port=free_port();base=f'http://127.0.0.1:{port}'
    env={**os.environ,'PATH':os.environ['SAC_NODE_BIN']+':'+os.environ['PATH'],
         'VITE_SUPABASE_URL':edge_stack['harness'].url,'VITE_SUPABASE_ANON_KEY':anon_key(),
         'VITE_BASE_PATH':'/','VITE_SITE_URL':base}
    built=subprocess.run(['npm','run','build'],cwd=ROOT/'web',env=env,capture_output=True,text=True,timeout=180)
    assert built.returncode==0,built.stdout[-1500:]+built.stderr[-1500:]
    process=subprocess.Popen(['npm','exec','vite','--','preview','--host','127.0.0.1','--port',str(port),'--strictPort'],
        cwd=ROOT/'web',env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:urllib.request.urlopen(base,timeout=1).close();break
            except OSError:time.sleep(.1)
        else:pytest.fail('Portal site did not start')
        yield base
    finally:
        process.terminate()
        try:process.wait(timeout=5)
        except subprocess.TimeoutExpired:process.kill();process.wait()


def test_single_entry_repository_zip_review_and_preserved_csv_journey(portal_site,run_setup,tmp_path):
    s=run_setup;uri=s['uri'];password='local-browser-test-password-92'
    query(uri,"update private.observer_site_mode set mode='competition',phase_id=%s",(s['phase'],))
    legacy=uuid.uuid4()
    query(uri,"insert into public.phases(id,slug,name_en,name_zh) values(%s,%s,'Practice','练习赛')",(legacy,str(legacy)))
    query(uri,'insert into public.phase_scenarios values(%s,%s)',(legacy,s['scenario']))
    query(uri,'update auth.users set raw_user_meta_data=raw_user_meta_data || %s where id=%s',
          (Jsonb({'password_hash':hashlib.sha256(password.encode()).hexdigest()}),s['user']))
    # The HTTP fixture opens a run for session tests. This portal journey needs no
    # active batch until the participant presses Evaluate.
    query(uri,"update public.observer_runs set status='failed' where id=%s",(s['run'],))
    query(uri,"update public.observer_batches set status='failed' where id=(select batch_id from public.observer_runs where id=%s)",(s['run'],))
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel=os.environ.get('OBSERVER_BROWSER_CHANNEL'))
        context=browser.new_context(viewport={'width':1365,'height':950},locale='en-US')
        page=context.new_page();script_errors=[];page.on('pageerror',lambda e:script_errors.append(str(e)))
        # Route the Supabase function URL to the actual local Deno server. Request
        # bodies, bearer authorization and responses are untouched, not mocked.
        page.route('**/functions/v1/observer-portal',lambda route:route.fulfill(response=route.fetch(url=s['stack']['urls']['observer-portal'])))
        page.goto(portal_site+'/register?mode=login&lang=en')
        page.get_by_test_id('login-email').fill(f"{s['user']}@example.test")
        page.get_by_test_id('login-password').fill(password)
        page.get_by_test_id('login-submit').click()
        expect(page).to_have_url(portal_site+'/dashboard',timeout=20000)
        page.get_by_test_id('primary-submit').click()
        expect(page.get_by_test_id('project-title')).to_be_visible(timeout=15000)
        page.get_by_test_id('project-title').fill('Repository project')
        page.get_by_test_id('project-url').fill('https://github.com/owner/project')
        page.get_by_test_id('project-submit').click()
        expect(page.get_by_role('heading',name='Repository project',exact=True)).to_be_visible(timeout=15000)
        revision=query(uri,'select r.id from public.observer_revisions r join public.observer_projects p on p.id=r.project_id where p.team_id=%s and p.title=%s',
                       (s['team'],'Repository project'))[0][0]
        query(uri,"""insert into private.observer_installations(organization,organization_id,installation_id,repository_id,approved_sha,enabled)
          values('AGENTIC-OBSERVER26-runner-1','101',202,'303',%s,true)""",('a'*40,))
        job=uuid.uuid4();nonce=secrets.token_urlsafe(32)
        rpc(uri,'observer_enqueue_job',job,'prepare',None,revision,'AGENTIC-OBSERVER26-runner-1',nonce,'private input','private nonce')
        rpc(uri,'observer_claim_job',job,nonce,'404','1','303','101','a'*40)
        rpc(uri,'observer_finish_job',job,'404','1',{'diagnostics':{'code':'completed','log':'Compiler output <script>alert(1)</script>'}},'')
        # Logs are a quiet link, separate from the step actions.
        page.get_by_test_id('project-versions').get_by_role('button',name='Logs',exact=True).first.click()
        logs=page.get_by_test_id('project-diagnostics')
        expect(logs.locator('pre')).to_have_text('Compiler output <script>alert(1)</script>',timeout=15000)
        assert 'private input' not in page.content()
        logs.get_by_role('button',name='Close review',exact=True).click()
        manifest={'schema_version':'observer-project-v1','image':'python@sha256:'+'c'*64,'run':['python','agent.py']}
        query(uri,"""update public.observer_revisions set status='reviewable',source_digest=%s,approval_digest=%s,
          manifest=%s,adapter_files=%s,public_test='{"passed":true}',explanation='Calls the submitted strategy.' where id=%s""",
          ('a'*64,'b'*64,Jsonb(manifest),Jsonb({'.observer-adapter/main.py':'# <script>alert(1)</script>\nimport agent'}),revision))
        page.get_by_role('button',name='Refresh',exact=True).click()
        page.get_by_role('button',name='Review interface',exact=True).click()
        expect(page.get_by_test_id('project-approve')).to_be_disabled()
        expect(page.locator('pre').filter(has_text='<script>')).to_have_count(1)
        page.get_by_test_id('project-confirm').check()
        page.get_by_test_id('project-approve').click()
        evaluate=page.get_by_test_id('project-evaluate')
        expect(evaluate.get_by_role('button',name='Evaluate this version',exact=True)).to_be_visible(timeout=15000)
        assert query(uri,'select status from public.observer_revisions where id=%s',(revision,))==[('approved',)]
        # The fixture's failed batch was never refunded, so one of three is used.
        expect(evaluate.get_by_test_id('evaluation-quota')).to_contain_text('Evaluations left today: 2')
        evaluate.get_by_role('button',name='Evaluate this version',exact=True).click()
        expect(page.get_by_role('status').filter(has_text='Evaluation queued.')).to_be_visible(timeout=15000)
        batch=query(uri,"select id from public.observer_batches where revision_id=%s and purpose='formal'",(revision,))[0][0]
        # Disabled from the click on (reading "Working…" until the list is back), then while the evaluation is active.
        expect(evaluate.get_by_test_id('project-evaluate-button')).to_be_disabled()
        expect(evaluate.get_by_test_id('project-evaluate-button')).not_to_have_text('Working…',timeout=15000)
        expect(evaluate.get_by_test_id('project-evaluate-button')).to_be_disabled()
        # A platform failure is labelled and refunded; retrying it is not a repeat.
        query(uri,"update public.observer_runs set status='failed',error='engine_job_failed' where batch_id=%s",(batch,))
        query(uri,'select private.observer_finalize_batch(%s)',(batch,))
        page.get_by_role('button',name='Refresh',exact=True).click()
        expect(page.locator('#batch-'+str(batch)).get_by_test_id('batch-refunded')).to_have_text('Not counted toward the daily limit',timeout=15000)
        # Each run of an evaluation names its scenario.
        expect(page.locator('#batch-'+str(batch)).get_by_test_id('run-scenario')).to_have_text(str(s['scenario']))
        expect(evaluate.get_by_test_id('evaluation-quota')).to_contain_text('Evaluations left today: 2')
        evaluate.get_by_role('button',name='Evaluate this version',exact=True).click()
        expect(page.get_by_role('status').filter(has_text='Evaluation queued.')).to_be_visible(timeout=15000)
        query(uri,"""update public.observer_runs set status='scored',score=5,finished_at=now()
          where batch_id=(select id from public.observer_batches where revision_id=%s and status='queued')""",(revision,))
        query(uri,"select private.observer_finalize_batch(id) from public.observer_batches where revision_id=%s",(revision,))
        page.get_by_role('button',name='Refresh',exact=True).click()
        # Evaluating the same version again asks first; declining creates nothing.
        dialogs=[]
        page.once('dialog',lambda d:(dialogs.append(d.message),d.dismiss()))
        evaluate.get_by_role('button',name='Evaluate again',exact=True).click(timeout=15000)
        assert dialogs and '1 left today' in dialogs[0]
        assert query(uri,"select count(*) from public.observer_batches where revision_id=%s and purpose='formal'",(revision,))==[(2,)]
        page.get_by_role('radio',name='Private ZIP upload',exact=True).check()
        page.get_by_test_id('project-title').fill('ZIP project')
        page.get_by_test_id('project-zip').set_input_files({'name':'project.zip','mimeType':'application/zip',
            'buffer':pack_files((ProjectFile('main.rs',b'fn main() {}'),))})
        page.get_by_test_id('project-submit').click()
        expect(page.get_by_role('heading',name='ZIP project',exact=True)).to_be_visible(timeout=15000)
        expect(page.get_by_role('button',name='Start local CSV session',exact=True)).to_have_count(0)
        models=page.get_by_test_id('model-api-settings')
        ensure_model_panel(page)
        expect(models.get_by_role('heading',name='Model API (optional)',exact=True)).to_be_visible()
        # Default: saving encrypted on the server is pre-selected; no page relay shown.
        expect(models.get_by_test_id('model-mode-stored')).to_be_checked()
        expect(models.get_by_test_id('model-mode-relay')).not_to_be_checked()
        expect(models.get_by_test_id('model-mode-tradeoff')).to_have_text(
            'Saved (default): stored encrypted and deleted automatically after the results are verified. Not saved: keep this page open during evaluations.')
        expect(models.get_by_test_id('team-model-form')).to_be_visible(timeout=15000)
        expect(models.get_by_test_id('personal-model-settings')).to_have_count(0)
        assert query(uri,'select count(*) from private.observer_team_model_modes where team_id=%s',(s['team'],))==[(0,)]
        expect(models).to_contain_text('you can close this page during evaluation')
        key='browser-saved-key-fixture-4Kd9'
        # Any public https:// address is accepted; the organizer's list only feeds suggestions.
        suggested=page.locator('#model-base-suggestions option').first.get_attribute('value')
        assert suggested and suggested.startswith('https://')
        models.get_by_test_id('team-model-endpoint').fill(suggested)
        models.get_by_label('Model',exact=True).fill('team-model')
        models.get_by_test_id('team-model-key').fill(key)
        models.get_by_role('button',name='Save encrypted key',exact=True).click()
        expect(models.get_by_test_id('team-model-hint')).to_contain_text('Key ending in 4Kd9',timeout=15000)
        expect(models.get_by_test_id('team-model-key')).to_have_count(0)
        assert key not in page.content()
        assert key not in page.evaluate('JSON.stringify({...localStorage,...sessionStorage})')
        stored=query(uri,'''select p.encrypted_key from private.observer_team_models m
            join private.observer_providers p on p.id=m.provider_id where m.team_id=%s''',(s['team'],))
        assert len(stored)==1 and stored[0][0].startswith('v1.') and key not in stored[0][0]
        page.reload()
        models=page.get_by_test_id('model-api-settings')
        # A saved key reopens the panel on its own.
        expect(models.get_by_test_id('team-model-hint')).to_contain_text('Key ending in 4Kd9',timeout=15000)
        # Choosing not to save deletes the saved key at once and shows the page relay.
        expect(models).to_contain_text('Choosing this deletes the saved key.')
        models.get_by_test_id('model-mode-relay').check()
        personal=models.get_by_test_id('personal-model-settings')
        expect(personal).to_contain_text('Keep this page open until each evaluation finishes',timeout=15000)
        expect(models).to_contain_text('open this page at the time agreed with the organizers')
        # The form follows the choice at once; the notice confirms the server deleted the key.
        expect(page.get_by_role('status').filter(has_text='Saved key deleted.')).to_be_visible(timeout=15000)
        assert query(uri,'select count(*) from private.observer_team_models where team_id=%s',(s['team'],))==[(0,)]
        assert query(uri,"select count(*) from private.observer_providers where team_id=%s and encrypted_key<>''",(s['team'],))==[(0,)]
        personal.get_by_test_id('personal-model-endpoint').fill(suggested)
        personal.get_by_label('Model',exact=True).fill('own-model')
        personal.get_by_test_id('personal-api-key').fill('in-memory-browser-fixture')
        personal.get_by_role('button',name='Connect (key not saved)',exact=True).click()
        expect(personal.get_by_role('button',name='Disconnect and clear key')).to_be_visible()
        assert 'in-memory-browser-fixture' not in page.evaluate('JSON.stringify({...localStorage,...sessionStorage})')
        assert query(uri,"select count(*) from private.observer_providers where team_id=%s and encrypted_key<>''",(s['team'],))==[(0,)]
        page.reload()
        # The key connected earlier in this tab reopens the panel (session marker, no key material stored).
        expect(page.get_by_test_id('personal-api-key')).to_have_value('',timeout=15000)
        expect(page.get_by_test_id('model-mode-relay')).to_be_checked()
        # Opting in again: the relay form and its keep-open message disappear.
        page.get_by_test_id('model-mode-stored').check()
        expect(page.get_by_test_id('team-model-form')).to_be_visible(timeout=15000)
        expect(page.get_by_test_id('personal-model-settings')).to_have_count(0)
        expect(page.get_by_text('Keep this page open until each evaluation finishes, including')).to_have_count(0)
        expect(page.get_by_role('status').filter(has_text='Keys will be saved encrypted on the server.')).to_be_visible(timeout=15000)
        for lang,title in (('fr','API de modèle (facultatif)'),('ja','モデル API（任意）'),('zh','模型 API（可选）')):
            page.goto(portal_site+'/projects?lang='+lang)
            ensure_model_panel(page)
            expect(page.get_by_test_id('model-api-settings').get_by_role('heading',name=title,exact=True)).to_be_visible(timeout=15000)
        page.goto(portal_site+'/projects?lang=en')
        ensure_model_panel(page)
        expect(page.get_by_test_id('model-api-settings').get_by_role('heading',name='Model API (optional)')).to_be_visible(timeout=15000)
        # A version that was never evaluated can be withdrawn after a confirmation.
        page.get_by_test_id('project-title').fill('Withdrawn project')
        page.get_by_test_id('project-url').fill('https://github.com/owner/withdrawn')
        page.get_by_test_id('project-submit').click()
        versions=page.get_by_test_id('project-versions')
        expect(versions.get_by_role('heading',name='Withdrawn project',exact=True)).to_be_visible(timeout=15000)
        expect(page.get_by_test_id('project-title')).to_have_value('')
        page.once('dialog',lambda d:d.accept())
        versions.locator('article',has_text='Withdrawn project').get_by_test_id('project-withdraw').click()
        expect(versions.get_by_role('heading',name='Withdrawn project',exact=True)).to_have_count(0,timeout=15000)
        versions.get_by_role('button',name='Show withdrawn versions (1)',exact=True).click()
        expect(versions.locator('article',has_text='Withdrawn project').locator('.pill')).to_have_text('Withdrawn')
        expect(versions.locator('article',has_text='Repository project').get_by_test_id('project-withdraw')).to_have_count(0)

        page.get_by_role('button',name='Review interface',exact=True).click()
        page.get_by_label('Architecture and reproduction notes').fill('Run the project using the submitted manifest.')
        page.get_by_role('button',name='Save evidence',exact=True).click()
        expect(page.get_by_role('status').filter(has_text='Saved.')).to_be_visible(timeout=15000)
        page.set_viewport_size({'width':390,'height':844})
        page.goto(portal_site+'/projects?lang=zh')
        expect(page.get_by_test_id('project-title')).to_be_visible(timeout=15000)
        expect(page.get_by_role('heading',name='ZIP project',exact=True)).to_be_visible(timeout=15000)
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        shots=ROOT/'artifacts/screenshots-projects';shots.mkdir(parents=True,exist_ok=True)
        page.screenshot(path=str(shots/'portal-mobile.png'),full_page=True)
        # The administrator selects one current competition; the participant
        # never chooses between stages and old project links use the same entry.
        query(uri,"update private.observer_site_mode set mode='practice',phase_id=%s",(legacy,))
        page.goto(portal_site+'/submit?phase='+str(s['phase'])+'&lang=zh')
        expect(page.get_by_test_id('csv-workflow')).to_be_visible(timeout=15000)
        expect(page.get_by_test_id('current-submission-phase')).to_contain_text('练习赛')
        expect(page.get_by_test_id('submit-phase')).to_have_count(0)
        expect(page.get_by_test_id('project-title')).to_have_count(0)
        page.goto(portal_site+'/projects?lang=zh')
        expect(page.get_by_test_id('csv-workflow')).to_be_visible()
        assert '/compete' in page.url
        assert not script_errors,script_errors
        context.close();browser.close()


def test_beta_entry_serves_only_its_team_while_the_site_stays_practice(portal_site,run_setup):
    """The team-restricted formal phase is reachable for its access team even
    while every other visitor keeps the unchanged single practice entry."""
    s=run_setup;uri=s['uri'];password='local-browser-beta-password-17'
    other,_=identity(uri)
    legacy=uuid.uuid4()
    query(uri,"insert into public.phases(id,slug,name_en,name_zh) values(%s,%s,'Practice','练习赛')",(legacy,'practice'))
    query(uri,'insert into public.phase_scenarios values(%s,%s)',(legacy,s['scenario']))
    query(uri,"update private.observer_site_mode set mode='practice',phase_id=%s",(legacy,))
    query(uri,'update public.observer_phase_settings set access_team_id=%s where phase_id=%s',(s['team'],s['phase']))
    query(uri,"update public.observer_runs set status='failed' where id=%s",(s['run'],))
    query(uri,"update public.observer_batches set status='failed' where id=(select batch_id from public.observer_runs where id=%s)",(s['run'],))
    for user in (s['user'],other):
        query(uri,"update auth.users set raw_user_meta_data=raw_user_meta_data || %s where id=%s",
              (Jsonb({'password_hash':hashlib.sha256(password.encode()).hexdigest()}),user))
    portal_url=s['stack']['urls']['observer-portal']
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel=os.environ.get('OBSERVER_BROWSER_CHANNEL'))
        errors=[]
        def login(context,user):
            page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
            page.route('**/functions/v1/observer-portal',lambda route:route.fulfill(response=route.fetch(url=portal_url)))
            page.goto(portal_site+'/register?mode=login&lang=en')
            page.get_by_test_id('login-email').fill(f'{user}@example.test')
            page.get_by_test_id('login-password').fill(password)
            page.get_by_test_id('login-submit').click()
            expect(page).to_have_url(portal_site+'/dashboard',timeout=20000)
            return page
        # The access team member reaches the formal project entry.
        member_context=browser.new_context(viewport={'width':1365,'height':950},locale='en-US')
        page=login(member_context,s['user'])
        page.goto(portal_site+'/compete')
        expect(page.get_by_test_id('project-title')).to_be_visible(timeout=15000)
        expect(page.locator('.poster-kicker')).to_have_text('Test')
        shots=ROOT/'artifacts'/'screenshots-beta-entry';shots.mkdir(parents=True,exist_ok=True)
        page.screenshot(path=str(shots/'beta-member-project-entry.png'),full_page=True)
        member_context.close()
        # Everyone else keeps the single practice entry: anonymous visitors are
        # sent to login and an unrelated participant still sees the CSV route.
        other_context=browser.new_context(viewport={'width':1365,'height':950},locale='en-US')
        page=other_context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
        page.route('**/functions/v1/observer-portal',lambda route:route.fulfill(response=route.fetch(url=portal_url)))
        page.goto(portal_site+'/compete')
        expect(page).to_have_url(portal_site+'/register?mode=login&next=/compete',timeout=20000)
        page.get_by_test_id('login-email').fill(f'{other}@example.test')
        page.get_by_test_id('login-password').fill(password)
        page.get_by_test_id('login-submit').click()
        expect(page).to_have_url(re.compile(re.escape(portal_site)+'/compete$'),timeout=20000)
        expect(page.get_by_test_id('csv-workflow')).to_be_visible(timeout=15000)
        expect(page.get_by_test_id('project-title')).to_have_count(0)
        page.screenshot(path=str(shots/'ordinary-participant-practice-csv.png'),full_page=True)
        other_context.close();browser.close()
        assert not errors,errors


def test_beta_entry_follows_team_changes_within_a_session(portal_site,run_setup):
    """A same-session team change (no auth event) must switch or drop the beta
    entry on the next in-app navigation instead of reusing the previous team."""
    s=run_setup;uri=s['uri'];password='local-browser-beta-password-18'
    _,team_b=identity(uri)
    _,team_none=identity(uri)
    phase_b=uuid.uuid4()
    legacy=query(uri,"select id from public.phases where slug='practice' limit 1")
    if legacy:
        legacy=legacy[0][0]
    else:
        legacy=uuid.uuid4()
        query(uri,"insert into public.phases(id,slug,name_en,name_zh) values(%s,'practice','Practice','练习赛')",(legacy,))
    query(uri,"insert into public.phases(id,slug,name_en,name_zh,sort_order) values(%s,'acceptance-b','Beta B','验收B',10100)",(phase_b,))
    query(uri,'insert into public.observer_phase_settings(phase_id,projects_enabled,local_sessions_enabled,access_team_id) values(%s,true,false,%s)',(phase_b,team_b))
    query(uri,'insert into public.phase_scenarios values(%s,%s)',(legacy,s['scenario']))
    query(uri,"update private.observer_site_mode set mode='practice',phase_id=%s",(legacy,))
    query(uri,'update public.observer_phase_settings set access_team_id=%s where phase_id=%s',(s['team'],s['phase']))
    query(uri,"update public.observer_runs set status='failed' where id=%s",(s['run'],))
    query(uri,"update public.observer_batches set status='failed' where id=(select batch_id from public.observer_runs where id=%s)",(s['run'],))
    query(uri,"update auth.users set raw_user_meta_data=raw_user_meta_data || %s where id=%s",
          (Jsonb({'password_hash':hashlib.sha256(password.encode()).hexdigest()}),s['user']))
    portal_url=s['stack']['urls']['observer-portal']
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel=os.environ.get('OBSERVER_BROWSER_CHANNEL'))
        context=browser.new_context(viewport={'width':1365,'height':950},locale='en-US')
        page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.route('**/functions/v1/observer-portal',lambda route:route.fulfill(response=route.fetch(url=portal_url)))
        page.goto(portal_site+'/register?mode=login&lang=en')
        page.get_by_test_id('login-email').fill(f"{s['user']}@example.test")
        page.get_by_test_id('login-password').fill(password)
        page.get_by_test_id('login-submit').click()
        expect(page).to_have_url(portal_site+'/dashboard',timeout=20000)
        page.goto(portal_site+'/compete')
        expect(page.get_by_test_id('project-title')).to_be_visible(timeout=15000)
        expect(page.locator('.poster-kicker')).to_have_text('Test')
        nav=page.locator('nav.admin-nav')
        # Joining another acceptance team swaps the entry to that team's phase.
        query(uri,'update public.profiles set team_id=%s where id=%s',(team_b,s['user']))
        nav.get_by_role('link',name='Dashboard',exact=True).click()
        expect(page).to_have_url(portal_site+'/dashboard',timeout=20000)
        nav.get_by_role('link',name='Participate',exact=True).click()
        expect(page).to_have_url(portal_site+'/compete',timeout=20000)
        expect(page.get_by_test_id('project-title')).to_be_visible(timeout=15000)
        expect(page.locator('.poster-kicker')).to_have_text('Beta B')
        # Leaving every acceptance team falls back to the unchanged practice entry.
        query(uri,'update public.profiles set team_id=%s where id=%s',(team_none,s['user']))
        nav.get_by_role('link',name='Dashboard',exact=True).click()
        expect(page).to_have_url(portal_site+'/dashboard',timeout=20000)
        nav.get_by_role('link',name='Participate',exact=True).click()
        expect(page.get_by_test_id('csv-workflow')).to_be_visible(timeout=15000)
        expect(page.get_by_test_id('project-title')).to_have_count(0)
        context.close();browser.close()
        assert not errors,errors


@pytest.mark.parametrize('calibrated',[False,True])
def test_online_board_shows_same_batch_mean_and_keeps_private_artifacts_hidden(portal_site,run_setup,calibrated):
    s=run_setup;uri=s['uri'];batch=query(uri,'select batch_id from public.observer_runs where id=%s',(s['run'],))[0][0]
    query(uri,"update private.observer_site_mode set mode='competition',phase_id=%s",(s['phase'],))
    raw={'total':60,'base_science':70,'program_bonus':0,'request_reward':0,'penalties':{'bad':10}}
    summary={'score':raw,'completed_tiles':4,'required_missing':2}
    score=10000 if calibrated else 60
    if calibrated:summary.update({'score':{'total':score},'raw_score':raw,'calibration':{'version':'observer-reference-panel-v1'}})
    query(uri,"update public.observer_runs set status='scored',score=%s,score_summary=%s,result_path='github:private-board-result',finished_at=now() where id=%s",
      (score,Jsonb(summary),s['run']))
    query(uri,'select private.observer_finalize_batch(%s)',(batch,))
    query(uri,'update public.phases set counts_for_final=(id=%s)',(s['phase'],))
    team_name=query(uri,'select name from public.teams where id=%s',(s['team'],))[0][0]
    slug=query(uri,'select slug from public.phases where id=%s',(s['phase'],))[0][0]
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel=os.environ.get('OBSERVER_BROWSER_CHANNEL'))
        page=browser.new_page(viewport={'width':1365,'height':950});errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(portal_site+'/leaderboard/'+slug+'?lang=en')
        row=page.get_by_test_id('lb-row').filter(has_text=team_name)
        expect(row).to_be_visible(timeout=15000)
        expect(row.locator('td').nth(2)).to_contain_text('10000' if calibrated else '60')
        row.click();dialog=page.get_by_test_id('team-detail')
        expect(dialog).to_be_visible()
        expect(dialog).to_contain_text('60')
        if calibrated:
            expect(dialog).to_contain_text('Calibrated score')
            expect(dialog).to_contain_text('Raw score: 60')
            expect(dialog).to_contain_text('70')
        assert 'private-board-result' not in page.content()
        page.keyboard.press('Escape')
        page.goto(portal_site+'/?lang=en')
        expect(page.locator('#board').get_by_test_id('lb-row').filter(has_text=team_name)).to_be_visible(timeout=15000)
        page.goto(portal_site+'/rules?lang=en')
        phase_row=page.locator('[data-testid="phase-row"][data-phase="'+slug+'"]')
        expect(phase_row).to_contain_text('Complete project / Local-session CSV',timeout=15000)
        assert not errors,errors
        browser.close()


def test_team_chooses_clears_and_is_locked_out_of_its_final_version(portal_site,run_setup):
    from test_project_eval_ux import revision
    s=run_setup;uri=s['uri'];password='local-browser-test-password-93'
    query(uri,"update private.observer_site_mode set mode='competition',phase_id=%s",(s['phase'],))
    query(uri,"update public.phases set counts_for_final=true,ends_at=now()+interval '1 day' where id=%s",(s['phase'],))
    query(uri,'update auth.users set raw_user_meta_data=raw_user_meta_data || %s where id=%s',
          (Jsonb({'password_hash':hashlib.sha256(password.encode()).hexdigest()}),s['user']))
    query(uri,"update public.observer_runs set status='failed' where id=%s",(s['run'],))
    query(uri,"update public.observer_batches set status='failed' where id=(select batch_id from public.observer_runs where id=%s)",(s['run'],))
    first=revision(s);second=revision(s)
    query(uri,"update public.observer_projects set title='Second agent' where id=(select project_id from public.observer_revisions where id=%s)",(second,))
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel=os.environ.get('OBSERVER_BROWSER_CHANNEL'))
        context=browser.new_context(viewport={'width':1365,'height':950},locale='en-US')
        page=context.new_page();script_errors=[];page.on('pageerror',lambda e:script_errors.append(str(e)))
        page.on('dialog',lambda d:d.accept())
        page.route('**/functions/v1/observer-portal',lambda route:route.fulfill(response=route.fetch(url=s['stack']['urls']['observer-portal'])))
        page.goto(portal_site+'/register?mode=login&lang=en')
        page.get_by_test_id('login-email').fill(f"{s['user']}@example.test")
        page.get_by_test_id('login-password').fill(password)
        page.get_by_test_id('login-submit').click()
        expect(page).to_have_url(portal_site+'/dashboard',timeout=20000)
        page.get_by_test_id('primary-submit').click()
        final=page.get_by_test_id('final-version')
        expect(final).to_contain_text('No final version yet',timeout=15000)
        expect(final).to_contain_text('Only that hidden score decides the final ranking')
        final.locator(f'[data-final-revision-id="{second}"]').get_by_test_id('final-version-set').click()
        expect(page.get_by_role('status').filter(has_text='Final version saved.')).to_be_visible(timeout=15000)
        expect(final.get_by_test_id('final-version-current')).to_contain_text('Second agent')
        expect(final.get_by_test_id('final-version-current')).to_contain_text('Chosen by your team')
        expect(page.get_by_test_id('project-versions').locator(f'[data-revision-id="{second}"]').get_by_test_id('final-version-badge')).to_be_visible()
        # A chosen version cannot be withdrawn; the button is not offered.
        expect(page.get_by_test_id('project-versions').locator(f'[data-revision-id="{second}"]').get_by_test_id('project-withdraw')).to_have_count(0)
        final.get_by_test_id('final-version-clear').click()
        expect(page.get_by_role('status').filter(has_text='Choice cleared')).to_be_visible(timeout=15000)
        expect(final).to_contain_text('No final version yet')
        final.locator(f'[data-final-revision-id="{first}"]').get_by_test_id('final-version-set').click()
        expect(final.get_by_test_id('final-version-current')).to_contain_text('Chosen by your team',timeout=15000)
        query(uri,"update public.phases set ends_at=now()-interval '1 second' where id=%s",(s['phase'],))
        page.get_by_role('button',name='Refresh',exact=True).click()
        expect(final.get_by_test_id('final-version-locked')).to_be_visible(timeout=15000)
        expect(final.get_by_test_id('final-version-set')).to_have_count(0)
        expect(final.get_by_test_id('final-version-clear')).to_have_count(0)
        assert query(uri,'select revision_id from private.observer_final_versions where team_id=%s',(s['team'],))==[(first,)]
        context.close();browser.close()
        assert not script_errors,script_errors

#!/usr/bin/env python3
"""Configure only Observer secrets from macOS Keychain and process environment."""
import argparse
import base64
import json
import os
from pathlib import Path
import secrets
import subprocess
import urllib.request
from urllib.parse import urlsplit
import uuid

ROOT=Path(__file__).resolve().parents[1]
SERVICE='agentic-observer26-backend'
DEFAULT_TEAM_BASES=('https://openrouter.ai/api/v1','https://api.deepseek.com')


def approved_bases(extra):
    result=list(DEFAULT_TEAM_BASES)
    for value in extra:
        parsed=urlsplit(value)
        if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or ',' in value or any(c.isspace() for c in value):
            raise ValueError('Additional model endpoints must be HTTPS base URLs without credentials or query strings')
        value=value.rstrip('/')
        if value not in result:result.append(value)
    return result


def keychain(service,account):
    result=subprocess.run(['security','find-generic-password','-s',service,'-a',account,'-w'],capture_output=True,text=True)
    if result.returncode: return None
    return json.loads(result.stdout)


def request(path,data=None):
    req=urllib.request.Request('https://api.supabase.com/v1/projects/'+os.environ['SUPABASE_PROJECT_REF']+path,
        data=None if data is None else json.dumps(data).encode(),
        headers={'Authorization':'Bearer '+os.environ['SUPABASE_ACCESS_TOKEN'],'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=60) as response:
        raw=response.read()
        return json.loads(raw) if raw else None


def quote(value):return "'"+str(value).replace("'","''")+"'"


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--deno',required=True)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--model-base',action='append',default=[],help='Additional organizer-approved HTTPS chat-completions API base')
    args=parser.parse_args()
    team_bases=approved_bases(args.model_base)
    if not args.apply:
        print('Will configure Observer app, encryption, dispatch and the authorized test provider; no change made.')
        return
    ref=os.environ['SUPABASE_PROJECT_REF']
    app=keychain('agentic-observer26-github-app','BH3GEI')
    if not app:raise RuntimeError('GitHub App is not present in Keychain')
    state=keychain(SERVICE,ref)
    if not state:
        if any(s['name']=='OBSERVER_KEY_ENCRYPTION_KEY' for s in request('/secrets')):
            raise RuntimeError('Existing encryption key must be recovered; automatic rotation is refused')
        state={'master':base64.b64encode(secrets.token_bytes(32)).decode(),'dispatch':secrets.token_urlsafe(48),
               'provider_id':str(uuid.uuid4())}
    if os.environ.get('OBSERVER_TEST_MODEL_KEY'):state['model_key']=os.environ['OBSERVER_TEST_MODEL_KEY']
    if not state.get('model_key'):raise RuntimeError('Supply the authorized test model key through process environment')
    stored=subprocess.run(['security','add-generic-password','-U','-s',SERVICE,'-a',ref,'-w',json.dumps(state)],capture_output=True)
    if stored.returncode:raise RuntimeError('Could not persist backend credentials in Keychain')
    # The organizer's own relay (an http:// address): supplied at run time, never stored in the repository.
    base=os.environ.get('OBSERVER_ORGANIZER_RELAY_BASE','')
    if not base.startswith('http://'):raise RuntimeError('Set OBSERVER_ORGANIZER_RELAY_BASE to the organizer relay base URL')
    values={'OBSERVER_GITHUB_APP_ID':str(app['id']),'OBSERVER_GITHUB_APP_PEM':app['pem'],
      'OBSERVER_KEY_ENCRYPTION_KEY':state['master'],'OBSERVER_DISPATCH_SECRET':state['dispatch'],
      'OBSERVER_DEFAULT_MODEL_PROVIDER':state['provider_id'],'OBSERVER_MODEL_BASES':','.join([base,*team_bases]),'OBSERVER_MODEL_HTTP_BASES':base}
    request('/secrets',[{'name':name,'value':value} for name,value in values.items()])
    code='''import {encryptCredential} from './_shared/observer-model.ts';
const d=await new Response(Deno.stdin.readable).json();
console.log(await encryptCredential(d.key,d.id,d.master));'''
    encrypted=subprocess.run([args.deno,'eval',code],input=json.dumps({'key':state['model_key'],
      'id':state['provider_id'],'master':state['master']}),cwd=ROOT/'supabase/functions',capture_output=True,text=True)
    if encrypted.returncode:raise RuntimeError('Provider encryption failed; diagnostic output suppressed')
    sql="""insert into private.observer_providers(id,name,base_url,encrypted_key,models,allow_http,enabled,daily_token_limit)
      values(%s,'Organizer test API',%s,%s,array['qwen3.6:35b-a3b'],true,true,50000)
      on conflict(id) do update set encrypted_key=excluded.encrypted_key,base_url=excluded.base_url,models=excluded.models""" % (
          quote(state['provider_id']),quote(base),quote(encrypted.stdout.strip()))
    request('/database/query',{'query':sql})
    print(json.dumps({'configured_secret_names':list(values),'provider_id':state['provider_id'],
      'daily_token_limit':50000,'keychain_service':SERVICE,'phase_settings':'unchanged'}))


if __name__=='__main__':main()

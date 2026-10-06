"""Real Edge TypeScript -> PostgREST -> PostgreSQL -> simulator integration.

SAC_POSTGREST_BIN and OBSERVER_DENO_BIN select installed test dependencies.
OBSERVER_TEST_PYTHON_IMAGE enables the additional real container exercise.
No hosted database, participant account or repository is used here.
"""
from __future__ import annotations

import base64
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from psycopg.types.json import Jsonb

from challenge.scenario_builder import generate_scenario
from challenge.scoring_core import score_files
from project_platform.docker_runtime import DockerWorkspace
from project_platform.artifacts import pack_files
from project_platform.job_client import Http, JobClient
from project_platform.job_runner import run_claimed
from project_platform.executor import execute
from project_platform.manifest import ProjectManifest
from project_platform.model_adapter import propose_adapter
from project_platform.model_client import ModelClient
from project_platform.package import ProjectFile, extract_project, project_digest, read_project_zip
from project_platform.session import SessionClient, SessionError
from project_platform.trusted_engine import result_summary, run_session
from test_project_database import identity, query, rpc, session

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tests/supabase"))
from harness import Harness, service_key, anon_key, user_token  # noqa: E402

pytestmark=pytest.mark.skipif(not os.environ.get("OBSERVER_DENO_BIN") or not os.environ.get("SAC_POSTGREST_BIN"),
                             reason="Explicit Deno and PostgREST binaries required")


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1",0))
        return sock.getsockname()[1]


def post(url,body,credential,*,timeout=30):
    req=urllib.request.Request(url,data=json.dumps(body).encode(),
        headers={"Authorization":"Bearer "+credential,"Content-Type":"application/json"},method="POST")
    try:
        with urllib.request.urlopen(req,timeout=timeout) as response:
            return response.status,json.load(response)
    except urllib.error.HTTPError as exc:
        return exc.code,json.load(exc)


class _TeamProvider(BaseHTTPRequestHandler):
    """Stub participant provider behind HTTPS. Records what the proxy sends."""
    def do_POST(self):
        length=int(self.headers.get("Content-Length") or 0)
        body=json.loads(self.rfile.read(length) or b"{}")
        auth=self.headers.get("Authorization","")
        api_key=self.headers.get("X-Api-Key","")
        anthropic_version=self.headers.get("Anthropic-Version","")
        self.server.requests.append({"path":self.path,"auth":auth,"api_key":api_key,
                                     "anthropic_version":anthropic_version,"body":body})
        mode=self.server.mode
        messages_shape=self.path.endswith("/v1/messages")
        if mode=="redirect":
            self.send_response(307);self.send_header("Location","https://127.0.0.1:9/v1/chat/completions")
            self.send_header("Content-Length","0");self.end_headers();return
        if mode=="reject":
            code,payload=401,{"error":{"message":"rejected "+(api_key or auth)}}
        elif body.get("model")=="unknown-model":
            code,payload=404,{"error":{"code":"model_not_found","message":"The model does not exist"}}
        elif messages_shape:
            code,payload=200,{"type":"message","role":"assistant",
                "content":[{"type":"text","text":"echo "+api_key if mode=="echo" else "OK"}],
                "usage":{"input_tokens":12,"output_tokens":5}}
        else:
            code,payload=200,{"choices":[{"message":{"role":"assistant","content":"echo "+auth if mode=="echo" else "OK"}}],
                              "usage":{"prompt_tokens":12,"completion_tokens":5,"total_tokens":17}}
        data=json.dumps(payload).encode()
        self.send_response(code);self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(data)));self.end_headers();self.wfile.write(data)
    def log_message(self,*_args): pass


def https_team_provider(root):
    """HTTPS stub signed by a throwaway test CA. None when openssl is unavailable."""
    openssl=shutil.which("openssl")
    if not openssl:
        return None
    def run(*args):
        subprocess.run([openssl,*map(str,args)],check=True,capture_output=True)
    ec=("-newkey","ec","-pkeyopt","ec_paramgen_curve:prime256v1","-nodes")
    run("req","-x509",*ec,"-keyout",root/"ca.key","-out",root/"ca.pem","-days","2","-subj","/CN=observer-test-ca")
    run("req",*ec,"-keyout",root/"provider.key","-out",root/"provider.csr","-subj","/CN=127.0.0.1")
    (root/"provider.ext").write_text("subjectAltName=IP:127.0.0.1,DNS:localhost\nbasicConstraints=CA:FALSE\n"
                                     "keyUsage=digitalSignature\nextendedKeyUsage=serverAuth\n")
    run("x509","-req","-in",root/"provider.csr","-CA",root/"ca.pem","-CAkey",root/"ca.key","-CAcreateserial",
        "-out",root/"provider.pem","-days","2","-extfile",root/"provider.ext")
    server=ThreadingHTTPServer(("127.0.0.1",0),_TeamProvider)
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(root/"provider.pem",root/"provider.key")
    server.socket=context.wrap_socket(server.socket,server_side=True)
    server.requests=[];server.mode="ok"
    threading.Thread(target=server.serve_forever,daemon=True).start()
    return server,f"https://127.0.0.1:{server.server_port}/v1",root/"ca.pem"


@pytest.fixture(scope="module")
def edge_stack(tmp_path_factory):
    root=tmp_path_factory.mktemp("observer-edge")
    harness=Harness().start()
    model_requests=[]
    upstream_key="integration-upstream-key"
    class Upstream(BaseHTTPRequestHandler):
        def do_POST(self):
            length=int(self.headers["Content-Length"])
            body=json.loads(self.rfile.read(length))
            model_requests.append({"body":body,"auth":self.headers.get("Authorization")})
            payload=json.dumps({"choices":[{"message":{"role":"assistant","content":"OK"}}],
                                "usage":{"prompt_tokens":12,"completion_tokens":2,"total_tokens":14}}).encode()
            self.send_response(200); self.send_header("Content-Type","application/json")
            self.send_header("Content-Length",str(len(payload))); self.end_headers(); self.wfile.write(payload)
        def log_message(self,*_args): pass
    upstream=ThreadingHTTPServer(("127.0.0.1",0),Upstream)
    threading.Thread(target=upstream.serve_forever,daemon=True).start()
    base=f"http://127.0.0.1:{upstream.server_port}/v1"
    provider=uuid.uuid4()
    master=secrets.token_bytes(32)
    import base64
    encoded=base64.b64encode(master).decode()
    env={key:os.environ[key] for key in ("PATH","HOME","TMPDIR") if key in os.environ}
    bases=[base]
    if os.environ.get("OBSERVER_LIVE_MODEL_BASE"):
        bases.append(os.environ["OBSERVER_LIVE_MODEL_BASE"].rstrip("/"))
    team=https_team_provider(root)
    if team:
        bases.append(team[1])
        # Trusted only by these Edge processes; they reach nothing else over TLS.
        env["DENO_CERT"]=str(team[2])
    env.update({"SUPABASE_URL":harness.url,"SUPABASE_SERVICE_ROLE_KEY":service_key(),"SUPABASE_ANON_KEY":anon_key(),
                "OBSERVER_KEY_ENCRYPTION_KEY":encoded,"OBSERVER_DEFAULT_MODEL_PROVIDER":str(provider),
                "OBSERVER_MODEL_BASES":",".join([*bases,"https://personal.example/v1"]),"OBSERVER_MODEL_HTTP_BASES":",".join(b for b in bases if b.startswith("http://"))})
    deno=os.environ["OBSERVER_DENO_BIN"]
    # Only disposable test credentials are used in this integration fixture.
    encrypted=subprocess.run([deno,"eval",
        'import {encryptCredential} from "./_shared/observer-model.ts"; '
        'console.log(await encryptCredential(Deno.env.get("TEST_UPSTREAM_KEY"),Deno.env.get("OBSERVER_DEFAULT_MODEL_PROVIDER"),'
        'Deno.env.get("OBSERVER_KEY_ENCRYPTION_KEY")));'],
        cwd=ROOT/"supabase/functions",env={**env,"TEST_UPSTREAM_KEY":upstream_key},
        capture_output=True,text=True,check=True).stdout.strip()
    query(harness.db_uri,"""insert into private.observer_providers
        (id,name,base_url,encrypted_key,models,allow_http,enabled,daily_token_limit)
        values(%s,'Integration',%s,%s,array['test-model'],true,true,100000)""",(provider,base,encrypted))
    processes=[]
    urls={}
    try:
        for name in ("observer-session","observer-model","observer-portal"):
            port=free_port()
            log=(root/(name+".log")).open("w")
            process=subprocess.Popen([deno,"run","--allow-env","--allow-net",name+"/index.ts"],
                cwd=ROOT/"supabase/functions",env={**env,"OBSERVER_LISTEN_PORT":str(port)},
                stdout=log,stderr=subprocess.STDOUT)
            processes.append((process,log))
            url=f"http://127.0.0.1:{port}/{name}"
            for _ in range(100):
                if process.poll() is not None:
                    pytest.fail((root/(name+".log")).read_text())
                try:
                    urllib.request.urlopen(urllib.request.Request(url,method="OPTIONS"),timeout=1).close()
                    break
                except (OSError,urllib.error.URLError):
                    time.sleep(0.05)
            else:
                pytest.fail("Edge service did not start")
            urls[name]=url
        yield {"harness":harness,"urls":urls,"provider":provider,"requests":model_requests,
               "upstream_key":upstream_key,"master":encoded,"team_provider":team[0] if team else None,
               "team_base":team[1] if team else None,"logs":root}
    finally:
        for process,log in processes:
            process.terminate()
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired: process.kill(); process.wait()
            log.close()
        if team: team[0].shutdown(); team[0].server_close()
        upstream.shutdown(); upstream.server_close(); harness.stop()


@pytest.fixture
def run_setup(edge_stack):
    uri=edge_stack["harness"].db_uri
    user,team=identity(uri)
    phase,scenario=uuid.uuid4(),uuid.uuid4()
    query(uri,"insert into public.phases(id,slug,name_en,name_zh) values(%s,%s,'Test','Test')",(phase,str(phase)))
    query(uri,"""insert into public.observer_phase_settings(phase_id,projects_enabled,local_sessions_enabled,
        runtime_seconds,model_token_limit,model_call_limit) values(%s,true,true,120,5000,1)""",(phase,))
    query(uri,"insert into public.scenarios(id,slug,name) values(%s,%s,'Test')",(scenario,str(scenario)))
    query(uri,"insert into public.phase_scenarios values(%s,%s)",(phase,scenario))
    s={"uri":uri,"user":user,"team":team,"phase":phase,"scenario":scenario,"provider":edge_stack["provider"]}
    run,participant,engine=session(s)
    s.update({"run":run,"participant":participant,"engine":engine,"stack":edge_stack})
    return s


def test_real_edge_model_proxy_authorizes_charges_and_stops_at_quota(run_setup):
    s=run_setup
    url=s["stack"]["urls"]["observer-model"]+"/v1/chat/completions"
    credential=f"obs_{s['run']}.{s['participant']}"
    body={"model":"test-model","messages":[{"role":"user","content":"Reply OK"}],"max_tokens":32}
    before=len(s["stack"]["requests"])
    assert post(url,body,"obs_"+str(s["run"])+"."+"z"*43)[0]==401
    assert len(s["stack"]["requests"])==before
    status,response=post(url,body,credential)
    assert status==200,response
    assert response["choices"][0]["message"]["content"]=="OK"
    received=s["stack"]["requests"][-1]
    assert received["auth"]=="Bearer "+s["stack"]["upstream_key"]
    assert received["body"]["max_tokens"]==32
    assert credential not in json.dumps(received)
    assert query(s["uri"],"select tokens_used,tokens_reserved,calls_used,calls_active from private.observer_sessions where run_id=%s",
                 (s["run"],))==[(14,0,1,0)]
    status,response=post(url,body,credential)
    assert status==429 and response["error"]["code"]=="run_model_quota"
    assert len(s["stack"]["requests"])==before+1


def test_real_edge_does_not_expose_privileged_methods(run_setup):
    s=run_setup
    client=SessionClient(s["stack"]["urls"]["observer-session"],f"obs_{s['run']}.{s['participant']}")
    with pytest.raises(SessionError,match="unknown_session_action"):
        client.call("observer_settle_model",p_call=str(uuid.uuid4()),p_actual_tokens=0)
    with pytest.raises(SessionError,match="invalid_or_expired_capability"):
        client.call("initialize",publication={"future":"forged"})
    with pytest.raises(SessionError,match="invalid_or_expired_capability"):
        client.call("finish",summary={"score":{"total":99999}},decisions_digest="a"*64,result_path="forged")


def test_large_public_catalog_crosses_real_edge_and_database_losslessly(run_setup):
    s = run_setup
    url = s["stack"]["urls"]["observer-session"]
    engine = SessionClient(url, f"obs_{s['run']}.{s['engine']}")
    participant = SessionClient(url, f"obs_{s['run']}.{s['participant']}")
    publication = {"target_catalog": [{"target_id": str(i), "value": hashlib.sha512(str(i).encode()).hexdigest()}
                                      for i in range(120000)],
                   "evaluation": {"instance_commitment": "public-commitment"}}
    assert len(json.dumps(publication)) > 16 * 1024 * 1024
    engine.call("initialize", publication=publication)
    engine.call("initialize", publication=publication)  # idempotent after compression
    assert participant.call("poll")["publication"] == publication
    participant.call("ready")
    assert engine.call("poll", scope="engine")["ready"]
    assert participant.call("poll", initialized=True)["publication"] is None


def test_real_portal_auth_private_keys_project_submission_and_team_isolation(run_setup):
    s=run_setup;url=s['stack']['urls']['observer-portal'];uri=s['uri']
    token=user_token(str(s['user']),f"{s['user']}@example.test")
    other,other_team=identity(uri)
    other_token=user_token(str(other),f'{other}@example.test')
    assert post(url,{'action':'list'},'invalid-token')[0]==401
    status,listed=post(url,{'action':'list'},token)
    assert status==200,listed
    # Saving on the server is the default for a team that has not chosen.
    assert listed['data']['team_model']=={'mode':'stored','protocol':'openai','saved':None}
    https_base=next(b for b in listed['data']['model_bases'] if b.startswith('https://'))
    http_base=next(b for b in listed['data']['model_bases'] if b.startswith('http://'))
    key='only-the-trusted-proxy-can-read-this-key-7Qx2'
    # The retired multi-provider settings still cannot store a key.
    status,saved=post(url,{'action':'save_provider','name':'My API','base_url':https_base,
        'key':key,'models':['test-model'],'daily_token_limit':5000},token)
    assert status==410 and saved['error']=='ephemeral_credentials_required'
    # Formal keys go only to approved HTTPS bases, never the organizer's HTTP test base.
    for base in (http_base,'https://unapproved.test/v1'):
        status,refused=post(url,{'action':'save_team_model','base_url':base,'model':'m','key':key},token)
        assert status==400 and refused['error']=='model_destination_not_enabled'
    assert query(uri,'select count(*) from private.observer_providers where team_id=%s',(s['team'],))==[(0,)]
    status,saved=post(url,{'action':'save_team_model','base_url':https_base+'/','model':'team-model','key':key},token)
    assert status==200,saved
    assert saved['data']['team_model']['saved']['key_hint']=='7Qx2' and key not in json.dumps(saved)
    stored=query(uri,'''select p.encrypted_key,p.base_url from private.observer_team_models m
        join private.observer_providers p on p.id=m.provider_id where m.team_id=%s''',(s['team'],))
    assert len(stored)==1 and stored[0][0].startswith('v1.') and key not in stored[0][0] and stored[0][1]==https_base
    status,listed=post(url,{'action':'list'},token)
    assert status==200 and key not in json.dumps(listed) and stored[0][0] not in json.dumps(listed)
    assert listed['data']['team_model']['saved']['model']=='team-model'
    status,foreign=post(url,{'action':'list'},other_token)
    assert status==200 and foreign['data']['team_model']=={'mode':'stored','protocol':'openai','saved':None}
    assert post(url,{'action':'delete_team_model'},other_token)[1]['data']=={'deleted':False}
    assert post(url,{'action':'set_team_model_mode','mode':'relay'},other_token)[1]['data']=={'mode':'relay'}
    assert post(url,{'action':'list'},token)[1]['data']['team_model']['saved'] is not None
    # Choosing not to save a key deletes the saved key immediately.
    assert post(url,{'action':'set_team_model_mode','mode':'relay'},token)[1]['data']=={'mode':'relay'}
    assert post(url,{'action':'list'},token)[1]['data']['team_model']=={'mode':'relay','protocol':'openai','saved':None}
    assert query(uri,'select count(*) from private.observer_providers where team_id=%s',(s['team'],))==[(0,)]
    assert post(url,{'action':'model_routes'},token)[1]['data']==[]
    assert post(url,{'action':'set_team_model_mode','mode':'organizer'},token)[1]['error']=='invalid_team_model_mode'
    # Saving a key again selects the stored mode; the team can also delete it.
    assert post(url,{'action':'save_team_model','base_url':https_base,'model':'team-model','key':key},token)[0]==200
    assert post(url,{'action':'list'},token)[1]['data']['team_model']['mode']=='stored'
    assert post(url,{'action':'delete_team_model'},token)[1]['data']=={'deleted':True}
    assert post(url,{'action':'list'},token)[1]['data']['team_model']=={'mode':'stored','protocol':'openai','saved':None}
    assert query(uri,'select count(*) from private.observer_providers where team_id=%s',(s['team'],))==[(0,)]
    status,submitted=post(url,{'action':'submit_repository','title':'Complete project','url':'https://github.com/owner/repo.git'},token)
    assert status==200,submitted
    revision=submitted['data']['revision_id']
    assert post(url,{'action':'approve','revision_id':revision,'digest':'a'*64},token)[0]==400
    assert post(url,{'action':'evidence','revision_id':revision,'notes':'Original architecture','code_url':''},token)[0]==200
    assert post(url,{'action':'evidence','revision_id':revision,'notes':'Foreign overwrite','code_url':''},other_token)[0]==400
    own=post(url,{'action':'list'},token)[1]['data']['projects']
    foreign=post(url,{'action':'list'},other_token)[1]['data']['projects']
    assert any(r['id']==revision for p in own for r in p['observer_revisions'])
    assert not any(r['id']==revision for p in foreign for r in p['observer_revisions'])


def test_formal_run_calls_the_saved_https_provider_without_page_or_organizer_fallback(run_setup):
    s=run_setup;stack=s['stack'];uri=s['uri'];provider=stack['team_provider']
    if provider is None:
        pytest.skip('openssl is required for the HTTPS provider stub')
    portal=stack['urls']['observer-portal'];model_url=stack['urls']['observer-model']+'/v1/chat/completions'
    token=user_token(str(s['user']),f"{s['user']}@example.test")
    credential=f"obs_{s['run']}.{s['participant']}"
    # The run becomes formal, like the competition phase. No page is open anywhere.
    query(uri,'update public.phases set counts_for_final=true where id=%s',(s['phase'],))
    query(uri,'update private.observer_sessions set call_limit=10 where run_id=%s',(s['run'],))
    body={'model':'project-default','messages':[{'role':'user','content':'Reply OK'}],'max_tokens':32}
    organizer=len(stack['requests'])
    # The team opts in to a saved key but has not saved one yet.
    assert post(portal,{'action':'set_team_model_mode','mode':'stored'},token)[1]['data']=={'mode':'stored'}
    status,response=post(model_url,body,credential)
    assert status==403 and response['error']['code']=='team_model_not_configured'
    assert len(stack['requests'])==organizer and provider.requests==[]
    key='formal-team-key-'+secrets.token_hex(16)
    status,saved=post(portal,{'action':'save_team_model','base_url':stack['team_base'],'model':'team-model-v1','key':key},token)
    assert status==200,saved
    provider.mode='echo'
    try:
        status,response=post(model_url,body,credential)
        assert status==200,response
        sent=provider.requests[-1]
        assert (sent['path'],sent['auth'])==('/v1/chat/completions','Bearer '+key)
        # The project's own model name reaches the team's provider unchanged.
        assert sent['body']['model']=='project-default' and sent['body']['max_tokens']==32
        assert s['participant'] not in json.dumps(sent)
        assert key not in json.dumps(response) and '[REDACTED]' in response['choices'][0]['message']['content']
        assert query(uri,'select tokens_used,calls_used,calls_active from private.observer_sessions where run_id=%s',
                     (s['run'],))==[(17,1,0)]
        # The agent picks the model per call on the team's own key; team-model means the default.
        for model,expected in (('fast-model','fast-model'),('team-model','team-model-v1')):
            status,response=post(model_url,{**body,'model':model},credential)
            assert status==200,response
            assert provider.requests[-1]['body']['model']==expected
        # A model the provider does not know is retried once with the default model.
        count=len(provider.requests)
        status,response=post(model_url,{**body,'model':'unknown-model'},credential)
        assert status==200,response
        assert [r['body']['model'] for r in provider.requests[count:]]==['unknown-model','team-model-v1']
        status,response=post(model_url,{**body,'model':'bad\nmodel'},credential)
        assert (status,response['error']['code'])==(400,'invalid_model')
        # Redirects are never followed and provider rejections are never forwarded.
        for mode,code in (('redirect','model_provider_unavailable'),('reject','model_provider_error')):
            provider.mode=mode;count=len(provider.requests)
            status,response=post(model_url,body,credential)
            assert (status,response['error']['code'])==(502,code),response
            assert key not in json.dumps(response) and len(provider.requests)==count+1
    finally:
        provider.mode='ok'
    # Deleting the key stops formal model use immediately; nothing falls back.
    assert post(portal,{'action':'delete_team_model'},token)[1]['data']=={'deleted':True}
    count=len(provider.requests)
    status,response=post(model_url,body,credential)
    assert status==403 and response['error']['code']=='team_model_not_configured'
    assert len(provider.requests)==count and len(stack['requests'])==organizer
    # Relay mode: the call waits for the team's open page. This harness has no
    # Realtime service, so the relay fails closed without any server-side call.
    post(portal,{'action':'save_team_model','base_url':stack['team_base'],'model':'team-model-v1','key':key},token)
    assert post(portal,{'action':'set_team_model_mode','mode':'relay'},token)[1]['data']=={'mode':'relay'}
    assert query(uri,'select count(*) from private.observer_team_models where team_id=%s',(s['team'],))==[(0,)]
    status,response=post(model_url,body,credential,timeout=150)
    assert status in (502,503) and response['error']['code'] in ('model_relay_unavailable','personal_api_not_connected'),response
    assert len(provider.requests)==count and len(stack['requests'])==organizer
    relay=query(uri,'select status from private.observer_personal_model_calls where run_id=%s',(s['run'],))
    assert relay==[('timeout',)]
    # The plaintext key was never persisted in any table or written to an Edge log.
    tables=query(uri,"""select table_schema,table_name from information_schema.tables
        where table_schema in ('public','private') and table_type='BASE TABLE'""")
    for schema,table in tables:
        rows=query(uri,f'select coalesce(string_agg(t::text,chr(10)),\'\') from "{schema}"."{table}" t')[0][0]
        assert key not in rows,(schema,table)
    for log in stack['logs'].glob('*.log'):
        assert key not in log.read_text()


def test_formal_run_calls_the_saved_anthropic_provider_with_x_api_key_and_no_translation(run_setup):
    s=run_setup;stack=s['stack'];uri=s['uri'];provider=stack['team_provider']
    if provider is None:
        pytest.skip('openssl is required for the HTTPS provider stub')
    portal=stack['urls']['observer-portal']
    messages_url=stack['urls']['observer-model']+'/v1/messages'
    chat_url=stack['urls']['observer-model']+'/v1/chat/completions'
    token=user_token(str(s['user']),f"{s['user']}@example.test")
    credential=f"obs_{s['run']}.{s['participant']}"
    query(uri,'update public.phases set counts_for_final=true where id=%s',(s['phase'],))
    query(uri,'update private.observer_sessions set call_limit=10 where run_id=%s',(s['run'],))
    body={'model':'project-default','messages':[{'role':'user','content':'Reply OK'}],'max_tokens':32}
    key='formal-anthropic-key-'+secrets.token_hex(16)
    status,saved=post(portal,{'action':'save_team_model','base_url':stack['team_base'],'model':'claude-team-v1',
        'key':key,'protocol':'anthropic'},token)
    assert status==200 and saved['data']['team_model']['protocol']=='anthropic',saved
    provider.mode='echo'
    try:
        # The official Anthropic SDK's own header: a bare key, no "Bearer " scheme.
        req=urllib.request.Request(messages_url,data=json.dumps(body).encode(),
            headers={'x-api-key':credential,'content-type':'application/json','anthropic-version':'2023-06-01'},
            method='POST')
        try:
            with urllib.request.urlopen(req,timeout=30) as response:
                status,result=response.status,json.load(response)
        except urllib.error.HTTPError as exc:
            status,result=exc.code,json.load(exc)
        assert status==200,result
        sent=provider.requests[-1]
        assert sent['path']=='/v1/messages'
        assert sent['api_key']==key and sent['anthropic_version']=='2023-06-01' and not sent['auth']
        assert sent['body']['model']=='project-default' and sent['body']['max_tokens']==32
        assert s['participant'] not in json.dumps(sent)
        assert key not in json.dumps(result) and '[REDACTED]' in result['content'][0]['text']
        assert query(uri,'select tokens_used,calls_used,calls_active from private.observer_sessions where run_id=%s',
                     (s['run'],))==[(17,1,0)]
        # No translation between formats: an OpenAI-shaped call to an anthropic-protocol
        # provider is refused before any reservation, so the quota above is unchanged.
        status,mismatch=post(chat_url,body,credential)
        assert status==400 and mismatch['error']['code']=='protocol_mismatch',mismatch
        assert query(uri,'select tokens_used,calls_used,calls_active from private.observer_sessions where run_id=%s',
                     (s['run'],))==[(17,1,0)]
    finally:
        provider.mode='ok'
    # Saving an openai-protocol key instead makes a Messages call the mismatch.
    assert post(portal,{'action':'save_team_model','base_url':stack['team_base'],'model':'team-model-v1',
        'key':key},token)[0]==200
    req=urllib.request.Request(messages_url,data=json.dumps(body).encode(),
        headers={'x-api-key':credential,'content-type':'application/json'},method='POST')
    try:
        urllib.request.urlopen(req,timeout=30)
        assert False,'expected protocol_mismatch'
    except urllib.error.HTTPError as exc:
        assert exc.code==400 and json.load(exc)['error']['code']=='protocol_mismatch'
    assert post(portal,{'action':'delete_team_model'},token)[1]['data']=={'deleted':True}


def test_real_portal_signed_zip_upload_and_retry(run_setup):
    s=run_setup;url=s['stack']['urls']['observer-portal'];uri=s['uri']
    token=user_token(str(s['user']),f"{s['user']}@example.test")
    status,response=post(url,{'action':'upload','purpose':'source'},token)
    assert status==200,response
    slot=response['data']
    assert post(url,{'action':'submit_zip','upload_id':slot['id'],'title':'Not uploaded'},token)[0]==400
    raw=pack_files((ProjectFile('agent.py',b"print('project')"),))
    signed=s['stack']['harness'].url+'/storage/v1/object/upload/sign/observer-staging/'+slot['path']+'?token='+slot['token']
    req=urllib.request.Request(signed,data=raw,method='PUT',headers={'Content-Type':'application/zip'})
    with urllib.request.urlopen(req) as reply:assert reply.status==200
    with pytest.raises(urllib.error.HTTPError) as exc:urllib.request.urlopen(req)
    assert exc.value.code==409
    body={'action':'submit_zip','upload_id':slot['id'],'title':'ZIP project'}
    status,first=post(url,body,token);assert status==200,first
    status,second=post(url,body,token);assert status==200 and first==second
    assert query(uri,'select count(*) from public.observer_revisions where source_location=%s',(slot['path'],))==[(1,)]


def test_real_portal_hashes_csv_bytes_and_result_download_is_team_scoped(run_setup):
    s=run_setup;url=s['stack']['urls']['observer-portal'];uri=s['uri'];h=s['stack']['harness']
    token=user_token(str(s['user']),f"{s['user']}@example.test")
    other,_=identity(uri);other_token=user_token(str(other),f'{other}@example.test')
    raw=b'night,action\n2026-10-05,wait\n';digest=hashlib.sha256(raw).hexdigest()
    result_path=f"{s['team']}/{s['run']}/result.zip"
    result_file=h.storage_root/'observer-staging'/result_path;result_file.parent.mkdir(parents=True)
    result_file.write_bytes(pack_files((ProjectFile('decisions.csv',raw),)))
    query(uri,"""update public.observer_runs set status='awaiting_csv',score=10,decisions_digest=%s,
          result_path=%s,finished_at=now() where id=%s""",(digest,result_path,s['run']))
    def upload(data):
        status,response=post(url,{'action':'upload','purpose':'csv'},token);assert status==200,response
        slot=response['data']
        signed=h.url+'/storage/v1/object/upload/sign/observer-staging/'+slot['path']+'?token='+slot['token']
        request=urllib.request.Request(signed,data=data,method='PUT',headers={'Content-Type':'text/csv'})
        with urllib.request.urlopen(request) as reply:assert reply.status==200
        return slot['id']
    bad=upload(b'fake csv')
    status,response=post(url,{'action':'accept_csv','run_id':str(s['run']),'upload_id':bad,'digest':digest},token)
    assert status==400 and response['error']=='csv_does_not_match_session'
    good=upload(raw)
    assert post(url,{'action':'accept_csv','run_id':str(s['run']),'upload_id':good},other_token)[0]==404
    assert post(url,{'action':'accept_csv','run_id':str(s['run']),'upload_id':good},token)[0]==200
    assert query(uri,'select status from public.observer_runs where id=%s',(s['run'],))==[('scored',)]
    assert post(url,{'action':'download_result','run_id':str(s['run'])},other_token)[0]==404
    status,response=post(url,{'action':'download_result','run_id':str(s['run'])},token)
    assert status==200,response
    with urllib.request.urlopen(response['data']['url']) as reply:
        assert next(f.data for f in read_project_zip(reply.read()) if f.path=='decisions.csv')==raw
    # The executor's stored log completes the download; the result itself is unchanged.
    log=h.storage_root/'observer-staging'/'agent-logs'/str(s['run'])/'agent-log.zip';log.parent.mkdir(parents=True)
    log.write_bytes(pack_files((ProjectFile('agent.log',b'[platform] project stderr\nTraceback: boom\n'),)))
    assert post(url,{'action':'download_result','run_id':str(s['run'])},other_token)[0]==404
    status,response=post(url,{'action':'download_result','run_id':str(s['run'])},token)
    assert status==200,response
    with urllib.request.urlopen(response['data']['url']) as reply:
        files={f.path:f.data for f in read_project_zip(reply.read())}
    assert files=={'decisions.csv':raw,'agent.log':b'[platform] project stderr\nTraceback: boom\n'}
    assert result_file.read_bytes()==pack_files((ProjectFile('decisions.csv',raw),))


def test_real_portal_hides_sealed_final_results_until_published(run_setup):
    s=run_setup;url=s['stack']['urls']['observer-portal'];uri=s['uri'];h=s['stack']['harness']
    token=user_token(str(s['user']),f"{s['user']}@example.test")
    result_path=f"{s['team']}/{s['run']}/result.zip"
    result_file=h.storage_root/'observer-staging'/result_path;result_file.parent.mkdir(parents=True)
    result_file.write_bytes(pack_files((ProjectFile('decisions.csv',b'night,action\n'),)))
    query(uri,"update public.observer_runs set status='scored',score=10,result_path=%s,finished_at=now() where id=%s",
          (result_path,s['run']))
    query(uri,"update public.observer_phase_settings set sealed=true where phase_id=%s",(s['phase'],))
    query(uri,"update public.phases set leaderboard_mode='hidden',counts_for_final=true where id=%s",(s['phase'],))
    assert post(url,{'action':'download_result','run_id':str(s['run'])},token)==(404,{'error':'result_not_ready'})
    assert post(url,{'action':'diagnostics','run_id':str(s['run'])},token)==(400,{'error':'diagnostics_not_found'})
    status,listed=post(url,{'action':'list'},token);assert status==200,listed
    assert not listed['data']['batches'] and all(p['phase_id']!=str(s['phase']) for p in listed['data']['phases'])
    assert all(f['phase_id']!=str(s['phase']) for f in listed['data']['final_versions'])
    query(uri,"update public.phases set leaderboard_mode='published' where id=%s",(s['phase'],))
    status,response=post(url,{'action':'download_result','run_id':str(s['run'])},token)
    assert status==200,response
    status,listed=post(url,{'action':'list'},token)
    assert [b['observer_runs'][0]['id'] for b in listed['data']['batches']]==[str(s['run'])]


def test_real_portal_sets_and_lists_the_final_version(run_setup):
    from test_project_eval_ux import revision
    s=run_setup;url=s['stack']['urls']['observer-portal'];uri=s['uri']
    token=user_token(str(s['user']),f"{s['user']}@example.test")
    query(uri,"update public.phases set counts_for_final=true,ends_at=now()+interval '1 day' where id=%s",(s['phase'],))
    rev=revision(s)
    status,response=post(url,{'action':'set_final_version','phase_id':str(s['phase']),'revision_id':str(rev)},token)
    assert status==200 and response['data']['final_version']['revision_id']==str(rev),response
    status,listed=post(url,{'action':'list'},token)
    mine=next(f for f in listed['data']['final_versions'] if f['phase_id']==str(s['phase']))
    assert (mine['revision_id'],mine['source'],mine['locked'])==(str(rev),'chosen',False)
    query(uri,"update public.phases set ends_at=now()-interval '1 second' where id=%s",(s['phase'],))
    assert post(url,{'action':'set_final_version','phase_id':str(s['phase'])},token)==(400,{'error':'final_version_locked'})


def test_local_cli_and_scoped_credential_export_exact_official_csv(run_setup,tmp_path):
    s=run_setup;uri=s['uri'];url=s['stack']['urls']['observer-session']
    # Retire the fixture's direct session. This journey starts through the real
    # portal, then the actual TypeScript scheduler opens the new session via HTTP.
    SessionClient(url,f"obs_{s['run']}.{s['engine']}").call('fail',error='fixture_replaced')
    user=user_token(str(s['user']),f"{s['user']}@example.test")
    portal_url=s['stack']['urls']['observer-portal']
    status,created=post(portal_url,{'action':'evaluate','phase_id':str(s['phase'])},user)
    assert status==200
    s['run']=query(uri,'select id from public.observer_runs where batch_id=%s',(created['data']['batch_id'],))[0][0]
    query(uri,'insert into private.observer_scenario_bundles values(%s,%s,%s)',
          (s['scenario'],f"{s['scenario']}/bundle.zip",'a'*64))
    for i in range(1,7):
        query(uri,"""insert into private.observer_installations
          (organization,organization_id,installation_id,repository_id,approved_sha,enabled)
          values(%s,%s,%s,%s,%s,true) on conflict(organization) do update set enabled=true""",
          (f'AGENTIC-OBSERVER26-runner-{i}',str(100+i),200+i,str(300+i),'b'*40))
    scheduled=subprocess.run([os.environ['OBSERVER_DENO_BIN'],'eval','''
import {scheduleRuns} from './_shared/observer-orchestrate.ts';
import {decryptCredential} from './_shared/observer-model.ts';
const d=await new Response(Deno.stdin.readable).json();
let input;
const rpc=async(name,args)=>{
 const response=await fetch(d.url+'/rest/v1/rpc/'+name,{method:'POST',
  headers:{authorization:'Bearer '+d.service,apikey:d.service,'content-type':'application/json'},body:JSON.stringify(args)});
 const body=await response.text(); const data=body?JSON.parse(body):null;
 if(!response.ok)throw new Error(JSON.stringify(data));
 if(name==='observer_schedule_run')input=JSON.parse(await decryptCredential(args.p_jobs[0].encrypted_input,args.p_jobs[0].id,d.master));
 return data;
};
const outcomes=await scheduleRuns({rpc,masterKey:d.master,apiBase:'https://platform.test',ensureRepository:async()=>{}});
console.log(JSON.stringify({outcomes,input}));
'''],input=json.dumps({'url':s['stack']['harness'].url,'service':service_key(),'master':s['stack']['master']}),
        cwd=ROOT/'supabase/functions',capture_output=True,text=True,timeout=30)
    assert scheduled.returncode==0,scheduled.stderr
    result=json.loads(scheduled.stdout)
    assert {'id':str(s['run']),'scheduled':True} in result['outcomes']
    assert result['input']['kind']=='engine'
    s['engine']=result['input']['run_credential'].split('.',1)[1]
    assert query(uri,'select kind from private.observer_jobs where run_id=%s',(s['run'],))==[('engine',)]
    status,access=post(portal_url,{'action':'local_access','run_id':str(s['run'])},user)
    assert status==200
    credential=access['data']['credential']
    assert credential.startswith(f"obs_{s['run']}.")
    assert s['engine'] not in json.dumps(access)
    other,_=identity(uri)
    assert post(portal_url,{'action':'local_access','run_id':str(s['run'])},user_token(str(other),f'{other}@example.test'))[0]==409
    scenario=tmp_path/'hidden';generate_scenario(scenario,scenario_id='native-local',seed=9,days=7,
        start_date='2026-10-05',global_wallclock_seconds=120)
    project=tmp_path/'local-project';project.mkdir()
    (project/'observer.project.json').write_text(json.dumps({'schema_version':'observer-project-v1','image':'python:3.12',
        'run':[sys.executable,'agent.py']}))
    (project/'agent.py').write_text('''import json,sys
for line in sys.stdin:
 m=json.loads(line)
 if m['message_type']=='initialize':continue
 print(json.dumps({'protocol_version':m['protocol_version'],'message_type':'decision_response',
   'decision_sequence':m['decision_sequence'],'action':'wait'}),flush=True)
''')
    official=tmp_path/'official';local=tmp_path/'decisions.csv'
    def trusted():
        client=SessionClient(url,f"obs_{s['run']}.{s['engine']}")
        result,digest=run_session(scenario,official,client,wallclock_seconds=120)
        client.call('finish',summary=result_summary(result),decisions_digest=digest,result_path='private/native-test')
        return digest
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(trusted)
        environment={k:os.environ[k] for k in ('PATH','HOME','TMPDIR') if k in os.environ}
        environment['OBSERVER_RUN_TOKEN']=credential
        # The host's system proxy must not intercept disposable loopback services.
        environment['NO_PROXY']='127.0.0.1,localhost'
        environment['no_proxy']='127.0.0.1,localhost'
        process=subprocess.run([sys.executable,'-m','project_platform.local','--native','--project',str(project),
            '--session-url',url,'--model-base-url',s['stack']['urls']['observer-model']+'/v1','--output',str(local)],
            cwd=ROOT,env=environment,capture_output=True,text=True,timeout=150)
        if process.returncode:
            SessionClient(url,f"obs_{s['run']}.{s['engine']}").call('fail',error='local_test_failed')
        assert process.returncode==0,process.stdout+process.stderr
        digest=future.result(timeout=5)
    assert credential not in process.stdout+process.stderr
    assert local.read_bytes()==(official/'decisions.csv').read_bytes()
    assert hashlib.sha256(local.read_bytes()).hexdigest()==digest
    assert post(portal_url,{'action':'local_access','run_id':str(s['run'])},user)[0]==409
    rpc(uri,'observer_accept_csv',s['run'],s['user'],digest)


@pytest.mark.skipif(not os.environ.get("OBSERVER_LIVE_MODEL_KEY"),reason="Opt-in live provider test; consumes a small bounded request")
def test_live_model_through_real_proxy_and_accounting(run_setup):
    s=run_setup; provider=uuid.uuid4()
    base=os.environ["OBSERVER_LIVE_MODEL_BASE"].rstrip("/")
    model=os.environ["OBSERVER_LIVE_MODEL_NAME"]
    # Secrets are passed on stdin/in memory, never arguments, source or test logs.
    payload={"key":os.environ["OBSERVER_LIVE_MODEL_KEY"],"master":s["stack"]["master"],"provider":str(provider)}
    proc=subprocess.run([os.environ["OBSERVER_DENO_BIN"],"eval",
        'import {encryptCredential} from "./_shared/observer-model.ts"; '
        'const data=await new Response(Deno.stdin.readable).json(); '
        'console.log(await encryptCredential(data.key,data.provider,data.master));'],
        input=json.dumps(payload),cwd=ROOT/"supabase/functions",capture_output=True,text=True,check=True)
    query(s["uri"],"""insert into private.observer_providers
        (id,name,base_url,encrypted_key,models,allow_http,enabled,daily_token_limit)
        values(%s,'Opt-in live test',%s,%s,%s,%s,true,5000)""",
        (provider,base,proc.stdout.strip(),[model],base.startswith("http://")))
    url=s["stack"]["urls"]["observer-model"]+"/v1/chat/completions"
    credential=f"obs_{s['run']}.{s['participant']}"
    status,response=post(url,{"model":str(provider)+"::"+model,
        "messages":[{"role":"user","content":"Reply only OK."}],"max_tokens":32,
        "chat_template_kwargs":{"enable_thinking":False}},credential,timeout=135)
    assert status==200,{"status":status,"error":response.get("error")}
    assert response["choices"][0]["message"]["content"].strip()=="OK"
    used,reserved,calls=query(s["uri"],"select tokens_used,tokens_reserved,calls_used from private.observer_sessions where run_id=%s",
                              (s["run"],))[0]
    assert 0<used<5000 and reserved==0 and calls==1


@pytest.mark.skipif(not os.environ.get("OBSERVER_LIVE_MODEL_KEY") or not os.environ.get("OBSERVER_TEST_PYTHON_IMAGE"),
                   reason="Opt-in live adaptation and isolated container test")
def test_live_model_adapter_calls_original_strategy_in_container(run_setup,tmp_path):
    from dataclasses import replace
    s=run_setup; provider=uuid.uuid4()
    base=os.environ["OBSERVER_LIVE_MODEL_BASE"].rstrip("/")
    model=os.environ["OBSERVER_LIVE_MODEL_NAME"]
    payload={"key":os.environ["OBSERVER_LIVE_MODEL_KEY"],"master":s["stack"]["master"],"provider":str(provider)}
    proc=subprocess.run([os.environ["OBSERVER_DENO_BIN"],"eval",
        'import {encryptCredential} from "./_shared/observer-model.ts"; '
        'const data=await new Response(Deno.stdin.readable).json(); '
        'console.log(await encryptCredential(data.key,data.provider,data.master));'],
        input=json.dumps(payload),cwd=ROOT/"supabase/functions",capture_output=True,text=True,check=True)
    query(s["uri"],"""insert into private.observer_providers
        (id,name,base_url,encrypted_key,models,allow_http,enabled,daily_token_limit)
        values(%s,'Opt-in adapter test',%s,%s,%s,%s,true,20000)""",
        (provider,base,proc.stdout.strip(),[model],base.startswith("http://")))
    query(s["uri"],"update private.observer_sessions set token_limit=20000 where run_id=%s",(s["run"],))
    client=ModelClient(s["stack"]["urls"]["observer-model"]+"/v1",f"obs_{s['run']}.{s['participant']}")
    source=(ProjectFile("README.md",b"Python agent. Call agent.choose(snapshot). No dependencies outside the standard library."),
            ProjectFile("agent.py",b"from pathlib import Path\ndef choose(snapshot):\n"
              b"    if snapshot.get('force_error'): raise RuntimeError('participant failure')\n"
              b"    Path('called.txt').write_text(str(snapshot['decision_sequence']))\n"
              b"    return {'action':'wait','reason':'participant-policy-v1'}\n"))
    proposal=propose_adapter(source,str(provider)+"::"+model,
        lambda body:client({**body,"chat_template_kwargs":{"enable_thinking":False}}))
    # Image resolution is recorded before review; this fixture supplies a pulled
    # Python image instead of fetching a mutable model-suggested image tag.
    assert "python" in proposal.manifest.image
    proposal=replace(proposal,manifest=replace(proposal.manifest,image=os.environ["OBSERVER_TEST_PYTHON_IMAGE"]))
    workspace=tmp_path/"adapted-project"
    extract_project(proposal.materialize(source,confirmed_digest=proposal.digest),workspace)
    with DockerWorkspace(workspace,proposal.manifest,proposal.manifest.image) as runtime:
        try:
            runtime.build()
        except Exception as exc:
            pytest.fail(f"{exc}\n{runtime.build_log}")
        transport=runtime.start({})
        transport.publish_initial({"tiles":[]})
        response=transport({"decision_sequence":1,"candidate_tiles":[]},time.monotonic()+30)
        assert response["action"]=="wait" and response["reason"]=="participant-policy-v1"
        from project_platform.transport import ExecutionError
        with pytest.raises(ExecutionError):
            transport({"decision_sequence":2,"candidate_tiles":[],"force_error":True},time.monotonic()+10)
    assert (workspace/"called.txt").read_text()=="1"
    assert (workspace/"agent.py").read_bytes()==source[1].data
    calls=query(s["uri"],"select calls_used from private.observer_sessions where run_id=%s",(s["run"],))[0][0]
    assert calls==1


@pytest.mark.skipif(not os.environ.get("OBSERVER_TEST_PYTHON_IMAGE"),reason="Explicit resolved container image required")
@pytest.mark.parametrize('expire',[False,True])
def test_container_to_real_edge_session_to_trusted_scoring_and_csv(run_setup,tmp_path,expire):
    s=run_setup
    if expire:query(s['uri'],'update public.observer_phase_settings set runtime_seconds=10 where phase_id=%s',(s['phase'],))
    scenario=tmp_path/"hidden-scenario"
    generate_scenario(scenario,scenario_id="session-integration",seed=71,days=7,
                      start_date="2026-10-05",global_wallclock_seconds=120)
    workspace=tmp_path/"project"; workspace.mkdir()
    (workspace/"agent.py").write_text("""
import json,sys,time
for line in sys.stdin:
 m=json.loads(line)
 if m['message_type']=='initialize': continue
 DELAY
 tiles=[t for t in m['payload']['candidate_tiles'] if t['effective_weather']['is_observable']]
 t=tiles[0] if tiles else None
 print(json.dumps({'protocol_version':m['protocol_version'],'message_type':'decision_response',
 'decision_sequence':m['decision_sequence'],'action':'observe' if t else 'wait',
 'tile_id':t['tile_id'] if t else '', 'program':'BACKUP' if t else ''}),flush=True)
""".replace(' DELAY',' time.sleep(0.2)' if expire else ' pass'))
    parsed=ProjectManifest.parse({"schema_version":"observer-project-v1","image":os.environ["OBSERVER_TEST_PYTHON_IMAGE"],
                                  "run":["python3","-u","agent.py"]})
    url=s["stack"]["urls"]["observer-session"]
    engine=SessionClient(url,f"obs_{s['run']}.{s['engine']}")
    participant=SessionClient(url,f"obs_{s['run']}.{s['participant']}")
    output=tmp_path/"trusted-result"
    def trusted():
        try:
            # Generous for slow CI hosts; the expiring case uses the 10 s phase limit.
            result,digest=run_session(scenario,output,engine,wallclock_seconds=300)
            assert result["termination_reason"]==('global_wallclock_expired' if expire else 'survey_complete'),result["commit_log"][-3:]
            engine.call("finish",summary=result_summary(result),decisions_digest=digest,result_path="private/test-artifact")
            return result,digest
        except Exception:
            engine.call("fail",error="integration_engine_failed")
            raise
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        future=pool.submit(trusted)
        with DockerWorkspace(workspace,parsed,parsed.image) as runtime:
            outcome=execute(runtime,participant,{},startup_seconds=30)
        result,digest=future.result(timeout=10)
    assert outcome["status"]=="awaiting_csv"
    scored=score_files(scenario,output/"decisions.csv",output/"official-score.json",result["termination_reason"])
    assert scored["score"]["total"]==pytest.approx(result["score_report"]["score"]["total"])
    assert digest==hashlib.sha256((output/"decisions.csv").read_bytes()).hexdigest()
    assert query(s["uri"],"select count(*) from private.observer_messages where run_id=%s and committed is not null",
                 (s["run"],))[0][0]==len(result["commit_log"])
    rpc(s["uri"],"observer_accept_csv",s["run"],s["user"],digest)
    board=query(s["uri"],"select team_id,score from public.observer_leaderboard(%s)",(s["phase"],),role="anon")
    assert board==[(s["team"],scored["score"]["total"])]


@pytest.mark.skipif(not os.environ.get("OBSERVER_TEST_PYTHON_IMAGE"),reason="Explicit resolved container image required")
@pytest.mark.parametrize('storage_kind,randomized,colocated',[('staging',False,False),('github',False,False),('github',True,False),('github',False,True)])
def test_workflow_jobs_download_run_and_upload_before_publishing_score(run_setup,tmp_path,monkeypatch,storage_kind,randomized,colocated):
    """Both production handlers, private downloads, actual Docker, Edge and scorer.

    GitHub OIDC signatures are covered by the TypeScript tests. This fixture uses
    a local job broker but the real Python claim/receipt HTTP client.
    """
    from project_platform.manifest import MANIFEST_NAME
    s=run_setup
    scenario=tmp_path/"original-scenario"
    generate_scenario(scenario,scenario_id="job-integration",seed=83,days=7,
                      start_date="2026-10-05",global_wallclock_seconds=120)
    scenario_zip=pack_files(tuple(ProjectFile(p.relative_to(scenario).as_posix(),p.read_bytes())
                                  for p in scenario.rglob("*") if p.is_file()))
    instance = None
    if randomized:
        from project_platform.scenario_instances import directory_digest, POLICIES
        # A fresh phase exercises the real configuration and seed-allocation
        # path, without mutating an already admitted fixed-scenario run.
        s = {**s, 'phase': uuid.uuid4()}
        s['user'], s['team'] = identity(s['uri'])
        query(s['uri'], "insert into public.phases(id,slug,name_en,name_zh) values(%s,%s,'Random','Random')",
              (s['phase'], str(s['phase'])))
        query(s['uri'], 'insert into public.phase_scenarios values(%s,%s)', (s['phase'], s['scenario']))
        query(s['uri'], 'insert into public.observer_phase_settings(phase_id,local_sessions_enabled,runtime_seconds) values(%s,true,120)', (s['phase'],))
        bundle = hashlib.sha256(scenario_zip).hexdigest()
        query(s['uri'], "insert into private.observer_scenario_bundles values(%s,'fixture/template.zip',%s)", (s['scenario'], bundle))
        profile = {'schema_version':'observer-calibration-profile-v1', 'panel_version':'observer-reference-panel-v1',
            'template_digest':directory_digest(scenario), 'bounds':{'span':[1,1e9],'open_fraction':[0,1],
            **{name:[-1e9,1e9] for name in POLICIES}}}
        profile_id = query(s['uri'], 'insert into private.observer_calibration_profiles(scenario_id,bundle_digest,profile) values(%s,%s,%s) returning id',
            (s['scenario'], bundle, Jsonb(profile)))[0][0]
        query(s['uri'], 'insert into private.observer_scenario_calibration values(%s,%s,%s)', (s['phase'],s['scenario'],profile_id))
        run, participant, engine = session(s)
        s.update(run=run, participant=participant, engine=engine)
        instance = rpc(s['uri'], 'observer_instance_input', run)
    manifest=ProjectManifest.parse({"schema_version":"observer-project-v1","image":os.environ["OBSERVER_TEST_PYTHON_IMAGE"],
                                   "run":["python3","-u","agent.py"]})
    source=(ProjectFile(MANIFEST_NAME,manifest.canonical_bytes()),ProjectFile("agent.py",b'''
import json,os,sys
assert not any(key in os.environ for key in ('GITHUB_TOKEN','SUPABASE_SERVICE_ROLE_KEY','ACTIONS_ID_TOKEN_REQUEST_TOKEN'))
print('agent booted with', os.environ['OBSERVER_RUN_TOKEN'], file=sys.stderr, flush=True)
for line in sys.stdin:
 m=json.loads(line)
 if m['message_type']=='initialize': continue
 tiles=[t for t in m['payload']['candidate_tiles'] if t['effective_weather']['is_observable']]
 t=tiles[0] if tiles else None
 print(json.dumps({'protocol_version':m['protocol_version'],'message_type':'decision_response',
 'decision_sequence':m['decision_sequence'],'action':'observe' if t else 'wait',
 'tile_id':t['tile_id'] if t else '', 'program':'BACKUP' if t else ''}),flush=True)
'''))
    project_zip=pack_files(source)
    ids={kind:str(uuid.uuid4()) for kind in ("execute","engine")}
    jobs={};receipts={};uploads=[];requests=[];credential_requests=[];agent_logs={}
    session_url=s["stack"]["urls"]["observer-session"]
    result_path=f"{s['team']}/{s['run']}/result.zip"
    if storage_kind=='github':
        import project_platform.artifacts as artifacts
        from test_project_repository import LocalSnapshot
        remote=tmp_path/'private-results.git'
        subprocess.run(['git','init','--bare',str(remote)],capture_output=True,check=True)
        repository=LocalSnapshot(remote)
        def scoped_store(name,token):
            assert name==repository.full_name and token=='fresh-artifact-token'
            return repository
        monkeypatch.setattr(artifacts,'SnapshotRepository',scoped_store)
    class Broker(BaseHTTPRequestHandler):
        def reply(self,status,value):
            raw=value if isinstance(value,bytes) else json.dumps(value).encode()
            self.send_response(status);self.send_header("Content-Length",str(len(raw)))
            self.end_headers();self.wfile.write(raw)
        def do_GET(self):
            requests.append((self.path,self.headers.get("Authorization")))
            self.reply(200,scenario_zip if self.path=="/scenario" else project_zip)
        def do_POST(self):
            assert self.headers["Authorization"]=="Bearer test-job-identity"
            body=json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if body["action"]=="claim":
                self.reply(200,{"data":jobs[body["job_id"]]})
            elif body['action']=='agent_log':
                # Sent by the executor only, before its receipt, with its claim nonce.
                assert body['job_id']==ids['execute'] and body['job_id'] not in receipts and body['nonce']=='n'*43
                agent_logs[body['job_id']]=base64.b64decode(body['log']).decode()
                self.reply(200,{'data':{'accepted':True}})
            elif body['action']=='artifact_repository':
                assert body['job_id']==ids['engine']
                assert query(s['uri'],'select score from public.observer_runs where id=%s',(s['run'],))==[(None,)]
                credential_requests.append(body['job_id'])
                self.reply(200,{'data':{'full_name':repository.full_name,'token':'fresh-artifact-token',
                    'artifact_id':str(s['run']),'kind':'engine'}})
            else:
                receipts[body["job_id"]]=body
                self.reply(200,{"data":{"accepted":True}})
        def do_PUT(self):
            # The public score must still be absent while its private result is
            # being stored. Publishing a score before upload would lose evidence.
            assert query(s["uri"],"select score from public.observer_runs where id=%s",(s["run"],))==[(None,)]
            uploads.append(self.rfile.read(int(self.headers["Content-Length"])))
            self.reply(200,{})
        def log_message(self,*_args):pass
    broker=ThreadingHTTPServer(("127.0.0.1",0),Broker)
    threading.Thread(target=broker.serve_forever,daemon=True).start()
    base=f"http://127.0.0.1:{broker.server_port}"
    jobs[ids["execute"]]={"kind":"execute","job_id":ids["execute"],"run_id":str(s["run"]),
        "archive_url":base+"/project","source_digest":project_digest(source),"manifest":manifest.as_dict(),
        "session_url":session_url,"run_credential":f"obs_{s['run']}.{s['participant']}",
        "model_base_url":s["stack"]["urls"]["observer-model"]+"/v1"}
    jobs[ids["engine"]]={"kind":"engine","job_id":ids["engine"],"run_id":str(s["run"]),
        "scenario_url":base+"/scenario","scenario_digest":hashlib.sha256(scenario_zip).hexdigest(),
        "session_url":session_url,"run_credential":f"obs_{s['run']}.{s['engine']}","runtime_seconds":120,
        "artifact_upload":{"url":base+"/upload","path":result_path}}
    if storage_kind=='github':jobs[ids['engine']]['artifact_upload']={'kind':'github'}
    if colocated:
        # One engine job starts the participant container itself; no execute job.
        execute=jobs.pop(ids['execute'])
        jobs[ids['engine']]['archive_url']=execute['archive_url']
        jobs[ids['engine']]['colocated']={key:execute[key] for key in ('manifest','model_base_url','source_digest')}
        jobs[ids['engine']]['colocated']['run_credential']=execute['run_credential']
    if instance is not None:
        jobs[ids['engine']]['instance'] = instance
    # The runtime is already present by explicit digest. Avoid a remote registry
    # availability dependency; all execution below still happens in real Docker.
    monkeypatch.setattr(DockerWorkspace,"pull",lambda self:None)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY","test-host-only-secret")
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN","test-host-only-identity")
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures=[]
            for kind in (("engine",) if colocated else ("engine","execute")):
                root=tmp_path/kind;root.mkdir()
                client=JobClient(base+"/job",ids[kind],"n"*43,lambda:"test-job-identity",http=Http(local=True))
                futures.append(pool.submit(run_claimed,kind,client,root))
            for future in futures:future.result(timeout=150)
    finally:
        broker.shutdown();broker.server_close()
    assert len(receipts)==(1 if colocated else 2)
    assert all(not value["error"] for value in receipts.values()),receipts
    if colocated:
        assert not agent_logs  # the engine job writes agent.log into the result itself
    else:
        assert list(agent_logs)==[ids['execute']] and 'agent booted with [REDACTED]' in agent_logs[ids['execute']]
        assert s['participant'] not in agent_logs[ids['execute']]
        assert '_agent_log' not in receipts[ids['execute']]['result']
    assert {path for path,auth in requests}=={"/project","/scenario"}
    assert all(auth is None for path,auth in requests)
    if storage_kind=='github':
        assert not uploads and credential_requests==[ids['engine']]
        result_path=receipts[ids['engine']]['result']['result_path']
        assert result_path.startswith('github:'+repository.full_name+'@')
        commit=result_path.rsplit('@',1)[1]
        archive=subprocess.run(['git','--git-dir',str(remote),'archive','--format=zip',commit],capture_output=True,check=True).stdout
        artifacts=read_project_zip(archive)
        assert {f.path for f in artifacts}=={'decisions.csv','workflow_result.json'}|({'agent.log'} if colocated else set())
        if colocated:
            log=next(f.data for f in artifacts if f.path=='agent.log').decode()
            assert 'agent booted with [REDACTED]' in log and s['participant'] not in log
    else:
        assert len(uploads)==1
        artifacts=read_project_zip(uploads[0])
    decisions=next(f.data for f in artifacts if f.path=="decisions.csv")
    digest=hashlib.sha256(decisions).hexdigest()
    status,path,stored=query(s["uri"],"select status,result_path,decisions_digest from public.observer_runs where id=%s",(s["run"],))[0]
    assert (status,path,stored)==("awaiting_csv",result_path,digest)
    output=tmp_path/"downloaded-result";extract_project(artifacts,output)
    expected_scenario = scenario
    if randomized:
        from project_platform.scenario_instances import generate_candidate, calibrated_score
        record = query(s['uri'], 'select record from private.observer_scenario_instances where run_id=%s', (s['run'],))[0][0]
        assert record and record['seed'] == instance['seed']
        expected_scenario = tmp_path/'replay-private'
        regenerated = generate_candidate(scenario, expected_scenario, seed=record['seed'], candidate=record['candidate'])
        assert regenerated['instance_digest'] == record['instance_digest']
        assert all(instance['seed'].encode() not in f.data for f in artifacts)
        assert instance['seed'] not in json.dumps(receipts)
    scored=score_files(expected_scenario,output/"decisions.csv",output/"verified-score.json","survey_complete")
    rpc(s["uri"],"observer_accept_csv",s["run"],s["user"],digest)
    board=query(s["uri"],"select team_id,score from public.observer_leaderboard(%s)",(s["phase"],),role="anon")
    expected_score = calibrated_score(scored['score']['total'], record['difficulty']) if randomized else scored['score']['total']
    assert board==[(s["team"],expected_score)]

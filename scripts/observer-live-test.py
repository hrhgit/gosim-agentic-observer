#!/usr/bin/env python3
"""Exercise the deployed Observer portal using the isolated lab identity.

Only disposable test projects are submitted. Credentials and run identifiers
remain in Keychain; output deliberately excludes tokens and signed URLs.
"""
import argparse
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile

SERVICE = 'agentic-observer26-backend'

PYTHON_AGENT = '''import json, os, sys, urllib.request, uuid
assert 'SUPABASE_SERVICE_ROLE_KEY' not in os.environ
assert 'GITHUB_TOKEN' not in os.environ
assert not os.path.exists('/var/run/docker.sock')
for line in sys.stdin:
    m = json.loads(line)
    if m['message_type'] == 'initialize':
        body = json.dumps({'model':'qwen3.6:35b-a3b','messages':[{'role':'user','content':'Reply OK.'}],'max_tokens':16}).encode()
        req = urllib.request.Request(os.environ['OPENAI_BASE_URL']+'/chat/completions',data=body,
            headers={'Authorization':'Bearer '+os.environ['OPENAI_API_KEY'],'Content-Type':'application/json','Idempotency-Key':str(uuid.uuid4())})
        with urllib.request.urlopen(req,timeout=45) as response:
            assert json.loads(response.read())['choices']
        print('MODEL_PROXY_OK ISOLATION_OK',file=sys.stderr,flush=True)
        continue
    tiles = [t for t in m['payload']['candidate_tiles'] if t['effective_weather']['is_observable']]
    t = tiles[0] if tiles else None
    print(json.dumps({'protocol_version':m['protocol_version'],'message_type':'decision_response',
        'decision_sequence':m['decision_sequence'],'action':'observe' if t else 'wait',
        'tile_id':t['tile_id'] if t else '', 'program':'BACKUP' if t else ''}),flush=True)
'''

RUST_AGENT = r'''use std::io::{self, BufRead, Write};
fn main() {
    for line in io::stdin().lock().lines() {
        let line = line.unwrap();
        if !line.contains("decision_request") { continue; }
        let after = line.split("\"decision_sequence\":").nth(1).unwrap();
        let seq: String = after.trim_start().chars().take_while(|c| c.is_ascii_digit()).collect();
        println!("{{\"protocol_version\":\"participant-agent-protocol-v2\",\"message_type\":\"decision_response\",\"decision_sequence\":{},\"action\":\"wait\"}}", seq);
        io::stdout().flush().unwrap();
    }
}
'''


def fixture(kind):
    files = {'README.md': '# Observer acceptance project\nComplete project for the public JSONL protocol.\n'}
    if kind == 'rust':
        files.update({'Cargo.toml':'[package]\nname="observer-probe"\nversion="0.1.0"\nedition="2021"\n',
                      'src/main.rs':RUST_AGENT})
        manifest = {'image':'rust:1.98-bookworm','build':[['rustc','src/main.rs','-o','agent']],'run':['./agent']}
    else:
        files.update({'pyproject.toml':'[project]\nname="observer-probe"\nversion="0.1.0"\nrequires-python=">=3.12"\n',
                      'src/agent.py':PYTHON_AGENT})
        manifest = {'image':'python:3.12-slim','run':['python3','-u','src/agent.py']}
    if kind == 'adapter':
        files['src/agent.py'] = 'def choose_action(observation):\n    return {"action":"wait"}\n'
        files['README.md'] = '# Python survey project\nImport choose_action(observation) from src/agent.py. It returns an action dict. Preserve this policy; add a JSONL protocol adapter. No third-party dependencies.\n'
    else:
        files['observer.project.json'] = json.dumps({'schema_version':'observer-project-v1',**manifest})
    data = io.BytesIO()
    with zipfile.ZipFile(data,'w',zipfile.ZIP_DEFLATED) as archive:
        for name,content in files.items():archive.writestr(name,content)
    return data.getvalue()


class Lab:
    def __init__(self):
        self.state=json.loads(subprocess.check_output(['security','find-generic-password','-s',SERVICE,
            '-a',os.environ['SUPABASE_PROJECT_REF'],'-w'],text=True))
        self.lab=self.state['lab'];self.base=os.environ['SUPABASE_URL'];self.token=os.environ['SUPABASE_ANON_KEY']
        auth=self.request('/auth/v1/token?grant_type=password',{'email':self.lab['email'],'password':self.lab['password']})
        self.token=auth['access_token']

    def request(self,path,data=None,method=None,token=None,content_type=None):
        raw=isinstance(data,bytes)
        req=urllib.request.Request(self.base+path,data=data if raw else json.dumps(data).encode() if data is not None else None,
            method=method,headers={'apikey':os.environ['SUPABASE_ANON_KEY'],'Authorization':'Bearer '+(token or self.token),
            'Content-Type':content_type or ('application/zip' if raw else 'application/json')})
        try:
            with urllib.request.urlopen(req,timeout=120) as response:return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            try:
                body=json.loads(exc.read());code=body.get('error',body.get('code','request_failed'))
            except ValueError:code='request_failed'
            raise RuntimeError(f'HTTP {exc.code}: {code}') from None

    def portal(self,action,**kwargs):
        value=self.request('/functions/v1/observer-portal',{'action':action,**kwargs})
        return value['data']

    def save(self):
        subprocess.run(['security','add-generic-password','-U','-s',SERVICE,'-a',os.environ['SUPABASE_PROJECT_REF'],
            '-w',json.dumps(self.state)],check=True,capture_output=True)

    def tick(self):return self.request('/functions/v1/observer-dispatch',{},token=self.state['dispatch'])

    def submit(self,kind,case=None):
        case=case or kind
        cases=self.lab.setdefault('cases',{})
        if case in cases:raise RuntimeError('Case already submitted; inspect status before starting another')
        upload=self.portal('upload',purpose='source')
        self.request('/storage/v1/object/upload/sign/observer-staging/'+urllib.parse.quote(upload['path'],safe='/')+
                     '?token='+urllib.parse.quote(upload['token'],safe=''),fixture(kind),method='PUT')
        revision=self.portal('submit_zip',title='Acceptance '+case,upload_id=upload['id'])
        cases[case]=revision;self.save()
        return revision

    def submit_repository(self,case):
        cases=self.lab.setdefault('cases',{})
        if case in cases:raise RuntimeError('Repository case already submitted')
        revision=self.portal('submit_repository',title='Acceptance '+case,
            url='https://github.com/BH3GEI/observer-project-example')
        cases[case]=revision;self.save();return revision

    def status(self):
        data=self.portal('list')
        return {'projects':[{'id':p['id'],'title':p['title'],'revisions':[
            {k:r.get(k) for k in ('id','status','public_test','error','approval_digest')} for r in p['observer_revisions']]} for p in data['projects']],
            'batches':[{'id':b['id'],'status':b['status'],'runs':[{k:r.get(k) for k in ('id','status','score','error')} for r in b['observer_runs']]} for b in data['batches']]}

    def approve(self,kind):
        revision_id=self.lab['cases'][kind]['revision_id']
        rows=[r for p in self.portal('list')['projects'] for r in p['observer_revisions'] if r['id']==revision_id]
        revision=rows[0]
        if revision['status']!='reviewable' or not revision['public_test'].get('passed'):raise RuntimeError('Public test has not passed')
        return self.portal('approve',revision_id=revision_id,digest=revision['approval_digest'])

    def evaluate(self,kind):
        case=self.lab['cases'][kind]
        if 'batch_id' in case:raise RuntimeError('Formal case already submitted')
        result=self.portal('evaluate',revision_id=case['revision_id'],phase_id=self.lab['phase_id'])
        case.update(result);self.save();return result

    def diagnostics(self,kind):
        return self.portal('diagnostics',revision_id=self.lab['cases'][kind]['revision_id'])

    def start_local(self,case):
        cases=self.lab.setdefault('cases',{})
        if case in cases:raise RuntimeError('Local case already submitted; inspect it before retrying')
        result=self.portal('evaluate',revision_id=None,phase_id=self.lab['phase_id'])
        cases[case]=result;self.save();return result

    def batch(self,case):
        batch_id=self.lab['cases'][case]['batch_id']
        return next(b for b in self.portal('list')['batches'] if b['id']==batch_id)

    def run_local(self,case):
        # Run only our known acceptance fixture, with the normal Docker runner.
        # No user-controlled project is executed on this host.
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
        from project_platform.local import run_local,export_decisions
        from project_platform.session import SessionClient
        batch=self.batch(case)
        if batch['mode']!='local':raise RuntimeError('Expected a local CSV batch')
        runs=[r for r in batch['observer_runs'] if r['status']!='scored']
        if not runs:return {'batch_id':batch['id'],'status':batch['status']}
        run=next((r for r in runs if r['status'] in ('starting','ready','running','awaiting_csv')),None)
        if run is None:return {'batch_id':batch['id'],'waiting_for_engine':True}
        credential=self.portal('local_access',run_id=run['id'])['credential']
        cache=Path.home()/'.cache';cache.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='observer-local-acceptance-',dir=cache) as temporary:
            root=Path(temporary);project=root/'project';project.mkdir()
            with zipfile.ZipFile(io.BytesIO(fixture('python'))) as archive:archive.extractall(project)
            # Reuse the already downloaded, immutable Python image on this Mac.
            manifest=json.loads((project/'observer.project.json').read_text())
            manifest['image']='mcr.microsoft.com/playwright/python@sha256:3de745b23fc4b33fccbcb3f592ee52dd5c80ce79f19f839c825ce23364e403c1'
            (project/'observer.project.json').write_text(json.dumps(manifest))
            output=root/'decisions.csv';session_url=self.base+'/functions/v1/observer-session'
            if run['status']=='awaiting_csv':export_decisions(SessionClient(session_url,credential),output)
            else:run_local(project,session_url,credential,self.base+'/functions/v1/observer-model/v1',output)
            data=output.read_bytes()
        def upload(raw):
            slot=self.portal('upload',purpose='csv')
            self.request('/storage/v1/object/upload/sign/observer-staging/'+urllib.parse.quote(slot['path'],safe='/')+
                '?token='+urllib.parse.quote(slot['token'],safe=''),raw,method='PUT',content_type='text/csv')
            return slot['id']
        altered=upload(data+b'\n')
        try:self.portal('accept_csv',run_id=run['id'],upload_id=altered)
        except RuntimeError as exc:
            if 'csv_does_not_match_session' not in str(exc):raise
        else:raise RuntimeError('Altered CSV was incorrectly accepted')
        self.portal('accept_csv',run_id=run['id'],upload_id=upload(data))
        return {'batch_id':batch['id'],'run_id':run['id'],'csv_bytes':len(data),
                'altered_csv_rejected':True,'canonical_csv_accepted':True}

    def check_batch(self,case):
        batch=self.batch(case);runs=batch['observer_runs']
        if batch['status']!='scored' or len(runs)!=2 or any(r['status']!='scored' for r in runs):
            return {'batch_id':batch['id'],'status':batch['status'],
                    'runs':[{k:r.get(k) for k in ('id','status','score','error')} for r in runs]}
        mean=sum(r['score'] for r in runs)/len(runs)
        if abs(batch['score']-mean)>1e-6:raise RuntimeError('Batch score differs from scenario mean')
        results=[]
        for run in runs:
            url=self.portal('download_result',run_id=run['id'])['url']
            with urllib.request.urlopen(url,timeout=60) as response:raw=response.read(52428801)
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                names=[n for n in archive.namelist() if n=='decisions.csv' or n.endswith('/decisions.csv')]
                if len(names)!=1:raise RuntimeError('Missing private result CSV')
                size=len(archive.read(names[0]))
            results.append({'run_id':run['id'],'score':run['score'],'private_csv_bytes':size})
        return {'batch_id':batch['id'],'status':'scored','mode':batch['mode'],'score':batch['score'],
                'same_batch_mean_verified':True,'private_results':results}

    def check_result(self,kind):
        revision_id=self.lab['cases'][kind]['revision_id']
        revision=next(r for p in self.portal('list')['projects'] for r in p['observer_revisions'] if r['id']==revision_id)
        if not revision['public_test'].get('passed'):raise RuntimeError('Public test has not passed')
        run=revision['public_test']['run_id']
        link=self.portal('download_result',run_id=run)['url']
        # The returned signed URL is intentionally never printed or persisted.
        with urllib.request.urlopen(link,timeout=60) as response:data=response.read(52428801)
        if len(data)>52428800:raise RuntimeError('Result download exceeded the limit')
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names=archive.namelist()
            decisions=[n for n in names if n.endswith('/decisions.csv') or n=='decisions.csv']
            if len(decisions)!=1:raise RuntimeError('Missing canonical CSV')
            csv=archive.read(decisions[0])
        logs=self.diagnostics(kind)
        return {'revision_id':revision_id,'run_id':run,'private_result_downloaded':True,'csv_bytes':len(csv),
                'score':revision['public_test'].get('score'),'jobs':[{'kind':j['kind'],'status':j['status'],'code':j['code']} for j in logs],
                'model_probe':any('MODEL_PROXY_OK ISOLATION_OK' in j['log'] for j in logs)}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['submit','status','tick','approve','evaluate','diagnostics','check_result',
                                        'start_local','run_local','check_batch','submit_repository'])
    parser.add_argument('--kind',choices=['python','rust','adapter'],default='python')
    parser.add_argument('--case',help='Unique acceptance-case label; preserves earlier failed attempts')
    args=parser.parse_args();lab=Lab()
    method=getattr(lab,args.action)
    result=method(args.kind,args.case) if args.action=='submit' else method() if args.action in ('status','tick') else method(args.case or args.kind)
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()

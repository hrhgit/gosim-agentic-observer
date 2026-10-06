#!/usr/bin/env python3
"""Verify exported sources against every runner control repo before enabling dispatch.

A row with "enabled": false in ops/github-installations.json is verified and
recorded but stays disabled (for example a new organization still being checked).
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import urllib.request

ROOT=Path(__file__).resolve().parents[1]


def gh(*args):
    return subprocess.check_output(['gh',*args],text=True).strip()


def quote(value):return "'"+str(value).replace("'","''")+"'"


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--export',type=Path,required=True)
    parser.add_argument('--apply',action='store_true');args=parser.parse_args()
    if gh('api','user','--jq','.login')!='BH3GEI':raise RuntimeError('Use the local BH3GEI gh login')
    file=ROOT/'ops/github-installations.json';config=json.loads(file.read_text())
    expected=json.loads((args.export/'control-inventory.json').read_text())
    paths=set(expected)|{'README.md','.gitignore','control-inventory.json'}
    verified=[]
    for row in config['installations']:
        repo=row['organization']+'/'+config['control_repository']
        remote=json.loads(gh('api','repos/'+repo))
        if not remote['private'] or remote['fork'] or str(remote['owner']['id'])!=row['organization_id']:
            raise RuntimeError('Control repository identity mismatch: '+repo)
        head=json.loads(gh('api','repos/'+repo+'/commits/main'))['sha']
        tree=json.loads(gh('api','repos/'+repo+'/git/trees/'+head+'?recursive=1'))
        blobs={f['path']:f for f in tree['tree'] if f['type']=='blob'}
        if tree.get('truncated') or set(blobs)!=paths:raise RuntimeError('Unexpected published files: '+repo)
        for name in paths:
            data=(args.export/name).read_bytes()
            digest=hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
            if digest!=blobs[name]['sha']:raise RuntimeError('Published source differs: '+repo+'/'+name)
        verified.append({**row,'repository_id':str(remote['id']),'approved_sha':head})
    if args.apply:
        values=[]
        for row in verified:
            repo=row['organization']+'/'+config['control_repository']
            tag='observer-runtime-'+row['approved_sha']
            lookup=subprocess.run(['gh','api','repos/'+repo+'/git/ref/tags/'+tag],capture_output=True,text=True)
            if lookup.returncode:
                if '404' not in lookup.stderr:raise RuntimeError('Could not verify runtime release tag')
                gh('api','repos/'+repo+'/git/refs','-X','POST','-f','ref=refs/tags/'+tag,'-f','sha='+row['approved_sha'])
                tagged=json.loads(gh('api','repos/'+repo+'/git/ref/tags/'+tag))
            else:tagged=json.loads(lookup.stdout)
            if tagged['object']['type']!='commit' or tagged['object']['sha']!=row['approved_sha']:
                raise RuntimeError('Runtime tag differs from the approved commit; refusing to overwrite it')
            gh('variable','set','OBSERVER_JOB_URL','--repo',repo,'--body',os.environ['SUPABASE_URL']+'/functions/v1/observer-job')
            enabled='true' if row.get('enabled',True) else 'false'
            values.append('('+','.join(quote(row[k]) for k in ('organization','organization_id','installation_id','repository_id','approved_sha'))+','+enabled+')')
        sql='insert into private.observer_installations(organization,organization_id,installation_id,repository_id,approved_sha,enabled) values '+','.join(values)+'''
          on conflict(organization) do update set organization_id=excluded.organization_id,installation_id=excluded.installation_id,
          repository_id=excluded.repository_id,approved_sha=excluded.approved_sha,enabled=excluded.enabled'''
        req=urllib.request.Request('https://api.supabase.com/v1/projects/'+os.environ['SUPABASE_PROJECT_REF']+'/database/query',
          data=json.dumps({'query':sql}).encode(),headers={'Authorization':'Bearer '+os.environ['SUPABASE_ACCESS_TOKEN'],'Content-Type':'application/json'})
        with urllib.request.urlopen(req,timeout=60) as response:response.read()
        config.update(installations=verified,enabled=True)
        file.write_text(json.dumps(config,indent=2)+'\n')
    print(json.dumps({'verified':verified,'applied':args.apply,'account':'BH3GEI'}))


if __name__=='__main__':main()

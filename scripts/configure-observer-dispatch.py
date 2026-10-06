#!/usr/bin/env python3
"""Connect the idle-aware queue timer to the scoped dispatcher credential.

Only Keychain and Supabase Vault hold the capability. The cron command and
configuration contain no plaintext credential. No existing schedule is changed.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('observer_deploy',ROOT/'scripts/deploy-observer-backend.py')
deploy=importlib.util.module_from_spec(spec);spec.loader.exec_module(deploy)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    if not args.apply:
        print('Will connect the private dispatch timer using the existing Keychain credential.');return
    ref=os.environ['SUPABASE_PROJECT_REF']
    state=json.loads(subprocess.check_output(['security','find-generic-password','-s','agentic-observer26-backend',
        '-a',ref,'-w'],text=True))
    endpoint='https://'+ref+'.supabase.co/functions/v1/observer-dispatch'
    q=deploy.quote
    deploy.query("""begin;
do $configure$ declare v uuid;begin
  if not exists(select 1 from pg_extension where extname='pg_net') or
     not exists(select 1 from pg_extension where extname='pg_cron') or
     to_regclass('vault.decrypted_secrets') is null then raise exception 'Required dispatch extensions unavailable';end if;
  select id into v from vault.secrets where name='observer-platform-dispatch';
  if v is null then select vault.create_secret(%s,'observer-platform-dispatch','Scoped Observer queue dispatcher') into v;
  else perform vault.update_secret(v,%s);end if;
  insert into private.observer_dispatch_config(id,endpoint,secret_id,enabled) values(true,%s,v,true)
  on conflict(id) do update set endpoint=excluded.endpoint,secret_id=excluded.secret_id,enabled=true;
end $configure$;
commit;""" % (q(state['dispatch']),q(state['dispatch']),q(endpoint)))
    rows=deploy.query("select jobname,schedule,active from cron.job where jobname='observer-platform-dispatch'")
    if len(rows)!=1 or not rows[0]['active']:raise RuntimeError('Dispatcher schedule is not active')
    print(json.dumps({'dispatch_schedule':rows[0],'credential_storage':'Supabase Vault','idle_requests':False}))


if __name__=='__main__':main()

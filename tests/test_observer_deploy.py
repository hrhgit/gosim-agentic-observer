"""The deployment transaction must roll back an accidental legacy data edit."""
import hashlib
import importlib.util
from pathlib import Path
import sys

import psycopg
from psycopg.rows import dict_row
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tests/supabase'))
from pg import start  # noqa: E402


def test_deployment_records_hashes_is_repeatable_and_rolls_back_legacy_changes(tmp_path,monkeypatch):
    spec=importlib.util.spec_from_file_location('observer_deploy',ROOT/'scripts/deploy-observer-backend.py')
    deploy=importlib.util.module_from_spec(spec);spec.loader.exec_module(deploy)
    server,uri=start(apply_migrations=False)
    def query(statement):
        with psycopg.connect(uri,autocommit=True,row_factory=dict_row) as connection:
            cursor=connection.execute(statement)
            # The management API returns only the last command's result.
            while cursor.nextset(): pass
            return cursor.fetchall() if cursor.description else []
    try:
        query((ROOT/'tests/supabase/auth_stub.sql').read_text())
        for path in sorted((ROOT/'supabase/migrations').glob('*.sql')):
            if path.name<'20260925000100':query(path.read_text())
        query("insert into public.phases(slug,name_en,name_zh) values('protected','Original','原有赛程')")
        monkeypatch.setattr(deploy,'query',query)
        monkeypatch.setattr(sys,'argv',['deploy-observer-backend.py','--apply'])
        deploy.main();deploy.main()
        files=deploy.observer_migrations(ROOT);count=len(files)
        # Later-dated Observer migrations are applied too; legacy event ones never are.
        assert {p.stem[:8] for p in files}>={'20260925','20260926'}
        assert all(p.stem>='20260925000100' for p in files)
        assert query('select count(*) as n from private.observer_migrations')[0]['n']==count
        recorded={r['version']:r['digest'] for r in query('select version,digest from private.observer_migrations')}
        assert recorded=={p.stem:hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
        assert query("select to_regclass('private.observer_team_models') is not null as present")==[{'present':True}]
        assert query("select name_en from public.phases where slug='protected'")==[{'name_en':'Original'}]
        path=tmp_path/'supabase/migrations';path.mkdir(parents=True)
        (path/'20260925009999_accidental.sql').write_text("update public.phases set name_en='Oops' where slug='protected';")
        monkeypatch.setattr(deploy,'ROOT',tmp_path)
        with pytest.raises(psycopg.Error,match='Existing data changed'):
            deploy.main()
        assert query("select name_en from public.phases where slug='protected'")==[{'name_en':'Original'}]
        assert query('select count(*) as n from private.observer_migrations')[0]['n']==count
    finally:server.cleanup()


def test_an_unrecorded_migration_that_is_already_applied_is_refused_and_reported(tmp_path,monkeypatch,capsys):
    spec=importlib.util.spec_from_file_location('observer_deploy',ROOT/'scripts/deploy-observer-backend.py')
    deploy=importlib.util.module_from_spec(spec);spec.loader.exec_module(deploy)
    spec=importlib.util.spec_from_file_location('registry_check',ROOT/'scripts/check-migration-registry.py')
    check=importlib.util.module_from_spec(spec);spec.loader.exec_module(check)
    server,uri=start()
    def query(statement):
        with psycopg.connect(uri,autocommit=True,row_factory=dict_row) as connection:
            cursor=connection.execute(statement)
            while cursor.nextset(): pass
            return cursor.fetchall() if cursor.description else []
    try:
        monkeypatch.setattr(deploy,'query',query)
        monkeypatch.setattr(check,'deploy',deploy)
        path=tmp_path/'supabase/migrations';path.mkdir(parents=True)
        hot=path/'20260925000101_hot_patch.sql'
        hot.write_text("create table if not exists private.hot_patch_probe(id int);\n"
                       "create or replace function public.hot_patch_probe() returns int language sql as $$ select 1 $$;\n")
        query(hot.read_text())                   # applied by hand, never recorded
        query('create table if not exists private.observer_migrations(version text primary key,digest text not null,'
              'applied_at timestamptz not null default now())')
        fresh=path/'20260925000102_new.sql'
        fresh.write_text("create or replace function public.hot_patch_probe() returns int language sql as $$ select 2 $$;\n")
        report=check.check(tmp_path)
        assert sorted(report['unrecorded'])==[hot.name,fresh.name]
        assert report['unrecorded'][hot.name]['already_present']==['table private.hot_patch_probe','function public.hot_patch_probe (identical body)']
        assert report['unrecorded'][fresh.name]['already_present']==[]
        monkeypatch.setattr(deploy,'ROOT',tmp_path)
        monkeypatch.setattr(sys,'argv',['deploy-observer-backend.py','--apply'])
        with pytest.raises(SystemExit,match='Refused'):
            deploy.main()
        assert query('select count(*) as n from private.observer_migrations')==[{'n':0}]
        assert query('select public.hot_patch_probe() as v')==[{'v':1}]
        # Recorded after verification: only the new migration runs.
        query("insert into private.observer_migrations(version,digest) values("+deploy.quote(hot.stem)+","
              +deploy.quote(hashlib.sha256(hot.read_bytes()).hexdigest())+")")
        deploy.main()
        assert query('select public.hot_patch_probe() as v')==[{'v':2}]
        assert check.check(tmp_path)['unrecorded']=={}
    finally:server.cleanup()

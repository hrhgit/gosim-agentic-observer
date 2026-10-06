"""Egress route (出网线路) in real PostgreSQL (migration 20261005020000): the team's choice,
the switches, the scheduler's labels-only route and the per-path record."""
import json
import uuid

import psycopg
import pytest
from test_project_database import database, setup, session, identity, query, rpc  # noqa: F401


def env(uri, user):
    return rpc(uri, 'observer_team_environment', role='authenticated', user=user)


def choose(uri, user, route, fallback=None):
    return rpc(uri, 'observer_set_team_egress_route', route, fallback, role='authenticated', user=user)


def test_members_choose_a_route_only_while_it_is_offered(setup):
    s = setup; uri = s['uri']
    teammate, _ = identity(uri, team=s['team'])
    outsider, _ = identity(uri)
    assert env(uri, s['user'])['egress_route'] == {'available': False, 'route': 'direct', 'auto_fallback': True}
    with pytest.raises(psycopg.Error, match='egress_route_unavailable'):
        choose(uri, s['user'], 'cn')
    choose(uri, s['user'], 'direct', False)   # direct is always allowed
    # Routes need open egress and the route switch (or a pilot team).
    query(uri, 'update private.observer_hardening set open_egress=true, egress_routes_teams=array[%s]::uuid[] where id',
          (s['team'],))
    assert env(uri, teammate)['egress_route'] == {'available': True, 'route': 'direct', 'auto_fallback': False}
    view = choose(uri, teammate, 'overseas', True)
    assert view['egress_route'] == {'available': True, 'route': 'overseas', 'auto_fallback': True}
    assert choose(uri, s['user'], 'cn')['egress_route']['auto_fallback'] is True   # unchanged when not given
    for bad in ('', 'CN', 'proxy', None):
        with pytest.raises(psycopg.Error, match='invalid_egress_route'):
            choose(uri, s['user'], bad)
    with pytest.raises(psycopg.Error, match='egress_route_unavailable'):   # another team, not a pilot
        choose(uri, outsider, 'cn')
    with pytest.raises(psycopg.Error, match='permission denied'):
        query(uri, 'select * from private.observer_team_egress_route', role='authenticated', user=s['user'])
    with pytest.raises(psycopg.Error, match='permission denied'):
        rpc(uri, 'observer_set_team_egress_route', 'cn', None, role='anon')
    assert query(uri, "select count(*) from public.audit_log where action='observer.team_egress_route_saved'")[0][0] >= 3


def test_scheduler_gets_the_route_label_only_while_switched_on(setup):
    s = setup; uri = s['uri']
    run, _, _ = session(s)
    assert rpc(uri, 'observer_run_team_egress', run)['route'] is None
    query(uri, 'update private.observer_hardening set open_egress=true, egress_routes=true where id')
    choose(uri, s['user'], 'overseas', False)
    assert rpc(uri, 'observer_run_team_egress', run)['route'] == {'name': 'overseas', 'fallback': False,
                                                                  'cap_bytes': 500 * 1024 * 1024}
    query(uri, 'update private.observer_hardening set egress_route_cap_bytes=1000 where id')
    assert rpc(uri, 'observer_run_team_egress', run)['route']['cap_bytes'] == 1000
    assert rpc(uri, 'observer_hardening')['egress_routes'] is True
    # Rollback: the switch off (or open egress off) sends everyone direct; the choice is kept.
    query(uri, "update private.observer_hardening set egress_routes=false, egress_routes_teams='{}' where id")
    assert rpc(uri, 'observer_run_team_egress', run)['route'] is None
    assert env(uri, s['user'])['egress_route'] == {'available': False, 'route': 'overseas', 'auto_fallback': False}
    query(uri, 'update private.observer_hardening set egress_routes=true, open_egress=false where id')
    assert rpc(uri, 'observer_run_team_egress', run)['route'] is None
    query(uri, 'update private.observer_hardening set open_egress=true where id')
    choose(uri, s['user'], 'direct')
    assert rpc(uri, 'observer_run_team_egress', run)['route'] is None


def test_receipts_record_the_path_and_the_route_summary(setup):
    s = setup; uri = s['uri']
    teammate, _ = identity(uri, team=s['team'])
    run, _, _ = session(s)
    job = uuid.uuid4()
    query(uri, """insert into private.observer_installations(organization,organization_id,installation_id,repository_id,
        approved_sha) values('AGENTIC-OBSERVER26-runner-36','1','1','1',%s) on conflict do nothing""", ('a' * 40,))
    query(uri, """insert into private.observer_jobs(id,kind,run_id,organization,repository_id,organization_id,
        workflow_sha,nonce_hash,encrypted_nonce,encrypted_input,status)
        select %s,'engine',%s,organization,repository_id,organization_id,approved_sha,'h','n','i','claimed'
        from private.observer_installations where organization='AGENTIC-OBSERVER26-runner-36'""", (job, run))
    row = {"port": 443, "connections": 1, "refused": 0, "bytes_up": 10, "bytes_down": 20,
           "first": "2026-10-05T01:00:00Z", "last": "2026-10-05T01:00:01Z"}
    egress = [{**row, "host": "api.kimi.com", "path": "overseas:node2"},
              {**row, "host": "api.kimi.com", "path": "direct"},           # the same destination on two paths
              {**row, "host": "ipinfo.io", "path": "overseas:node2"},
              {**row, "host": "x.com", "path": "198.51.100.7:443"},          # never stored as given
              {**row, "host": "old.example", "connections": 2}]              # earlier runtimes: no path
    summary = {"route": "overseas", "fallback": True, "cap_bytes": 524288000, "nodes": 3, "proxied_bytes": 60,
               "capped": False, "failovers": 1, "fallbacks": 1,
               "paths": {"overseas:node2": {"connections": 2, "bytes_up": 20, "bytes_down": 40},
                         "direct": {"connections": 1, "bytes_up": 10, "bytes_down": 20},
                         "secret.host:1": {"connections": 1, "bytes_up": 1, "bytes_down": 1}}}
    query(uri, "update private.observer_jobs set result=%s::jsonb, status='succeeded' where id=%s",
          (json.dumps({"run_id": str(run), "egress": egress, "egress_route": summary}), job))
    rows = query(uri, 'select host,path,connections,bytes_up from private.observer_run_egress where run_id=%s order by host',
                 (run,))
    assert rows == [('api.kimi.com', 'mixed', 2, 20), ('ipinfo.io', 'overseas:node2', 1, 10),
                    ('old.example', None, 2, 10), ('x.com', None, 1, 10)]
    stored = query(uri, 'select route,auto_fallback,nodes,proxied_bytes,failovers,fallbacks,paths '
                        'from private.observer_run_egress_route where run_id=%s', (run,))
    assert stored == [('overseas', True, 3, 60, 1, 1, {k: v for k, v in summary["paths"].items() if k != "secret.host:1"})]
    log = rpc(uri, 'observer_run_egress_log', run, role='authenticated', user=teammate)
    assert {(e['host'], e['path']) for e in log} >= {('ipinfo.io', 'overseas:node2'), ('old.example', 'direct')}
    with pytest.raises(psycopg.Error, match='permission denied'):
        query(uri, 'select * from private.observer_run_egress_route', role='authenticated', user=teammate)
    # A malformed summary never blocks the receipt.
    job2 = uuid.uuid4()
    query(uri, """insert into private.observer_jobs(id,kind,run_id,organization,repository_id,organization_id,
        workflow_sha,nonce_hash,encrypted_nonce,encrypted_input,status)
        select %s,'execute',%s,organization,repository_id,organization_id,approved_sha,'h','n','i','claimed'
        from private.observer_installations where organization='AGENTIC-OBSERVER26-runner-36'""", (job2, run))
    query(uri, "update private.observer_jobs set result=%s::jsonb, status='succeeded' where id=%s",
          (json.dumps({"run_id": str(run), "egress_route": {"route": "nowhere"}}), job2))
    assert query(uri, 'select count(*) from private.observer_run_egress_route where job_id=%s', (job2,)) == [(0,)]

"""The rebalance script's SQL against a real database: dry-run reads and the
apply transaction, including the rollback when a participant becomes busy."""
from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path

import psycopg
import pytest

from test_project_database import database, identity, query, rpc, setup  # noqa: F401
from test_runner_placement import ORG, claimed_job, finished_job, install

spec = importlib.util.spec_from_file_location(
    'rebalance_observer_placements',
    Path(__file__).resolve().parents[1] / 'scripts/rebalance-observer-placements.py')
rebalance = importlib.util.module_from_spec(spec)
sys.modules['rebalance_observer_placements_sql'] = rebalance
spec.loader.exec_module(rebalance)


def placed_users(uri):
    rows = query(uri, rebalance.USAGE_SQL % 7)
    return [{'user_id': str(r[0]), 'organization': r[1], 'recent_seconds': float(r[2]), 'busy': bool(r[3])}
            for r in rows]


@pytest.fixture
def three_orgs(setup):
    s = setup; uri = s['uri']
    query(uri, 'delete from private.observer_placements')
    for n in range(1, 4):
        install(uri, n)
    for n in range(4, 13):
        install(uri, n, enabled=False)
    return s


def test_dry_run_usage_and_apply_transaction(three_orgs):
    s = three_orgs; uri = s['uri']
    organizations = [r[0] for r in query(uri, rebalance.ENABLED_SQL)]
    assert organizations == [ORG + str(n) for n in (1, 2, 3)]
    # s['user'] is placed on org-1 with one hour of recent usage; two idle
    # participants sit on org-1 and org-2 with no usage at all.
    query(uri, 'insert into private.observer_placements(user_id,organization) values(%s,%s)', (s['user'], ORG + '1'))
    finished_job(s, ORG + '1', 3600)
    idle_a, _ = identity(uri)
    idle_b, _ = identity(uri)
    query(uri, 'insert into private.observer_placements(user_id,organization) values(%s,%s)', (idle_a, ORG + '1'))
    query(uri, 'insert into private.observer_placements(user_id,organization) values(%s,%s)', (idle_b, ORG + '2'))
    users = placed_users(uri)
    by_id = {u['user_id']: u for u in users}
    assert by_id[str(s['user'])]['recent_seconds'] == pytest.approx(3600, abs=5)
    assert by_id[str(idle_a)]['busy'] is False
    plan = rebalance.plan_moves(organizations, users)
    assert plan['moves'] and plan['moves'][0]['user_id'] == str(s['user'])
    assert plan['moves'][0]['to'] == ORG + '2'
    query(uri, rebalance.apply_sql(plan['moves']))
    moved = {r[0]: r[1] for r in query(uri, 'select user_id::text,organization from private.observer_placements')}
    assert moved[str(s['user'])] == ORG + '2'


def test_apply_rolls_back_when_a_participant_becomes_busy(three_orgs):
    s = three_orgs; uri = s['uri']
    query(uri, 'insert into private.observer_placements(user_id,organization) values(%s,%s)', (s['user'], ORG + '1'))
    finished_job(s, ORG + '1', 3600)
    plan = rebalance.plan_moves([ORG + str(n) for n in (1, 2, 3)], placed_users(uri))
    assert [m['user_id'] for m in plan['moves']] == [str(s['user'])]
    # Between the dry-run and the apply the participant starts new work.
    claimed_job(s, ORG + '1')
    assert {u['user_id'] for u in placed_users(uri) if u['busy']} == {str(s['user'])}
    with pytest.raises(psycopg.Error, match='rebalance_refused'):
        query(uri, rebalance.apply_sql(plan['moves']))
    assert query(uri, 'select organization from private.observer_placements where user_id=%s',
                 (s['user'],)) == [(ORG + '1',)]

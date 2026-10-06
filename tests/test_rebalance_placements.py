"""Pure planning logic of scripts/rebalance-observer-placements.py."""
import importlib.util
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    'rebalance_observer_placements',
    Path(__file__).resolve().parents[1] / 'scripts/rebalance-observer-placements.py')
rebalance = importlib.util.module_from_spec(spec)
sys.modules['rebalance_observer_placements'] = rebalance
spec.loader.exec_module(rebalance)

ORGS = [f'AGENTIC-OBSERVER26-runner-{n}' for n in range(1, 13)]


def user(name, org, seconds, busy=False):
    return {'user_id': name, 'organization': org, 'recent_seconds': float(seconds), 'busy': busy}


def test_balances_usage_onto_empty_organizations():
    # Four indivisible one-hour users: the optimum is one per organization.
    users = [user(f'heavy-{n}', ORGS[0], 3600) for n in range(4)] + [user('light', ORGS[0], 60)]
    plan = rebalance.plan_moves(ORGS, users)
    moves = plan['moves']
    assert {m['user_id'] for m in moves} == {'heavy-0', 'heavy-1', 'heavy-2', 'heavy-3'}
    assert all(m['from'] == ORGS[0] for m in moves)
    assert len({m['to'] for m in moves}) == 4
    after = plan['load_after']
    assert after[ORGS[0]]['recent_seconds'] == 60
    assert max(o['recent_seconds'] for o in after.values()) == 3600


def test_busy_participants_never_move():
    users = [user('stuck', ORGS[0], 7200, busy=True), user('free', ORGS[1], 10)]
    plan = rebalance.plan_moves(ORGS, users)
    assert plan['moves'] == []
    assert plan['load_after'][ORGS[0]]['recent_seconds'] == 7200


def test_no_moves_inside_the_tolerance_band():
    users = [user('a', ORGS[0], 300), user('b', ORGS[1], 100)]
    assert rebalance.plan_moves(ORGS, users)['moves'] == []


def test_zero_usage_participants_even_out_headcounts():
    users = [user(f'p{n}', ORGS[0], 0) for n in range(6)]
    plan = rebalance.plan_moves(ORGS, users)
    counts = {o: v['participants'] for o, v in plan['load_after'].items() if v['participants']}
    assert max(counts.values()) - min(counts.values()) <= 1
    assert sum(counts.values()) == 6
    assert all(m['recent_seconds'] == 0 for m in plan['moves'])


def test_disabled_organizations_are_never_targets():
    users = [user(f'p{n}', ORGS[11], 0) for n in range(4)]
    plan = rebalance.plan_moves(ORGS[:2], users)
    assert {m['to'] for m in plan['moves']} <= {ORGS[0], ORGS[1]}
    assert plan['load_after'][ORGS[11]]['participants'] == 2
    moved = plan['load_after'][ORGS[0]]['participants'] + plan['load_after'][ORGS[1]]['participants']
    assert moved == 2


def test_planning_is_deterministic():
    users = [user(f'u{n}', ORGS[n % 3], (n * 137) % 900, busy=n % 5 == 0) for n in range(30)]
    assert rebalance.plan_moves(ORGS, users) == rebalance.plan_moves(ORGS, list(reversed(users)))


def test_apply_sql_rechecks_idleness_and_verifies_in_one_transaction():
    moves = [{'user_id': '7f3f7a14-7cb6-4b0b-9a8b-9f2f0f7a1111',
              'from': ORGS[0], 'to': ORGS[1], 'recent_seconds': 120.0}]
    sql = rebalance.apply_sql(moves)
    assert sql.startswith('begin;') and sql.rstrip().endswith('commit;')
    assert "pg_advisory_xact_lock(hashtext('observer-placement'))" in sql
    assert "set organization='" + ORGS[1] + "'" in sql and ORGS[0] in sql
    # The busy predicate is re-evaluated inside the transaction, per move.
    assert sql.count("'queued','dispatched','claimed'") == 1
    assert 'rebalance_refused' in sql


def test_apply_sql_quotes_every_value():
    moves = [{'user_id': "x'y", 'from': ORGS[0], 'to': ORGS[1], 'recent_seconds': 1.0}]
    sql = rebalance.apply_sql(moves)
    assert "'x''y'" in sql and "x'y" not in sql.replace("'x''y'", '')


def test_apply_sql_requires_moves():
    import pytest
    with pytest.raises(ValueError):
        rebalance.apply_sql([])

"""Kimi Coding Plan codes: eligibility after a successful practice score, captain-only atomic claim."""
import concurrent.futures
import uuid

import psycopg
import pytest

from test_project_database import database, identity, query, rpc  # noqa: F401


@pytest.fixture(scope='module')
def phases(database):
    uri = database
    ids = {slug: uuid.uuid4() for slug in ('practice', 'practice-projects', 'dev-lab')}
    for slug, pid in ids.items():
        query(uri, 'insert into public.phases(id,slug,name_en,name_zh) values(%s,%s,%s,%s)', (pid, slug, slug, slug))
    return ids


def admin(uri):
    user, team = identity(uri)
    query(uri, 'update public.profiles set is_admin=true where id=%s', (user,))
    return user, team


def member(uri, team):
    return identity(uri, team=team)[0]


def score_csv(uri, team, user, phase, status='scored'):
    query(uri, """insert into public.submissions(team_id,user_id,phase_id,kind,storage_path,status,score)
      values(%s,%s,%s,'results','x.csv',%s,%s)""", (team, user, phase, status, 10 if status == 'scored' else None))


def score_batch(uri, team, user, phase, purpose='formal'):
    query(uri, """insert into public.observer_batches(team_id,user_id,phase_id,purpose,mode,status,score,finished_at)
      values(%s,%s,%s,%s,'local','scored',42,now())""", (team, user, phase, purpose))


def status(uri, user):
    return rpc(uri, 'kimi_plan_status', role='authenticated', user=user)


def claim(uri, user):
    return rpc(uri, 'claim_kimi_plan_code', role='authenticated', user=user)


def import_codes(uri, codes):
    boss, _ = admin(uri)
    return rpc(uri, 'admin_import_redeem_codes', 'kimi', '\n'.join(codes), 'Kimi Coding Plan', role='authenticated', user=boss)


def test_eligibility_needs_a_successful_practice_score(database, phases):
    uri = database
    captain, team = identity(uri)
    before = status(uri, captain)
    assert before['has_team'] and before['is_captain'] and not before['eligible'] and before['code'] is None
    score_csv(uri, team, captain, phases['practice'], status='failed')
    score_csv(uri, team, captain, phases['dev-lab'])          # other phases do not count
    score_batch(uri, team, captain, phases['practice-projects'], purpose='preview')  # nor previews
    assert not status(uri, captain)['eligible']
    score_csv(uri, team, captain, phases['practice'])
    assert status(uri, captain)['eligible']

    other, team2 = identity(uri)
    score_batch(uri, team2, other, phases['practice-projects'])
    mate = member(uri, team2)
    seen = status(uri, mate)
    assert seen['eligible'] and not seen['is_captain']
    assert rpc(uri, 'kimi_plan_status', role='authenticated', user=uuid.uuid4())['has_team'] is False


def test_captain_claims_once_and_the_whole_team_sees_it(database, phases):
    uri = database
    captain, team = identity(uri)
    mate = member(uri, team)
    score_csv(uri, team, captain, phases['practice'])
    with pytest.raises(psycopg.Error, match='no_codes_left'):
        claim(uri, captain)
    s = status(uri, captain)
    assert s['eligible'] and s['available'] == 0
    import_codes(uri, [f'kimi-{uuid.uuid4()}' for _ in range(2)])
    with pytest.raises(psycopg.Error, match='captain_only'):
        claim(uri, mate)
    first = claim(uri, captain)
    assert first['already'] is False and first['code'].startswith('kimi-')
    again = claim(uri, captain)
    assert again['already'] is True and again['code'] == first['code']
    seen = status(uri, mate)
    assert seen['code'] == first['code'] and seen['claimed_by']
    rows = query(uri, 'select code from public.redeem_codes', role='authenticated', user=mate)
    assert rows == [(first['code'],)]
    # the generic per-member claim cannot bypass the captain rule
    with pytest.raises(psycopg.Error, match='kimi_plan_claim_required'):
        rpc(uri, 'claim_redeem_code', 'kimi', role='authenticated', user=mate)
    assert all(r['provider'] != 'kimi' for r in rpc_rows(uri, 'redeem_providers', mate))


def rpc_rows(uri, name, user):
    return [r[0] for r in query(uri, f'select row_to_json(x) from public.{name}() x', role='authenticated', user=user)]


def test_ineligible_and_hidden_teams_cannot_claim(database, phases):
    uri = database
    import_codes(uri, [f'kimi-{uuid.uuid4()}'])
    captain, team = identity(uri)
    with pytest.raises(psycopg.Error, match='not_eligible'):
        claim(uri, captain)
    score_csv(uri, team, captain, phases['practice'])
    query(uri, 'update public.teams set is_hidden=true where id=%s', (team,))
    s = status(uri, captain)
    assert s['qualified'] and s['hidden'] and not s['eligible']
    with pytest.raises(psycopg.Error, match='not_eligible'):
        claim(uri, captain)
    boss, hidden_team = admin(uri)
    query(uri, 'update public.teams set is_hidden=true where id=%s', (hidden_team,))
    score_csv(uri, hidden_team, boss, phases['practice'])
    assert status(uri, boss)['eligible']  # admins keep testing on hidden teams
    assert claim(uri, boss)['already'] is False


def test_concurrent_claims_take_one_code_per_team(database, phases):
    uri = database
    import_codes(uri, [f'kimi-{uuid.uuid4()}' for _ in range(3)])
    teams = []
    for _ in range(3):
        captain, team = identity(uri)
        score_csv(uri, team, captain, phases['practice'])
        teams.append((captain, team))
    jobs = [c for c, _ in teams for _ in range(4)]

    def attempt(user):
        try:
            return claim(uri, user)['code']
        except psycopg.Error as exc:
            assert 'no_codes_left' in str(exc)
            return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(attempt, jobs))
    per_team = query(uri, """select team_id,count(*) from public.redeem_codes where provider='kimi' and status='assigned'
      and team_id = any(%s) group by team_id""", ([t for _, t in teams],))
    assert len(per_team) == 3 and all(n == 1 for _, n in per_team)  # earlier tests left spare codes in the pool


def test_admin_sees_eligible_teams_and_claimers(database, phases):
    uri = database
    captain, team = identity(uri)
    score_batch(uri, team, captain, phases['practice-projects'])
    boss, _ = admin(uri)
    rows = rpc_rows(uri, 'admin_kimi_plan_teams', boss)
    mine = next(r for r in rows if r['team_id'] == str(team))
    assert mine['qualified'] and mine['first_scored_at'] and mine['code'] is None
    claimed = [r for r in rows if r['code']]
    assert claimed and all(r['claimed_by_email'] for r in claimed)
    with pytest.raises(psycopg.Error, match='admin_only'):
        rpc_rows(uri, 'admin_kimi_plan_teams', captain)

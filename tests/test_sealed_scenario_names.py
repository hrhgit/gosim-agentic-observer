"""A sealed phase that does not count for the final (organizer-verify, staging for hidden cards)
keeps its scenario names private until its results are published."""
import uuid

from test_project_database import database, identity, query  # noqa: F401


def visible(uri, sid, role='anon', user=None):
    return query(uri, 'select id from public.scenarios where id=%s', (sid,), role=role, user=user) != []


def test_sealed_non_final_phase_does_not_name_its_scenarios(database):
    uri = database
    phase, scenario, public_phase, shared = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    query(uri, "insert into public.phases(id,slug,name_en,name_zh,leaderboard_mode) values(%s,%s,'Staging','Staging','hidden')",
          (phase, 'staging-' + str(phase)[:8]))
    query(uri, 'insert into public.observer_phase_settings(phase_id,projects_enabled,sealed) values(%s,true,true)', (phase,))
    query(uri, "insert into public.scenarios(id,slug,name) values(%s,%s,'Hidden card')", (scenario, 'card-' + str(scenario)[:8]))
    query(uri, 'insert into public.phase_scenarios values(%s,%s)', (phase, scenario))
    user, _ = identity(uri)
    assert not visible(uri, scenario) and not visible(uri, scenario, 'authenticated', user)
    query(uri, "update public.phases set leaderboard_mode='published' where id=%s", (phase,))
    assert visible(uri, scenario)
    # An ordinary open practice phase still lists its scenarios.
    query(uri, "insert into public.phases(id,slug,name_en,name_zh) values(%s,%s,'Practice','Practice')", (public_phase, 'p-' + str(public_phase)[:8]))
    query(uri, "insert into public.scenarios(id,slug,name) values(%s,%s,'Public')", (shared, 'pub-' + str(shared)[:8]))
    query(uri, 'insert into public.phase_scenarios values(%s,%s)', (public_phase, shared))
    assert visible(uri, shared)

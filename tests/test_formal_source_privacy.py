"""Scenario generator inputs: formal (flags false) and hidden-final scenarios never open; a fully public formal one opens at start."""
import uuid

import pytest
import psycopg

from test_project_database import database, identity, query  # noqa: F401


def test_formal_sources_never_reopen_at_start_or_with_public_weather(database):
    uri = database
    participant, _ = identity(uri)
    admin, _ = identity(uri)
    query(uri, 'update public.profiles set is_admin=true where id=%s', (admin,))
    phase, formal, practice = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    prefix = str(formal)
    query(uri, "insert into public.phases(id,slug,name_en,name_zh,counts_for_final) values(%s,%s,'F','F',true)", (phase,str(phase)))
    for sid in (formal, practice):
        query(uri, "insert into public.scenarios(id,slug,name,weather_public,forecasts_public,events_public) values(%s,%s,'S',true,true,true)", (sid,str(sid)))
    query(uri, 'insert into public.phase_scenarios values(%s,%s)', (phase,formal))
    paths = ['config/scenario_config.json', 'config/weather_config.json',
             'config/tile_config.json', 'config/request_config.json',
             'outputs/reference/scenario_manifest.json', 'outputs/reference/catalog_metadata.json',
             'outputs/reference/observation_request_metadata.json', 'outputs/reference/weather.csv',
             'outputs/reference/weather_forecasts.csv', 'outputs/reference/weather_events.csv',
             'outputs/reference/tiles.csv']
    for sid in (formal, practice):
        for path in paths:
            query(uri, "insert into storage.objects(bucket_id,name) values('scenarios',%s)", (str(sid)+'/'+path,))
    def visible(role, user=None):
        return {r[0] for r in query(uri, "select name from storage.objects where bucket_id='scenarios'", role=role,user=user)}
    for start, end in (("now()+interval '1 day'", 'null'),
                       ("now()-interval '1 day'", "now()+interval '1 day'"),
                       ("now()-interval '2 days'", "now()-interval '1 day'")):
        query(uri, f'update public.phases set starts_at={start},ends_at={end} where id=%s', (phase,))
        started = start != "now()+interval '1 day'"
        for role,user in (('anon',None),('authenticated',participant)):
            names = visible(role,user)
            # Only a formal scenario whose weather, forecasts and events are all public opens, when the phase starts.
            assert all((prefix+'/'+p in names) == started for p in paths)
            assert all(str(practice)+'/'+p in names for p in paths)
        for role,user in (('authenticated',admin),('service_role',None)):
            assert all(prefix+'/'+p in visible(role,user) for p in paths)
    # A formal scenario that is not fully public keeps its generator inputs private.
    query(uri, 'update public.scenarios set events_public=false where id=%s', (formal,))
    for role,user in (('anon',None),('authenticated',participant)):
        assert not any(n.startswith(prefix+'/') for n in visible(role,user))
    # A hidden final scenario never opens, also after its results are published.
    hidden_phase, hidden = uuid.uuid4(), uuid.uuid4()
    query(uri, "insert into public.phases(id,slug,name_en,name_zh,counts_for_final,leaderboard_mode) values(%s,%s,'H','H',true,'hidden')", (hidden_phase,str(hidden_phase)))
    query(uri, 'insert into public.observer_phase_settings(phase_id,projects_enabled,local_sessions_enabled,sealed) values(%s,true,false,true)', (hidden_phase,))
    query(uri, "insert into public.scenarios(id,slug,name,weather_public,forecasts_public,events_public) values(%s,%s,'S',true,true,true)", (hidden,str(hidden)))
    query(uri, 'insert into public.phase_scenarios values(%s,%s)', (hidden_phase,hidden))
    for path in paths:
        query(uri, "insert into storage.objects(bucket_id,name) values('scenarios',%s)", (str(hidden)+'/'+path,))
    for mode in ('hidden', 'published'):
        query(uri, 'update public.phases set leaderboard_mode=%s where id=%s', (mode, hidden_phase))
        for role,user in (('anon',None),('authenticated',participant)):
            assert not any(n.startswith(str(hidden)+'/') for n in visible(role,user))
        assert all(str(hidden)+'/'+p in visible('authenticated',admin) for p in paths)
    # It belongs to its sealed phase only.
    with pytest.raises(psycopg.Error, match='sealed_scenario_shared'):
        query(uri, 'insert into public.phase_scenarios values(%s,%s)', (phase,hidden))
    with pytest.raises(psycopg.Error, match='sealed_scenario_shared'):
        query(uri, 'insert into public.phase_scenarios values(%s,%s)', (hidden_phase,formal))
    # A phase whose scenario is shared with another phase cannot become sealed.
    other = uuid.uuid4()
    query(uri, "insert into public.phases(id,slug,name_en,name_zh) values(%s,%s,'O','O')", (other,str(other)))
    query(uri, 'insert into public.phase_scenarios values(%s,%s)', (other,formal))
    with pytest.raises(psycopg.Error, match='sealed_scenario_shared'):
        query(uri, "insert into public.observer_phase_settings(phase_id,projects_enabled,local_sessions_enabled,sealed) values(%s,true,false,true)", (phase,))
    # SQL columns are a separate disclosure channel; the previous seed-column
    # protections must remain effective as well.
    for column in ('seed', 'checksum', 'manifest'):
        with pytest.raises(psycopg.Error, match='permission denied'):
            query(uri, f'select {column} from public.scenarios', role='anon')

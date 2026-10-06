"""Private seeds, immutable calibration and publication ordering in real SQL."""
import secrets
import uuid
import importlib.util
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb
import pytest

from test_project_database import database, identity, query, rpc, setup  # noqa: F401
from test_project_eval_ux import revision

spec = importlib.util.spec_from_file_location('calibration_activation',
    Path(__file__).resolve().parents[1]/'scripts/configure-observer-calibration.py')
activation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(activation)


def configure(s):
    uri = s["uri"]
    profile = {"schema_version": "observer-calibration-profile-v1",
               "panel_version": "observer-reference-panel-v1", "template_digest": "d" * 64,
               "bounds": {"span": [1000, 2000], "open_fraction": [0.8, 1],
                          "gain_rate": [9000, 11000], "required_first": [9000, 11000],
                          "requests_first": [9000, 11000]}}
    query(uri, "insert into private.observer_scenario_bundles values(%s,'test/template.zip',%s)",
          (s["scenario"], "a" * 64))
    pid = query(uri, """insert into private.observer_calibration_profiles(scenario_id,bundle_digest,profile)
                        values(%s,%s,%s) returning id""", (s["scenario"], "a" * 64, Jsonb(profile)))[0][0]
    query(uri, "insert into private.observer_scenario_calibration values(%s,%s,%s)",
          (s["phase"], s["scenario"], pid))
    return pid


def batch_run(s, *, user=None):
    batch = rpc(s["uri"], "observer_create_batch", s["phase"], None,
                role="authenticated", user=user or s["user"])
    run = query(s["uri"], "select id from public.observer_runs where batch_id=%s", (batch,))[0][0]
    return batch, run


def test_each_team_gets_one_fixed_private_seed_and_fixed_profile(setup):
    s = setup; uri = s["uri"]
    pid = configure(s)
    first_batch, first = batch_run(s)
    a = rpc(uri, "observer_instance_input", first)
    assert len(a["seed"]) == 64 and a["profile_id"] == str(pid)
    assert rpc(uri, "observer_instance_input", first) == a
    outsider, _ = identity(uri)
    _, second = batch_run(s, user=outsider)
    b = rpc(uri, "observer_instance_input", second)
    query(uri, "update public.observer_batches set status='failed',finished_at=now() where id=%s", (first_batch,))
    _, third = batch_run(s)
    c = rpc(uri, "observer_instance_input", third)
    # Every evaluation of one team reuses its instance; other teams differ.
    assert a["seed"] == c["seed"] and a["seed"] != b["seed"]
    assert a["profile"] == b["profile"] == c["profile"]
    for role in ("authenticated", "anon"):
        with pytest.raises(psycopg.Error, match="permission denied"):
            rpc(uri, "observer_instance_input", first, role=role, user=s["user"])
        with pytest.raises(psycopg.Error, match="permission denied"):
            query(uri, "select seed from private.observer_scenario_instances", role=role, user=s["user"])
    assert "seed" not in query(uri, "select to_jsonb(r) from public.observer_runs r where id=%s", (first,),
                               role="authenticated", user=s["user"])[0][0]
    assert rpc(uri, "observer_instance_input", first) == a  # reading other attempts cannot rotate it
    with pytest.raises(psycopg.Error, match="instance_immutable"):
        query(uri, "update private.observer_scenario_instances set seed=%s where run_id=%s", ("0" * 64, first))


def test_per_evaluation_instances_remain_available_as_a_switch(setup):
    s = setup; uri = s["uri"]
    configure(s)
    query(uri, "update private.observer_scenario_calibration set seed_scope='run' where phase_id=%s", (s["phase"],))
    first_batch, first = batch_run(s)
    query(uri, "update public.observer_batches set status='failed',finished_at=now() where id=%s", (first_batch,))
    _, second = batch_run(s)
    assert rpc(uri, "observer_instance_input", first)["seed"] != rpc(uri, "observer_instance_input", second)["seed"]


def test_fixed_practice_runs_are_unchanged_and_configuration_freezes(setup):
    s = setup; uri = s["uri"]
    _, run = batch_run(s)
    assert rpc(uri, "observer_instance_input", run) is None
    with pytest.raises(psycopg.Error, match="configuration_immutable"):
        configure(s)


def test_record_must_precede_first_observation_and_cannot_be_replaced(setup):
    s = setup; uri = s["uri"]
    pid = configure(s)
    _, run = batch_run(s)
    participant, engine = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    rpc(uri, "observer_open_session", run, participant, engine)
    private = rpc(uri, "observer_instance_input", run)
    record = {"seed": private["seed"], "generator_version": "observer-weather-instance-v1",
              "template_digest": "d" * 64, "instance_digest": "e" * 64, "profile_digest": "f" * 64,
              "candidate": 0, "difficulty": {"panel_version": "observer-reference-panel-v1",
                "wait_score": -1000, "span": 1500, "reference_score": 500, "open_fraction": 0.9,
                "policies": {name: {"score": -1000 if name == "wait" else 500}
                             for name in ("gain_rate", "required_first", "requests_first", "wait")}}}
    with pytest.raises(psycopg.Error, match="instance_not_recorded"):
        rpc(uri, "observer_publish_initial", run, engine, {"calendar": {"night_count": 30}})
    with pytest.raises(psycopg.Error, match="invalid_or_expired_capability"):
        rpc(uri, "observer_record_instance", run, participant, record)
    with pytest.raises(psycopg.Error, match="invalid_instance_record"):
        rpc(uri, "observer_record_instance", run, engine, {**record, "seed": "0" * 64})
    with pytest.raises(psycopg.Error, match="scenario_not_comparable"):
        rpc(uri, "observer_record_instance", run, engine,
            {**record, "difficulty": {**record["difficulty"], "open_fraction": 0.2}})
    rpc(uri, "observer_record_instance", run, engine, record)
    rpc(uri, "observer_record_instance", run, engine, record)
    with pytest.raises(psycopg.Error, match="instance_immutable"):
        query(uri, "update private.observer_scenario_instances set record='{}' where run_id=%s", (run,))
    rpc(uri, "observer_publish_initial", run, engine, {"calendar": {"night_count": 30}})
    with pytest.raises(psycopg.Error, match="instance_record_conflict"):
        rpc(uri, "observer_record_instance", run, engine, {**record, "instance_digest": "c" * 64})
    with pytest.raises(psycopg.Error, match="profile_immutable"):
        query(uri, "update private.observer_calibration_profiles set bundle_digest=%s where id=%s", ("b" * 64, pid))
    with pytest.raises(psycopg.Error, match="configuration_immutable"):
        query(uri, "delete from private.observer_scenario_calibration where phase_id=%s", (s["phase"],))
    rpc(uri, "observer_ready", run, participant)
    rpc(uri, "observer_begin", run, engine)
    calibration = {"version": "observer-reference-panel-v1", "instance_commitment": "e" * 64,
                   "wait_score": -1000, "reference_score": 500, "span": 1500, "adjusted_score": 10000}
    summary = {"score": {"total": 10000}, "raw_score": {"total": 500}, "calibration": calibration}
    with pytest.raises(psycopg.Error, match="invalid_calibrated_score"):
        rpc(uri, "observer_finish_run", run, engine, {**summary, "score": {"total": 500}}, "a" * 64, "private/results")
    rpc(uri, "observer_finish_run", run, engine, summary, "a" * 64, "private/results")
    assert query(uri, "select score,score_summary->'raw_score'->>'total' from public.observer_runs where id=%s",
                 (run,)) == [(10000, '500')]
    rpc(uri, "observer_accept_csv", run, s["user"], "a" * 64)
    board = rpc(uri, "observer_board", s["phase"], 100, role="anon")
    assert len(board) == 1
    assert board[0]["calibrated"] and board[0]["total_score"] == 10000 and board[0]["raw_total_score"] == 500


def test_partially_configured_phase_cannot_start_mixed_scoring(setup):
    s = setup; uri = s["uri"]
    configure(s)
    extra = uuid.uuid4()
    query(uri, "insert into public.scenarios(id,slug,name) values(%s,%s,'Another')", (extra, str(extra)))
    query(uri, "insert into public.phase_scenarios values(%s,%s)", (s["phase"], extra))
    with pytest.raises(psycopg.Error, match="configuration_incomplete"):
        batch_run(s)
    assert query(uri, "select count(*) from public.observer_batches where phase_id=%s", (s["phase"],)) == [(0,)]


def test_calibrated_scenario_roster_freezes_after_first_batch(setup):
    s = setup; uri = s["uri"]
    configure(s)
    batch_run(s)
    extra = uuid.uuid4()
    query(uri, "insert into public.scenarios(id,slug,name) values(%s,%s,'Another')", (extra, str(extra)))
    for sql, params in (
        ("delete from public.phase_scenarios where phase_id=%s", (s["phase"],)),
        ("insert into public.phase_scenarios values(%s,%s)", (s["phase"], extra)),
        ("update public.phase_scenarios set scenario_id=%s where phase_id=%s", (extra, s["phase"])),
    ):
        with pytest.raises(psycopg.Error, match="configuration_immutable"):
            query(uri, sql, params)
    assert query(uri, "select scenario_id from public.phase_scenarios where phase_id=%s", (s["phase"],)) == [(s["scenario"],)]


def test_profile_activation_is_atomic_repeatable_and_requires_deployed_runtimes(setup):
    s = setup; uri = s['uri']
    versions = {f'AGENTIC-OBSERVER26-runner-{i}':str(i)*40 for i in range(1,7)}
    profile = {'schema_version':'observer-calibration-profile-v1', 'panel_version':'observer-reference-panel-v1',
               'template_digest':'d'*64, 'bounds':{'span':[1,1e9], 'open_fraction':[0,1],
               **{p:[-1e9,1e9] for p in ('gain_rate','required_first','requests_first')}}}
    entries = [{'scenario_id':str(s['scenario']), 'bundle_digest':'a'*64, 'profile':profile}]
    statement = activation.activation_sql(str(s['phase']), entries, versions)
    query(uri, "insert into private.observer_scenario_bundles values(%s,'fixture/template.zip',%s)", (s['scenario'],'a'*64))
    with pytest.raises(psycopg.Error, match='runtimes are not deployed'):
        query(uri, statement)
    assert query(uri, 'select count(*) from private.observer_calibration_profiles where scenario_id=%s', (s['scenario'],)) == [(0,)]
    for i,(org,sha) in enumerate(versions.items(),1):
        query(uri, 'insert into private.observer_installations(organization,organization_id,installation_id,repository_id,approved_sha,enabled) values(%s,%s,%s,%s,%s,true)',
              (org,str(i),i,str(i+10),sha))
    before = query(uri, 'select row_to_json(p)::text from public.phases p order by id')
    query(uri, statement)
    query(uri, statement)
    assert query(uri, 'select row_to_json(p)::text from public.phases p order by id') == before
    assert query(uri, 'select count(*) from private.observer_calibration_profiles where scenario_id=%s', (s['scenario'],)) == [(1,)]
    changed = [{**entries[0], 'profile':{**profile, 'training_samples':999}}]
    with pytest.raises(psycopg.Error, match='refusing replacement'):
        query(uri, activation.activation_sql(str(s['phase']), changed, versions))
    batch_run(s)
    with pytest.raises(psycopg.Error, match='Existing attempts'):
        query(uri, statement)


def test_public_formal_phases_use_the_template_and_calibrated_phases_fail_closed(setup):
    s = setup; uri = s["uri"]
    # A final or 'online' phase without calibration is public: every team plays the template.
    query(uri, "update public.phases set counts_for_final=true where id=%s", (s["phase"],))
    rev = revision(s)
    create = lambda: rpc(uri, "observer_create_batch", s["phase"], rev, True, role="authenticated", user=s["user"])
    public = create()
    run = query(uri, "select id from public.observer_runs where batch_id=%s", (public,))[0][0]
    assert rpc(uri, "observer_instance_input", run) is None
    summary = {"score": {"total": 1}, "raw_score": {"total": 1}}
    query(uri, "update public.observer_runs set status='scored',score=1,score_summary=%s,finished_at=now() where id=%s",
          (Jsonb(summary), run))
    query(uri, "select private.observer_finalize_batch(%s)", (public,))


def test_a_calibrated_phase_run_without_instance_cannot_schedule_or_score(setup):
    s = setup; uri = s["uri"]
    configure(s)
    # Simulate a run that escaped allocation (e.g. inserted with the trigger disabled).
    query(uri, "alter table public.observer_runs disable trigger observer_allocate_instance")
    try:
        first, run = batch_run(s)
    finally:
        query(uri, "alter table public.observer_runs enable trigger observer_allocate_instance")
    with pytest.raises(psycopg.Error, match="formal_instance_missing"):
        rpc(uri, "observer_instance_input", run)
    summary = {"score": {"total": 1}, "raw_score": {"total": 1}}
    with pytest.raises(psycopg.Error, match="formal_instance_missing"):
        query(uri, "update public.observer_runs set score=1,score_summary=%s where id=%s", (Jsonb(summary), run))
    query(uri, "update public.observer_batches set status='failed',finished_at=now() where id=%s", (first,))
    # A calibrated phase whose scenario lost its calibration row cannot allocate either.
    other = uuid.uuid4()
    query(uri, "insert into public.scenarios(id,slug,name) values(%s,%s,'Other')", (other, str(other)))
    query(uri, "alter table public.phase_scenarios disable trigger observer_freeze_calibrated_roster")
    try:
        query(uri, "insert into public.phase_scenarios values(%s,%s)", (s["phase"], other))
    finally:
        query(uri, "alter table public.phase_scenarios enable trigger observer_freeze_calibrated_roster")
    query(uri, "alter table public.observer_batches disable trigger observer_check_calibrated_batch")
    try:
        with pytest.raises(psycopg.Error, match="formal_instance_not_configured"):
            batch_run(s)
    finally:
        query(uri, "alter table public.observer_batches enable trigger observer_check_calibrated_batch")


def test_migration_removes_online_calibration_only():
    from pg import start as start_database
    root = Path(__file__).resolve().parents[1]
    migration = root / "supabase/migrations/20260927000800_fixed_formal_hidden_final.sql"
    server, uri = start_database(apply_migrations=False)
    try:
        query(uri, (root / "tests/supabase/auth_stub.sql").read_text())
        for path in sorted((root / "supabase/migrations").glob("*.sql")):
            if path.name < migration.name:
                query(uri, path.read_text())
        pids = {}
        for slug in ("online", "observer-acceptance-x"):
            user, team = identity(uri)
            phase, scenario = uuid.uuid4(), uuid.uuid4()
            query(uri, "insert into public.phases(id,slug,name_en,name_zh,counts_for_final) values(%s,%s,'P','P',%s)",
                  (phase, slug, slug == "online"))
            query(uri, "insert into public.observer_phase_settings(phase_id,projects_enabled,local_sessions_enabled) values(%s,true,true)", (phase,))
            query(uri, "insert into public.scenarios(id,slug,name) values(%s,%s,'S')", (scenario, str(scenario)))
            query(uri, "insert into public.phase_scenarios values(%s,%s)", (phase, scenario))
            pids[slug] = configure({"uri": uri, "phase": phase, "scenario": scenario})
        query(uri, migration.read_text())
        assert query(uri, "select p.slug from private.observer_scenario_calibration c join public.phases p on p.id=c.phase_id") == [("observer-acceptance-x",)]
    finally:
        server.cleanup()


def test_clients_hold_no_unused_write_privileges_on_scenarios_and_submissions(setup):
    uri = setup["uri"]
    def has(role, table, privilege):
        return query(uri, "select has_table_privilege(%s,%s,%s)", (role, table, privilege))[0][0]
    for privilege in ("INSERT", "DELETE", "TRUNCATE", "TRIGGER", "REFERENCES"):
        for role in ("anon", "authenticated"):
            assert not has(role, "public.scenarios", privilege)
        assert not has("anon", "public.submissions", privilege)
    assert not has("anon", "public.scenarios", "UPDATE") and not has("anon", "public.submissions", "UPDATE")
    # Admin scenario edits (RLS is_admin policy) and client reads keep working.
    assert has("authenticated", "public.scenarios", "UPDATE")
    for role in ("anon", "authenticated"):
        assert query(uri, "select has_column_privilege(%s,%s,%s,%s)", (role, "public.scenarios", "slug", "SELECT"))[0][0]
    assert has("authenticated", "public.submissions", "SELECT")
    with pytest.raises(psycopg.Error, match="permission denied"):
        query(uri, "insert into public.scenarios(slug,name) values('x','x')", role="anon")

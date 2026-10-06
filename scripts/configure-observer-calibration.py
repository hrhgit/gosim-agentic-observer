#!/usr/bin/env python3
"""Register reviewed private studies atomically; keep practice and old scores intact.

The input manifest contains a phase UUID and one entry per scenario with
scenario_id, template (local private directory), study (local private JSON) and
bundle_digest. runner_versions maps all six organizations to tested runtime SHAs.
Dry-run is the default. No seeds or private scenario data are printed.
"""
import argparse
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from project_platform.scenario_instances import acceptance_reasons, calibrated_score, directory_digest


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT/'scripts'/filename)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


deploy = module('calibration_deploy', 'deploy-observer-backend.py')
study_tools = module('calibration_study', 'calibrate-observer-scenarios.py')
q = deploy.quote


def reviewed_profile(template, study_path):
    raw = Path(study_path).read_bytes()
    study = json.loads(raw)
    training = study['summary']['training_seeds']
    validation = study['summary']['validation_seeds']
    rows = study['samples']
    if (type(training) is not int or training < 20 or type(validation) is not int or validation < 10 or
        len(rows) != training + validation or [r['index'] for r in rows] != list(range(len(rows)))):
        raise ValueError('Independent training and validation samples are required')
    for row in rows:
        if row['adjusted_holdout_score'] != calibrated_score(row['holdout']['score'], row['difficulty']):
            raise ValueError('Validation score does not match the recorded measurements')
    profile = study_tools.fit_profile(rows[:training], directory_digest(Path(template)))
    if profile != study['candidate_profile']:
        raise ValueError('Study profile or template differs from the frozen experiment')
    accepted = [r for r in rows[training:] if not acceptance_reasons(r['difficulty'], profile)]
    from statistics import median
    scale = median(r['difficulty']['span'] for r in rows[:training])
    report = study_tools.summarize(accepted, scale)
    # These preregistered checks reduce measured seed sensitivity for a policy
    # excluded from fitting. They do not establish fairness for every strategy.
    if (len(accepted) < math.ceil(validation/2) or
        report['adjusted_holdout_sd'] > report['fixed_scale_holdout_sd'] or
        report['adjusted_holdout_mean'] <= 0 or
        report['adjusted_holdout_sd'] / report['adjusted_holdout_mean'] > 0.05):
        raise ValueError('Independent validation does not meet the calibration criteria')
    return {**profile, 'study_digest': hashlib.sha256(raw).hexdigest(),
            'validation_samples': validation, 'accepted_validation': report}


def activation_sql(phase, entries, runner_versions):
    phase = str(uuid.UUID(phase))
    if not entries or len({e['scenario_id'] for e in entries}) != len(entries):
        raise ValueError('Each scenario requires exactly one study')
    # Every enabled runner organization must be covered; the database check below
    # compares against the live installation list rather than a fixed count.
    if not runner_versions or any(not re.fullmatch(r'AGENTIC-OBSERVER26-runner-(?:[1-9]|[1-9][0-9])', o) or
                                  not re.fullmatch('[0-9a-f]{40}', s) for o, s in runner_versions.items()):
        raise ValueError('Tested runtime versions for the runner organizations are required')
    rows = []
    for entry in entries:
        sid = str(uuid.UUID(entry['scenario_id']))
        if not re.fullmatch('[0-9a-f]{64}', entry['bundle_digest']):
            raise ValueError('Invalid bundle digest')
        rows.append('('+','.join((q(sid)+'::uuid', q(entry['bundle_digest']),
                    q(json.dumps(entry['profile'], allow_nan=False))+'::jsonb'))+')')
    runtimes = ','.join('('+q(org)+','+q(sha)+')' for org,sha in sorted(runner_versions.items()))
    return f"""begin;
select pg_advisory_xact_lock(hashtextextended('observer-calibration/'||{q(phase)},0));
create temp table reviewed_calibration(scenario_id uuid,bundle_digest text,profile jsonb) on commit drop;
insert into reviewed_calibration values {','.join(rows)};
do $verify$ begin
  if exists(select 1 from public.observer_batches where phase_id={q(phase)} and purpose='formal') or
    exists(select 1 from public.submissions where phase_id={q(phase)}) then
    raise exception 'Existing attempts prevent calibration activation'; end if;
  if not exists(select 1 from public.observer_phase_settings where phase_id={q(phase)} and
    (projects_enabled or local_sessions_enabled)) then raise exception 'Phase is not configured'; end if;
  if exists(select scenario_id from public.phase_scenarios where phase_id={q(phase)} except select scenario_id from reviewed_calibration) or
    exists(select scenario_id from reviewed_calibration except select scenario_id from public.phase_scenarios where phase_id={q(phase)}) then
    raise exception 'All phase scenarios require reviewed profiles'; end if;
  if exists(select 1 from reviewed_calibration r left join private.observer_scenario_bundles b using(scenario_id)
    where b.digest is distinct from r.bundle_digest) then raise exception 'Template bundle changed'; end if;
  if exists(select 1 from (values {runtimes}) as tested(organization,sha)
    left join private.observer_installations i using(organization)
    where i.approved_sha is distinct from tested.sha or i.enabled is distinct from true) then
    raise exception 'Tested runtimes are not deployed'; end if;
  if exists(select organization from private.observer_installations where enabled
    except select organization from (values {runtimes}) as tested(organization,sha)) then
    raise exception 'An enabled runner has no tested runtime'; end if;
  if exists(select 1 from private.observer_scenario_calibration c join private.observer_calibration_profiles p on p.id=c.profile_id
    join reviewed_calibration r on r.scenario_id=c.scenario_id where c.phase_id={q(phase)} and
    (p.profile,p.bundle_digest) is distinct from (r.profile,r.bundle_digest)) then
    raise exception 'Existing calibration differs; refusing replacement'; end if;
end $verify$;
insert into private.observer_calibration_profiles(scenario_id,bundle_digest,profile)
  select r.* from reviewed_calibration r where not exists(select 1 from private.observer_calibration_profiles p
    where (p.scenario_id,p.bundle_digest,p.profile)=(r.scenario_id,r.bundle_digest,r.profile));
insert into private.observer_scenario_calibration(phase_id,scenario_id,profile_id)
  select {q(phase)}::uuid,r.scenario_id,(select p.id from private.observer_calibration_profiles p
    where (p.scenario_id,p.bundle_digest,p.profile)=(r.scenario_id,r.bundle_digest,r.profile) order by p.created_at,p.id limit 1)
  from reviewed_calibration r where not exists(select 1 from private.observer_scenario_calibration c
    where c.phase_id={q(phase)} and c.scenario_id=r.scenario_id);
select private.audit('observer.calibration_enabled',jsonb_build_object('phase_id',{q(phase)},'scenario_count',{len(entries)}));
commit;
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    entries = [{**e, 'profile': reviewed_profile(e['template'], e['study'])} for e in manifest['scenarios']]
    statement = activation_sql(manifest['phase_id'], entries, manifest['runner_versions'])
    if args.apply:
        deploy.query(statement)
    print(json.dumps({'phase_id':manifest['phase_id'], 'reviewed_scenarios':len(entries), 'applied':args.apply}))


if __name__ == '__main__':
    main()

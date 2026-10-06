-- Public-scenario project runs can start the participant container inside the
-- engine job. The step-by-step protocol is unchanged, but steps no longer make a
-- database round trip each (about 2 s per step from US runners to Singapore).
-- Never used for private instances: those keep the separate execute job.
alter table public.observer_phase_settings add column if not exists colocated boolean not null default false;

create or replace function public.observer_run_colocated(p_run uuid)
returns boolean language sql stable security definer set search_path=public,pg_temp as $$
  select coalesce((select c.colocated and b.mode='project'
      and not exists(select 1 from private.observer_scenario_instances i where i.run_id=r.id)
    from public.observer_runs r join public.observer_batches b on b.id=r.batch_id
    join public.observer_phase_settings c on c.phase_id=b.phase_id where r.id=p_run),false)
$$;
revoke all on function public.observer_run_colocated(uuid) from public,anon,authenticated;
grant execute on function public.observer_run_colocated(uuid) to service_role;

-- A colocated run's project failures are reported by the engine job with the
-- same diagnostics code; they count toward the daily limit like before.
create or replace function private.observer_participant_failure(p_run uuid)
returns boolean language sql stable security definer set search_path=public,pg_temp as $$
  select exists(select 1 from public.observer_runs r join public.observer_batches b on b.id=r.batch_id
    where r.id=p_run and r.status='failed' and (r.error='local_runner_stopped'
      or (r.error='evaluation_expired' and b.mode='local')
      or (r.error in ('execute_job_failed','engine_job_failed') and exists(select 1 from private.observer_jobs j
        where j.run_id=r.id and j.status='failed' and j.result->'diagnostics'->>'code'='project_operation_failed'
          and (j.kind='execute' or (j.kind='engine' and j.result->'diagnostics'->>'stage'='execute'))))))
$$;

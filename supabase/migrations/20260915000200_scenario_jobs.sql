-- Rotating a competition seed used to mean running `python -m worker.main gen-scenario` by hand. Generating a
-- scenario is real computation, so the browser cannot do it: the admin console queues a job here and the
-- evaluation worker (already polling for submissions) picks it up, regenerates the scenario and re-registers it.

create table if not exists public.scenario_jobs (
  id bigserial primary key,
  slug text not null,
  seed integer,                                   -- null: the worker draws a fresh random seed
  days integer,                                   -- null on all four: keep the scenario's current value
  start_date date,
  wallclock integer,
  weather_public boolean,
  forecasts_public boolean,
  events_public boolean,
  status text not null default 'queued',          -- queued | running | done | failed
  requested_by uuid references public.profiles(id) on delete set null,
  worker_id text not null default '',
  error text not null default '',
  result_seed integer,                            -- what the worker actually used
  created_at timestamptz not null default now(),
  started_at timestamptz,
  finished_at timestamptz
);

create index if not exists scenario_jobs_pending_idx on public.scenario_jobs (created_at) where status = 'queued';

alter table public.scenario_jobs enable row level security;
drop policy if exists "scenario_jobs admin read" on public.scenario_jobs;
create policy "scenario_jobs admin read" on public.scenario_jobs for select to authenticated using (public.is_admin());
drop policy if exists "scenario_jobs admin write" on public.scenario_jobs;
create policy "scenario_jobs admin write" on public.scenario_jobs for all to authenticated using (public.is_admin()) with check (public.is_admin());

-- Queue a rotation. Guards live here rather than in the client so the console cannot queue a job for an
-- unknown scenario or stack two rotations on the same one.
create or replace function public.admin_queue_scenario_job(
  p_slug text, p_seed integer default null, p_days integer default null,
  p_start_date date default null, p_wallclock integer default null
) returns public.scenario_jobs language plpgsql security definer set search_path = public as $$
declare v_row public.scenario_jobs; v_scn public.scenarios;
begin
  if not public.is_admin() then raise exception 'admin_only'; end if;
  select * into v_scn from public.scenarios where slug = p_slug;
  if v_scn.id is null then raise exception 'unknown_scenario'; end if;
  if exists (select 1 from public.scenario_jobs where slug = p_slug and status in ('queued', 'running')) then
    raise exception 'job_already_pending';
  end if;
  insert into public.scenario_jobs (slug, seed, days, start_date, wallclock, weather_public, forecasts_public, events_public, requested_by)
  values (p_slug, p_seed, coalesce(p_days, v_scn.n_nights), p_start_date, coalesce(p_wallclock, v_scn.global_wallclock_seconds),
          v_scn.weather_public, v_scn.forecasts_public, v_scn.events_public, auth.uid())
  returning * into v_row;
  perform private.audit('scenario.rotate_queued', jsonb_build_object('slug', p_slug, 'job_id', v_row.id));
  return v_row;
end $$;
revoke execute on function public.admin_queue_scenario_job(text, integer, integer, date, integer) from public, anon;
grant execute on function public.admin_queue_scenario_job(text, integer, integer, date, integer) to authenticated;

-- Worker side: atomic claim, mirroring claim_submission.
create or replace function public.claim_scenario_job(p_worker text)
returns public.scenario_jobs language plpgsql security definer set search_path = public as $$
declare v_row public.scenario_jobs;
begin
  update public.scenario_jobs set status = 'running', worker_id = p_worker, started_at = now(), error = ''
  where id = (select id from public.scenario_jobs where status = 'queued' order by created_at limit 1 for update skip locked)
  returning * into v_row;
  return v_row;
end $$;
revoke execute on function public.claim_scenario_job(text) from public, anon, authenticated;

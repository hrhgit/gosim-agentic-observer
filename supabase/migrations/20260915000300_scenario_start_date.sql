-- A rotation has to rebuild the scenario on the same calendar, but the first observing night lived only inside
-- the generated files, so the first rotations silently fell back to the reference start date. Record it on the
-- scenario and let admin_queue_scenario_job carry it into the job.

alter table public.scenarios add column if not exists start_date date;

-- Participants may see it: the calendar is published in night_calendar.csv anyway.
grant select (start_date) on public.scenarios to anon, authenticated;

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
  values (p_slug, p_seed, coalesce(p_days, v_scn.n_nights), coalesce(p_start_date, v_scn.start_date),
          coalesce(p_wallclock, v_scn.global_wallclock_seconds),
          v_scn.weather_public, v_scn.forecasts_public, v_scn.events_public, auth.uid())
  returning * into v_row;
  perform private.audit('scenario.rotate_queued', jsonb_build_object('slug', p_slug, 'job_id', v_row.id));
  return v_row;
end $$;
revoke execute on function public.admin_queue_scenario_job(text, integer, integer, date, integer) from public, anon;
grant execute on function public.admin_queue_scenario_job(text, integer, integer, date, integer) to authenticated;

-- Restore the intended competition calendars; the rows regenerated before this migration lost them.
update public.scenarios set start_date = '2026-10-05' where slug = 'eval-a' and start_date is null;
update public.scenarios set start_date = '2026-11-01' where slug = 'eval-b' and start_date is null;

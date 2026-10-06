-- With results files the only way to submit, a team has to run its agent on its own machine, and that
-- needs the scenario's weather. The organisers decided (2026-09-24): the online competition's weather,
-- forecasts and weather events are published the moment the phase opens. Before that they stay hidden.
--
-- tile_anomalies.csv is not part of this: it is the answer key for anomaly reports and is never in the
-- downloadable list (see the "scenario files read" storage policy).
--
-- A database-side schedule does the switch, so it happens on time whether or not anyone is online.

create or replace function public.publish_open_phase_weather()
returns integer language plpgsql security definer set search_path = public as $$
declare n integer;
begin
  update public.scenarios s
     set weather_public = true, forecasts_public = true, events_public = true
   where (not s.weather_public or not s.forecasts_public or not s.events_public)
     and exists (select 1 from public.phase_scenarios ps join public.phases p on p.id = ps.phase_id
                  where ps.scenario_id = s.id and p.is_active and p.starts_at is not null and p.starts_at <= now());
  get diagnostics n = row_count;
  if n > 0 then
    perform private.audit('scenario.weather_published', jsonb_build_object('scenarios', n));
  end if;
  return n;
end $$;
revoke all on function public.publish_open_phase_weather() from public, anon, authenticated;

-- The schedule needs pg_cron, which the hosted database has. Plain Postgres (the migration tests) does
-- not; there the function is installed and simply never called on a timer.
do $cron$
begin
  if exists (select 1 from pg_available_extensions where name = 'pg_cron') then
    create extension if not exists pg_cron;
    perform cron.unschedule(jobid) from cron.job where jobname = 'publish-open-phase-weather';
    perform cron.schedule('publish-open-phase-weather', '* * * * *', 'select public.publish_open_phase_weather()');
  end if;
end $cron$;

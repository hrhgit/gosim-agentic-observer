-- Preserve automatic weather publication for legacy CSV phases only. New
-- online phases reveal observations incrementally through the session API.
create or replace function public.publish_open_phase_weather()
returns integer language plpgsql security definer set search_path=public,pg_temp as $$
declare n integer;
begin
  update public.scenarios s
    set weather_public=true,forecasts_public=true,events_public=true
  where (not s.weather_public or not s.forecasts_public or not s.events_public)
    and not exists (
      select 1 from public.phase_scenarios ps
      join public.observer_phase_settings o on o.phase_id=ps.phase_id
      where ps.scenario_id=s.id and (o.projects_enabled or o.local_sessions_enabled)
    )
    and exists (
      select 1 from public.phase_scenarios ps join public.phases p on p.id=ps.phase_id
      where ps.scenario_id=s.id and p.is_active and p.starts_at is not null and p.starts_at<=now()
    );
  get diagnostics n=row_count;
  if n>0 then perform private.audit('scenario.weather_published',jsonb_build_object('scenarios',n)); end if;
  return n;
end $$;
revoke all on function public.publish_open_phase_weather() from public,anon,authenticated;

-- A sealed phase keeps its scenarios unnamed until its results are published.
-- public.observer_scenario_listed (20260927000800) treated a scenario as listed
-- when it was linked to any active, open, non-final phase without an access
-- team; a *sealed* phase such as organizer-verify or a staging phase for hidden
-- v4 cards satisfies that too, so the names (and public columns) of its
-- scenarios were readable by participants although the phase itself was not.
-- Only that branch changes: a sealed phase no longer lists its scenarios.
create or replace function public.observer_scenario_listed(p_scenario uuid)
returns boolean language sql stable security definer set search_path=public,pg_temp as $$
  select not exists(select 1 from public.phase_scenarios ps join public.phases p on p.id=ps.phase_id
      left join public.observer_phase_settings s on s.phase_id=p.id
      where ps.scenario_id=p_scenario and (((p.counts_for_final or p.slug='online') and s.access_team_id is null
        and (p.starts_at is null or now()<p.starts_at)) or public.observer_phase_sealed(p.id)))
    or exists(select 1 from public.phase_scenarios ps join public.phases p on p.id=ps.phase_id
      left join public.observer_phase_settings s on s.phase_id=p.id
      where ps.scenario_id=p_scenario and not p.counts_for_final and p.slug<>'online' and p.is_active
        and s.access_team_id is null and not coalesce(s.sealed,false) and (p.starts_at is null or now()>=p.starts_at))
$$;

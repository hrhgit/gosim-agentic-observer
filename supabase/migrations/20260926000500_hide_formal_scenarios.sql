-- Formal scenarios stay unnamed until the competition opens. A scenario linked
-- to a formal phase (counts for the final, or the 'online' phase) that has not
-- started yet is invisible to everyone but administrators, unless it is also
-- used by an open, public, non-formal phase. The scenario contents were never
-- public; this also hides the names and the phase links.
create function public.observer_scenario_listed(p_scenario uuid)
returns boolean language sql stable security definer set search_path=public,pg_temp as $$
  select not exists(select 1 from public.phase_scenarios ps join public.phases p on p.id=ps.phase_id
      where ps.scenario_id=p_scenario and (p.counts_for_final or p.slug='online')
        and (p.starts_at is null or now()<p.starts_at))
    or exists(select 1 from public.phase_scenarios ps join public.phases p on p.id=ps.phase_id
      left join public.observer_phase_settings s on s.phase_id=p.id
      where ps.scenario_id=p_scenario and not p.counts_for_final and p.slug<>'online' and p.is_active
        and s.access_team_id is null and (p.starts_at is null or now()>=p.starts_at))
$$;
revoke all on function public.observer_scenario_listed(uuid) from public;
grant execute on function public.observer_scenario_listed(uuid) to anon,authenticated,service_role;

drop policy if exists "scenarios read" on public.scenarios;
create policy "scenarios read" on public.scenarios for select to anon, authenticated
  using ((is_active and public.observer_scenario_listed(id)) or public.is_admin());
drop policy if exists "phase_scenarios read" on public.phase_scenarios;
create policy "phase_scenarios read" on public.phase_scenarios for select to anon, authenticated
  using (public.observer_scenario_listed(scenario_id) or public.is_admin());

-- Private beta entry for the formal competition. While the site-wide switch
-- stays on practice, exactly one team-restricted phase becomes reachable for
-- its access team; anonymous visitors, ordinary participants and organizers
-- outside that team keep the unchanged single practice entry.
create function public.my_observer_phase()
returns uuid language sql stable security definer set search_path=public,pg_temp as $$
  select c.phase_id from public.observer_phase_settings c join public.phases p on p.id=c.phase_id
  where c.access_team_id is not null and (c.projects_enabled or c.local_sessions_enabled)
    and p.is_active and p.slug<>'practice'
    and (p.starts_at is null or now()>=p.starts_at) and (p.ends_at is null or now()<p.ends_at)
    and exists(select 1 from public.profiles u where u.id=auth.uid() and not u.is_banned
      and u.team_id=c.access_team_id)
  order by p.sort_order,p.id limit 1
$$;
revoke all on function public.my_observer_phase() from public,anon,authenticated;
grant execute on function public.my_observer_phase() to authenticated,service_role;

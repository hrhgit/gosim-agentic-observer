-- Optional team-restricted phases allow live acceptance tests without opening
-- project submissions to ordinary participants. Existing phases remain public.
alter table public.observer_phase_settings add column access_team_id uuid references public.teams(id);

create function public.observer_phase_visible(p_phase uuid)
returns boolean language sql stable security definer set search_path=public,pg_temp as $$
  select not exists(select 1 from public.observer_phase_settings c where c.phase_id=p_phase
    and c.access_team_id is not null and not exists(select 1 from public.profiles p
      where p.id=auth.uid() and not p.is_banned and (p.team_id=c.access_team_id or p.is_admin)))
$$;
revoke all on function public.observer_phase_visible(uuid) from public;
grant execute on function public.observer_phase_visible(uuid) to anon,authenticated,service_role;

create policy observer_phase_access on public.phases as restrictive for select to anon,authenticated
  using(public.observer_phase_visible(id));
create policy observer_phase_settings_access on public.observer_phase_settings as restrictive for select to anon,authenticated
  using(public.observer_phase_visible(phase_id));
create policy observer_phase_scenarios_access on public.phase_scenarios as restrictive for select to anon,authenticated
  using(public.observer_phase_visible(phase_id));

create function private.observer_restrict_phase_writes() returns trigger
language plpgsql security definer set search_path=public,pg_temp as $$
declare team uuid;
begin
  if tg_table_name='observer_batches' then
    if exists(select 1 from public.observer_phase_settings where phase_id=new.phase_id
      and access_team_id is not null and access_team_id<>new.team_id) then raise exception 'phase_closed'; end if;
  else
    select team_id into team from public.observer_projects where id=new.project_id;
    if not exists(select 1 from public.observer_phase_settings c join public.phases p on p.id=c.phase_id
      where c.projects_enabled and p.is_active and (p.ends_at is null or now()<p.ends_at)
        and (c.access_team_id is null or c.access_team_id=team)) then raise exception 'projects_not_enabled'; end if;
  end if;
  return new;
end $$;
create trigger observer_batch_access before insert on public.observer_batches
  for each row execute function private.observer_restrict_phase_writes();
create trigger observer_revision_access before insert on public.observer_revisions
  for each row execute function private.observer_restrict_phase_writes();
revoke all on function private.observer_restrict_phase_writes() from public,anon,authenticated;

-- One participant-facing competition. The switch never edits dates or scores.
create table private.observer_site_mode (
  id boolean primary key default true check(id),
  mode text not null default 'practice' check(mode in ('practice','competition')),
  phase_id uuid references public.phases(id),
  updated_at timestamptz not null default now()
);
insert into private.observer_site_mode(id) values(true);
revoke all on private.observer_site_mode from public,anon,authenticated;

create function public.current_competition()
returns jsonb language sql stable security definer set search_path=public,pg_temp as $$
  select jsonb_build_object('mode',m.mode,'phase_id',coalesce(m.phase_id,
    (select id from public.phases where slug=case when m.mode='practice' then 'practice' else 'online' end)))
  from private.observer_site_mode m where m.id
$$;

create function public.set_competition_mode(p_mode text)
returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$
declare v_phase uuid;
begin
  perform private.assert_not_banned();
  if not public.is_admin() then raise exception 'admin_required'; end if;
  if p_mode not in ('practice','competition') or p_mode is null then raise exception 'invalid_competition_mode'; end if;
  select id into v_phase from public.phases where is_active and slug=case when p_mode='practice' then 'practice' else 'online' end;
  if v_phase is null then raise exception 'competition_not_ready'; end if;
  if p_mode='competition' and (not exists(select 1 from public.observer_phase_settings where phase_id=v_phase
    and projects_enabled) or
    not exists(select 1 from public.phase_scenarios where phase_id=v_phase) or
    exists(select 1 from public.phase_scenarios ps where ps.phase_id=v_phase and not exists(
      select 1 from private.observer_scenario_calibration c where c.phase_id=ps.phase_id and c.scenario_id=ps.scenario_id))) then
    raise exception 'competition_not_ready'; end if;
  update private.observer_site_mode set mode=p_mode,phase_id=v_phase,updated_at=clock_timestamp() where id;
  perform private.audit('competition.mode',jsonb_build_object('mode',p_mode,'phase_id',v_phase));
  return public.current_competition();
end $$;
revoke all on function public.current_competition(),public.set_competition_mode(text) from public,anon,authenticated;
grant execute on function public.current_competition() to anon,authenticated;
grant execute on function public.set_competition_mode(text) to authenticated;

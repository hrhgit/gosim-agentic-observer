-- The formal phase now uses fixed scenarios without per-team calibration
-- (20260927000800). Switching the site to competition still demanded a
-- calibration row for every online scenario, so it would have been refused on
-- the opening day. Require an evaluation bundle for each scenario instead.
CREATE OR REPLACE FUNCTION public.set_competition_mode(p_mode text)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public', 'pg_temp'
AS $function$
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
      select 1 from private.observer_scenario_bundles b where b.scenario_id=ps.scenario_id))) then
    raise exception 'competition_not_ready'; end if;
  update private.observer_site_mode set mode=p_mode,phase_id=v_phase,updated_at=clock_timestamp() where id;
  perform private.audit('competition.mode',jsonb_build_object('mode',p_mode,'phase_id',v_phase));
  return public.current_competition();
end $function$
;

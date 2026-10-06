-- The online competition accepts a decisions.csv as well as an agent package.
-- The scorer holds the weather truth server-side, so a results file can be scored against a
-- hidden-weather scenario: the team simply cannot react to weather it never saw.
-- Drops the rule that refused results submissions for scenarios whose weather is not published.

update public.phases set allow_results = true where slug = 'online';

create or replace function public.create_submission(
  p_phase_slug text, p_kind public.submission_kind, p_scenario_slug text, p_storage_path text,
  p_filename text default '', p_sha256 text default '', p_title text default '', p_notes text default '')
returns bigint language plpgsql security definer set search_path = public as $$
declare
  v_uid uuid := auth.uid(); v_team uuid; v_phase public.phases; v_scn public.scenarios; v_id bigint; v_admin boolean := public.is_admin();
begin
  if v_uid is null then raise exception 'not_authenticated'; end if;
  select team_id into v_team from public.profiles where id = v_uid and not is_banned;
  if v_team is null then raise exception 'need_team'; end if;
  select * into v_phase from public.phases where slug = p_phase_slug and is_active;
  if not found then raise exception 'bad_phase'; end if;
  if public.phase_status(v_phase) <> 'open' and not v_admin then raise exception 'phase_closed'; end if;
  if p_kind = 'results' and not v_phase.allow_results then raise exception 'results_not_allowed'; end if;
  if p_kind = 'agent' and not v_phase.allow_agents then raise exception 'agents_not_allowed'; end if;
  if p_kind = 'results' then
    select s.* into v_scn from public.scenarios s join public.phase_scenarios ps on ps.scenario_id = s.id
    where s.slug = p_scenario_slug and ps.phase_id = v_phase.id and s.is_active;
    if not found then raise exception 'bad_scenario'; end if;
  end if;
  if p_storage_path is null or position(v_team::text || '/' in p_storage_path) <> 1 then raise exception 'bad_storage_path'; end if;
  if not v_admin and public.team_daily_count(p_phase_slug) >= v_phase.daily_limit then raise exception 'daily_limit'; end if;
  insert into public.submissions (team_id, user_id, phase_id, scenario_id, kind, title, notes, storage_path, original_filename, sha256)
  values (v_team, v_uid, v_phase.id, v_scn.id, p_kind, left(coalesce(p_title, ''), 160), left(coalesce(p_notes, ''), 2000), p_storage_path, left(coalesce(p_filename, ''), 255), coalesce(p_sha256, ''))
  returning id into v_id;
  perform private.audit('submission.create', jsonb_build_object('submission_id', v_id, 'kind', p_kind, 'phase', p_phase_slug));
  return v_id;
end $$;

notify pgrst, 'reload schema';

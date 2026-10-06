-- Avatar changes never reached the leaderboard: it only carried the team leader's GitHub handle
-- (PR #171 threaded avatar_url into me()/participants_wall()/team_members(), explicitly not here),
-- so an uploaded avatar was invisible there even though UserAvatar already knows how to show it.
-- Also give teams a real rename path: team_name is what the leaderboard shows, but besides the
-- admin-only rename there was no way to change it after creation -- including for the common case
-- of a solo team whose name was simply copied from the captain's nickname at signup.

drop function if exists public.leaderboard(text, integer, text);
create function public.leaderboard(p_phase_slug text default null, p_limit integer default 500, p_scenario_slug text default null)
returns table (
  rank integer, team_id uuid, team_name text, team_slug text, total_score double precision, science_score double precision,
  completion_rate double precision, uniformity_score double precision, base_science double precision, program_bonus double precision,
  request_reward double precision, coverage_bonus double precision, coverage_evenness double precision, penalty_total double precision,
  completed_tiles integer, required_missing integer, submission_count bigint, best_submission_id bigint,
  kind public.submission_kind, scored_at timestamptz, leader_github text, scenario_slug text, leader_avatar_url text)
language plpgsql stable security definer set search_path = public as $$
#variable_conflict use_column
declare
  v_phase public.phases;
  v_admin boolean := public.is_admin();
  v_targets uuid[];
  v_single text;
begin
  if p_phase_slug is null then
    select * into v_phase from public.phases p where p.is_active
    order by (p.counts_for_final and public.phase_status(p) in ('open', 'closed')) desc, (public.phase_status(p) = 'open') desc, p.sort_order limit 1;
  else
    select * into v_phase from public.phases p where p.slug = p_phase_slug and (p.is_active or v_admin);
  end if;
  if v_phase.id is null then return; end if;
  if v_phase.leaderboard_mode = 'hidden' and not v_admin then return; end if;

  if p_scenario_slug is not null then
    select array_agg(sc.id), min(sc.slug) into v_targets, v_single
    from public.phase_scenarios x join public.scenarios sc on sc.id = x.scenario_id
    where x.phase_id = v_phase.id and sc.slug = p_scenario_slug;
  elsif v_phase.counts_for_final then
    select array_agg(x.scenario_id) into v_targets from public.phase_scenarios x where x.phase_id = v_phase.id;
  else
    select array[sc.id], sc.slug into v_targets, v_single
    from public.phase_scenarios x join public.scenarios sc on sc.id = x.scenario_id
    where x.phase_id = v_phase.id
    order by sc.n_nights desc nulls last, sc.slug limit 1;
  end if;
  if v_targets is null or cardinality(v_targets) = 0 then return; end if;
  if cardinality(v_targets) = 1 and v_single is null then
    select sc.slug into v_single from public.scenarios sc where sc.id = v_targets[1];
  end if;

  return query
  with scored as (
    select e.submission_id, e.scenario_id, e.score, e.science_score, e.completion, e.uniformity, e.base_science, e.program_bonus,
           e.request_reward, e.coverage_bonus, e.coverage_evenness, e.penalty_total, e.completed_tiles, e.required_missing,
           coalesce(e.finished_at, s.finished_at, s.created_at) as done_at,
           s.team_id as tid, s.kind as skind, s.created_at as screated, t.name as tname, t.slug as tslug, t.leader_id as tleader
    from public.evaluations e
    join public.submissions s on s.id = e.submission_id
    join public.teams t on t.id = s.team_id
    where s.phase_id = v_phase.id and s.status = 'scored' and not s.is_excluded and e.score is not null
      and e.scenario_id = any(v_targets) and (not t.is_hidden or v_admin)
  ), best as (
    select distinct on (sc.tid, sc.scenario_id) sc.* from scored sc order by sc.tid, sc.scenario_id, sc.score desc, sc.screated asc
  ), agg as (
    select b.tid, min(b.tname) as tname, min(b.tslug) as tslug, (array_agg(b.tleader))[1] as tleader,
           avg(b.score) as a_score, avg(b.science_score) as a_science, avg(b.completion) as a_completion, avg(b.uniformity) as a_uniformity,
           avg(b.base_science) as a_base, avg(b.program_bonus) as a_bonus, avg(b.request_reward) as a_request,
           avg(b.coverage_bonus) as a_coverage, avg(b.coverage_evenness) as a_evenness, avg(b.penalty_total) as a_penalty,
           round(avg(b.completed_tiles))::integer as a_tiles, round(avg(b.required_missing))::integer as a_missing,
           max(b.submission_id) as best_id,
           case when count(distinct b.skind) = 1 then (array_agg(b.skind))[1] end as a_kind,
           max(b.done_at) as a_done, max(b.screated) as last_created, count(*) as n_scen
    from best b group by b.tid
  ), counts as (
    select sc.tid, count(distinct sc.submission_id) as n from scored sc group by sc.tid
  )
  select (rank() over (order by a.a_score desc))::integer, a.tid, a.tname, a.tslug, a.a_score, a.a_science, a.a_completion, a.a_uniformity,
         a.a_base, a.a_bonus, a.a_request, a.a_coverage, a.a_evenness, a.a_penalty, a.a_tiles, a.a_missing,
         c.n, a.best_id, a.a_kind, a.a_done,
         (select p.github from public.profiles p where p.id = a.tleader),
         v_single,
         (select p.avatar_url from public.profiles p where p.id = a.tleader)
  from agg a join counts c on c.tid = a.tid
  where a.n_scen = cardinality(v_targets)
  order by a.a_score desc, a.last_created asc
  limit greatest(1, least(coalesce(p_limit, 500), 1000));
end $$;
grant execute on function public.leaderboard(text, integer, text) to anon, authenticated;

-- Team leaders can now rename their own team (2-60 chars, unique, same bounds as create_team);
-- previously only admin_set_team(..., 'rename') could touch teams.name after creation.
-- A new parameter list is a distinct overload to Postgres, not a replacement, so drop the old one
-- first or PostgREST ends up with two ambiguous update_team functions.
drop function if exists public.update_team(text, text, integer, boolean);
create function public.update_team(p_project_idea text, p_github_repo text, p_max_size integer, p_is_locked boolean, p_name text default null)
returns void language plpgsql security definer set search_path = public as $$
declare v_uid uuid := auth.uid(); v_team public.teams; v_count integer; v_name text;
begin
  select * into v_team from public.teams where leader_id = v_uid for update;
  if not found then raise exception 'leader_only'; end if;
  select count(*) into v_count from public.profiles where team_id = v_team.id;
  if p_name is not null then
    v_name := trim(p_name);
    if length(v_name) < 2 or length(v_name) > 60 then raise exception 'name_length'; end if;
    if lower(v_name) <> lower(v_team.name) and exists (select 1 from public.teams where lower(name) = lower(v_name)) then
      raise exception 'name_taken';
    end if;
  end if;
  update public.teams set
    name = coalesce(v_name, name),
    project_idea = left(coalesce(p_project_idea, project_idea), 2000),
    github_repo = left(coalesce(p_github_repo, github_repo), 255),
    max_size = greatest(v_count, least(3, coalesce(p_max_size, max_size))),
    is_locked = coalesce(p_is_locked, is_locked)
  where id = v_team.id;
end $$;

notify pgrst, 'reload schema';

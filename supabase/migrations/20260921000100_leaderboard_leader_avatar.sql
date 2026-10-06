-- Leaderboard rows carry the team leader's GitHub handle, so every board shows real avatars
-- (the same falls back to a letter disc client-side when a leader has no handle).
-- Return type changes, so drop and recreate.

drop function if exists public.leaderboard(text, integer);
create function public.leaderboard(p_phase_slug text default null, p_limit integer default 500)
returns table (
  rank integer, team_id uuid, team_name text, team_slug text, total_score double precision, science_score double precision,
  completion_rate double precision, uniformity_score double precision, base_science double precision, program_bonus double precision,
  request_reward double precision, coverage_bonus double precision, coverage_evenness double precision, penalty_total double precision,
  completed_tiles integer, required_missing integer, submission_count bigint, best_submission_id bigint,
  kind public.submission_kind, scored_at timestamptz, leader_github text)
language plpgsql stable security definer set search_path = public as $$
declare v_phase public.phases; v_admin boolean := public.is_admin();
begin
  if p_phase_slug is null then
    select * into v_phase from public.phases p where p.is_active
    order by (p.counts_for_final and public.phase_status(p) in ('open', 'closed')) desc, (public.phase_status(p) = 'open') desc, p.sort_order limit 1;
  else
    select * into v_phase from public.phases p where p.slug = p_phase_slug and (p.is_active or v_admin);
  end if;
  if v_phase.id is null then return; end if;
  if v_phase.leaderboard_mode = 'hidden' and not v_admin then return; end if;
  return query
  with scored as (
    select s.*, t.name as tname, t.slug as tslug, t.leader_id as tleader
    from public.submissions s join public.teams t on t.id = s.team_id
    where s.phase_id = v_phase.id and s.status = 'scored' and not s.is_excluded and s.score is not null and (not t.is_hidden or v_admin)
  ), best as (
    select distinct on (s.team_id) s.* from scored s order by s.team_id, s.score desc, s.created_at asc
  ), counts as (
    select s.team_id, count(*) as n from scored s group by s.team_id
  )
  select (rank() over (order by b.score desc))::integer, b.team_id, b.tname, b.tslug, b.score, b.science_score, b.completion, b.uniformity,
         b.base_science, b.program_bonus, b.request_reward, b.coverage_bonus, b.coverage_evenness, b.penalty_total, b.completed_tiles, b.required_missing,
         c.n, b.id, b.kind, coalesce(b.finished_at, b.created_at),
         (select p.github from public.profiles p where p.id = b.tleader)
  from best b join counts c on c.team_id = b.team_id
  order by b.score desc, b.created_at asc
  limit greatest(1, least(coalesce(p_limit, 500), 1000));
end $$;
grant execute on function public.leaderboard(text, integer) to anon, authenticated;

notify pgrst, 'reload schema';

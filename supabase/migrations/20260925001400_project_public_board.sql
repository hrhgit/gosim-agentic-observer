create or replace function public.observer_leaderboard(p_phase uuid,p_limit integer default 100)
returns table(rank bigint,team_id uuid,team_name text,batch_id uuid,score double precision,finished_at timestamptz)
language sql stable security definer set search_path = public, pg_temp as $$
  with best as (
    select distinct on(b.team_id) b.team_id,t.name,b.id,b.score,b.finished_at,b.created_at
    from public.observer_batches b join public.teams t on t.id=b.team_id join public.phases p on p.id=b.phase_id
    where b.phase_id=p_phase and public.observer_phase_visible(p_phase) and b.purpose='formal' and b.status='scored' and p.is_active
      and (public.is_admin() or (not t.is_hidden and p.leaderboard_mode in ('live','published')))
    order by b.team_id,b.score desc,b.created_at
  )
  select rank() over(order by b.score desc),b.team_id,b.name,b.id,b.score,b.finished_at
    from best b order by b.score desc,b.created_at limit greatest(1,least(coalesce(p_limit,100),1000))
$$;

-- New online boards use the best complete batch, never independent scenario
-- maxima. Only public aggregate fields are returned; result artifacts stay private.
create function public.observer_board(p_phase uuid,p_limit integer default 100)
returns jsonb language sql stable security definer set search_path=public,pg_temp as $$
 select coalesce(jsonb_agg(to_jsonb(rows) order by rows.rank,rows.scored_at),'[]') from (
  select board.rank,board.team_id,board.team_name,board.score as total_score,board.finished_at as scored_at,
    board.batch_id as observer_batch_id,'observer'::text as kind,
    avg(coalesce((r.score_summary->'score'->>'base_science')::double precision,0)) as base_science,
    avg(coalesce((r.score_summary->'score'->>'program_bonus')::double precision,0)) as program_bonus,
    avg(coalesce((r.score_summary->'score'->>'request_reward')::double precision,0)) as request_reward,
    avg(coalesce((r.score_summary->'score'->>'report_reward')::double precision,0)) as report_reward,
    avg(coalesce((r.score_summary->'score'->>'coverage_bonus')::double precision,0)) as coverage_bonus,
    avg(coalesce((r.score_summary->'score'->>'coverage_evenness')::double precision,0)) as coverage_evenness,
    avg(coalesce((select sum(value::double precision) from jsonb_each_text(r.score_summary->'score'->'penalties')),0)) as penalty_total,
    avg((r.score_summary->>'completed_tiles')::double precision) as completed_tiles,
    avg((r.score_summary->>'required_missing')::double precision) as required_missing,
    (select count(*) from public.observer_batches b where b.phase_id=p_phase and b.team_id=board.team_id
      and b.purpose='formal' and b.status='scored') as submission_count
  from public.observer_leaderboard(p_phase,p_limit) board
  join public.observer_runs r on r.batch_id=board.batch_id
  where public.observer_phase_visible(p_phase)
  group by board.rank,board.team_id,board.team_name,board.score,board.finished_at,board.batch_id
 ) rows
$$;
revoke all on function public.observer_board(uuid,integer) from public;
grant execute on function public.observer_board(uuid,integer) to anon,authenticated,service_role;

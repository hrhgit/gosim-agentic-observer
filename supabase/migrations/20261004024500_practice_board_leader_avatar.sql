-- #223 added leader_avatar_url only to the legacy leaderboard(); the practice board reads
-- observer_board()/observer_card_board(), so uploaded avatars never showed there.
-- Adds leader_github + leader_avatar_url (the fields CardBoardTable already renders).
-- Already applied to production.
CREATE OR REPLACE FUNCTION public.observer_board(p_phase uuid, p_limit integer DEFAULT 100)
 RETURNS jsonb
 LANGUAGE sql
 STABLE SECURITY DEFINER
 SET search_path TO 'public', 'pg_temp'
AS $function$
 select coalesce(jsonb_agg(to_jsonb(rows) order by rows.rank,rows.scored_at),'[]') from (
  select board.rank,board.team_id,board.team_name,board.score as total_score,board.finished_at as scored_at,
    board.batch_id as observer_batch_id,'observer'::text as kind,
    (select p.github from public.teams tt join public.profiles p on p.id=tt.leader_id where tt.id=board.team_id) as leader_github,(select p.avatar_url from public.teams tt join public.profiles p on p.id=tt.leader_id where tt.id=board.team_id) as leader_avatar_url,
    bool_and(r.score_summary ? 'calibration') as calibrated,
    avg(coalesce((parts.raw->>'total')::double precision,r.score)) as raw_total_score,
    avg(coalesce((parts.raw->>'base_science')::double precision,0)) as base_science,
    avg(coalesce((parts.raw->>'program_bonus')::double precision,0)) as program_bonus,
    avg(coalesce((parts.raw->>'request_reward')::double precision,0)) as request_reward,
    avg(coalesce((parts.raw->>'report_reward')::double precision,0)) as report_reward,
    avg(coalesce((parts.raw->>'coverage_bonus')::double precision,0)) as coverage_bonus,
    avg(coalesce((parts.raw->>'coverage_evenness')::double precision,0)) as coverage_evenness,
    avg(coalesce((select sum(value::double precision) from jsonb_each_text(parts.raw->'penalties')),0)) as penalty_total,
    avg((r.score_summary->>'completed_tiles')::double precision) as completed_tiles,
    avg((r.score_summary->>'required_missing')::double precision) as required_missing,
    (select count(*) from public.observer_batches b where b.phase_id=p_phase and b.team_id=board.team_id
      and b.purpose='formal' and b.status='scored') as submission_count
  from public.observer_leaderboard(p_phase,p_limit) board
  join public.observer_runs r on r.batch_id=board.batch_id
  cross join lateral (select coalesce(r.score_summary->'raw_score',r.score_summary->'score') as raw) parts
  where public.observer_phase_visible(p_phase)
  group by board.rank,board.team_id,board.team_name,board.score,board.finished_at,board.batch_id
 ) rows
$function$;

CREATE OR REPLACE FUNCTION public.observer_card_board(p_phase uuid, p_scenario_slug text DEFAULT NULL::text, p_limit integer DEFAULT 100)
 RETURNS jsonb
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO 'public', 'pg_temp'
AS $function$
declare
  v_layout text; v_mode text; v_named boolean; v_cards jsonb:='[]'; v_card uuid; v_rows jsonb;
  v_limit integer:=greatest(1,least(coalesce(p_limit,100),1000));
  v_zero boolean:=private.observer_failed_cards_score_zero(p_phase);
begin
  select coalesce(s.board_layout,'overall'),p.leaderboard_mode into v_layout,v_mode
    from public.phases p left join public.observer_phase_settings s on s.phase_id=p.id where p.id=p_phase;
  if v_layout is null or not public.observer_phase_visible(p_phase) then
    return jsonb_build_object('layout',coalesce(v_layout,'overall'),'cards','[]'::jsonb,'scenario',null,'rows','[]'::jsonb);
  end if;
  if v_layout='overall' then
    return jsonb_build_object('layout','overall','cards','[]'::jsonb,'scenario',null,
      'rows',public.observer_board(p_phase,v_limit));
  end if;

  select coalesce(bool_and(public.is_admin() or v_mode='published' or public.observer_scenario_listed(sc.id)),false),
         coalesce(jsonb_agg(jsonb_build_object('slug',sc.slug,'name',sc.name) order by sc.slug),'[]'::jsonb)
    into v_named,v_cards
    from public.phase_scenarios ps join public.scenarios sc on sc.id=ps.scenario_id where ps.phase_id=p_phase;
  if not v_named then v_cards:='[]'; end if;

  if p_scenario_slug is not null then
    select sc.id into v_card from public.phase_scenarios ps join public.scenarios sc on sc.id=ps.scenario_id
      where ps.phase_id=p_phase and sc.slug=p_scenario_slug;
    if v_card is null or not v_named then
      return jsonb_build_object('layout',v_layout,'cards',v_cards,'scenario',null,'rows','[]'::jsonb);
    end if;
  end if;

  with cards as (select scenario_id from public.phase_scenarios where phase_id=p_phase),
  eligible as (
    select b.id,b.team_id,t.name as team_name,b.created_at,b.finished_at,avg(coalesce(r.score,0)) as overall
    from public.observer_batches b join public.teams t on t.id=b.team_id join public.phases p on p.id=b.phase_id
    join public.observer_runs r on r.batch_id=b.id and r.scenario_id in (select scenario_id from cards)
      and (r.status='scored' or (v_zero and private.observer_participant_failure(r.id)))
    where b.phase_id=p_phase and b.purpose='formal' and b.status='scored' and p.is_active
      and (public.is_admin() or (not t.is_hidden and p.leaderboard_mode in ('live','published')))
    group by b.id,t.name
    having count(*)=(select count(*) from cards) and count(*)>0
  ),
  best as (
    select distinct on(e.team_id) e.* from eligible e order by e.team_id,e.overall desc,e.created_at
  ),
  ranked as (
    select b.*,rank() over(order by b.overall desc) as overall_rank,
      (select count(*) from public.observer_batches x where x.phase_id=p_phase and x.team_id=b.team_id
        and x.purpose='formal' and x.status='scored') as submission_count
    from best b
  )
  select coalesce(jsonb_agg(to_jsonb(rows) order by rows.rank,rows.scored_at,rows.team_name),'[]'::jsonb) into v_rows from (
    select * from (
      select
        case when v_card is null then k.overall_rank else rank() over(order by coalesce(r.score,0) desc) end as rank,
        k.team_id,k.team_name,
        (select p.github from public.teams tt join public.profiles p on p.id=tt.leader_id where tt.id=k.team_id) as leader_github,(select p.avatar_url from public.teams tt join public.profiles p on p.id=tt.leader_id where tt.id=k.team_id) as leader_avatar_url,
        case when v_card is null then k.overall else coalesce(r.score,0) end as total_score,
        k.overall as overall_score,k.overall_rank,k.finished_at as scored_at,
        k.id as observer_batch_id,'observer'::text as kind,
        case when v_card is null then null else p_scenario_slug end as scenario_slug,
        k.submission_count,
        case when v_card is null then (
          select case when v_named then jsonb_object_agg(sc.slug,coalesce(rr.score,0)) end
            from public.observer_runs rr join public.scenarios sc on sc.id=rr.scenario_id
            where rr.batch_id=k.id and rr.scenario_id in (select scenario_id from cards)
        ) end as card_scores,
        case when v_card is null then (
          select case when v_named then coalesce(jsonb_agg(sc.slug order by sc.slug),'[]'::jsonb) end
            from public.observer_runs rr join public.scenarios sc on sc.id=rr.scenario_id
            where rr.batch_id=k.id and rr.scenario_id in (select scenario_id from cards) and rr.status<>'scored'
        ) end as unfinished_cards,
        case when v_card is null then null else r.status<>'scored' end as unfinished,
        agg.calibrated,agg.raw_total_score,agg.base_science,agg.program_bonus,agg.request_reward,agg.report_reward,
        agg.coverage_bonus,agg.coverage_evenness,agg.penalty_total,agg.completed_tiles,agg.required_missing,
        agg.targets_observed,agg.components,
        case when v_card is null then null else (
          select case when r.status='scored' and r.score_summary->>'termination_reason' ~ '^[a-z_]{1,64}$' then r.score_summary->>'termination_reason' end
        ) end as termination_reason
      from ranked k
      left join public.observer_runs r on v_card is not null and r.batch_id=k.id and r.scenario_id=v_card
      cross join lateral (
        select bool_and(x.score_summary ? 'calibration') filter (where x.status='scored') as calibrated,
          avg(case when x.status='scored' then coalesce((parts.raw->>'total')::double precision,x.score) else 0 end) as raw_total_score,
          avg(coalesce((parts.raw->>'base_science')::double precision,0)) as base_science,
          avg(coalesce((parts.raw->>'program_bonus')::double precision,0)) as program_bonus,
          avg(coalesce((parts.raw->>'request_reward')::double precision,0)) as request_reward,
          avg(coalesce((parts.raw->>'report_reward')::double precision,0)) as report_reward,
          avg(coalesce((parts.raw->>'coverage_bonus')::double precision,0)) as coverage_bonus,
          avg(coalesce((parts.raw->>'coverage_evenness')::double precision,0)) as coverage_evenness,
          avg(coalesce(case when jsonb_typeof(parts.raw->'penalties')='object' then
            (select sum(value::double precision) from jsonb_each_text(parts.raw->'penalties')) end,0)) as penalty_total,
          avg((x.score_summary->>'completed_tiles')::double precision) filter (where x.status='scored') as completed_tiles,
          avg(private.observer_summary_count(x.score_summary,'required_missing')) filter (where x.status='scored') as required_missing,
          avg(private.observer_summary_count(x.score_summary,'targets_observed')) filter (where x.status='scored') as targets_observed,
          (select coalesce(jsonb_object_agg(c.key,c.value),'{}'::jsonb) from (
            select e.key,case when v_zero then sum(e.value::text::double precision)/greatest(1,(select count(*) from public.observer_runs z where z.batch_id=k.id
                  and (case when v_card is null then z.scenario_id in (select scenario_id from cards) else z.scenario_id=v_card end)))
                else avg(e.value::text::double precision) end as value
              from public.observer_runs y cross join lateral jsonb_each(private.observer_score_components(y.score_summary)) e
              where y.batch_id=k.id and y.status='scored'
                and (case when v_card is null then y.scenario_id in (select scenario_id from cards) else y.scenario_id=v_card end)
              group by e.key) c) as components
        from public.observer_runs x
        -- A voided (rejected) or failed card contributes 0, never its old summary.
        cross join lateral (select case when x.status='scored' then coalesce(x.score_summary->'raw_score',x.score_summary->'score') end as raw) parts
        where x.batch_id=k.id and (case when v_card is null then x.scenario_id in (select scenario_id from cards) else x.scenario_id=v_card end)
      ) agg
    ) all_rows order by rank,scored_at,team_name limit v_limit
  ) rows;

  return jsonb_build_object('layout',v_layout,'cards',v_cards,
    'scenario',case when v_card is null then null else p_scenario_slug end,'rows',v_rows);
end $function$;

-- Hidden final board and runner pool (2026-10-04).
--
-- 1. public.observer_card_board (based on the live definition, 20261004030000).
--    In a phase with repeat_runs > 1 each row also carries the range of the
--    averaged evaluations: score_range [lowest, highest] (the evaluation means on
--    the overall tab, this card's scores on a card tab) and, on the overall tab,
--    card_ranges {card: [lowest, highest]}. Nothing changes for phases with one
--    evaluation. Organizers (is_admin) also see the board of an inactive phase,
--    so a rehearsal phase can be checked on the site; everyone else is unchanged
--    (observer_phase_visible, leaderboard_mode and is_active still apply).
-- 2. private.observer_public_candidate (based on 20261002000500): in overflow
--    mode a sealed phase switched on for the public runner pool prefers the pool
--    (no Actions minutes) instead of waiting for its organization to be busy or
--    near its minutes. A sealed phase reaches this only with
--    sealed_transfer_verified, which the drill of 2026-10-04 set
--    (ops/public-runner-pool.md). Unsealed phases are unchanged.
-- 3. The hidden final's phase description (shown on its board once published)
--    says 3 evaluations per card, as the rules do since 20261004030000.

create or replace function public.observer_card_board(p_phase uuid, p_scenario_slug text default null, p_limit integer default 100)
returns jsonb language plpgsql stable security definer set search_path=public,pg_temp as $$
declare
  v_layout text; v_mode text; v_named boolean; v_cards jsonb:='[]'; v_card uuid; v_rows jsonb;
  v_limit integer:=greatest(1,least(coalesce(p_limit,100),1000));
  v_zero boolean:=private.observer_failed_cards_score_zero(p_phase);
  v_repeat integer; v_final boolean:=private.observer_final_phase(p_phase);
begin
  select coalesce(s.board_layout,'overall'),p.leaderboard_mode,greatest(1,coalesce(s.repeat_runs,1)) into v_layout,v_mode,v_repeat
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
    select b.id,b.team_id,t.name as team_name,b.revision_id,b.created_at,b.finished_at,avg(coalesce(r.score,0)) as overall
    from public.observer_batches b join public.teams t on t.id=b.team_id join public.phases p on p.id=b.phase_id
    join public.observer_runs r on r.batch_id=b.id and r.scenario_id in (select scenario_id from cards)
      and (r.status='scored' or (v_zero and private.observer_participant_failure(r.id)))
    where b.phase_id=p_phase and b.purpose='formal' and b.status='scored' and (p.is_active or public.is_admin())
      and (public.is_admin() or (not t.is_hidden and p.leaderboard_mode in ('live','published')))
    group by b.id,t.name
    having count(*)=(select count(*) from cards) and count(*)>0
  ),
  -- One batch per team: its best one; with repeat_runs > 1 its first repeat_runs complete ones, averaged.
  best as (
    select x.* from (select distinct on(e.team_id) e.id,e.team_id,e.team_name,e.created_at,e.finished_at,e.overall,
        array[e.id] as batch_ids,e.overall as low,e.overall as high
      from eligible e order by e.team_id,e.overall desc,e.created_at) x where v_repeat=1
    union all
    select (array_agg(o.id order by o.created_at,o.id))[1],o.team_id,min(o.team_name),min(o.created_at),max(o.finished_at),
      avg(o.overall),array_agg(o.id order by o.created_at,o.id),min(o.overall),max(o.overall)
    from (select e.*,row_number() over(partition by e.team_id order by e.created_at,e.id) as n from eligible e) o
    where v_repeat>1 and o.n<=v_repeat
    group by o.team_id having count(*)=v_repeat
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
        case when v_repeat>1 then cardinality(k.batch_ids) end as averaged_runs,
        -- Lowest and highest of the averaged evaluations: overall, this card, or per card on the overall tab.
        case when v_repeat>1 then case when v_card is null then jsonb_build_array(k.low,k.high)
          else jsonb_build_array(r.low,r.high) end end as score_range,
        case when v_repeat>1 and v_card is null then (
          select case when v_named then jsonb_object_agg(c.slug,jsonb_build_array(c.low,c.high)) end from (
            select sc.slug,min(coalesce(rr.score,0)) as low,max(coalesce(rr.score,0)) as high
            from public.observer_runs rr join public.scenarios sc on sc.id=rr.scenario_id
            where rr.batch_id=any(k.batch_ids) and rr.scenario_id in (select scenario_id from cards) group by sc.slug) c
        ) end as card_ranges,
        case when v_card is null then (
          select case when v_named then jsonb_object_agg(c.slug,c.score) end from (
            select sc.slug,avg(coalesce(rr.score,0)) as score
            from public.observer_runs rr join public.scenarios sc on sc.id=rr.scenario_id
            where rr.batch_id=any(k.batch_ids) and rr.scenario_id in (select scenario_id from cards) group by sc.slug) c
        ) end as card_scores,
        case when v_card is null then (
          select case when v_named then coalesce(jsonb_agg(c.slug order by c.slug),'[]'::jsonb) end from (
            select distinct sc.slug from public.observer_runs rr join public.scenarios sc on sc.id=rr.scenario_id
            where rr.batch_id=any(k.batch_ids) and rr.scenario_id in (select scenario_id from cards) and rr.status<>'scored') c
        ) end as unfinished_cards,
        case when v_card is null then null else r.unfinished end as unfinished,
        case when v_final then (
          select jsonb_build_object('chosen',f.chosen is not null,'score',(
            select case when v_card is null then e.overall else (select coalesce(rr.score,0) from public.observer_runs rr
                where rr.batch_id=e.id and rr.scenario_id=v_card) end
              from eligible e where e.team_id=k.team_id and e.revision_id=f.chosen
              order by e.overall desc,e.created_at limit 1))
          from (select (private.observer_final_version_state(k.team_id,p_phase)->>'chosen_revision_id')::uuid as chosen) f
        ) end as final_version,
        agg.calibrated,agg.raw_total_score,agg.base_science,agg.program_bonus,agg.request_reward,agg.report_reward,
        agg.coverage_bonus,agg.coverage_evenness,agg.penalty_total,agg.completed_tiles,agg.required_missing,
        agg.targets_observed,agg.components,
        case when v_card is null then null else r.termination_reason end as termination_reason
      from ranked k
      left join lateral (
        select avg(coalesce(x.score,0)) as score,bool_or(x.status<>'scored') as unfinished,
          min(coalesce(x.score,0)) as low,max(coalesce(x.score,0)) as high,
          case when count(*)=1 then max(case when x.status='scored' and x.score_summary->>'termination_reason' ~ '^[a-z_]{1,64}$'
            then x.score_summary->>'termination_reason' end) end as termination_reason
        from public.observer_runs x where x.batch_id=any(k.batch_ids) and x.scenario_id=v_card
      ) r on v_card is not null
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
            select e.key,case when v_zero then sum(e.value::text::double precision)/greatest(1,(select count(*) from public.observer_runs z where z.batch_id=any(k.batch_ids)
                  and (case when v_card is null then z.scenario_id in (select scenario_id from cards) else z.scenario_id=v_card end)))
                else avg(e.value::text::double precision) end as value
              from public.observer_runs y cross join lateral jsonb_each(private.observer_score_components(y.score_summary)) e
              where y.batch_id=any(k.batch_ids) and y.status='scored'
                and (case when v_card is null then y.scenario_id in (select scenario_id from cards) else y.scenario_id=v_card end)
              group by e.key) c) as components
        from public.observer_runs x
        -- A voided (rejected) or failed card contributes 0, never its old summary.
        cross join lateral (select case when x.status='scored' then coalesce(x.score_summary->'raw_score',x.score_summary->'score') end as raw) parts
        where x.batch_id=any(k.batch_ids) and (case when v_card is null then x.scenario_id in (select scenario_id from cards) else x.scenario_id=v_card end)
      ) agg
    ) all_rows order by rank,scored_at,team_name limit v_limit
  ) rows;

  return jsonb_build_object('layout',v_layout,'cards',v_cards,
    'scenario',case when v_card is null then null else p_scenario_slug end,'rows',v_rows);
end $$;

create or replace function private.observer_public_candidate(p_job uuid,p_require_trigger boolean default true)
returns boolean language plpgsql stable security definer set search_path=public,pg_temp as $$
declare p private.observer_public_pool; v private.observer_jobs; v_phase uuid; v_user uuid; v_sealed boolean;
  v_load record;
begin
  select * into p from private.observer_public_pool where id;
  if not found or p.mode='off' or p.approved_sha is null then return false; end if;
  if not exists(select 1 from private.observer_installations i where i.organization=p.organization and i.enabled) then
    return false; end if;
  select * into v from private.observer_jobs where id=p_job;
  if not found or v.kind<>'engine' or v.runner<>'github-hosted' or v.status<>'queued' or v.run_id is null
    or v.expires_at<=now() or v.public_declined_at is not null then return false; end if;
  if exists(select 1 from private.observer_jobs o where o.run_id=v.run_id and o.id<>v.id)
    or exists(select 1 from private.observer_scenario_instances i where i.run_id=v.run_id) then return false; end if;
  select b.phase_id,b.user_id,c.sealed into v_phase,v_user,v_sealed
    from public.observer_runs r join public.observer_batches b on b.id=r.batch_id
    join public.observer_phase_settings c on c.phase_id=b.phase_id
    where r.id=v.run_id and b.mode='project' and c.colocated;
  if v_phase is null or not exists(select 1 from private.observer_public_pool_phases f
    where f.phase_id=v_phase and f.enabled) then return false; end if;
  if v_sealed and not p.sealed_transfer_verified then return false; end if;
  if (select count(*) from private.observer_jobs j where j.runner='public-hosted'
      and j.status in ('queued','dispatched','claimed') and j.expires_at>now())>=p.max_active
    or private.observer_public_month_minutes()>=p.monthly_minute_cap then return false; end if;
  if v_user=any(p.drill_users) then return true; end if;
  if p.mode<>'overflow' then return false; end if;
  -- A sealed phase (the hidden final) switched on for the pool prefers it: its
  -- jobs cost no Actions minutes there, up to max_active at a time and the
  -- monthly cap; the rest run in their own organizations. Reaching this line
  -- already required sealed_transfer_verified.
  if v_sealed then return true; end if;
  if not coalesce(p_require_trigger,true) then return true; end if;
  select * into v_load from public.observer_organizations_by_load() o where o.organization=v.organization;
  return v_load.organization is null or v_load.over_limit
    or v_load.month_minutes>=v_load.monthly_minute_limit*p.overflow_ratio or v_load.active_jobs>=p.busy_jobs;
end $$;

update public.phases set
  description_en='Final ranking. After the online competition ends, each team''s final version is evaluated 3 times on each of four hidden cards E, F, G and H that no participant has seen, 900 s per card. Each card scores the mean of its 3 evaluations, and the final score is the arithmetic mean of the four card scores; only this score decides the final ranking. Exact ties are settled by the organizers and announced with the results.',
  description_zh='最终排名。线上赛结束后，每队的最终版本在四张没有选手见过的隐藏任务卡 E、F、G、H 上各评测 3 次，每张卡 900 秒。每张卡取 3 次评测的平均分，最终成绩为四张卡平均分的算术平均值；最终排名只看这个成绩。严格同分由主办方决定，并随成绩一起公布。'
  where slug='final-hidden';

revoke all on function public.observer_card_board(uuid,text,integer) from public;
grant execute on function public.observer_card_board(uuid,text,integer) to anon,authenticated,service_role;
revoke all on function private.observer_public_candidate(uuid,boolean) from public,anon,authenticated;
notify pgrst,'reload schema';

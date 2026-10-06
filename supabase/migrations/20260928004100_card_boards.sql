-- Per-card boards for complete-project phases (v4 gameplay, organizer decision
-- 2026-09-28). Additive only: existing boards, RPCs and phase settings keep
-- their behaviour until an organizer changes a phase's board_layout.
--
-- observer_phase_settings.board_layout:
--   'overall'        (default) the existing batch-mean board, public.observer_board;
--   'cards'          one board per card (scenario) of the phase, no overall tab;
--   'cards_overall'  one board per card plus the overall board.
-- In the card layouts a team's entry comes from its best *complete* batch: the
-- scored formal batch with a scored run on every card currently linked to the
-- phase and the highest mean over those cards. The per-card board ranks that
-- same batch's run on the card; the overall board ranks the mean. Batches made
-- for an earlier scenario set of the phase (e.g. before a v3 -> v4 switch) are
-- therefore never mixed into a card board.
-- Visibility is the one of observer_board: nothing for an invisible phase
-- (team-restricted or sealed and unpublished), hidden teams and non-live boards
-- only for organizers. Card names are returned only when participants may read
-- them (listed scenario) or the phase's results are published.
--
-- private.observer_phase_config_snapshots keeps the exact phase configuration
-- before scripts/configure-v4-phases.py switches phases, so its --reverse mode
-- can restore it.

alter table public.observer_phase_settings add column if not exists board_layout text not null default 'overall';
do $c$ begin
  if not exists(select 1 from pg_constraint where conname='observer_phase_settings_board_layout_check') then
    alter table public.observer_phase_settings add constraint observer_phase_settings_board_layout_check
      check (board_layout in ('overall','cards','cards_overall'));
  end if;
end $c$;

create table if not exists private.observer_phase_config_snapshots (
  id bigserial primary key,
  label text not null check (length(label) between 1 and 64),
  snapshot jsonb not null,
  taken_at timestamptz not null default now(),
  restored_at timestamptz
);
revoke all on private.observer_phase_config_snapshots from public,anon,authenticated;
revoke all on sequence private.observer_phase_config_snapshots_id_seq from public,anon,authenticated;

-- Numeric score components of one run, from the v4 'components' object when
-- present, else the numeric top-level fields of the (raw) score. Never returns
-- nested evidence, paths or strings.
create or replace function private.observer_score_components(p_summary jsonb)
returns jsonb language sql immutable set search_path=public,pg_temp as $$
  with score as (select coalesce(p_summary->'raw_score',p_summary->'score') as s),
  source as (select case when jsonb_typeof(s->'components')='object' then s->'components'
                         when jsonb_typeof(s)='object' then s-'total' else '{}'::jsonb end as c from score)
  select coalesce(jsonb_object_agg(e.key,e.value) filter (where jsonb_typeof(e.value)='number'),'{}'::jsonb)
    from source,jsonb_each(source.c) e
$$;
revoke all on function private.observer_score_components(jsonb) from public,anon,authenticated;

create or replace function private.observer_summary_count(p_summary jsonb,p_key text)
returns double precision language sql immutable set search_path=public,pg_temp as $$
  select case when jsonb_typeof(v)='number' then v::text::double precision end
    from (select coalesce(p_summary->p_key,p_summary->'counts'->p_key) as v) x
$$;
revoke all on function private.observer_summary_count(jsonb,text) from public,anon,authenticated;

create or replace function public.observer_card_board(p_phase uuid,p_scenario_slug text default null,p_limit integer default 100)
returns jsonb language plpgsql stable security definer set search_path=public,pg_temp as $$
declare
  v_layout text; v_mode text; v_named boolean; v_cards jsonb:='[]'; v_card uuid; v_rows jsonb;
  v_limit integer:=greatest(1,least(coalesce(p_limit,100),1000));
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
    select b.id,b.team_id,t.name as team_name,b.created_at,b.finished_at,avg(r.score) as overall
    from public.observer_batches b join public.teams t on t.id=b.team_id join public.phases p on p.id=b.phase_id
    join public.observer_runs r on r.batch_id=b.id and r.status='scored' and r.scenario_id in (select scenario_id from cards)
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
        case when v_card is null then k.overall_rank else rank() over(order by r.score desc) end as rank,
        k.team_id,k.team_name,
        case when v_card is null then k.overall else r.score end as total_score,
        k.overall as overall_score,k.overall_rank,k.finished_at as scored_at,
        k.id as observer_batch_id,'observer'::text as kind,
        case when v_card is null then null else p_scenario_slug end as scenario_slug,
        k.submission_count,
        case when v_card is null then (
          select case when v_named then jsonb_object_agg(sc.slug,rr.score) end
            from public.observer_runs rr join public.scenarios sc on sc.id=rr.scenario_id
            where rr.batch_id=k.id and rr.scenario_id in (select scenario_id from cards)
        ) end as card_scores,
        agg.calibrated,agg.raw_total_score,agg.base_science,agg.program_bonus,agg.request_reward,agg.report_reward,
        agg.coverage_bonus,agg.coverage_evenness,agg.penalty_total,agg.completed_tiles,agg.required_missing,
        agg.targets_observed,agg.components,
        case when v_card is null then null else (
          select case when r.score_summary->>'termination_reason' ~ '^[a-z_]{1,64}$' then r.score_summary->>'termination_reason' end
        ) end as termination_reason
      from ranked k
      left join public.observer_runs r on v_card is not null and r.batch_id=k.id and r.scenario_id=v_card
      cross join lateral (
        select bool_and(x.score_summary ? 'calibration') as calibrated,
          avg(coalesce((parts.raw->>'total')::double precision,x.score)) as raw_total_score,
          avg(coalesce((parts.raw->>'base_science')::double precision,0)) as base_science,
          avg(coalesce((parts.raw->>'program_bonus')::double precision,0)) as program_bonus,
          avg(coalesce((parts.raw->>'request_reward')::double precision,0)) as request_reward,
          avg(coalesce((parts.raw->>'report_reward')::double precision,0)) as report_reward,
          avg(coalesce((parts.raw->>'coverage_bonus')::double precision,0)) as coverage_bonus,
          avg(coalesce((parts.raw->>'coverage_evenness')::double precision,0)) as coverage_evenness,
          avg(coalesce(case when jsonb_typeof(parts.raw->'penalties')='object' then
            (select sum(value::double precision) from jsonb_each_text(parts.raw->'penalties')) end,0)) as penalty_total,
          avg((x.score_summary->>'completed_tiles')::double precision) as completed_tiles,
          avg(private.observer_summary_count(x.score_summary,'required_missing')) as required_missing,
          avg(private.observer_summary_count(x.score_summary,'targets_observed')) as targets_observed,
          (select coalesce(jsonb_object_agg(c.key,c.value),'{}'::jsonb) from (
            select e.key,avg(e.value::text::double precision) as value
              from public.observer_runs y cross join lateral jsonb_each(private.observer_score_components(y.score_summary)) e
              where y.batch_id=k.id and (case when v_card is null then y.scenario_id in (select scenario_id from cards) else y.scenario_id=v_card end)
              group by e.key) c) as components
        from public.observer_runs x
        cross join lateral (select coalesce(x.score_summary->'raw_score',x.score_summary->'score') as raw) parts
        where x.batch_id=k.id and (case when v_card is null then x.scenario_id in (select scenario_id from cards) else x.scenario_id=v_card end)
      ) agg
    ) all_rows order by rank,scored_at,team_name limit v_limit
  ) rows;

  return jsonb_build_object('layout',v_layout,'cards',v_cards,
    'scenario',case when v_card is null then null else p_scenario_slug end,'rows',v_rows);
end $$;
revoke all on function public.observer_card_board(uuid,text,integer) from public;
grant execute on function public.observer_card_board(uuid,text,integer) to anon,authenticated,service_role;

-- Hidden final scoring (organizer decision 2026-10-01): a card that fails
-- because of the team itself (private.observer_participant_failure: build, crash
-- or protocol failure of its project, or a trace voided by the score check)
-- scores 0, and the final score is still the mean over all four cards E-H.
-- Platform failures are unchanged: the evaluation fails and organizers rerun it
-- (scripts/run-hidden-final.py --retry-failed).
--
-- The rule applies to the sealed final phase only (counts_for_final and sealed,
-- the phase private.observer_run_hidden_final evaluates); every other phase
-- keeps failing an evaluation on its first failed card.
-- 1. private.observer_finalize_batch: in that phase a participant-failed card no
--    longer fails the batch, so its remaining cards are still scheduled. Once
--    every card is scored or participant-failed the batch is 'scored' with the
--    mean over all cards, failed cards counted as 0. Any other failed or
--    cancelled card fails the batch as before.
-- 2. private.observer_settle_score_check: a rejected trace in a scored batch of
--    that phase sets the card to 0 and recomputes the mean instead of failing
--    the batch.
-- 3. public.observer_card_board: in that phase a scored batch with
--    participant-failed cards is complete; those cards count and rank as 0 and
--    are listed in unfinished_cards (overall tab) or flagged unfinished (card tab).
-- 4. private.observer_run_hidden_final: a failed evaluation is 'participant' only
--    when every failed or cancelled card is the team's own failure; as soon as one
--    is not, it is 'platform' and rerun with --retry-failed.

create or replace function private.observer_failed_cards_score_zero(p_phase uuid)
returns boolean language sql stable security definer set search_path=public,pg_temp as $$
  select exists(select 1 from public.phases p join public.observer_phase_settings s on s.phase_id=p.id
    where p.id=p_phase and p.counts_for_final and s.sealed)
$$;
revoke all on function private.observer_failed_cards_score_zero(uuid) from public,anon,authenticated;

-- True when the run failed and, in the hidden final, does not count as 0.
create or replace function private.observer_run_fails_batch(p_run uuid)
returns boolean language sql stable security definer set search_path=public,pg_temp as $$
  select exists(select 1 from public.observer_runs r join public.observer_batches b on b.id=r.batch_id
    where r.id=p_run and r.status in ('failed','cancelled')
      and not (private.observer_failed_cards_score_zero(b.phase_id) and private.observer_participant_failure(r.id)))
$$;
revoke all on function private.observer_run_fails_batch(uuid) from public,anon,authenticated;

create or replace function private.observer_finalize_batch(p_batch uuid)
returns void language plpgsql security definer set search_path=public,pg_temp as $$
begin
  perform 1 from public.observer_batches where id=p_batch for update;
  if exists(select 1 from public.observer_runs r where r.batch_id=p_batch and private.observer_run_fails_batch(r.id)) then
    -- Decided once, when the batch fails: later runs of a failed batch no longer count.
    update public.observer_batches set status='failed',finished_at=now(),
      quota_refunded=not exists(select 1 from public.observer_runs r where r.batch_id=p_batch
        and private.observer_participant_failure(r.id))
      where id=p_batch and status in ('queued','running');
  elsif exists(select 1 from public.observer_runs where batch_id=p_batch)
    and not exists(select 1 from public.observer_runs where batch_id=p_batch and status not in ('scored','failed')) then
    -- Only scored cards and (hidden final) the team's own failed cards are left; those count as 0.
    update public.observer_batches set status='scored',
      score=(select avg(coalesce(score,0)) from public.observer_runs where batch_id=p_batch),finished_at=now()
      where id=p_batch and status in ('queued','running');
  end if;
end $$;
revoke all on function private.observer_finalize_batch(uuid) from public,anon,authenticated;

-- Unchanged except for the hidden final rule above.
create or replace function private.observer_settle_score_check(p_job uuid,p_outcome text,p_recomputed jsonb)
returns void language plpgsql security definer set search_path=public,pg_temp as $$
declare j private.observer_jobs; r public.observer_runs; b public.observer_batches; v_total double precision;
  v_outcome text:=p_outcome;
begin
  select * into j from private.observer_jobs where id=p_job and kind='score';
  if not found then raise exception 'job_unavailable'; end if;
  select * into r from public.observer_runs where id=j.run_id for update;
  if r.status<>'scored' or r.score_check is distinct from 'pending' then return; end if;
  if v_outcome='scored' then
    if jsonb_typeof(p_recomputed) is distinct from 'object' or p_recomputed->>'run_id' is distinct from r.id::text
      or jsonb_typeof(p_recomputed->'score') is distinct from 'object' or octet_length(p_recomputed::text)>65536 then
      raise exception 'invalid_job_result'; end if;
    -- The job proved its trace against this digest; re-check the binding so a
    -- receipt can never pair a score with a different trace.
    if p_recomputed->>'decisions_digest' is distinct from r.decisions_digest then
      raise exception 'invalid_job_result'; end if;
    v_total:=(p_recomputed->'score'->>'total')::double precision;
    if v_total is null or v_total in ('NaN'::double precision,'Infinity'::double precision,'-Infinity'::double precision) then
      raise exception 'invalid_job_result'; end if;
    v_outcome:=case when abs(v_total-r.score)<=1e-6*greatest(1,abs(v_total)) then 'verified' else 'corrected' end;
  end if;
  if v_outcome not in ('verified','corrected','rejected','unverified') then raise exception 'invalid_job_result'; end if;
  -- Switched off while the job ran: record the evidence, change nothing.
  if v_outcome in ('corrected','rejected') and not coalesce((select h.rescore from private.observer_hardening h where h.id),false) then
    insert into private.observer_score_checks(run_id,job_id,outcome,reported_score,recomputed)
      values(r.id,j.id,'unverified',r.score,p_recomputed) on conflict(run_id) do nothing;
    update public.observer_runs set score_check='unverified' where id=r.id;
    return;
  end if;
  insert into private.observer_score_checks(run_id,job_id,outcome,reported_score,reported_summary,recomputed)
    values(r.id,j.id,v_outcome,r.score,case when v_outcome in ('corrected','rejected') then r.score_summary end,p_recomputed)
    on conflict(run_id) do nothing;
  if v_outcome in ('verified','unverified') then
    update public.observer_runs set score_check=v_outcome where id=r.id;
    return;
  end if;
  select * into b from public.observer_batches where id=r.batch_id for update;
  if v_outcome='corrected' then
    update public.observer_runs set score=v_total,score_check='corrected',
      score_summary=jsonb_set(r.score_summary,'{score}',p_recomputed->'score',true)
        ||jsonb_build_object('rescore',jsonb_build_object('reported_total',r.score,'recomputed_total',v_total))
      where id=r.id;
    if b.status='scored' then
      update public.observer_batches set score=(select avg(coalesce(score,0)) from public.observer_runs where batch_id=b.id)
        where id=b.id;
    end if;
    perform private.audit('observer.score_corrected',jsonb_build_object('run_id',r.id,'job_id',j.id,
      'reported',r.score,'recomputed',v_total));
  else
    update public.observer_runs set status='failed',error='score_verification_failed',score=null,
      score_check='rejected' where id=r.id;
    -- A voided trace is the team's run, never a platform failure: no refund and
    -- no automatic retry. In the hidden final the card counts as 0.
    if b.status='scored' and private.observer_failed_cards_score_zero(b.phase_id) then
      update public.observer_batches set score=(select avg(coalesce(score,0)) from public.observer_runs where batch_id=b.id)
        where id=b.id;
    elsif b.status='scored' then
      update public.observer_batches set status='failed',score=null,quota_refunded=false where id=b.id;
    else
      perform private.observer_finalize_batch(b.id);
    end if;
    perform private.audit('observer.score_rejected',jsonb_build_object('run_id',r.id,'job_id',j.id,
      'reported',r.score));
  end if;
end $$;
revoke all on function private.observer_settle_score_check(uuid,text,jsonb) from public,anon,authenticated;

-- Unchanged except for the hidden final rule above: unfinished_cards (overall
-- tab, card slugs; only where card names are returned) and unfinished (card tab).
-- A card that is not scored contributes 0 to the raw total and the components
-- (hidden final: their sum over all cards of the scope) and never shows the
-- summary of a voided trace.
create or replace function public.observer_card_board(p_phase uuid,p_scenario_slug text default null,p_limit integer default 100)
returns jsonb language plpgsql stable security definer set search_path=public,pg_temp as $$
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
end $$;
revoke all on function public.observer_card_board(uuid,text,integer) from public;
grant execute on function public.observer_card_board(uuid,text,integer) to anon,authenticated,service_role;

-- Unchanged except for the failure classification (4. above).
create or replace function private.observer_run_hidden_final(p_source uuid, p_target uuid, p_team uuid default null,
  p_apply boolean default false, p_before_freeze boolean default false, p_retry_failed boolean default false,
  p_retry_participant boolean default false, p_limit integer default null)
returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$
declare v_source public.phases; v_target public.phases; t record; v_state jsonb; v_revision uuid; v_user uuid;
  v_batch uuid; v_items jsonb:='[]'; v_item jsonb; v_reason text; v_created integer:=0;
  v_last record; v_failure text; v_stale integer;
begin
  perform pg_advisory_xact_lock(hashtextextended('observer-hidden-final/'||p_target::text,0));
  select * into v_source from public.phases where id=p_source;
  select * into v_target from public.phases where id=p_target;
  if v_source.id is null or v_target.id is null then raise exception 'phase_not_found'; end if;
  if not exists(select 1 from public.observer_phase_settings where phase_id=p_target and sealed and projects_enabled)
    or not v_target.counts_for_final or not v_target.is_active then raise exception 'target_not_sealed_final_phase'; end if;
  if not exists(select 1 from public.phase_scenarios where phase_id=p_target)
    or exists(select 1 from public.phase_scenarios ps where ps.phase_id=p_target
      and not exists(select 1 from private.observer_scenario_bundles b where b.scenario_id=ps.scenario_id))
    then raise exception 'target_scenarios_not_ready'; end if;
  if v_source.ends_at is null or now()<v_source.ends_at then
    if not p_before_freeze or p_team is null then raise exception 'source_phase_not_finished'; end if;
  end if;
  for t in select x.id,x.name,x.is_hidden,x.leader_id from public.teams x
    where (p_team is null or x.id=p_team) order by x.name,x.id
  loop
    v_state:=private.observer_final_version_state(t.id,p_source);
    v_revision:=(v_state->>'revision_id')::uuid;
    if v_revision is null and p_team is null then continue; end if;
    v_reason:=null; v_user:=null; v_batch:=null; v_failure:=null; v_last:=null;
    select b.id,b.status,b.revision_id into v_last from public.observer_batches b
      where b.team_id=t.id and b.phase_id=p_target and b.purpose='formal'
        and private.observer_batch_covers_phase(b.id,p_target)
      order by b.created_at desc,b.id desc limit 1;
    select count(*) into v_stale from public.observer_batches b
      where b.team_id=t.id and b.phase_id=p_target and b.purpose='formal'
        and not private.observer_batch_covers_phase(b.id,p_target);
    if v_last.status in ('failed','cancelled') then
      -- 'participant' only when every failed or cancelled card is the team's own failure.
      v_failure:=case when exists(select 1 from public.observer_runs r where r.batch_id=v_last.id
          and private.observer_participant_failure(r.id))
        and not exists(select 1 from public.observer_runs r where r.batch_id=v_last.id
          and r.status in ('failed','cancelled') and not private.observer_participant_failure(r.id))
        then 'participant' else 'platform' end;
    end if;
    if v_revision is null then v_reason:='no_final_version';
    elsif v_last.id is not null and v_failure is null then v_reason:='already_evaluated';
    elsif v_failure is not null and exists(select 1 from public.observer_runs where batch_id=v_last.id
        and status in ('starting','ready','running')) then v_reason:='failed_settling';
    elsif v_failure='platform' and not p_retry_failed then v_reason:='failed_platform';
    elsif v_failure='participant' and not p_retry_participant then v_reason:='failed_participant';
    elsif not exists(select 1 from private.observer_materializations where revision_id=v_revision) then
      v_reason:='version_not_materialized';
    end if;
    if v_reason is null then
      -- The batch runs as a current, unbanned member: the chooser, the confirmer, the leader, else the earliest member.
      select u.id into v_user from public.profiles u
        where u.team_id=t.id and not u.is_banned
        order by (u.id=(v_state->>'chosen_by')::uuid) desc nulls last,
          (u.id=(select approved_by from public.observer_revisions where id=v_revision)) desc nulls last,
          (u.id=t.leader_id) desc nulls last, u.created_at, u.id limit 1;
      if v_user is null then v_reason:='no_active_member'; end if;
    end if;
    if v_reason is null and p_apply and v_created>=coalesce(p_limit,v_created+1) then v_reason:='deferred'; end if;
    if v_reason is null and p_apply then
      insert into public.observer_batches(team_id,user_id,phase_id,revision_id,mode,purpose)
        values(t.id,v_user,p_target,v_revision,'project','formal') returning id into v_batch;
      insert into public.observer_runs(batch_id,scenario_id,created_at)
        select v_batch,scenario_id,clock_timestamp() from public.phase_scenarios where phase_id=p_target order by scenario_id;
      if v_failure is not null then
        update public.observer_runs set status='cancelled',finished_at=now() where batch_id=v_last.id and status='queued';
      end if;
      v_created:=v_created+1;
    end if;
    v_item:=jsonb_build_object('team_id',t.id,'team_name',t.name,'team_hidden',t.is_hidden,
      'revision_id',v_revision,'source',v_state->>'source','best_score',v_state->'best_score',
      'user_id',v_user,'model_mode',private.observer_team_model_mode(t.id),
      'model_key_saved',exists(select 1 from private.observer_team_models m join private.observer_providers v on v.id=m.provider_id
        where m.team_id=t.id and v.encrypted_key<>''),
      'action',case when v_reason='deferred' then 'deferred' when v_reason is not null then 'skip'
        when p_apply then 'created' else 'would_create' end,
      'reason',v_reason,'batch_id',v_batch,
      'previous_batch_id',v_last.id,'previous_status',v_last.status,'failure',v_failure,'stale_batches',v_stale);
    v_items:=v_items||jsonb_build_array(v_item);
  end loop;
  if p_apply and v_created>0 then
    perform private.audit('observer.hidden_final_started',jsonb_build_object('source',p_source,'target',p_target,
      'team',p_team,'batches',v_created,'retry_failed',p_retry_failed,'retry_participant',p_retry_participant,'limit',p_limit));
  end if;
  return jsonb_build_object('apply',p_apply,'source_phase',v_source.slug,'target_phase',v_target.slug,
    'source_ends_at',v_source.ends_at,'frozen',v_source.ends_at is not null and now()>=v_source.ends_at,
    'created',v_created,'teams',v_items);
end $$;
revoke all on function private.observer_run_hidden_final(uuid,uuid,uuid,boolean,boolean,boolean,boolean,integer)
  from public,anon,authenticated;
notify pgrst,'reload schema';

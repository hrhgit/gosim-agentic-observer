-- Hidden final on the v4 cards (E-H, phase 'final-hidden'): one evaluation per
-- team, retried only after a platform failure (rules: evaluated once; failures
-- caused by the platform do not count).
--
-- private.observer_run_hidden_final (20260927000800) changes in two ways:
-- 1. Only batches that ran exactly the target phase's current card set count.
--    Batches from an earlier scenario set (the v3 'eval-final' tests before the
--    switch to the v4 cards) neither block a team nor appear as its result; this
--    matches the card boards (20260928004100), which only rank complete batches
--    on the currently linked cards.
-- 2. A failed or cancelled evaluation is classified: 'participant' when one of
--    its runs failed because of the team's project (private.observer_participant_failure:
--    build, crash or protocol failure before the engine could score), else
--    'platform'. p_retry_failed now recreates only platform failures;
--    participant failures need the separate p_retry_participant (an organizer
--    decision, not part of the normal procedure). A batch is marked failed as soon
--    as its first run fails while its other runs may still be running; it is
--    classified and retried only once none of its runs is starting, ready or
--    running ('failed_settling' until then). Its runs still queued are never
--    scheduled for a failed batch; a retry cancels them.
-- 3. The runs of every created batch get increasing created_at values, so the
--    dispatcher (which schedules by created_at) works through the teams in the
--    order they were created instead of interleaving all teams' cards.
-- p_limit creates at most that many evaluations in one call (a canary batch
-- before the rest); further eligible teams are reported as 'deferred'.
-- The result rows carry the latest current batch, its status and failure kind,
-- and the number of ignored batches from earlier scenario sets.

-- True when the batch ran exactly the phase's currently linked scenarios.
create or replace function private.observer_batch_covers_phase(p_batch uuid, p_phase uuid)
returns boolean language sql stable security definer set search_path=public,pg_temp as $$
  select exists(select 1 from public.phase_scenarios where phase_id=p_phase)
    and not exists(select scenario_id from public.phase_scenarios where phase_id=p_phase
      except select scenario_id from public.observer_runs where batch_id=p_batch)
    and not exists(select scenario_id from public.observer_runs where batch_id=p_batch
      except select scenario_id from public.phase_scenarios where phase_id=p_phase)
$$;
revoke all on function private.observer_batch_covers_phase(uuid,uuid) from public,anon,authenticated;

drop function if exists private.observer_run_hidden_final(uuid,uuid,uuid,boolean,boolean,boolean);
create function private.observer_run_hidden_final(p_source uuid, p_target uuid, p_team uuid default null,
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
      v_failure:=case when exists(select 1 from public.observer_runs r where r.batch_id=v_last.id
        and private.observer_participant_failure(r.id)) then 'participant' else 'platform' end;
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

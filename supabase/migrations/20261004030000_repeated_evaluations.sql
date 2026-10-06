-- Repeated evaluations (organizer decision 2026-10-04): the final ranking averages
-- several evaluations, so that run-to-run variation in a team's own program
-- (wall-clock-adaptive search, unseeded randomness) does not decide places. The
-- engine itself is deterministic. Teams can run the same 3-run average themselves
-- as a self-check during practice and the online phase.
--
-- 1. observer_phase_settings.repeat_runs (default 1): how many evaluations of the
--    final version the hidden final runs per team. Set to 3 for 'final-hidden'.
-- 2. private.observer_run_hidden_final creates repeat_runs evaluations per team
--    (round by round: every team's first evaluation, then every team's second, ...)
--    and tops a team up to repeat_runs after a platform failure (--retry-failed).
--    A card the team itself fails still scores 0 in that evaluation (20261001000900).
-- 3. public.observer_card_board in a phase with repeat_runs > 1: each team's row is
--    the mean of its first repeat_runs complete evaluations (per card and overall)
--    instead of its best one; teams with fewer complete evaluations are not listed
--    yet. averaged_runs says how many evaluations a row averages. Every individual
--    evaluation and run stays stored and downloadable as before.
-- 4. Evaluations of one team in one phase run one after another
--    (public.observer_pending_runs): the repeated evaluations of a team never compete
--    with each other for its model key or rate limits. Ordinary evaluations are
--    unaffected (a team has at most one active evaluation anyway).
-- 5. public.observer_create_repeat_batches: a team starts 3 evaluations of one
--    version at once (self-check, practice and online phases). It uses 3 of the
--    day's evaluations (refused unless 3 are left), at most one such set runs at a
--    time (like any evaluation: batch_already_active), and the batches share
--    repeat_group so the evaluation record can show their mean and spread. Each one
--    is an ordinary evaluation: the online board still keeps the single best one.
-- 6. public.observer_card_board in the formal phase where teams choose a final
--    version: final_version {chosen, score} per row (the score of that version's
--    best complete evaluation in this phase, on the shown tab).

alter table public.observer_phase_settings add column if not exists repeat_runs integer not null default 1;
alter table public.observer_phase_settings drop constraint if exists observer_phase_settings_repeat_runs_check;
alter table public.observer_phase_settings add constraint observer_phase_settings_repeat_runs_check
  check (repeat_runs between 1 and 10);
update public.observer_phase_settings set repeat_runs=3
  where phase_id=(select id from public.phases where slug='final-hidden');

alter table public.observer_batches add column if not exists repeat_group uuid;
alter table public.observer_batches add column if not exists repeat_runs smallint;
create index if not exists observer_batches_repeat_group on public.observer_batches(repeat_group) where repeat_group is not null;

-- The number of evaluations in a team's self-check (the final's default).
create or replace function private.observer_self_check_runs()
returns integer language sql immutable as $$ select 3 $$;

-- 4. Unchanged except for the last condition of the run selection.
create or replace function public.observer_pending_runs(p_limit integer default 5)
returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$
declare r record; result jsonb:='[]'; v_lease uuid; v_minutes integer; v_old private.observer_run_leases;
begin
  -- Scheduling leases past the retry budget are parked, never failed: the run
  -- stays 'queued' for the contestant and an incident pages the organizers.
  for r in select run.id,run.batch_id,l.attempts,l.first_leased_at from public.observer_runs run
    join private.observer_run_leases l on l.run_id=run.id
    where run.status='queued' and l.paused_at is null and l.expires_at<=now()
      and now()-l.first_leased_at>interval '2 hours'
    for update of run skip locked limit 100
  loop
    update private.observer_run_leases set paused_at=now() where run_id=r.id;
    perform private.observer_raise_incident('run',r.id,'run_schedule_retry_budget_exhausted',
      jsonb_build_object('attempts',r.attempts,'batch_id',r.batch_id));
  end loop;
  -- Serialize reservations, including first insert, on the public run row. A
  -- crashed scheduler leaves a short lease, never a half-open session.
  for r in select run.id,b.user_id,b.mode,
      case when b.purpose='preview' then least(c.runtime_seconds,300) else c.runtime_seconds end as runtime_seconds,
      s.storage_path,s.digest as scenario_digest,
      m.archive_ref,m.digest as materialized_digest,rev.manifest
    from public.observer_runs run join public.observer_batches b on b.id=run.batch_id
    join public.observer_phase_settings c on c.phase_id=b.phase_id
    join private.observer_scenario_bundles s on s.scenario_id=run.scenario_id
    join public.profiles p on p.id=b.user_id and not p.is_banned and p.team_id=b.team_id
    left join public.observer_revisions rev on rev.id=b.revision_id
    left join private.observer_materializations m on m.revision_id=rev.id
    left join private.observer_run_leases l on l.run_id=run.id
    where run.status='queued' and b.purpose in ('formal','preview') and b.status in ('queued','running')
      and ((b.mode='local' and c.local_sessions_enabled) or
        (b.mode='project' and c.projects_enabled and m.revision_id is not null and
          ((b.purpose='formal' and rev.status='approved') or (b.purpose='preview' and rev.status='preparing'))))
      -- A local user starts one scenario at a time. Do not spend hosted engine
      -- minutes waiting for the other scenarios while their first CLI is busy.
      and (b.mode<>'local' or not exists(select 1 from public.observer_runs other
        where other.batch_id=run.batch_id and other.id<>run.id and
          (other.status in ('starting','ready','running') or
            (other.status='queued' and (other.created_at,other.id)<(run.created_at,run.id)))))
      and (l.run_id is null or (l.expires_at<=now() and l.paused_at is null))
      -- One team's evaluations in a phase run one after another (repeated evaluations).
      and (b.purpose<>'formal' or not exists(select 1 from public.observer_batches earlier
        where earlier.team_id=b.team_id and earlier.phase_id=b.phase_id and earlier.purpose='formal'
          and earlier.id<>b.id and earlier.status in ('queued','running')
          and (earlier.created_at,earlier.id)<(b.created_at,b.id)))
    order by run.created_at,run.id for update of run skip locked limit greatest(1,least(coalesce(p_limit,5),10))
  loop
    -- The join snapshot may predate a concurrently committed first lease even
    -- though this row lock was acquired afterwards. Re-read under the lock.
    select * into v_old from private.observer_run_leases where run_id=r.id;
    if found and (v_old.expires_at>now() or v_old.paused_at is not null) then continue; end if;
    v_lease:=gen_random_uuid();
    v_minutes:=case when found then (least(20,2^greatest(v_old.attempts,1)))::int else 2 end;
    insert into private.observer_run_leases(run_id,lease,expires_at)
      values(r.id,v_lease,now()+(v_minutes||' minutes')::interval)
      on conflict(run_id) do update set lease=excluded.lease,expires_at=excluded.expires_at,
        attempts=private.observer_run_leases.attempts+1;
    result:=result || jsonb_build_array(to_jsonb(r)||jsonb_build_object('lease',v_lease));
  end loop;
  return result;
end $$;

-- 5. Self-check: 3 evaluations of one version, started together, run one after another.
create or replace function public.observer_create_repeat_batches(p_phase uuid, p_revision uuid, p_confirm_repeat boolean default false)
returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$
declare v_team uuid; v_config public.observer_phase_settings; v_runs integer:=private.observer_self_check_runs();
  v_group uuid:=gen_random_uuid(); v_first uuid; v_id uuid; v_ids jsonb; i integer;
begin
  perform private.assert_not_banned();
  select team_id into v_team from public.profiles where id=auth.uid();
  if v_team is null then raise exception 'team_required'; end if;
  if p_revision is null then raise exception 'revision_not_approved'; end if;
  perform 1 from public.teams where id=v_team for update;
  select * into v_config from public.observer_phase_settings where phase_id=p_phase;
  -- Checked before the first evaluation is created: all three or none.
  if found and private.observer_batches_used(v_team,p_phase)+v_runs > v_config.daily_batches then
    raise exception 'repeat_daily_limit';
  end if;
  -- Every other check (phase open, confirmed version, one active evaluation, repeat confirmation) as usual.
  v_first:=public.observer_create_batch(p_phase,p_revision,p_confirm_repeat);
  update public.observer_batches set repeat_group=v_group,repeat_runs=v_runs where id=v_first;
  v_ids:=jsonb_build_array(v_first);
  for i in 2..v_runs loop
    insert into public.observer_batches(team_id,user_id,phase_id,revision_id,mode,repeat_group,repeat_runs,created_at)
      values(v_team,auth.uid(),p_phase,p_revision,'project',v_group,v_runs,clock_timestamp()) returning id into v_id;
    insert into public.observer_runs(batch_id,scenario_id,created_at)
      select v_id,scenario_id,clock_timestamp() from public.phase_scenarios where phase_id=p_phase order by scenario_id;
    v_ids:=v_ids||jsonb_build_array(v_id);
  end loop;
  perform private.audit('observer.repeat_evaluation',jsonb_build_object('phase_id',p_phase,'revision_id',p_revision,
    'repeat_group',v_group,'batches',v_ids));
  return jsonb_build_object('repeat_group',v_group,'batch_ids',v_ids);
end $$;
revoke all on function public.observer_create_repeat_batches(uuid,uuid,boolean) from public,anon;
grant execute on function public.observer_create_repeat_batches(uuid,uuid,boolean) to authenticated,service_role;

-- 2. Hidden final: repeat_runs evaluations of the final version per team.
create or replace function private.observer_run_hidden_final(p_source uuid, p_target uuid, p_team uuid default null,
  p_apply boolean default false, p_before_freeze boolean default false, p_retry_failed boolean default false,
  p_retry_participant boolean default false, p_limit integer default null)
returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$
declare v_source public.phases; v_target public.phases; t record; v_state jsonb; v_revision uuid; v_user uuid;
  v_batch uuid; v_items jsonb:='[]'; v_item jsonb; v_reason text; v_created integer:=0; v_teams integer:=0;
  v_last record; v_failure text; v_stale integer; v_done integer; v_need integer; v_runs integer;
  v_plan jsonb:='[]'; v_step jsonb; k integer; n integer;
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
  select greatest(1,coalesce(repeat_runs,1)) into v_runs from public.observer_phase_settings where phase_id=p_target;
  for t in select x.id,x.name,x.is_hidden,x.leader_id from public.teams x
    where (p_team is null or x.id=p_team) order by x.name,x.id
  loop
    v_state:=private.observer_final_version_state(t.id,p_source);
    v_revision:=(v_state->>'revision_id')::uuid;
    if v_revision is null and p_team is null then continue; end if;
    v_reason:=null; v_user:=null; v_failure:=null; v_last:=null; v_need:=0;
    -- Evaluations on the current card set that count or still run; failed ones are replaced.
    select count(*) into v_done from public.observer_batches b
      where b.team_id=t.id and b.phase_id=p_target and b.purpose='formal' and b.status in ('queued','running','scored')
        and private.observer_batch_covers_phase(b.id,p_target);
    select b.id,b.status,b.revision_id into v_last from public.observer_batches b
      where b.team_id=t.id and b.phase_id=p_target and b.purpose='formal' and b.status in ('failed','cancelled')
        and private.observer_batch_covers_phase(b.id,p_target)
      order by b.created_at desc,b.id desc limit 1;
    select count(*) into v_stale from public.observer_batches b
      where b.team_id=t.id and b.phase_id=p_target and b.purpose='formal'
        and not private.observer_batch_covers_phase(b.id,p_target);
    if v_done<v_runs and v_last.id is not null then
      -- 'participant' only when every failed or cancelled card is the team's own failure.
      v_failure:=case when exists(select 1 from public.observer_runs r where r.batch_id=v_last.id
          and private.observer_participant_failure(r.id))
        and not exists(select 1 from public.observer_runs r where r.batch_id=v_last.id
          and r.status in ('failed','cancelled') and not private.observer_participant_failure(r.id))
        then 'participant' else 'platform' end;
    end if;
    if v_revision is null then v_reason:='no_final_version';
    elsif v_done>=v_runs then v_reason:='already_evaluated';
    elsif v_failure is not null and exists(select 1 from public.observer_runs where batch_id=v_last.id
        and status in ('starting','ready','running')) then v_reason:='failed_settling';
    elsif v_failure='platform' and not p_retry_failed then v_reason:='failed_platform';
    elsif v_failure='participant' and not p_retry_participant then v_reason:='failed_participant';
    elsif not exists(select 1 from private.observer_materializations where revision_id=v_revision) then
      v_reason:='version_not_materialized';
    end if;
    if v_reason is null then
      -- The batches run as a current, unbanned member: the chooser, the confirmer, the leader, else the earliest member.
      select u.id into v_user from public.profiles u
        where u.team_id=t.id and not u.is_banned
        order by (u.id=(v_state->>'chosen_by')::uuid) desc nulls last,
          (u.id=(select approved_by from public.observer_revisions where id=v_revision)) desc nulls last,
          (u.id=t.leader_id) desc nulls last, u.created_at, u.id limit 1;
      if v_user is null then v_reason:='no_active_member'; end if;
    end if;
    if v_reason is null and p_apply and v_teams>=coalesce(p_limit,v_teams+1) then v_reason:='deferred'; end if;
    if v_reason is null then
      v_need:=v_runs-v_done;
      if p_apply then
        v_teams:=v_teams+1;
        v_plan:=v_plan||jsonb_build_array(jsonb_build_object('item',jsonb_array_length(v_items),'team',t.id,
          'user',v_user,'revision',v_revision,'need',v_need));
        if v_failure is not null then
          update public.observer_runs set status='cancelled',finished_at=now() where batch_id=v_last.id and status='queued';
        end if;
      end if;
    end if;
    v_item:=jsonb_build_object('team_id',t.id,'team_name',t.name,'team_hidden',t.is_hidden,
      'revision_id',v_revision,'source',v_state->>'source','best_score',v_state->'best_score',
      'user_id',v_user,'model_mode',private.observer_team_model_mode(t.id),
      'model_key_saved',exists(select 1 from private.observer_team_models m join private.observer_providers v on v.id=m.provider_id
        where m.team_id=t.id and v.encrypted_key<>''),
      'action',case when v_reason='deferred' then 'deferred' when v_reason is not null then 'skip'
        when p_apply then 'created' else 'would_create' end,
      'reason',v_reason,'batch_id',null,'batch_ids','[]'::jsonb,'runs',v_runs,'evaluations',v_done,'new_evaluations',v_need,
      'previous_batch_id',v_last.id,'previous_status',v_last.status,'failure',v_failure,'stale_batches',v_stale);
    v_items:=v_items||jsonb_build_array(v_item);
  end loop;
  -- Round by round: every team's first missing evaluation before anyone's second, so
  -- runs scheduled in creation order give every team the same number of evaluations
  -- as early as possible, and one team's evaluations are spread out in time.
  for k in 1..v_runs loop
    for n in 0..jsonb_array_length(v_plan)-1 loop
      v_step:=v_plan->n;
      continue when (v_step->>'need')::integer<k;
      insert into public.observer_batches(team_id,user_id,phase_id,revision_id,mode,purpose,created_at)
        values((v_step->>'team')::uuid,(v_step->>'user')::uuid,p_target,(v_step->>'revision')::uuid,'project','formal',
          clock_timestamp()) returning id into v_batch;
      insert into public.observer_runs(batch_id,scenario_id,created_at)
        select v_batch,scenario_id,clock_timestamp() from public.phase_scenarios where phase_id=p_target order by scenario_id;
      v_items:=jsonb_set(v_items,array[(v_step->>'item'),'batch_ids'],
        (v_items->((v_step->>'item')::integer)->'batch_ids')||jsonb_build_array(v_batch));
      if k=1 then v_items:=jsonb_set(v_items,array[(v_step->>'item'),'batch_id'],to_jsonb(v_batch)); end if;
      v_created:=v_created+1;
    end loop;
  end loop;
  if p_apply and v_created>0 then
    perform private.audit('observer.hidden_final_started',jsonb_build_object('source',p_source,'target',p_target,
      'team',p_team,'batches',v_created,'teams',v_teams,'runs',v_runs,'retry_failed',p_retry_failed,
      'retry_participant',p_retry_participant,'limit',p_limit));
  end if;
  return jsonb_build_object('apply',p_apply,'source_phase',v_source.slug,'target_phase',v_target.slug,
    'source_ends_at',v_source.ends_at,'frozen',v_source.ends_at is not null and now()>=v_source.ends_at,
    'runs',v_runs,'created',v_created,'teams',v_items);
end $$;
revoke all on function private.observer_run_hidden_final(uuid,uuid,uuid,boolean,boolean,boolean,boolean,integer)
  from public,anon,authenticated;

-- 3. and 6. Based on the live definition (20261004024500); changes: eligible carries
-- the revision, best/ranked carry batch_ids (one batch, or the averaged ones), every
-- per-batch aggregate reads all of batch_ids, and the rows gain averaged_runs and
-- final_version.
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
    where b.phase_id=p_phase and b.purpose='formal' and b.status='scored' and p.is_active
      and (public.is_admin() or (not t.is_hidden and p.leaderboard_mode in ('live','published')))
    group by b.id,t.name
    having count(*)=(select count(*) from cards) and count(*)>0
  ),
  -- One batch per team: its best one; with repeat_runs > 1 its first repeat_runs complete ones, averaged.
  best as (
    select x.* from (select distinct on(e.team_id) e.id,e.team_id,e.team_name,e.created_at,e.finished_at,e.overall,
        array[e.id] as batch_ids
      from eligible e order by e.team_id,e.overall desc,e.created_at) x where v_repeat=1
    union all
    select (array_agg(o.id order by o.created_at,o.id))[1],o.team_id,min(o.team_name),min(o.created_at),max(o.finished_at),
      avg(o.overall),array_agg(o.id order by o.created_at,o.id)
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
revoke all on function public.observer_card_board(uuid,text,integer) from public;
grant execute on function public.observer_card_board(uuid,text,integer) to anon,authenticated,service_role;
notify pgrst,'reload schema';

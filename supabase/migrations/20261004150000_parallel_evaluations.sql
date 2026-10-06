-- Parallel evaluations (owner decision 2026-10-04, contestants' A/B comparisons): a team may
-- have up to observer_phase_settings.max_active_evaluations (default 4) evaluations in flight
-- at once instead of one. A "evaluate 3 times and average" set counts as one and its three
-- evaluations still run one after another; at most one such set at a time. The daily quota
-- is unchanged. The hidden final (repeat_runs > 1) is unchanged: its repeats start together.
-- The organizers' evaluations in sealed phases never count toward a team's limit.

alter table public.observer_phase_settings add column if not exists max_active_evaluations integer not null default 4;
alter table public.observer_phase_settings drop constraint if exists observer_phase_settings_max_active_evaluations_check;
alter table public.observer_phase_settings add constraint observer_phase_settings_max_active_evaluations_check
  check (max_active_evaluations between 1 and 10);

-- The team's evaluations in flight outside sealed phases; a self-check set counts once.
create or replace function private.observer_active_evaluations(p_team uuid)
returns integer language sql stable security definer set search_path=public,pg_temp as $$
  select count(distinct coalesce(b.repeat_group,b.id))::integer from public.observer_batches b
  where b.team_id=p_team and b.purpose='formal' and b.status in ('queued','running')
    and not exists(select 1 from public.observer_phase_settings s where s.phase_id=b.phase_id and s.sealed)
$$;
revoke all on function private.observer_active_evaluations(uuid) from public,anon,authenticated;

create or replace function public.observer_create_batch(p_phase uuid, p_revision uuid DEFAULT NULL::uuid, p_confirm_repeat boolean DEFAULT false)
 RETURNS uuid
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public', 'pg_temp'
AS $function$
declare v_team uuid; v_config public.observer_phase_settings; v_phase public.phases; v_id uuid;
begin
  perform private.assert_not_banned();
  select team_id into v_team from public.profiles where id=auth.uid();
  if v_team is null then raise exception 'team_required'; end if;
  perform 1 from public.teams where id=v_team for update;
  select * into v_phase from public.phases where id=p_phase;
  select * into v_config from public.observer_phase_settings where phase_id=p_phase;
  if not found or not v_phase.is_active or (v_phase.starts_at is not null and now()<v_phase.starts_at)
     or (v_phase.ends_at is not null and now()>=v_phase.ends_at) then raise exception 'phase_closed'; end if;
  if p_revision is null then
    if not v_config.local_sessions_enabled then raise exception 'local_sessions_disabled'; end if;
  else
    if not v_config.projects_enabled then raise exception 'projects_not_enabled'; end if;
    if exists(select 1 from public.observer_revisions r join public.observer_projects p on p.id=r.project_id
      where r.id=p_revision and p.team_id=v_team and r.archived_at is not null) then raise exception 'revision_withdrawn'; end if;
    if not exists(select 1 from public.observer_revisions r join public.observer_projects p on p.id=r.project_id
      where r.id=p_revision and r.status='approved' and p.team_id=v_team) then raise exception 'revision_not_approved'; end if;
  end if;
  if private.observer_batches_used(v_team,p_phase) >= v_config.daily_batches then
    raise exception 'daily_limit';
  end if;
  -- Up to max_active_evaluations of the team's evaluations in flight (a self-check set counts once);
  -- the organizers' sealed final evaluations do not count.
  if private.observer_active_evaluations(v_team) >= coalesce(v_config.max_active_evaluations,4) then
    raise exception 'batch_already_active';
  end if;
  if not exists(select 1 from public.phase_scenarios where phase_id=p_phase) then raise exception 'no_scenarios'; end if;
  -- Another evaluation of the same version uses another daily evaluation; the
  -- team must ask for it explicitly. A refunded (platform-failed) try does not count.
  if p_revision is not null and not coalesce(p_confirm_repeat,false) and exists(select 1 from public.observer_batches
    where revision_id=p_revision and phase_id=p_phase and purpose='formal' and not quota_refunded) then
    raise exception 'revision_already_evaluated';
  end if;
  insert into public.observer_batches(team_id,user_id,phase_id,revision_id,mode)
    values(v_team,auth.uid(),p_phase,p_revision,case when p_revision is null then 'local' else 'project' end)
    returning id into v_id;
  insert into public.observer_runs(batch_id,scenario_id)
    select v_id,scenario_id from public.phase_scenarios where phase_id=p_phase;
  return v_id;
end $function$;

create or replace function public.observer_create_repeat_batches(p_phase uuid, p_revision uuid, p_confirm_repeat boolean DEFAULT false)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public', 'pg_temp'
AS $function$
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
  -- At most one self-check set in flight per team.
  if exists(select 1 from public.observer_batches where team_id=v_team and purpose='formal' and repeat_group is not null
    and status in ('queued','running')) then raise exception 'repeat_already_active'; end if;
  -- Every other check (phase open, confirmed version, evaluations in flight, repeat confirmation) as usual.
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
end $function$;

create or replace function public.observer_pending_runs(p_limit integer DEFAULT 5)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public', 'pg_temp'
AS $function$
declare r record; result jsonb:='[]'; v_lease uuid; v_minutes integer; v_old private.observer_run_leases;
  v_cap integer:=greatest(1,least(coalesce(p_limit,5),10)); v_count integer:=0; v_group text;
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
      m.archive_ref,m.digest as materialized_digest,rev.manifest,
      -- Repeated evaluations that must start together: the same card of one team's evaluations.
      case when c.repeat_runs>1 and b.purpose='formal' then b.team_id::text||'/'||run.scenario_id::text end as start_group
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
      -- The evaluations of one self-check set run one after another; a team's other evaluations
      -- run side by side (up to max_active_evaluations, enforced on creation). The hidden final's
      -- repeats start together (start_group).
      and (b.purpose<>'formal' or c.repeat_runs>1 or b.repeat_group is null or not exists(select 1 from public.observer_batches earlier
        where earlier.repeat_group=b.repeat_group and earlier.purpose='formal'
          and earlier.id<>b.id and earlier.status in ('queued','running')
          and (earlier.created_at,earlier.id)<(b.created_at,b.id)))
    order by run.created_at,run.id for update of run skip locked limit 40
  loop
    -- Up to the cap, but a start group (created adjacently) is never split between passes.
    exit when v_count>=v_cap and (r.start_group is null or r.start_group is distinct from v_group);
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
    result:=result || jsonb_build_array((to_jsonb(r)-'start_group')||jsonb_build_object('lease',v_lease));
    v_count:=v_count+1; v_group:=r.start_group;
  end loop;
  return result;
end $function$;

notify pgrst,'reload schema';

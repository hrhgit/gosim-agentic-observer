-- Complete-project evaluation workflow: a batch lost to a platform failure does
-- not use one of the team's daily evaluations, evaluating an already evaluated
-- version needs an explicit confirmation, and a team can withdraw a version it
-- never evaluated. Functions are based on the live definitions.

alter table public.observer_batches add column if not exists quota_refunded boolean not null default false;
alter table public.observer_revisions add column if not exists archived_at timestamptz;

-- A failed run is the participant's own failure only when its trusted executor
-- reports a project failure (build, crash, invalid protocol output), or a local
-- runner that the team operates stopped or never finished. Engine, session,
-- scheduling, dispatch and expiry failures (and organizer cancellations) are
-- platform failures. An unrecognized code counts as a platform failure.
create or replace function private.observer_participant_failure(p_run uuid)
returns boolean language sql stable security definer set search_path=public,pg_temp as $$
  select exists(select 1 from public.observer_runs r join public.observer_batches b on b.id=r.batch_id
    where r.id=p_run and r.status='failed' and (r.error='local_runner_stopped'
      or (r.error='evaluation_expired' and b.mode='local')
      or (r.error='execute_job_failed' and exists(select 1 from private.observer_jobs j where j.run_id=r.id
        and j.kind='execute' and j.status='failed' and j.result->'diagnostics'->>'code'='project_operation_failed'))))
$$;

create or replace function private.observer_finalize_batch(p_batch uuid)
returns void language plpgsql security definer set search_path=public,pg_temp as $$
begin
  perform 1 from public.observer_batches where id=p_batch for update;
  if exists(select 1 from public.observer_runs where batch_id=p_batch and status in ('failed','cancelled')) then
    -- Decided once, when the batch fails: later runs of a failed batch no longer count.
    update public.observer_batches set status='failed',finished_at=now(),
      quota_refunded=not exists(select 1 from public.observer_runs r where r.batch_id=p_batch
        and private.observer_participant_failure(r.id))
      where id=p_batch and status in ('queued','running');
  elsif exists(select 1 from public.observer_runs where batch_id=p_batch)
    and not exists(select 1 from public.observer_runs where batch_id=p_batch and status<>'scored') then
    update public.observer_batches set status='scored',
      score=(select avg(score) from public.observer_runs where batch_id=p_batch),finished_at=now()
      where id=p_batch and status in ('queued','running');
  end if;
end $$;

-- Batches that already failed for platform reasons are refunded now.
update public.observer_batches b set quota_refunded=true
  where b.status='failed' and not b.quota_refunded
    and exists(select 1 from public.observer_runs r where r.batch_id=b.id and r.status in ('failed','cancelled'))
    and not exists(select 1 from public.observer_runs r where r.batch_id=b.id and private.observer_participant_failure(r.id));

-- The single definition of "evaluations used today" (UTC day) for admission and display.
create or replace function private.observer_batches_used(p_team uuid, p_phase uuid)
returns integer language sql stable security definer set search_path=public,pg_temp as $$
  select count(*)::integer from public.observer_batches where team_id=p_team and phase_id=p_phase and purpose='formal'
    and not quota_refunded and created_at >= (date_trunc('day', now() at time zone 'UTC') at time zone 'UTC')
$$;

create or replace function public.observer_evaluation_quota()
returns jsonb language sql stable security definer set search_path=public,pg_temp as $$
  select coalesce(jsonb_agg(jsonb_build_object('phase_id',s.phase_id,'daily_batches',s.daily_batches,'used',q.used,
      'remaining',greatest(0,s.daily_batches-q.used),
      'resets_at',(date_trunc('day', now() at time zone 'UTC') at time zone 'UTC')+interval '1 day')),'[]'::jsonb)
  from public.profiles u join public.observer_phase_settings s on public.observer_phase_visible(s.phase_id)
  cross join lateral (select private.observer_batches_used(u.team_id,s.phase_id) as used) q
  where u.id=auth.uid() and u.team_id is not null and not u.is_banned
$$;

drop function if exists public.observer_create_batch(uuid,uuid);
create or replace function public.observer_create_batch(p_phase uuid, p_revision uuid default null, p_confirm_repeat boolean default false)
returns uuid language plpgsql security definer set search_path=public,pg_temp as $$
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
  if exists(select 1 from public.observer_batches where team_id=v_team and purpose='formal' and status in ('queued','running')) then
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
end $$;

-- An approved version stays frozen; withdrawal may only stamp archived_at once.
create or replace function private.observer_revision_immutable()
returns trigger language plpgsql as $$
begin
  if old.status = 'approved' and ((to_jsonb(new)-'archived_at') is distinct from (to_jsonb(old)-'archived_at')
    or (old.archived_at is not null and new.archived_at is distinct from old.archived_at)) then
    raise exception 'approved_revision_immutable'; end if;
  return new;
end $$;

create or replace function public.observer_approve_revision(p_revision uuid, p_digest text)
returns void language plpgsql security definer set search_path=public,pg_temp as $$
declare v public.observer_revisions;
begin
  perform private.assert_not_banned();
  select r.* into v from public.observer_revisions r join public.observer_projects p on p.id=r.project_id
    where r.id=p_revision and public.observer_team(p.team_id) for update of r;
  if not found then raise exception 'revision_not_found'; end if;
  if v.archived_at is not null then raise exception 'revision_withdrawn'; end if;
  if p_digest is null or v.approval_digest is distinct from p_digest then raise exception 'stale_approval'; end if;
  if v.status = 'approved' then return; end if;
  if v.status <> 'reviewable' then raise exception 'revision_not_ready'; end if;
  update public.observer_revisions set status='approved', approved_by=auth.uid(), approved_at=now() where id=p_revision;
  perform private.audit('observer.approve', jsonb_build_object('revision_id',p_revision,'digest',p_digest));
end $$;

-- A queued version has no preparation job yet: withdrawing fails it, so a lease
-- handed out concurrently is refused by observer_schedule_preparation. A version
-- being prepared has a running job and must finish first. Evaluated versions
-- stay, because their batches and scores refer to them.
create or replace function public.observer_withdraw_revision(p_revision uuid)
returns void language plpgsql security definer set search_path=public,pg_temp as $$
declare v public.observer_revisions;
begin
  perform private.assert_not_banned();
  select r.* into v from public.observer_revisions r join public.observer_projects p on p.id=r.project_id
    where r.id=p_revision and public.observer_team(p.team_id) for update of r;
  if not found then raise exception 'revision_not_found'; end if;
  if v.archived_at is not null then return; end if;
  if v.status not in ('queued','reviewable','failed','approved')
    or exists(select 1 from public.observer_batches where revision_id=p_revision and purpose='formal') then
    raise exception 'revision_not_withdrawable'; end if;
  update public.observer_revisions set archived_at=now(),
    status=case when v.status='queued' then 'failed' else v.status end,
    error=case when v.status='queued' then 'Withdrawn by the team.' else v.error end
    where id=p_revision;
  perform private.audit('observer.withdraw', jsonb_build_object('revision_id',p_revision,'status',v.status));
end $$;

revoke all on function private.observer_participant_failure(uuid), private.observer_batches_used(uuid,uuid)
  from public, anon, authenticated;
revoke all on function public.observer_evaluation_quota(), public.observer_create_batch(uuid,uuid,boolean),
  public.observer_withdraw_revision(uuid) from public, anon;
grant execute on function public.observer_evaluation_quota(), public.observer_create_batch(uuid,uuid,boolean),
  public.observer_withdraw_revision(uuid) to authenticated, service_role;
notify pgrst,'reload schema';

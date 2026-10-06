-- Self-hosted fallback runner: the last resort when GitHub-hosted evaluation
-- is unavailable in every runner organization. The fallback runner lives in an
-- organizer-owned, isolated Linux virtual machine registered to one
-- organization's observer-control repository (see
-- ops/self-hosted-fallback-runner.md). It is never used while any
-- GitHub-hosted organization can still take the job.
--
-- fallback_capacity is the number of fallback VMs (one runner each) behind the
-- fallback label in that organization; zero (the default) means none.
alter table private.observer_installations add column fallback_capacity integer not null default 0
  check (fallback_capacity between 0 and 8);
alter table private.observer_jobs add column runner text not null default 'github-hosted'
  check (runner in ('github-hosted','self-hosted'));

-- Free fallback slots across enabled organizations. A self-hosted job holds
-- its slot from the fallback decision until it finishes or expires, including
-- while GitHub keeps it queued for a busy runner.
create function private.observer_fallback_slots()
returns bigint language sql stable security definer set search_path=public,pg_temp as $$
  select coalesce(sum(greatest(i.fallback_capacity-(select count(*) from private.observer_jobs j
      where j.organization=i.organization and j.runner='self-hosted'
        and j.status in ('queued','dispatched','claimed') and j.expires_at>now()),0)),0)::bigint
  from private.observer_installations i where i.enabled and i.fallback_capacity>0
$$;
revoke all on function private.observer_fallback_slots() from public,anon,authenticated;

-- Jobs to dispatch now. fallback names the reason a job should go to the
-- self-hosted runner first, and is set only while a fallback slot is free:
--   stalled    GitHub accepted every dispatch but no run claimed the job ten
--              minutes after the last one (hosted runners unavailable, Actions
--              locked by billing or spending limits).
--   over_limit every enabled organization is over its monthly minute cap
--              (only for jobs not yet accepted by GitHub).
create or replace function public.observer_pending_jobs(p_limit integer default 10)
returns jsonb language plpgsql security definer set search_path = public,pg_temp as $$
declare v_result jsonb; v_slots boolean; v_over boolean;
begin
  v_slots:=private.observer_fallback_slots()>0;
  v_over:=false;
  if v_slots then
    v_over:=not exists(select 1 from public.observer_organizations_by_load() o where not o.over_limit);
  end if;
  -- Reserve a dispatch attempt before calling GitHub. An ambiguous network result
  -- may be retried after two minutes; only one resulting machine can claim the job.
  with picked as (
    select j.id,
      case when j.runner='github-hosted' and j.status='dispatched' and j.dispatch_count>=3 then 'stalled'
        when v_over and j.runner='github-hosted' and j.status='queued' then 'over_limit' end as fallback
    from private.observer_jobs j join private.observer_installations i on i.organization=j.organization
    where j.status in ('queued','dispatched') and i.enabled and j.expires_at>now() and (
      (j.dispatch_count<3 and (j.last_dispatch_at is null or j.last_dispatch_at<now()-interval '2 minutes')
        -- A self-hosted run accepted by GitHub waits for a free runner;
        -- re-dispatching it would only queue duplicates on the fallback.
        and not (j.runner='self-hosted' and j.status='dispatched'))
      or (v_slots and j.runner='github-hosted' and j.status='dispatched' and j.dispatch_count>=3
        and j.last_dispatch_at<now()-interval '10 minutes'))
    order by j.created_at for update of j skip locked limit greatest(1,least(coalesce(p_limit,10),20))
  ), reserved as (
    update private.observer_jobs j set dispatch_count=dispatch_count+1,last_dispatch_at=now()
    from picked p where j.id=p.id
    returning j.id,j.kind,j.organization,j.workflow_sha,j.encrypted_nonce,j.runner,p.fallback
  )
  select coalesce(jsonb_agg(to_jsonb(reserved)),'[]') into v_result from reserved;
  return v_result;
end $$;

-- Move a pending GitHub-hosted job to a free self-hosted fallback slot. Another
-- organization is preferred, so GitHub-hosted runs already queued for the job
-- can no longer claim it (claims verify the organization); p_avoid excludes
-- organizations that just failed. The other job of a split (execute + engine)
-- run has to run at the same time, so a still-pending partner moves along and
-- needs a slot too; it is dispatched on the next round. Like
-- observer_failover_job, the owner's placement follows the organization. The
-- job returns to 'queued' so an ambiguous fallback dispatch is retried.
-- The fallback VM is started on demand (ops/fallback-runner/fallback-watcher.py)
-- and needs a few minutes to boot, so a moved job gets a fresh thirty-minute
-- claim window, and the session of its run (or of its preparation's adaptation
-- run) is shifted by the time the job already waited: the team keeps the whole
-- window it had when the job was created.
create function public.observer_fallback_job(p_job uuid,p_avoid text[] default '{}')
returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$
declare v private.observer_jobs; v_partner private.observer_jobs; v_target private.observer_installations;
  v_owner uuid; v_need integer;
begin
  -- One fallback decision at a time keeps the slot count exact.
  perform pg_advisory_xact_lock(hashtext('observer-fallback'));
  select * into v from private.observer_jobs where id=p_job for update;
  if not found or v.status not in ('queued','dispatched') or v.expires_at<=now() then
    raise exception 'job_unavailable'; end if;
  if v.runner='self-hosted' then raise exception 'job_conflict'; end if;
  select * into v_partner from private.observer_jobs p
    where v.run_id is not null and p.run_id=v.run_id and p.id<>v.id and p.runner='github-hosted'
      and p.status in ('queued','dispatched') and p.expires_at>now() for update;
  v_need:=case when v_partner.id is null then 1 else 2 end;
  select i.* into v_target from private.observer_installations i
  where i.enabled and not (i.organization=any(coalesce(p_avoid,'{}')))
    and i.fallback_capacity-(select count(*) from private.observer_jobs j
      where j.organization=i.organization and j.runner='self-hosted'
        and j.status in ('queued','dispatched','claimed') and j.expires_at>now())>=v_need
  order by i.organization=v.organization, substring(i.organization from '[0-9]+$')::integer
  limit 1;
  if not found then raise exception 'fallback_unavailable'; end if;
  update private.observer_jobs set organization=v_target.organization,repository_id=v_target.repository_id,
    organization_id=v_target.organization_id,workflow_sha=v_target.approved_sha,runner='self-hosted',
    status='queued',error='',dispatch_count=1,last_dispatch_at=now(),
    expires_at=greatest(expires_at,now()+interval '30 minutes')
    where id=p_job;
  if v_partner.id is not null then
    update private.observer_jobs set organization=v_target.organization,repository_id=v_target.repository_id,
      organization_id=v_target.organization_id,workflow_sha=v_target.approved_sha,runner='self-hosted',
      status='queued',error='',dispatch_count=0,last_dispatch_at=null,
      expires_at=greatest(expires_at,now()+interval '30 minutes')
      where id=v_partner.id;
  end if;
  update private.observer_sessions s set expires_at=s.expires_at+(now()-v.created_at)
    where s.expires_at>now() and s.deadline_at is null and s.run_id in
      (v.run_id,(select p.model_run_id from private.observer_preparations p where p.revision_id=v.revision_id));
  if v_target.organization<>v.organization then
    select coalesce(b.user_id,pr.owner_id) into v_owner
      from private.observer_jobs j
      left join public.observer_runs r on r.id=j.run_id
      left join public.observer_batches b on b.id=r.batch_id
      left join public.observer_revisions v2 on v2.id=j.revision_id
      left join public.observer_projects pr on pr.id=v2.project_id
    where j.id=p_job;
    if v_owner is not null then
      update private.observer_placements set organization=v_target.organization
        where user_id=v_owner and organization=v.organization;
    end if;
  end if;
  return jsonb_build_object('organization',v_target.organization,'approved_sha',v_target.approved_sha,
    'partner',v_partner.id);
end $$;

-- Fallback runs cost no GitHub-hosted minutes and are not GitHub-hosted load:
-- organization desirability and the monthly cap count GitHub-hosted jobs only.
create or replace function public.observer_organizations_by_load()
returns table(organization text, approved_sha text, active_jobs bigint, week_seconds double precision,
  month_minutes double precision, monthly_minute_limit integer, dispatch_failures bigint, placements bigint,
  over_limit boolean)
language sql stable security definer set search_path=public,pg_temp as $$
  select x.organization, x.approved_sha, x.active_jobs, x.week_seconds, x.month_minutes,
    x.monthly_minute_limit, x.dispatch_failures, x.placements,
    x.month_minutes>=x.monthly_minute_limit as over_limit
  from (
    select i.organization, i.approved_sha,
      (select count(*) from private.observer_jobs j where j.organization=i.organization and j.runner='github-hosted'
        and j.status in ('queued','dispatched','claimed') and j.expires_at>now()) as active_jobs,
      (select coalesce(sum(extract(epoch from j.finished_at-j.claimed_at)),0)::double precision
        from private.observer_jobs j where j.organization=i.organization and j.runner='github-hosted'
          and j.claimed_at is not null and j.finished_at is not null
          and j.finished_at>now()-interval '7 days') as week_seconds,
      (select coalesce(sum(extract(epoch from j.finished_at-j.claimed_at))/60,0)::double precision
        from private.observer_jobs j where j.organization=i.organization and j.runner='github-hosted'
          and j.claimed_at is not null and j.finished_at is not null
          and j.finished_at>=date_trunc('month',now())) as month_minutes,
      i.monthly_minute_limit,
      (select count(*) from private.observer_dispatch_failures f where f.organization=i.organization
        and f.failed_at>now()-interval '30 minutes') as dispatch_failures,
      (select count(*) from private.observer_placements p where p.organization=i.organization) as placements
    from private.observer_installations i where i.enabled) x
  order by x.month_minutes>=x.monthly_minute_limit, x.dispatch_failures>0, x.active_jobs, x.week_seconds,
    case when x.month_minutes>=x.monthly_minute_limit then x.month_minutes end,
    x.placements, substring(x.organization from '[0-9]+$')::integer
$$;

do $$
declare f record;
begin
  for f in select p.oid::regprocedure as signature from pg_proc p join pg_namespace n on n.oid=p.pronamespace
    where n.nspname='public' and p.proname in ('observer_pending_jobs','observer_fallback_job',
      'observer_organizations_by_load')
  loop
    execute format('revoke all on function %s from public,anon,authenticated',f.signature);
    execute format('grant execute on function %s to service_role',f.signature);
  end loop;
end $$;

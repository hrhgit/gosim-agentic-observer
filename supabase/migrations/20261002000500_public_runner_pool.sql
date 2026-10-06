-- Public-repository runner pool (ops/public-runner-pool.md): GitHub-hosted
-- runners of a PUBLIC repository cost no Actions minutes, so a public
-- repository in one runner organization takes colocated engine jobs as an
-- overflow while the private organizations are near their monthly minutes or
-- busy. Everything about a public run is visible to anyone, therefore:
--   * the only dispatch input is the job id; the claim is bound to the run id
--     GitHub returns at dispatch instead of a nonce;
--   * the scenario, the participant project and the claim payload are sealed
--     to a per-job runner key, the result to the backend's key
--     (observer-job, project_platform/sealing.py); the run log shows status only;
--   * a phase uses the pool only when switched on for that phase, and a sealed
--     (hidden) phase additionally only once the sealed transfer has been
--     verified end to end (sealed_transfer_verified).
-- Default: off. Nothing changes until an organizer switches it on.

create table private.observer_public_pool(
  id boolean primary key default true check (id),
  organization text not null references private.observer_installations(organization),
  repository text not null default 'observer-public' check (repository='observer-public'),
  repository_id text not null check (repository_id ~ '^[0-9]+$'),
  organization_id text not null check (organization_id ~ '^[0-9]+$'),
  approved_sha text check (approved_sha ~ '^[0-9a-f]{40}$'),
  -- off: never used. drill: only drill_users' runs. overflow: drill_users'
  -- runs, plus every enabled phase's runs while their organization is near
  -- its monthly minutes or busy.
  mode text not null default 'off' check (mode in ('off','drill','overflow')),
  drill_users uuid[] not null default '{}',
  -- Modest use: at most this many public jobs at once and minutes per month.
  max_active integer not null default 3 check (max_active between 1 and 20),
  monthly_minute_cap integer not null default 6000 check (monthly_minute_cap between 0 and 100000),
  -- Overflow trigger for a job's own organization.
  overflow_ratio numeric not null default 0.8 check (overflow_ratio between 0 and 1),
  busy_jobs integer not null default 12 check (busy_jobs between 1 and 20),
  sealed_transfer_verified boolean not null default false,
  updated_at timestamptz not null default now()
);
revoke all on private.observer_public_pool from public,anon,authenticated;

create table private.observer_public_pool_phases(
  phase_id uuid primary key references public.phases(id) on delete cascade,
  enabled boolean not null default false,
  updated_at timestamptz not null default now()
);
revoke all on private.observer_public_pool_phases from public,anon,authenticated;

alter table private.observer_jobs drop constraint observer_jobs_runner_check;
alter table private.observer_jobs add constraint observer_jobs_runner_check
  check (runner in ('github-hosted','self-hosted','public-hosted'));
alter table private.observer_jobs add column public_run_id text check (public_run_id ~ '^[0-9]+$');
alter table private.observer_jobs add column home_organization text references private.observer_installations(organization);
alter table private.observer_jobs add column home_dispatch_count integer;
alter table private.observer_jobs add column public_moved_at timestamptz;
-- Set when a job comes back from the public pool: it is never offered again,
-- so a broken pool cannot bounce a job until it expires.
alter table private.observer_jobs add column public_declined_at timestamptz;
alter table private.observer_jobs add column sealed_cleaned_at timestamptz;
alter table private.observer_jobs add constraint observer_jobs_public_home
  check (runner<>'public-hosted' or home_organization is not null);

-- Minutes the public pool used this calendar month (GitHub-hosted, free).
create function private.observer_public_month_minutes()
returns double precision language sql stable security definer set search_path=public,pg_temp as $$
  select coalesce(sum(extract(epoch from j.finished_at-j.claimed_at))/60,0)::double precision
  from private.observer_jobs j where j.runner='public-hosted' and j.claimed_at is not null
    and j.finished_at is not null and j.finished_at>=date_trunc('month',now())
$$;

-- Whether the public pool takes this job now. Only a queued, not yet
-- GitHub-accepted engine job of a single-job (colocated, public-scenario) run
-- of an enabled phase qualifies. p_require_trigger=false skips the overflow
-- condition (used when the job's own organizations already failed).
create function private.observer_public_candidate(p_job uuid,p_require_trigger boolean default true)
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
  if not coalesce(p_require_trigger,true) then return true; end if;
  select * into v_load from public.observer_organizations_by_load() o where o.organization=v.organization;
  return v_load.organization is null or v_load.over_limit
    or v_load.month_minutes>=v_load.monthly_minute_limit*p.overflow_ratio or v_load.active_jobs>=p.busy_jobs;
end $$;

-- Move a pending job to the public pool. The owner's placement does not move:
-- the result is committed by the backend to the team's own private repository.
create function public.observer_public_job(p_job uuid)
returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$
declare p private.observer_public_pool; v private.observer_jobs;
begin
  perform pg_advisory_xact_lock(hashtext('observer-public-pool'));
  select * into v from private.observer_jobs where id=p_job for update;
  if not found or not private.observer_public_candidate(p_job,false) then raise exception 'public_pool_unavailable'; end if;
  select * into p from private.observer_public_pool where id;
  update private.observer_jobs set home_organization=organization,home_dispatch_count=dispatch_count,
    public_moved_at=now(),organization=p.organization,repository_id=p.repository_id,organization_id=p.organization_id,
    workflow_sha=p.approved_sha,runner='public-hosted',status='queued',error='',public_run_id=null
    where id=p_job;
  return jsonb_build_object('organization',p.organization,'approved_sha',p.approved_sha,
    'repository_id',p.repository_id);
end $$;

-- The dispatch target of a job already in the public pool (a repeated dispatch
-- after an ambiguous GitHub response). Null once the pool is switched off.
create function public.observer_public_target(p_job uuid)
returns jsonb language sql stable security definer set search_path=public,pg_temp as $$
  select jsonb_build_object('organization',p.organization,'approved_sha',p.approved_sha,
    'repository_id',p.repository_id)
  from private.observer_jobs j join private.observer_public_pool p on p.id
  where j.id=p_job and j.runner='public-hosted' and j.status='queued' and p.mode<>'off'
    and j.organization=p.organization and j.workflow_sha=p.approved_sha and j.expires_at>now()
$$;

create function public.observer_mark_public_dispatched(p_job uuid,p_github_run text)
returns void language plpgsql security definer set search_path=public,pg_temp as $$
begin
  if p_github_run is null or p_github_run !~ '^[0-9]+$' then raise exception 'invalid_dispatch_run'; end if;
  update private.observer_jobs set status='dispatched',error='',public_run_id=p_github_run
    where id=p_job and runner='public-hosted' and status='queued';
end $$;

-- Back to the job's own organization (dispatch failure, pool switched off, or
-- the dispatched run never claimed it), for good. A later claim by the
-- abandoned public run is refused: claims verify repository, organization and
-- run id. The dispatch count is the one the job had when it moved, so the
-- dispatcher may still use the current round's attempt for its own organization.
create function public.observer_public_return_job(p_job uuid,p_error text default '')
returns void language plpgsql security definer set search_path=public,pg_temp as $$
declare v private.observer_jobs; i private.observer_installations;
begin
  select * into v from private.observer_jobs where id=p_job for update;
  if not found or v.runner<>'public-hosted' or v.status not in ('queued','dispatched') then return; end if;
  select * into i from private.observer_installations where organization=v.home_organization;
  update private.observer_jobs set organization=i.organization,repository_id=i.repository_id,
    organization_id=i.organization_id,workflow_sha=i.approved_sha,runner='github-hosted',status='queued',
    error=left(coalesce(p_error,''),1000),public_run_id=null,dispatch_count=coalesce(v.home_dispatch_count,0),
    home_organization=null,home_dispatch_count=null,public_moved_at=null,public_declined_at=now(),
    expires_at=greatest(expires_at,now()+interval '30 minutes')
    where id=p_job;
  -- The team keeps its full window: the time spent in the public pool is added
  -- once (it is only ever offered once).
  update private.observer_sessions s set expires_at=s.expires_at+(now()-coalesce(v.public_moved_at,now()))
    where s.expires_at>now() and s.deadline_at is null and s.run_id=v.run_id;
end $$;

create function public.observer_public_pool_reconcile()
returns integer language plpgsql security definer set search_path=public,pg_temp as $$
declare v record; n integer:=0; v_off boolean;
begin
  v_off:=not exists(select 1 from private.observer_public_pool where id and mode<>'off' and approved_sha is not null);
  for v in select j.id from private.observer_jobs j
    where j.runner='public-hosted' and j.expires_at>now() and (
      (j.status='queued' and (v_off or coalesce(j.last_dispatch_at,j.public_moved_at)<now()-interval '5 minutes'))
      or (j.status='dispatched' and j.last_dispatch_at<now()-interval '10 minutes'))
    order by j.created_at limit 50
  loop
    perform public.observer_public_return_job(v.id,'public_run_not_started');
    n:=n+1;
  end loop;
  return n;
end $$;

-- Jobs to dispatch now (supersedes 20261001000500): also flags public-pool
-- candidates, and never re-dispatches a public job GitHub already accepted
-- (its claim is bound to that run).
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
        and not (j.runner in ('self-hosted','public-hosted') and j.status='dispatched'))
      or (v_slots and j.runner='github-hosted' and j.status='dispatched' and j.dispatch_count>=3
        and j.last_dispatch_at<now()-interval '10 minutes'))
    order by j.created_at for update of j skip locked limit greatest(1,least(coalesce(p_limit,10),20))
  ), reserved as (
    update private.observer_jobs j set dispatch_count=dispatch_count+1,last_dispatch_at=now()
    from picked p where j.id=p.id
    returning j.id,j.kind,j.organization,j.workflow_sha,j.encrypted_nonce,j.runner,p.fallback,j.status
  )
  select coalesce(jsonb_agg(jsonb_build_object('id',r.id,'kind',r.kind,'organization',r.organization,
    'workflow_sha',r.workflow_sha,'encrypted_nonce',r.encrypted_nonce,'runner',r.runner,'fallback',r.fallback,
    'public',case when r.runner='github-hosted' and r.kind='engine' and r.status='queued'
      then private.observer_public_candidate(r.id) end)),'[]') into v_result from reserved r;
  return v_result;
end $$;

-- Claims (supersedes 20260925000200): a public-pool job has no nonce; it must
-- be claimed by exactly the run GitHub created for its dispatch.
create or replace function public.observer_claim_job(p_job uuid,p_nonce text,p_github_run text,p_attempt text,
  p_repository text,p_owner text,p_sha text)
returns text language plpgsql security definer set search_path = public,pg_temp as $$
declare v private.observer_jobs;
begin
  select j.* into v from private.observer_jobs j join private.observer_installations i on i.organization=j.organization
    where j.id=p_job and i.enabled for update of j;
  if not found or v.expires_at<=clock_timestamp() or v.status not in ('queued','dispatched','claimed') then
    raise exception 'job_unavailable'; end if;
  if v.runner='public-hosted' then
    if p_nonce is not null or v.public_run_id is null or p_github_run is distinct from v.public_run_id
      or not exists(select 1 from private.observer_public_pool p where p.id and p.organization=v.organization
        and p.repository_id=v.repository_id) then raise exception 'job_identity_mismatch'; end if;
  elsif p_nonce is null or v.nonce_hash is distinct from sha256(convert_to(p_nonce,'UTF8')) then
    raise exception 'job_identity_mismatch';
  end if;
  if p_repository is distinct from v.repository_id or p_owner is distinct from v.organization_id
    or p_sha is distinct from v.workflow_sha or p_github_run is null or p_github_run !~ '^[0-9]+$'
    or p_attempt is null or p_attempt !~ '^[0-9]+$' then raise exception 'job_identity_mismatch'; end if;
  if v.status='claimed' then
    if v.github_run_id is distinct from p_github_run or v.github_run_attempt is distinct from p_attempt then
      raise exception 'job_already_claimed'; end if;
    return v.encrypted_input;
  end if;
  update private.observer_jobs set status='claimed',github_run_id=p_github_run,github_run_attempt=p_attempt,
    claimed_at=now(),expires_at=now()+interval '6 hours' where id=p_job;
  return v.encrypted_input;
end $$;

-- The expected workflow identity (supersedes 20260925000200): the public pool
-- repository and its visibility for public-pool jobs.
create or replace function public.observer_job_identity(p_job uuid)
returns jsonb language sql stable security definer set search_path = public,pg_temp as $$
  select jsonb_build_object('repositoryId',j.repository_id,'organizationId',j.organization_id,
    'organization',j.organization,'workflow','observer-'||j.kind||'.yml','approvedSha',j.workflow_sha,
    'runId',j.github_run_id,'runAttempt',j.github_run_attempt,
    'repository',case when j.runner='public-hosted' then 'observer-public' else 'observer-control' end,
    'visibility',case when j.runner='public-hosted' then 'public' else 'private' end)
  from private.observer_jobs j join private.observer_installations i on i.organization=j.organization
  where j.id=p_job and i.enabled and j.expires_at>now()
$$;

-- Rescore jobs (supersedes 20261001000600): a run evaluated by the public pool
-- is rescored in the team's own organization, not on the pool organization's
-- private minutes.
create or replace function public.observer_pending_score_runs(p_limit integer default 5)
returns jsonb language sql stable security definer set search_path=public,pg_temp as $$
  select coalesce(jsonb_agg(x order by x.finished_at,x.id),'[]'::jsonb) from (
    select r.id,r.finished_at,
      coalesce(i.organization,(select o.organization from public.observer_organizations_by_load() o limit 1))
        as organization,
      s.storage_path,s.digest as scenario_digest,
      r.result_path,r.decisions_digest,coalesce(r.score_summary->>'termination_reason','') as termination_reason
    from public.observer_runs r
    join private.observer_scenario_bundles s on s.scenario_id=r.scenario_id
    left join private.observer_jobs e on e.run_id=r.id and e.kind='engine'
    left join private.observer_installations i on i.organization=coalesce(e.home_organization,e.organization)
      and i.enabled
    where r.status='scored' and r.score_check='pending'
      and coalesce((select h.rescore from private.observer_hardening h where h.id),false)
      and not exists(select 1 from private.observer_jobs j where j.run_id=r.id and j.kind='score')
    order by r.finished_at,r.id limit greatest(1,least(coalesce(p_limit,5),20))) x
$$;

create function public.observer_public_sealed_pending(p_limit integer default 20)
returns jsonb language sql stable security definer set search_path=public,pg_temp as $$
  select coalesce(jsonb_agg(x.id),'[]') from (select j.id from private.observer_jobs j
    where j.runner='public-hosted' and j.sealed_cleaned_at is null and j.claimed_at is not null
      and (j.status in ('succeeded','failed') or j.expires_at<=now())
    order by j.finished_at nulls first limit greatest(1,least(coalesce(p_limit,20),100))) x
$$;

create function public.observer_public_sealed_cleaned(p_job uuid)
returns void language sql security definer set search_path=public,pg_temp as $$
  update private.observer_jobs set sealed_cleaned_at=now() where id=p_job and runner='public-hosted'
$$;

-- Organizer switches. Every change is audited.
create function public.observer_public_pool()
returns jsonb language sql stable security definer set search_path=public,pg_temp as $$
  select coalesce((select jsonb_build_object('organization',p.organization,'repository',p.repository,
      'approved_sha',p.approved_sha,'mode',p.mode,'drill_users',to_jsonb(p.drill_users),'max_active',p.max_active,
      'monthly_minute_cap',p.monthly_minute_cap,'overflow_ratio',p.overflow_ratio,'busy_jobs',p.busy_jobs,
      'sealed_transfer_verified',p.sealed_transfer_verified,
      'month_minutes',round(private.observer_public_month_minutes()::numeric,1),
      'active_jobs',(select count(*) from private.observer_jobs j where j.runner='public-hosted'
        and j.status in ('queued','dispatched','claimed') and j.expires_at>now()),
      'phases',(select coalesce(jsonb_agg(ph.slug order by ph.slug),'[]') from private.observer_public_pool_phases f
        join public.phases ph on ph.id=f.phase_id where f.enabled))
    from private.observer_public_pool p where p.id),jsonb_build_object('mode','off'))
$$;

create function public.observer_set_public_pool(p_mode text default null,p_max_active integer default null,
  p_monthly_minute_cap integer default null,p_overflow_ratio numeric default null,p_busy_jobs integer default null,
  p_drill_users uuid[] default null,p_sealed_transfer_verified boolean default null)
returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$
begin
  if not exists(select 1 from private.observer_public_pool where id) then raise exception 'public_pool_not_configured'; end if;
  update private.observer_public_pool set mode=coalesce(p_mode,mode),max_active=coalesce(p_max_active,max_active),
    monthly_minute_cap=coalesce(p_monthly_minute_cap,monthly_minute_cap),
    overflow_ratio=coalesce(p_overflow_ratio,overflow_ratio),busy_jobs=coalesce(p_busy_jobs,busy_jobs),
    drill_users=coalesce(p_drill_users,drill_users),
    sealed_transfer_verified=coalesce(p_sealed_transfer_verified,sealed_transfer_verified),updated_at=now()
    where id;
  perform private.audit('observer.public_pool',public.observer_public_pool());
  return public.observer_public_pool();
end $$;

create function public.observer_set_public_pool_phase(p_phase text,p_enabled boolean)
returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$
declare v_phase uuid;
begin
  select ph.id into v_phase from public.phases ph join public.observer_phase_settings c on c.phase_id=ph.id
    where ph.slug=p_phase;
  if v_phase is null or p_enabled is null then raise exception 'phase_not_found'; end if;
  insert into private.observer_public_pool_phases(phase_id,enabled) values(v_phase,p_enabled)
    on conflict(phase_id) do update set enabled=excluded.enabled,updated_at=now();
  perform private.audit('observer.public_pool_phase',jsonb_build_object('phase',p_phase,'enabled',p_enabled));
  return public.observer_public_pool();
end $$;

do $$
declare f record;
begin
  for f in select p.oid::regprocedure as signature from pg_proc p join pg_namespace n on n.oid=p.pronamespace
    where (n.nspname='public' and p.proname in ('observer_public_job','observer_public_target',
      'observer_mark_public_dispatched','observer_public_return_job','observer_public_pool_reconcile',
      'observer_pending_jobs','observer_claim_job','observer_job_identity','observer_public_sealed_pending',
      'observer_public_sealed_cleaned','observer_public_pool','observer_set_public_pool',
      'observer_set_public_pool_phase','observer_pending_score_runs'))
      or (n.nspname='private' and p.proname in ('observer_public_candidate','observer_public_month_minutes'))
  loop
    execute format('revoke all on function %s from public,anon,authenticated',f.signature);
    if f.signature::text like 'public.%' then
      execute format('grant execute on function %s to service_role',f.signature);
    end if;
  end loop;
end $$;

notify pgrst, 'reload schema';

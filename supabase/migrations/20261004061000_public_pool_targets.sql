-- Public runner pool across every runner organization (owner decision 2026-10-04).
--
-- Each runner organization has its own public repository observer-public (same
-- runner-only content, same approved runtime tag, same sealed transfers and
-- run-bound claims as ops/public-runner-pool.md). Public repositories cost no
-- Actions minutes; each organization runs at most 20 jobs at once (GitHub Free),
-- shared between its private and public repository.
--
-- * private.observer_public_targets: one row per public repository with its own
--   switch (enabled), max_active and health (cooldown_until). The single row of
--   private.observer_public_pool keeps the global settings: mode, drill users,
--   phases, global cap (max_active, now up to 400), monthly cap, sealed flag.
-- * A job goes to the least busy healthy public repository with a free slot.
--   A repository that fails to start or dispatch 3 jobs within 15 minutes cools
--   down for 15 minutes (no new jobs; its load goes elsewhere); jobs it failed go
--   back to their own organization (unchanged behaviour) and run there.
-- * mode 'primary': public repositories are the first choice for the teams in
--   rollout_percent (a stable hash of the team id; 100 = everyone); everything
--   else keeps the overflow rules. drill/overflow/off are unchanged.
-- * public.observer_public_targets_status(): load and health per repository.

create table if not exists private.observer_public_targets(
  organization text primary key references private.observer_installations(organization),
  repository_id text not null check (repository_id ~ '^[0-9]+$'),
  organization_id text not null check (organization_id ~ '^[0-9]+$'),
  approved_sha text check (approved_sha ~ '^[0-9a-f]{40}$'),
  enabled boolean not null default false,
  max_active integer not null default 15 check (max_active between 1 and 20),
  cooldown_until timestamptz,
  cooldown_reason text not null default '',
  updated_at timestamptz not null default now()
);
revoke all on private.observer_public_targets from public,anon,authenticated;
insert into private.observer_public_targets(organization,repository_id,organization_id,approved_sha,enabled,max_active)
  select organization,repository_id,organization_id,approved_sha,true,least(max_active,20) from private.observer_public_pool where id
  on conflict (organization) do nothing;

create table if not exists private.observer_public_events(
  id bigint generated always as identity primary key,
  organization text not null,
  job_id uuid,
  kind text not null check (kind in ('dispatched','returned','cooldown')),
  detail text not null default '',
  at timestamptz not null default now()
);
create index if not exists observer_public_events_org_at on private.observer_public_events(organization,at);
revoke all on private.observer_public_events from public,anon,authenticated;

alter table private.observer_public_pool drop constraint if exists observer_public_pool_mode_check;
alter table private.observer_public_pool add constraint observer_public_pool_mode_check
  check (mode in ('off','drill','overflow','primary'));
alter table private.observer_public_pool drop constraint if exists observer_public_pool_max_active_check;
alter table private.observer_public_pool add constraint observer_public_pool_max_active_check
  check (max_active between 1 and 400);
alter table private.observer_public_pool add column if not exists rollout_percent integer not null default 0
  check (rollout_percent between 0 and 100);

-- Active public jobs on one repository.
create or replace function private.observer_public_active(p_org text)
returns integer language sql stable security definer set search_path=public,pg_temp as $$
  select count(*)::integer from private.observer_jobs j where j.runner='public-hosted' and j.organization=p_org
    and j.status in ('queued','dispatched','claimed') and j.expires_at>now()
$$;

-- The least busy healthy public repository with a free slot (null row if none).
create or replace function private.observer_public_free_target()
returns private.observer_public_targets language sql stable security definer set search_path=public,pg_temp as $$
  select t.* from private.observer_public_targets t
    join private.observer_installations i on i.organization=t.organization and i.enabled
  where t.enabled and t.approved_sha is not null and (t.cooldown_until is null or t.cooldown_until<=now())
    and private.observer_public_active(t.organization)<t.max_active
  order by private.observer_public_active(t.organization)::numeric/t.max_active, random() limit 1
$$;

-- A stable share of teams: rollout_percent=10 takes the same tenth of teams every time.
create or replace function private.observer_public_rollout(p_team uuid,p_percent integer)
returns boolean language sql immutable as $$
  select p_team is not null and (abs(hashtextextended(p_team::text,0)) % 100) < coalesce(p_percent,0)
$$;

-- Health record; three returns within 15 minutes cool the repository down for 15 minutes.
create or replace function private.observer_public_note(p_org text,p_job uuid,p_kind text,p_detail text default '')
returns void language plpgsql security definer set search_path=public,pg_temp as $$
begin
  if p_org is null then return; end if;
  insert into private.observer_public_events(organization,job_id,kind,detail) values(p_org,p_job,p_kind,left(coalesce(p_detail,''),200));
  if p_kind='returned' and (select count(*) from private.observer_public_events e where e.organization=p_org
      and e.kind='returned' and e.at>now()-interval '15 minutes')>=3
    and exists(select 1 from private.observer_public_targets t where t.organization=p_org
      and (t.cooldown_until is null or t.cooldown_until<=now())) then
    update private.observer_public_targets set cooldown_until=now()+interval '15 minutes',
      cooldown_reason=left(coalesce(p_detail,''),200),updated_at=now() where organization=p_org;
    insert into private.observer_public_events(organization,kind,detail) values(p_org,'cooldown',left(coalesce(p_detail,''),200));
    perform private.audit('observer.public_target_cooldown',jsonb_build_object('organization',p_org,'reason',p_detail));
  end if;
end $$;

create or replace function private.observer_public_candidate(p_job uuid, p_require_trigger boolean DEFAULT true)
 RETURNS boolean
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO 'public', 'pg_temp'
AS $function$
declare p private.observer_public_pool; v private.observer_jobs; v_phase uuid; v_user uuid; v_sealed boolean; v_team uuid;
  v_load record;
begin
  select * into p from private.observer_public_pool where id;
  if not found or p.mode='off' then return false; end if;
  select * into v from private.observer_jobs where id=p_job;
  if not found or v.kind<>'engine' or v.runner<>'github-hosted' or v.status<>'queued' or v.run_id is null
    or v.expires_at<=now() or v.public_declined_at is not null then return false; end if;
  if exists(select 1 from private.observer_jobs o where o.run_id=v.run_id and o.id<>v.id)
    or exists(select 1 from private.observer_scenario_instances i where i.run_id=v.run_id) then return false; end if;
  select b.phase_id,b.user_id,b.team_id,c.sealed into v_phase,v_user,v_team,v_sealed
    from public.observer_runs r join public.observer_batches b on b.id=r.batch_id
    join public.observer_phase_settings c on c.phase_id=b.phase_id
    where r.id=v.run_id and b.mode='project' and c.colocated;
  if v_phase is null or not exists(select 1 from private.observer_public_pool_phases f
    where f.phase_id=v_phase and f.enabled) then return false; end if;
  if v_sealed and not p.sealed_transfer_verified then return false; end if;
  if (select count(*) from private.observer_jobs j where j.runner='public-hosted'
      and j.status in ('queued','dispatched','claimed') and j.expires_at>now())>=p.max_active
    or private.observer_public_month_minutes()>=p.monthly_minute_cap then return false; end if;
  -- Some public repository must have a free slot and be healthy (enabled, not cooling down).
  if (private.observer_public_free_target()).organization is null then return false; end if;
  if v_user=any(p.drill_users) then return true; end if;
  if p.mode not in ('overflow','primary') then return false; end if;
  -- primary: the public repositories are the first choice for the teams in the rollout share.
  if p.mode='primary' and private.observer_public_rollout(v_team,p.rollout_percent) then return true; end if;
  -- A sealed phase (the hidden final) switched on for the pool prefers it: its
  -- jobs cost no Actions minutes there, up to max_active at a time and the
  -- monthly cap; the rest run in their own organizations. Reaching this line
  -- already required sealed_transfer_verified.
  if v_sealed then return true; end if;
  if not coalesce(p_require_trigger,true) then return true; end if;
  select * into v_load from public.observer_organizations_by_load() o where o.organization=v.organization;
  return v_load.organization is null or v_load.over_limit
    or v_load.month_minutes>=v_load.monthly_minute_limit*p.overflow_ratio or v_load.active_jobs>=p.busy_jobs;
end $function$;

create or replace function public.observer_public_job(p_job uuid)
returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$
declare t private.observer_public_targets; v private.observer_jobs;
begin
  perform pg_advisory_xact_lock(hashtext('observer-public-pool'));
  select * into v from private.observer_jobs where id=p_job for update;
  if not found or not private.observer_public_candidate(p_job,false) then raise exception 'public_pool_unavailable'; end if;
  t:=private.observer_public_free_target();
  if t.organization is null then raise exception 'public_pool_unavailable'; end if;
  update private.observer_jobs set home_organization=organization,home_dispatch_count=dispatch_count,
    public_moved_at=now(),organization=t.organization,repository_id=t.repository_id,organization_id=t.organization_id,
    workflow_sha=t.approved_sha,runner='public-hosted',status='queued',error='',public_run_id=null
    where id=p_job;
  return jsonb_build_object('organization',t.organization,'approved_sha',t.approved_sha,'repository_id',t.repository_id);
end $$;

create or replace function public.observer_public_target(p_job uuid)
returns jsonb language sql stable security definer set search_path=public,pg_temp as $$
  select jsonb_build_object('organization',t.organization,'approved_sha',t.approved_sha,'repository_id',t.repository_id)
  from private.observer_jobs j join private.observer_public_targets t on t.organization=j.organization
    join private.observer_public_pool p on p.id
  where j.id=p_job and j.runner='public-hosted' and j.status='queued' and p.mode<>'off' and t.enabled
    and j.repository_id=t.repository_id and j.workflow_sha=t.approved_sha and j.expires_at>now()
$$;

create or replace function public.observer_mark_public_dispatched(p_job uuid,p_github_run text)
returns void language plpgsql security definer set search_path=public,pg_temp as $$
declare v_org text;
begin
  if p_github_run is null or p_github_run !~ '^[0-9]+$' then raise exception 'invalid_dispatch_run'; end if;
  update private.observer_jobs set status='dispatched',error='',public_run_id=p_github_run
    where id=p_job and runner='public-hosted' and status='queued' returning organization into v_org;
  perform private.observer_public_note(v_org,p_job,'dispatched');
end $$;

create or replace function public.observer_public_return_job(p_job uuid, p_error text DEFAULT ''::text)
 RETURNS void
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public', 'pg_temp'
AS $function$
declare v private.observer_jobs; i private.observer_installations;
begin
  select * into v from private.observer_jobs where id=p_job for update;
  if not found or v.runner<>'public-hosted' or v.status not in ('queued','dispatched') then return; end if;
  -- Health: the public repository the job leaves is the one that failed it.
  perform private.observer_public_note(v.organization,p_job,'returned',p_error);
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
end $function$;

create or replace function public.observer_claim_job(p_job uuid, p_nonce text, p_github_run text, p_attempt text, p_repository text, p_owner text, p_sha text)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public', 'pg_temp'
AS $function$
declare v private.observer_jobs;
begin
  select j.* into v from private.observer_jobs j join private.observer_installations i on i.organization=j.organization
    where j.id=p_job and i.enabled for update of j;
  if not found or v.expires_at<=clock_timestamp() or v.status not in ('queued','dispatched','claimed') then
    raise exception 'job_unavailable'; end if;
  if v.runner='public-hosted' then
    if p_nonce is not null or v.public_run_id is null or p_github_run is distinct from v.public_run_id
      or not exists(select 1 from private.observer_public_targets t where t.organization=v.organization
        and t.repository_id=v.repository_id) then raise exception 'job_identity_mismatch'; end if;
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
end $function$;

create or replace function public.observer_public_pool_reconcile()
returns integer language plpgsql security definer set search_path=public,pg_temp as $$
declare v record; n integer:=0; v_off boolean;
begin
  v_off:=not exists(select 1 from private.observer_public_pool where id and mode<>'off');
  for v in select j.id from private.observer_jobs j
    left join private.observer_public_targets t on t.organization=j.organization
    where j.runner='public-hosted' and j.expires_at>now() and (
      (j.status='queued' and (v_off or not coalesce(t.enabled,false)
        or coalesce(j.last_dispatch_at,j.public_moved_at)<now()-interval '5 minutes'))
      or (j.status='dispatched' and j.last_dispatch_at<now()-interval '10 minutes'))
    order by j.created_at limit 50
  loop
    perform public.observer_public_return_job(v.id,'public_run_not_started');
    n:=n+1;
  end loop;
  return n;
end $$;

-- Load and health per public repository (organizers: select public.observer_public_targets_status();).
create or replace function public.observer_public_targets_status()
returns jsonb language sql stable security definer set search_path=public,pg_temp as $$
  select coalesce(jsonb_agg(jsonb_build_object('organization',t.organization,'enabled',t.enabled,
    'healthy',t.enabled and (t.cooldown_until is null or t.cooldown_until<=now()),
    'cooldown_until',t.cooldown_until,'cooldown_reason',nullif(t.cooldown_reason,''),
    'active',private.observer_public_active(t.organization),'max_active',t.max_active,'approved_sha',t.approved_sha,
    'dispatched_1h',(select count(*) from private.observer_public_events e where e.organization=t.organization and e.kind='dispatched' and e.at>now()-interval '1 hour'),
    'returned_1h',(select count(*) from private.observer_public_events e where e.organization=t.organization and e.kind='returned' and e.at>now()-interval '1 hour'),
    'succeeded_24h',(select count(*) from private.observer_jobs j where j.runner='public-hosted' and j.organization=t.organization and j.status='succeeded' and j.finished_at>now()-interval '1 day'),
    'failed_24h',(select count(*) from private.observer_jobs j where j.runner='public-hosted' and j.organization=t.organization and j.status='failed' and j.finished_at>now()-interval '1 day'),
    'start_seconds_avg_24h',(select round(avg(extract(epoch from j.claimed_at-j.last_dispatch_at))) from private.observer_jobs j
      where j.runner='public-hosted' and j.organization=t.organization and j.claimed_at>now()-interval '1 day'))
    order by t.organization),'[]'::jsonb)
  from private.observer_public_targets t
$$;

create or replace function public.observer_public_pool()
returns jsonb language sql stable security definer set search_path=public,pg_temp as $$
  select coalesce((select jsonb_build_object('mode',p.mode,'rollout_percent',p.rollout_percent,
      'drill_users',to_jsonb(p.drill_users),'max_active',p.max_active,
      'monthly_minute_cap',p.monthly_minute_cap,'overflow_ratio',p.overflow_ratio,'busy_jobs',p.busy_jobs,
      'sealed_transfer_verified',p.sealed_transfer_verified,
      'month_minutes',round(private.observer_public_month_minutes()::numeric,1),
      'active_jobs',(select count(*) from private.observer_jobs j where j.runner='public-hosted'
        and j.status in ('queued','dispatched','claimed') and j.expires_at>now()),
      'phases',(select coalesce(jsonb_agg(ph.slug order by ph.slug),'[]') from private.observer_public_pool_phases f
        join public.phases ph on ph.id=f.phase_id where f.enabled),
      'targets',public.observer_public_targets_status(),
      -- Kept for scripts that read the single-repository fields.
      'organization',p.organization,'repository','observer-public','approved_sha',p.approved_sha)
    from private.observer_public_pool p where p.id),jsonb_build_object('mode','off'))
$$;

drop function if exists public.observer_set_public_pool(text,integer,integer,numeric,integer,uuid[],boolean);
create function public.observer_set_public_pool(p_mode text default null,p_max_active integer default null,
  p_monthly_minute_cap integer default null,p_overflow_ratio numeric default null,p_busy_jobs integer default null,
  p_drill_users uuid[] default null,p_sealed_transfer_verified boolean default null,p_rollout_percent integer default null)
returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$
begin
  if not exists(select 1 from private.observer_public_pool where id) then raise exception 'public_pool_not_configured'; end if;
  update private.observer_public_pool set mode=coalesce(p_mode,mode),max_active=coalesce(p_max_active,max_active),
    monthly_minute_cap=coalesce(p_monthly_minute_cap,monthly_minute_cap),
    overflow_ratio=coalesce(p_overflow_ratio,overflow_ratio),busy_jobs=coalesce(p_busy_jobs,busy_jobs),
    drill_users=coalesce(p_drill_users,drill_users),
    sealed_transfer_verified=coalesce(p_sealed_transfer_verified,sealed_transfer_verified),
    rollout_percent=coalesce(p_rollout_percent,rollout_percent),updated_at=now()
    where id;
  perform private.audit('observer.public_pool',public.observer_public_pool()-'targets');
  return public.observer_public_pool();
end $$;

-- Register or switch one public repository (kill switch: p_enabled=>false).
create or replace function public.observer_set_public_target(p_organization text,p_enabled boolean default null,
  p_max_active integer default null,p_approved_sha text default null,p_repository_id text default null,
  p_organization_id text default null,p_clear_cooldown boolean default false)
returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$
begin
  if not exists(select 1 from private.observer_public_targets where organization=p_organization) then
    if p_repository_id is null or p_organization_id is null then raise exception 'public_target_not_registered'; end if;
    insert into private.observer_public_targets(organization,repository_id,organization_id,approved_sha,enabled,max_active)
      values(p_organization,p_repository_id,p_organization_id,p_approved_sha,coalesce(p_enabled,false),coalesce(p_max_active,15));
  else
    update private.observer_public_targets set enabled=coalesce(p_enabled,enabled),max_active=coalesce(p_max_active,max_active),
      approved_sha=coalesce(p_approved_sha,approved_sha),repository_id=coalesce(p_repository_id,repository_id),
      organization_id=coalesce(p_organization_id,organization_id),
      cooldown_until=case when p_clear_cooldown then null else cooldown_until end,
      cooldown_reason=case when p_clear_cooldown then '' else cooldown_reason end,updated_at=now()
      where organization=p_organization;
  end if;
  perform private.audit('observer.public_target',(select to_jsonb(t) from private.observer_public_targets t where t.organization=p_organization));
  return public.observer_public_targets_status();
end $$;

do $$
declare f record;
begin
  for f in select p.oid::regprocedure as signature from pg_proc p join pg_namespace n on n.oid=p.pronamespace
    where (n.nspname='public' and p.proname in ('observer_public_job','observer_public_target','observer_mark_public_dispatched',
      'observer_public_return_job','observer_claim_job','observer_public_pool_reconcile','observer_public_targets_status',
      'observer_public_pool','observer_set_public_pool','observer_set_public_target'))
      or (n.nspname='private' and p.proname in ('observer_public_candidate','observer_public_active','observer_public_free_target',
        'observer_public_rollout','observer_public_note'))
  loop
    execute format('revoke all on function %s from public,anon,authenticated',f.signature);
    if f.signature::text like 'public.%' then
      execute format('grant execute on function %s to service_role',f.signature);
    end if;
  end loop;
end $$;

notify pgrst,'reload schema';

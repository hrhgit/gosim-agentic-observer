-- Health, cooldown and a kill switch for the 13 private runner organizations, the
-- same way the 13 public repositories have them (20261004061000), so all 26 runner
-- targets are treated alike and none is a single point of failure.
--
-- Before: a private organization only lost work when a dispatch to it failed with an
-- organization-level error. A run GitHub accepted but never started (Actions locked,
-- minutes exhausted, hosted runners unavailable, a broken runtime that cannot claim)
-- waited for the 30-minute job lease, was re-enqueued into the same organization
-- (placement is sticky) and could loop there until the team's session expired.
--
-- Now (private.observer_target_health.mode):
--   off      everything as before (the rollback).
--   observe  health events are recorded and shown, nothing is moved or avoided.
--   on       additionally:
--     * stall   - a job GitHub accepted three times but no run claimed within
--                 stall_minutes of the last dispatch moves to the best healthy
--                 organization (a late claim by the old run is refused: the job's
--                 repository changed). A claim that keeps failing looks the same.
--     * platform_failed - a claimed job that failed for a platform reason (not the
--                 participant's code) or whose runner vanished (lease expired).
--     * organization-level dispatch failures (already recorded) count as well.
--     threshold (3) events within window_minutes (15) cool the organization down for
--     cooldown_minutes (15): no new placements, failovers or rescore jobs go there,
--     its queued jobs move to healthy organizations (and with them the owner's
--     placement), and its colocated jobs may overflow to the public repositories.
--     An organization at its monthly minute limit is avoided the same way.
--     Claims of jobs already running there are never refused.
--     If no organization is healthy nothing moves (work is never stranded).
--
-- GitHub minutes: our estimate (claimed -> finished) undercounts what GitHub bills
-- (rounding per job, setup time, runs that fail to claim; up to 3x on 2026-10-04).
-- scripts/sync-runner-minutes.py writes the billed minutes per organization from
-- GitHub's billing API; in mode 'on' the limit is checked against
-- greatest(estimate, billed at last sync + estimate growth since). A stale or
-- missing sync falls back to the estimate.
--
-- One-line switches:
--   select public.observer_set_target_health(p_mode=>'on');        -- enable
--   select public.observer_set_target_health(p_mode=>'off');       -- rollback
--   select public.observer_set_private_target('AGENTIC-OBSERVER26-runner-7', p_routing=>false);  -- kill switch
--   select public.observer_set_private_target('AGENTIC-OBSERVER26-runner-7', p_clear_cooldown=>true);
--   select public.observer_targets_status();                         -- all 26 targets

create table if not exists private.observer_target_health(
  id boolean primary key default true check (id),
  mode text not null default 'off' check (mode in ('off','observe','on')),
  stall_minutes integer not null default 4 check (stall_minutes between 1 and 60),
  threshold integer not null default 3 check (threshold between 1 and 50),
  window_minutes integer not null default 15 check (window_minutes between 1 and 240),
  cooldown_minutes integer not null default 15 check (cooldown_minutes between 1 and 240),
  updated_at timestamptz not null default now()
);
insert into private.observer_target_health(id) values(true) on conflict do nothing;
revoke all on private.observer_target_health from public,anon,authenticated;

alter table private.observer_installations
  add column if not exists routing boolean not null default true,
  add column if not exists cooldown_until timestamptz,
  add column if not exists cooldown_reason text not null default '',
  add column if not exists github_minutes double precision,
  add column if not exists github_minutes_estimate double precision,
  add column if not exists github_minutes_at timestamptz;

create table if not exists private.observer_private_events(
  id bigint generated always as identity primary key,
  organization text not null,
  job_id uuid,
  kind text not null check (kind in ('stalled','platform_failed','moved','cooldown')),
  detail text not null default '',
  at timestamptz not null default now()
);
create index if not exists observer_private_events_org_at on private.observer_private_events(organization,at);
create index if not exists observer_private_events_job on private.observer_private_events(job_id);
revoke all on private.observer_private_events from public,anon,authenticated;

-- Public repositories count platform failures of their claimed jobs too.
alter table private.observer_public_events drop constraint if exists observer_public_events_kind_check;
alter table private.observer_public_events add constraint observer_public_events_kind_check
  check (kind in ('dispatched','returned','cooldown','platform_failed'));
create index if not exists observer_public_events_job on private.observer_public_events(job_id);

create or replace function private.observer_health_mode()
returns text language sql stable security definer set search_path=public,pg_temp as $$
  select coalesce((select mode from private.observer_target_health where id),'off')
$$;

-- A failed job the participant's code did not cause.
create or replace function private.observer_platform_failure(v private.observer_jobs)
returns boolean language sql immutable as $$
  select v.status='failed' and v.claimed_at is not null and v.kind in ('engine','execute','score','prepare')
    and coalesce(v.result->'diagnostics'->>'code','') not in ('project_error','project_operation_failed')
$$;

create or replace function private.observer_public_note(p_org text,p_job uuid,p_kind text,p_detail text default '')
returns void language plpgsql security definer set search_path=public,pg_temp as $$
begin
  if p_org is null then return; end if;
  insert into private.observer_public_events(organization,job_id,kind,detail) values(p_org,p_job,p_kind,left(coalesce(p_detail,''),200));
  if p_kind in ('returned','platform_failed') and (select count(*) from private.observer_public_events e where e.organization=p_org
      and e.kind in ('returned','platform_failed') and e.at>now()-interval '15 minutes')>=3
    and exists(select 1 from private.observer_public_targets t where t.organization=p_org
      and (t.cooldown_until is null or t.cooldown_until<=now())) then
    update private.observer_public_targets set cooldown_until=now()+interval '15 minutes',
      cooldown_reason=left(coalesce(p_detail,''),200),updated_at=now() where organization=p_org;
    insert into private.observer_public_events(organization,kind,detail) values(p_org,'cooldown',left(coalesce(p_detail,''),200));
    perform private.audit('observer.public_target_cooldown',jsonb_build_object('organization',p_org,'reason',p_detail));
  end if;
end $$;

-- Health events of one private organization within the window (dispatch failures included).
create or replace function private.observer_private_strikes(p_org text)
returns bigint language sql stable security definer set search_path=public,pg_temp as $$
  select (select count(*) from private.observer_private_events e where e.organization=p_org
      and e.kind in ('stalled','platform_failed') and e.at>now()-make_interval(mins=>h.window_minutes))
    + (select count(*) from private.observer_dispatch_failures f where f.organization=p_org
      and f.error in ('organization_not_installed','installation_identity_mismatch','control_repository_must_be_private',
        'control_revision_not_approved','github_not_found','github_request_failed')
      and f.failed_at>now()-make_interval(mins=>h.window_minutes))
  from private.observer_target_health h where h.id
$$;

create or replace function private.observer_private_note(p_org text,p_job uuid,p_kind text,p_detail text default '')
returns void language plpgsql security definer set search_path=public,pg_temp as $$
declare h private.observer_target_health;
begin
  select * into h from private.observer_target_health where id;
  if p_org is null or not found or h.mode='off' then return; end if;
  insert into private.observer_private_events(organization,job_id,kind,detail) values(p_org,p_job,p_kind,left(coalesce(p_detail,''),200));
  if h.mode='on' and p_kind in ('stalled','platform_failed') and private.observer_private_strikes(p_org)>=h.threshold
    and exists(select 1 from private.observer_installations i where i.organization=p_org
      and (i.cooldown_until is null or i.cooldown_until<=now())) then
    update private.observer_installations set cooldown_until=now()+make_interval(mins=>h.cooldown_minutes),
      cooldown_reason=left(p_kind||': '||coalesce(p_detail,''),200) where organization=p_org;
    insert into private.observer_private_events(organization,kind,detail) values(p_org,'cooldown',left(p_kind||': '||coalesce(p_detail,''),200));
    perform private.audit('observer.private_target_cooldown',jsonb_build_object('organization',p_org,'reason',p_kind,'detail',p_detail));
  end if;
end $$;

-- Minutes GitHub bills this month: the estimate, raised to the last billed figure plus
-- what the estimate grew since (health mode 'on' only; otherwise the plain estimate).
create or replace function private.observer_effective_minutes(i private.observer_installations,p_estimate double precision)
returns double precision language sql stable security definer set search_path=public,pg_temp as $$
  select case when private.observer_health_mode()='on' and i.github_minutes is not null
      and i.github_minutes_at>=date_trunc('month',now())
    then greatest(p_estimate,i.github_minutes+greatest(p_estimate-coalesce(i.github_minutes_estimate,p_estimate),0))
    else p_estimate end
$$;

drop function if exists public.observer_organizations_by_load();
create function public.observer_organizations_by_load()
returns table(organization text, approved_sha text, active_jobs bigint, week_seconds double precision,
  month_minutes double precision, monthly_minute_limit integer, dispatch_failures bigint, placements bigint,
  over_limit boolean, healthy boolean, health text)
language sql stable security definer set search_path=public,pg_temp as $$
  select y.organization, y.approved_sha, y.active_jobs, y.week_seconds, y.month_minutes, y.monthly_minute_limit,
    y.dispatch_failures, y.placements, y.over_limit,
    y.health='ok' as healthy, y.health
  from (
    select x.*, x.month_minutes>=x.monthly_minute_limit as over_limit,
      case when private.observer_health_mode()<>'on' then 'ok'
        when not x.routing then 'switched_off'
        when x.cooldown_until>now() then 'cooldown'
        when x.month_minutes>=x.monthly_minute_limit then 'minute_limit'
        else 'ok' end as health
    from (
      select i.organization, i.approved_sha, i.routing, i.cooldown_until,
        (select count(*) from private.observer_jobs j where j.organization=i.organization and j.runner='github-hosted'
          and j.status in ('queued','dispatched','claimed') and j.expires_at>now()) as active_jobs,
        (select coalesce(sum(extract(epoch from j.finished_at-j.claimed_at)),0)::double precision
          from private.observer_jobs j where j.organization=i.organization and j.runner='github-hosted'
            and j.claimed_at is not null and j.finished_at is not null
            and j.finished_at>now()-interval '7 days') as week_seconds,
        private.observer_effective_minutes(i,(select coalesce(sum(extract(epoch from j.finished_at-j.claimed_at))/60,0)::double precision
          from private.observer_jobs j where j.organization=i.organization and j.runner='github-hosted'
            and j.claimed_at is not null and j.finished_at is not null
            and j.finished_at>=date_trunc('month',now()))) as month_minutes,
        i.monthly_minute_limit,
        (select count(*) from private.observer_dispatch_failures f where f.organization=i.organization
          and f.failed_at>now()-interval '30 minutes') as dispatch_failures,
        (select count(*) from private.observer_placements p where p.organization=i.organization) as placements
      from private.observer_installations i where i.enabled) x) y
  order by y.health<>'ok', y.over_limit, y.dispatch_failures>0, y.active_jobs, y.week_seconds,
    case when y.over_limit then y.month_minutes end,
    y.placements, substring(y.organization from '[0-9]+$')::integer
$$;

-- The best healthy organization other than p_avoid (null if none).
create or replace function private.observer_healthy_organization(p_avoid text)
returns text language sql stable security definer set search_path=public,pg_temp as $$
  select o.organization from public.observer_organizations_by_load() o
  where o.healthy and o.organization is distinct from p_avoid limit 1
$$;

-- Move one pending private job (and its owner's placement) to another organization.
create or replace function private.observer_health_move(p_job uuid,p_reason text)
returns text language plpgsql security definer set search_path=public,pg_temp as $$
declare v private.observer_jobs; v_target text;
begin
  select * into v from private.observer_jobs where id=p_job for update skip locked;
  if not found or v.runner<>'github-hosted' or v.kind='prepare' or v.status not in ('queued','dispatched')
    or v.claimed_at is not null or v.expires_at<=now() then return null; end if;
  v_target:=private.observer_healthy_organization(v.organization);
  if v_target is null then return null; end if;
  begin
    perform public.observer_failover_job(p_job,v_target);
  exception when others then
    return null;
  end;
  -- The job gets a fresh claim window; the team keeps the time it waited.
  update private.observer_jobs set status='queued',dispatch_count=0,last_dispatch_at=null,
    expires_at=greatest(expires_at,now()+interval '30 minutes') where id=p_job;
  update private.observer_sessions s set expires_at=s.expires_at+least(now()-v.created_at,interval '30 minutes')
    where s.expires_at>now() and s.deadline_at is null and s.run_id=v.run_id;
  insert into private.observer_private_events(organization,job_id,kind,detail)
    values(v.organization,p_job,'moved',left(p_reason||' -> '||v_target,200));
  return v_target;
end $$;

-- Runs every minute (pg_cron 'observer-target-health'), independent of the dispatcher.
create or replace function public.observer_target_health_reconcile()
returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$
declare h private.observer_target_health; v record; n_stall integer:=0; n_fail integer:=0; n_move integer:=0;
begin
  select * into h from private.observer_target_health where id;
  if not found or h.mode='off' then return jsonb_build_object('mode','off'); end if;
  if not pg_try_advisory_xact_lock(hashtext('observer-target-health')) then return jsonb_build_object('busy',true); end if;
  -- Platform failures of claimed jobs (private and public), each noted once.
  for v in select j.* from private.observer_jobs j
    where j.status='failed' and j.finished_at>now()-make_interval(mins=>h.window_minutes)
      and j.runner in ('github-hosted','public-hosted') and private.observer_platform_failure(j)
      and not exists(select 1 from private.observer_private_events e where e.job_id=j.id and e.kind='platform_failed')
      and not exists(select 1 from private.observer_public_events e where e.job_id=j.id and e.kind='platform_failed')
    order by j.finished_at limit 100
  loop
    if v.runner='public-hosted' then
      perform private.observer_public_note(v.organization,v.id,'platform_failed',coalesce(nullif(v.error,''),'failed'));
    else
      perform private.observer_private_note(v.organization,v.id,'platform_failed',coalesce(nullif(v.error,''),'failed'));
    end if;
    n_fail:=n_fail+1;
  end loop;
  -- Stalls: accepted three times by GitHub, no run claimed it.
  for v in select j.id,j.organization from private.observer_jobs j
    where j.runner='github-hosted' and j.status='dispatched' and j.claimed_at is null and j.expires_at>now()
      and j.dispatch_count>=3 and j.last_dispatch_at<now()-make_interval(mins=>h.stall_minutes)
      and not exists(select 1 from private.observer_private_events e where e.job_id=j.id and e.kind='stalled'
        and e.organization=j.organization)
    order by j.created_at limit 50
  loop
    perform private.observer_private_note(v.organization,v.id,'stalled','github_run_not_started');
    n_stall:=n_stall+1;
    if h.mode='on' and private.observer_health_move(v.id,'stalled') is not null then n_move:=n_move+1; end if;
  end loop;
  if h.mode='on' then
    -- Queued work leaves organizations that are cooling down, switched off or at their minute limit.
    for v in select j.id,o.health from private.observer_jobs j
      join public.observer_organizations_by_load() o on o.organization=j.organization
      where not o.healthy and j.runner='github-hosted' and j.kind<>'prepare' and j.status='queued'
        and j.claimed_at is null and j.expires_at>now()
        -- not while a dispatch of it may be in flight
        and (j.last_dispatch_at is null or j.last_dispatch_at<now()-interval '2 minutes')
      order by j.created_at limit 50
    loop
      if private.observer_health_move(v.id,v.health) is not null then n_move:=n_move+1; end if;
    end loop;
  end if;
  delete from private.observer_private_events where at<now()-interval '7 days';
  return jsonb_build_object('mode',h.mode,'platform_failed',n_fail,'stalled',n_stall,'moved',n_move);
end $$;

-- A cooling, switched-off or at-limit organization lets its colocated jobs overflow to the
-- public repositories (only the load check at the end changes).
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
  if (private.observer_public_free_target()).organization is null then return false; end if;
  if v_user=any(p.drill_users) then return true; end if;
  if p.mode not in ('overflow','primary') then return false; end if;
  if p.mode='primary' and private.observer_public_rollout(v_team,p.rollout_percent) then return true; end if;
  if v_sealed then return true; end if;
  if not coalesce(p_require_trigger,true) then return true; end if;
  select * into v_load from public.observer_organizations_by_load() o where o.organization=v.organization;
  return v_load.organization is null or v_load.over_limit or not v_load.healthy
    or v_load.month_minutes>=v_load.monthly_minute_limit*p.overflow_ratio or v_load.active_jobs>=p.busy_jobs;
end $function$;

-- Kill switch / cooldown reset for one private organization. routing=false sends no new
-- work there; jobs already running keep their claims (unlike enabled=false).
create or replace function public.observer_set_private_target(p_organization text,p_routing boolean default null,
  p_clear_cooldown boolean default false)
returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$
begin
  update private.observer_installations set routing=coalesce(p_routing,routing),
    cooldown_until=case when p_clear_cooldown then null else cooldown_until end,
    cooldown_reason=case when p_clear_cooldown then '' else cooldown_reason end
    where organization=p_organization;
  if not found then raise exception 'runner_not_configured'; end if;
  perform private.audit('observer.private_target',jsonb_build_object('organization',p_organization,
    'routing',p_routing,'clear_cooldown',p_clear_cooldown));
  return public.observer_targets_status();
end $$;

create or replace function public.observer_set_target_health(p_mode text default null,p_stall_minutes integer default null,
  p_threshold integer default null,p_window_minutes integer default null,p_cooldown_minutes integer default null)
returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$
begin
  update private.observer_target_health set mode=coalesce(p_mode,mode),stall_minutes=coalesce(p_stall_minutes,stall_minutes),
    threshold=coalesce(p_threshold,threshold),window_minutes=coalesce(p_window_minutes,window_minutes),
    cooldown_minutes=coalesce(p_cooldown_minutes,cooldown_minutes),updated_at=now() where id;
  perform private.audit('observer.target_health',(select to_jsonb(h) from private.observer_target_health h where h.id));
  return (select to_jsonb(h) from private.observer_target_health h where h.id);
end $$;

-- Billed GitHub Actions minutes this month per organization: {"<organization>": minutes, ...}.
create or replace function public.observer_set_github_minutes(p_minutes jsonb)
returns integer language plpgsql security definer set search_path=public,pg_temp as $$
declare k text; m double precision; n integer:=0; v_est double precision;
begin
  if p_minutes is null or jsonb_typeof(p_minutes)<>'object' then raise exception 'invalid_minutes'; end if;
  for k in select jsonb_object_keys(p_minutes) loop
    m:=(p_minutes->>k)::double precision;
    if m is null or m<0 or m>1000000 then raise exception 'invalid_minutes'; end if;
    select coalesce(sum(extract(epoch from j.finished_at-j.claimed_at))/60,0)::double precision into v_est
      from private.observer_jobs j where j.organization=k and j.runner='github-hosted'
        and j.claimed_at is not null and j.finished_at is not null and j.finished_at>=date_trunc('month',now());
    update private.observer_installations set github_minutes=m,github_minutes_estimate=v_est,github_minutes_at=now()
      where organization=k;
    if found then n:=n+1; end if;
  end loop;
  return n;
end $$;

-- All 26 runner targets: the private organizations and their public repositories.
create or replace function public.observer_targets_status()
returns jsonb language sql stable security definer set search_path=public,pg_temp as $$
  select jsonb_build_object(
    'health',(select to_jsonb(h)-'id' from private.observer_target_health h where h.id),
    'private',(select coalesce(jsonb_agg(jsonb_build_object('organization',o.organization,
        'routing',i.routing,'healthy',o.healthy,'health',o.health,
        'cooldown_until',i.cooldown_until,'cooldown_reason',nullif(i.cooldown_reason,''),
        'active',o.active_jobs,'strikes',private.observer_private_strikes(o.organization),
        'month_minutes',round(o.month_minutes::numeric,1),'monthly_minute_limit',o.monthly_minute_limit,
        'github_minutes',i.github_minutes,'github_minutes_at',i.github_minutes_at,
        'stalled_1h',(select count(*) from private.observer_private_events e where e.organization=o.organization and e.kind='stalled' and e.at>now()-interval '1 hour'),
        'platform_failed_1h',(select count(*) from private.observer_private_events e where e.organization=o.organization and e.kind='platform_failed' and e.at>now()-interval '1 hour'),
        'moved_away_1h',(select count(*) from private.observer_private_events e where e.organization=o.organization and e.kind='moved' and e.at>now()-interval '1 hour'),
        'dispatch_failures_30m',o.dispatch_failures)
      order by substring(o.organization from '[0-9]+$')::integer),'[]'::jsonb)
      from public.observer_organizations_by_load() o join private.observer_installations i on i.organization=o.organization),
    'public',public.observer_public_targets_status())
$$;

do $$
declare f record;
begin
  for f in select p.oid::regprocedure as signature from pg_proc p join pg_namespace n on n.oid=p.pronamespace
    where (n.nspname='public' and p.proname in ('observer_organizations_by_load','observer_target_health_reconcile',
      'observer_set_private_target','observer_set_target_health','observer_set_github_minutes','observer_targets_status'))
      or (n.nspname='private' and p.proname in ('observer_health_mode','observer_platform_failure','observer_public_note',
        'observer_private_strikes','observer_private_note','observer_effective_minutes','observer_healthy_organization',
        'observer_health_move','observer_public_candidate'))
  loop
    execute format('revoke all on function %s from public,anon,authenticated',f.signature);
    if f.signature::text like 'public.%' then
      execute format('grant execute on function %s to service_role',f.signature);
    end if;
  end loop;
end $$;

-- Every minute, independent of the dispatcher Edge function (a no-op while mode is 'off').
do $$
begin
  if exists(select 1 from pg_extension where extname='pg_cron') then
    perform cron.unschedule(jobid) from cron.job where jobname='observer-target-health';
    perform cron.schedule('observer-target-health','* * * * *','select public.observer_target_health_reconcile()');
  end if;
end $$;

notify pgrst,'reload schema';

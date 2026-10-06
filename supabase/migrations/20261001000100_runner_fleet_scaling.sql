-- The runner fleet is no longer capped at twelve organizations: installation
-- rows accept runner-1..99, so new organizations (13-36 are being provisioned)
-- become usable the moment their installation row is enabled.
alter table private.observer_installations drop constraint observer_installations_organization_check;
alter table private.observer_installations add constraint observer_installations_organization_check
  check (organization ~ '^AGENTIC-OBSERVER26-runner-([1-9]|[1-9][0-9])$');
alter table private.observer_materializations drop constraint observer_materializations_archive_ref_check;
alter table private.observer_materializations add constraint observer_materializations_archive_ref_check
  check (archive_ref ~ '^github:AGENTIC-OBSERVER26-runner-([1-9]|[1-9][0-9])/participant-[0-9a-f]{32}@[0-9a-f]{40}$');

-- GitHub-hosted minutes per organization are capped below the free monthly
-- allowance; the limit is configurable per installation row.
alter table private.observer_installations add column monthly_minute_limit integer not null default 1800
  check (monthly_minute_limit between 0 and 100000);

-- Dispatch failures are remembered per organization, independently of the job
-- that hit them, so a moved job cannot erase an organization's failure signal.
create table private.observer_dispatch_failures (
  id bigint generated always as identity primary key,
  organization text not null,
  error text not null,
  failed_at timestamptz not null default now()
);
revoke all on private.observer_dispatch_failures from public,anon,authenticated;

create or replace function public.observer_dispatch_error(p_job uuid,p_error text)
returns void language plpgsql security definer set search_path = public,pg_temp as $$
begin
  insert into private.observer_dispatch_failures(organization,error)
    select organization,left(coalesce(p_error,'dispatch_failed'),1000) from private.observer_jobs where id=p_job;
  delete from private.observer_dispatch_failures where failed_at<now()-interval '7 days';
  -- An ambiguous GitHub response is not proof that no workflow started. Keep the
  -- job claimable until its dispatch lease expires; the reconciler decides expiry.
  update private.observer_jobs set error=left(coalesce(p_error,'dispatch_failed'),1000)
    where id=p_job and status in ('queued','dispatched');
end $$;

-- A successful dispatch clears the job's earlier dispatch error.
create or replace function public.observer_mark_dispatched(p_job uuid)
returns void language plpgsql security definer set search_path = public,pg_temp as $$
begin
  update private.observer_jobs set status='dispatched',error=''
    where id=p_job and status in ('queued','dispatched');
end $$;

-- Enabled organizations ordered by current desirability: organizations over
-- their monthly minute cap last (they are skipped unless nothing else is
-- left), organizations with a dispatch failure in the last thirty minutes
-- demoted, then fewest active jobs, least usage in the last seven days, fewest
-- recorded placements and finally the organization number.
create function public.observer_organizations_by_load()
returns table(organization text, approved_sha text, active_jobs bigint, week_seconds double precision,
  month_minutes double precision, monthly_minute_limit integer, dispatch_failures bigint, placements bigint,
  over_limit boolean)
language sql stable security definer set search_path=public,pg_temp as $$
  select x.organization, x.approved_sha, x.active_jobs, x.week_seconds, x.month_minutes,
    x.monthly_minute_limit, x.dispatch_failures, x.placements,
    x.month_minutes>=x.monthly_minute_limit as over_limit
  from (
    select i.organization, i.approved_sha,
      (select count(*) from private.observer_jobs j where j.organization=i.organization
        and j.status in ('queued','dispatched','claimed') and j.expires_at>now()) as active_jobs,
      (select coalesce(sum(extract(epoch from j.finished_at-j.claimed_at)),0)::double precision
        from private.observer_jobs j where j.organization=i.organization and j.claimed_at is not null
          and j.finished_at is not null and j.finished_at>now()-interval '7 days') as week_seconds,
      (select coalesce(sum(extract(epoch from j.finished_at-j.claimed_at))/60,0)::double precision
        from private.observer_jobs j where j.organization=i.organization and j.claimed_at is not null
          and j.finished_at is not null and j.finished_at>=date_trunc('month',now())) as month_minutes,
      i.monthly_minute_limit,
      (select count(*) from private.observer_dispatch_failures f where f.organization=i.organization
        and f.failed_at>now()-interval '30 minutes') as dispatch_failures,
      (select count(*) from private.observer_placements p where p.organization=i.organization) as placements
    from private.observer_installations i where i.enabled) x
  order by x.month_minutes>=x.monthly_minute_limit, x.dispatch_failures>0, x.active_jobs, x.week_seconds,
    case when x.month_minutes>=x.monthly_minute_limit then x.month_minutes end,
    x.placements, substring(x.organization from '[0-9]+$')::integer
$$;

-- New participants are placed by live load instead of raw headcount.
create or replace function public.observer_placement(p_user uuid)
returns text language plpgsql security definer set search_path=public,pg_temp as $$
declare v text;
begin
  if p_user is null then raise exception 'invalid_participant'; end if;
  select organization into v from private.observer_placements where user_id=p_user;
  if found then return v; end if;
  -- One placement decision at a time keeps the least-loaded choice consistent.
  perform pg_advisory_xact_lock(hashtext('observer-placement'));
  select organization into v from private.observer_placements where user_id=p_user;
  if found then return v; end if;
  -- A participant with jobs from before this migration keeps that organization.
  v:=private.observer_job_organization(p_user);
  if v is null then
    select o.organization into v from public.observer_organizations_by_load() o limit 1;
  end if;
  if v is null then raise exception 'runner_not_configured'; end if;
  insert into private.observer_placements(user_id,organization) values(p_user,v);
  return v;
end $$;

-- Move a still-pending job to another enabled organization after an
-- organization-level dispatch failure (quota, suspension, missing control
-- repository). The owner's recorded placement follows the job so claim-time
-- repository credentials and future jobs land in the same organization;
-- archive references to the old organization stay readable because the same
-- GitHub App is installed everywhere. In-flight (claimed) jobs never move.
create function public.observer_failover_job(p_job uuid,p_organization text)
returns void language plpgsql security definer set search_path=public,pg_temp as $$
declare v private.observer_jobs; v_target private.observer_installations; v_owner uuid;
begin
  select * into v_target from private.observer_installations where organization=p_organization and enabled;
  if not found then raise exception 'runner_not_configured'; end if;
  select * into v from private.observer_jobs where id=p_job for update;
  if not found or v.status not in ('queued','dispatched') or v.expires_at<=now() then
    raise exception 'job_unavailable'; end if;
  if v.organization=p_organization then raise exception 'job_conflict'; end if;
  update private.observer_jobs set organization=p_organization,repository_id=v_target.repository_id,
    organization_id=v_target.organization_id,workflow_sha=v_target.approved_sha,error='',dispatch_count=0
    where id=p_job;
  select coalesce(b.user_id,pr.owner_id) into v_owner
    from private.observer_jobs j
    left join public.observer_runs r on r.id=j.run_id
    left join public.observer_batches b on b.id=r.batch_id
    left join public.observer_revisions v2 on v2.id=j.revision_id
    left join public.observer_projects pr on pr.id=v2.project_id
  where j.id=p_job;
  if v_owner is not null then
    update private.observer_placements set organization=p_organization
      where user_id=v_owner and organization=v.organization;
  end if;
end $$;

do $$
declare f record;
begin
  for f in select p.oid::regprocedure as signature from pg_proc p join pg_namespace n on n.oid=p.pronamespace
    where n.nspname='public' and p.proname in ('observer_dispatch_error','observer_mark_dispatched',
      'observer_organizations_by_load','observer_placement','observer_failover_job')
  loop
    execute format('revoke all on function %s from public,anon,authenticated',f.signature);
    execute format('grant execute on function %s to service_role',f.signature);
  end loop;
end $$;

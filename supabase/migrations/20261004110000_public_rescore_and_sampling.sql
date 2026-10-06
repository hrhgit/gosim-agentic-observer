-- Rescoring without private Actions minutes (owner decision 2026-10-04).
--
-- 1. Score jobs (the independent rescore) may run in the 13 public repositories
--    observer-public (no Actions minutes) with the sealed transfers of the public
--    pool: the scenario and the run's result are sealed to the job's in-memory key
--    and staged as ciphertext (sealed/<job>/{scenario,trace}.zip, deleted after the
--    job), the claim payload is sealed, the run log is one status line, and the
--    recomputed score goes back to the job API only. Placement: the least busy
--    healthy public repository with a free slot (observer_public_free_target);
--    when none is healthy the job runs in a private organization as before, and a
--    public dispatch that fails or does not start sends the job home for good.
--      select public.observer_set_public_pool(p_drill_users=>array['<test user>']::uuid[]);  -- stage: test teams only
--      select public.observer_set_public_score_jobs(true);    -- on
--      select public.observer_set_public_score_jobs(false);   -- rollback
-- 2. Safety net: rescore sampling. While active, only each team's best run per phase
--    and card plus a stable 20% of the other runs are rescored; the rest keep
--    score_check='pending' (their reported score stands, as before a rescore) and
--    are rescored when sampling ends. Mode 'auto' turns it on while the private
--    organizations' remaining GitHub minutes (included 2000 each, minus the billed /
--    estimated minutes of observer_organizations_by_load) fall below 3000, checked
--    every minute; every change is audited (observer.rescore_sampling).
--      select public.observer_set_rescore_sampling(p_mode=>'auto');   -- default
--      select public.observer_set_rescore_sampling(p_mode=>'off');    -- never sample
--      select public.observer_set_rescore_sampling(p_mode=>'on');     -- always sample

alter table private.observer_public_pool add column if not exists score_jobs boolean not null default false;

create table if not exists private.observer_rescore_policy(
  id boolean primary key default true check (id),
  mode text not null default 'auto' check (mode in ('off','auto','on')),
  threshold_minutes integer not null default 3000 check (threshold_minutes between 0 and 1000000),
  included_minutes integer not null default 2000 check (included_minutes between 0 and 1000000),
  sample_percent integer not null default 20 check (sample_percent between 0 and 100),
  active boolean not null default false,
  remaining_minutes double precision,
  checked_at timestamptz,
  updated_at timestamptz not null default now()
);
insert into private.observer_rescore_policy(id) values(true) on conflict do nothing;
revoke all on private.observer_rescore_policy from public,anon,authenticated;

-- Remaining included Actions minutes of the enabled private organizations this month.
create or replace function private.observer_private_minutes_left()
returns double precision language sql stable security definer set search_path=public,pg_temp as $$
  select coalesce(sum(greatest(p.included_minutes-o.month_minutes,0)),0)
  from public.observer_organizations_by_load() o cross join private.observer_rescore_policy p where p.id
$$;

create or replace function public.observer_rescore_policy_reconcile()
returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$
declare p private.observer_rescore_policy; v_left double precision; v_active boolean;
begin
  select * into p from private.observer_rescore_policy where id for update;
  if not found then return null; end if;
  v_left:=private.observer_private_minutes_left();
  v_active:=case p.mode when 'on' then true when 'off' then false else v_left<p.threshold_minutes end;
  update private.observer_rescore_policy set active=v_active,remaining_minutes=v_left,checked_at=now() where id;
  if v_active is distinct from p.active then
    perform private.audit('observer.rescore_sampling',jsonb_build_object('active',v_active,'mode',p.mode,
      'remaining_minutes',round(v_left::numeric),'threshold_minutes',p.threshold_minutes,'sample_percent',p.sample_percent));
  end if;
  return jsonb_build_object('mode',p.mode,'active',v_active,'remaining_minutes',round(v_left::numeric));
end $$;

-- Is this scored run rescored now? Always while sampling is inactive.
create or replace function private.observer_rescore_selected(p_run uuid)
returns boolean language sql stable security definer set search_path=public,pg_temp as $$
  select not coalesce((select active from private.observer_rescore_policy where id),false)
    or (abs(hashtextextended(p_run::text,26)) % 100) < (select sample_percent from private.observer_rescore_policy where id)
    or exists(select 1 from public.observer_runs r join public.observer_batches b on b.id=r.batch_id
      where r.id=p_run and not exists(select 1 from public.observer_runs r2 join public.observer_batches b2 on b2.id=r2.batch_id
        where b2.team_id=b.team_id and b2.phase_id=b.phase_id and r2.scenario_id=r.scenario_id and r2.id<>r.id
          and r2.status='scored' and r2.score>coalesce(r.score,'-infinity'::double precision)))
$$;

create or replace function public.observer_set_public_score_jobs(p_enabled boolean)
returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$
begin
  if not exists(select 1 from private.observer_public_pool where id) then raise exception 'public_pool_not_configured'; end if;
  update private.observer_public_pool set score_jobs=coalesce(p_enabled,score_jobs),updated_at=now() where id;
  perform private.audit('observer.public_score_jobs',jsonb_build_object('enabled',p_enabled));
  return jsonb_build_object('score_jobs',(select score_jobs from private.observer_public_pool where id));
end $$;

create or replace function public.observer_set_rescore_sampling(p_mode text default null,p_threshold_minutes integer default null,
  p_sample_percent integer default null,p_included_minutes integer default null)
returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$
begin
  update private.observer_rescore_policy set mode=coalesce(p_mode,mode),threshold_minutes=coalesce(p_threshold_minutes,threshold_minutes),
    sample_percent=coalesce(p_sample_percent,sample_percent),included_minutes=coalesce(p_included_minutes,included_minutes),
    updated_at=now() where id;
  perform private.audit('observer.rescore_policy',(select to_jsonb(p)-'id' from private.observer_rescore_policy p where p.id));
  return public.observer_rescore_policy_reconcile();
end $$;

CREATE OR REPLACE FUNCTION private.observer_public_candidate(p_job uuid, p_require_trigger boolean DEFAULT true)
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
  -- Rescore jobs (20261004110000): any healthy public repository with a free slot,
  -- when the organizer switched score_jobs on; a sealed phase needs the verified
  -- sealed transfer. No load trigger: public repositories cost no minutes.
  if found and v.kind='score' then
    if v.runner<>'github-hosted' or v.status<>'queued' or v.run_id is null
      or v.expires_at<=now() or v.public_declined_at is not null then return false; end if;
    select c.sealed,b.user_id into v_sealed,v_user from public.observer_runs r join public.observer_batches b on b.id=r.batch_id
      join public.observer_phase_settings c on c.phase_id=b.phase_id where r.id=v.run_id;
    -- Staged rollout: drill users' rescores first, then everyone's (score_jobs).
    if not p.score_jobs and not coalesce(v_user=any(p.drill_users),false) then return false; end if;
    if coalesce(v_sealed,true) and not p.sealed_transfer_verified then return false; end if;
    if (select count(*) from private.observer_jobs j where j.runner='public-hosted'
        and j.status in ('queued','dispatched','claimed') and j.expires_at>now())>=p.max_active
      or private.observer_public_month_minutes()>=p.monthly_minute_cap then return false; end if;
    return (private.observer_public_free_target()).organization is not null;
  end if;
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

CREATE OR REPLACE FUNCTION public.observer_pending_jobs(p_limit integer DEFAULT 10)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public', 'pg_temp'
AS $function$
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
    'public',case when r.runner='github-hosted' and r.kind in ('engine','score') and r.status='queued'
      then private.observer_public_candidate(r.id) end)),'[]') into v_result from reserved r;
  return v_result;
end $function$;

CREATE OR REPLACE FUNCTION public.observer_pending_score_runs(p_limit integer DEFAULT 5)
 RETURNS jsonb
 LANGUAGE sql
 STABLE SECURITY DEFINER
 SET search_path TO 'public', 'pg_temp'
AS $function$
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
      and private.observer_rescore_selected(r.id)
    order by r.finished_at,r.id limit greatest(1,least(coalesce(p_limit,5),20))) x
$function$;

do $$
declare f record;
begin
  for f in select p.oid::regprocedure as signature from pg_proc p join pg_namespace n on n.oid=p.pronamespace
    where (n.nspname='public' and p.proname in ('observer_rescore_policy_reconcile','observer_set_public_score_jobs',
      'observer_set_rescore_sampling','observer_pending_jobs','observer_pending_score_runs'))
      or (n.nspname='private' and p.proname in ('observer_private_minutes_left','observer_rescore_selected','observer_public_candidate'))
  loop
    execute format('revoke all on function %s from public,anon,authenticated',f.signature);
    if f.signature::text like 'public.%' then
      execute format('grant execute on function %s to service_role',f.signature);
    end if;
  end loop;
end $$;

do $$
begin
  if exists(select 1 from pg_extension where extname='pg_cron') then
    perform cron.unschedule(jobid) from cron.job where jobname='observer-rescore-policy';
    perform cron.schedule('observer-rescore-policy','* * * * *','select public.observer_rescore_policy_reconcile()');
  end if;
end $$;

notify pgrst,'reload schema';

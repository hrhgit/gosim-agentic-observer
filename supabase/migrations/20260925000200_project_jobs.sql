-- Dedicated trusted control repositories dispatch preparation, execution and
-- simulation on separate GitHub-hosted machines. Participant repositories never
-- run Actions. There are no installation credentials in public tables.
create table private.observer_installations (
  organization text primary key check (organization ~ '^AGENTIC-OBSERVER26-runner-[1-6]$'),
  organization_id text not null unique check (organization_id ~ '^[0-9]+$'),
  installation_id bigint not null unique check (installation_id > 0),
  repository_id text not null unique check (repository_id ~ '^[0-9]+$'),
  approved_sha text not null check (approved_sha ~ '^[0-9a-f]{40}$'),
  enabled boolean not null default false
);

create table private.observer_jobs (
  id uuid primary key default gen_random_uuid(),
  kind text not null check (kind in ('prepare','execute','engine')),
  run_id uuid references public.observer_runs(id),
  revision_id uuid references public.observer_revisions(id),
  organization text not null references private.observer_installations(organization),
  repository_id text not null,
  organization_id text not null,
  workflow_sha text not null,
  nonce_hash bytea not null,
  encrypted_nonce text not null,
  encrypted_input text not null,
  status text not null default 'queued' check (status in ('queued','dispatched','claimed','succeeded','failed')),
  github_run_id text,
  github_run_attempt text,
  dispatch_count integer not null default 0,
  last_dispatch_at timestamptz,
  result jsonb,
  error text not null default '',
  created_at timestamptz not null default now(),
  expires_at timestamptz not null default now()+interval '30 minutes',
  claimed_at timestamptz,
  finished_at timestamptz,
  check ((kind='prepare') = (revision_id is not null)),
  check ((kind<>'prepare') = (run_id is not null)),
  unique(run_id,kind),
  unique(revision_id,kind)
);
create index observer_jobs_dispatch on private.observer_jobs(created_at) where status in ('queued','dispatched');

revoke all on private.observer_installations,private.observer_jobs from public,anon,authenticated;

create function public.observer_enqueue_job(p_id uuid,p_kind text,p_run uuid,p_revision uuid,
  p_organization text,p_nonce text,p_encrypted_input text,p_encrypted_nonce text)
returns uuid language plpgsql security definer set search_path = public,pg_temp as $$
declare v_install private.observer_installations; v_existing private.observer_jobs;
begin
  select * into v_install from private.observer_installations where organization=p_organization and enabled;
  if not found then raise exception 'runner_not_configured'; end if;
  if p_id is null or p_nonce is null or length(p_nonce) not between 40 and 100 or
    p_encrypted_input is null or length(p_encrypted_input) not between 1 and 1048576 or
    p_encrypted_nonce is null or length(p_encrypted_nonce) not between 1 and 2048 then raise exception 'invalid_job'; end if;
  select * into v_existing from private.observer_jobs where id=p_id;
  if found then
    if v_existing.kind is distinct from p_kind or v_existing.run_id is distinct from p_run or
      v_existing.revision_id is distinct from p_revision or v_existing.organization is distinct from p_organization or
      v_existing.nonce_hash is distinct from sha256(convert_to(p_nonce,'UTF8')) or
      v_existing.encrypted_input is distinct from p_encrypted_input or
      v_existing.encrypted_nonce is distinct from p_encrypted_nonce then raise exception 'job_conflict'; end if;
    return p_id;
  end if;
  insert into private.observer_jobs(id,kind,run_id,revision_id,organization,repository_id,organization_id,
    workflow_sha,nonce_hash,encrypted_input,encrypted_nonce)
  values(p_id,p_kind,p_run,p_revision,p_organization,v_install.repository_id,v_install.organization_id,
    v_install.approved_sha,sha256(convert_to(p_nonce,'UTF8')),p_encrypted_input,p_encrypted_nonce);
  return p_id;
end $$;

-- Read only identity metadata before verifying OIDC. Never return credentials at
-- this stage; the caller must prove both workflow identity and dispatch nonce.
create function public.observer_job_identity(p_job uuid)
returns jsonb language sql stable security definer set search_path = public,pg_temp as $$
  select jsonb_build_object('repositoryId',j.repository_id,'organizationId',j.organization_id,
    'organization',j.organization,'workflow','observer-'||j.kind||'.yml','approvedSha',j.workflow_sha,
    'runId',j.github_run_id,'runAttempt',j.github_run_attempt)
  from private.observer_jobs j join private.observer_installations i on i.organization=j.organization
  where j.id=p_job and i.enabled and j.expires_at>now()
$$;

create function public.observer_mark_dispatched(p_job uuid)
returns void language plpgsql security definer set search_path = public,pg_temp as $$
begin
  update private.observer_jobs set status='dispatched'
    where id=p_job and status in ('queued','dispatched');
end $$;

create function public.observer_claim_job(p_job uuid,p_nonce text,p_github_run text,p_attempt text,
  p_repository text,p_owner text,p_sha text)
returns text language plpgsql security definer set search_path = public,pg_temp as $$
declare v private.observer_jobs;
begin
  select j.* into v from private.observer_jobs j join private.observer_installations i on i.organization=j.organization
    where j.id=p_job and i.enabled for update of j;
  if not found or v.expires_at<=clock_timestamp() or v.status not in ('queued','dispatched','claimed') then
    raise exception 'job_unavailable'; end if;
  if p_nonce is null or v.nonce_hash is distinct from sha256(convert_to(p_nonce,'UTF8'))
    or p_repository is distinct from v.repository_id or p_owner is distinct from v.organization_id
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

create function public.observer_finish_job(p_job uuid,p_github_run text,p_attempt text,p_result jsonb,p_error text default '')
returns void language plpgsql security definer set search_path = public,pg_temp as $$
declare v private.observer_jobs; v_batch uuid;
begin
  select * into v from private.observer_jobs where id=p_job for update;
  if not found or v.github_run_id is distinct from p_github_run or v.github_run_attempt is distinct from p_attempt
    or v.expires_at<=clock_timestamp() then raise exception 'job_identity_mismatch'; end if;
  if p_result is null or jsonb_typeof(p_result)<>'object' or octet_length(p_result::text)>1048576 then
    raise exception 'invalid_job_result'; end if;
  if v.status in ('succeeded','failed') then
    if v.result is distinct from p_result or v.error is distinct from left(coalesce(p_error,''),1000) then
      raise exception 'job_result_conflict'; end if;
    return;
  end if;
  if v.status<>'claimed' then raise exception 'job_not_claimed'; end if;
  update private.observer_jobs set status=case when coalesce(p_error,'')='' then 'succeeded' else 'failed' end,
    result=p_result,error=left(coalesce(p_error,''),1000),finished_at=now() where id=p_job;
  if coalesce(p_error,'')<>'' then
    -- A failed executor must release its run immediately; otherwise its paired
    -- engine waits for the full clock and may consume hours of shared capacity.
    -- Job failure may stop its own run but can never create/replace a score.
    if v.revision_id is not null then
      update public.observer_revisions set status='failed',error='Project preparation failed. Please retry.'
        where id=v.revision_id and status in ('queued','preparing');
    else
      update public.observer_runs set status='failed',error=v.kind||'_job_failed',finished_at=now()
        where id=v.run_id and status in ('queued','starting','ready','running') returning batch_id into v_batch;
      if v_batch is not null then perform private.observer_finalize_batch(v_batch); end if;
    end if;
  end if;
end $$;

create function public.observer_pending_jobs(p_limit integer default 10)
returns jsonb language plpgsql security definer set search_path = public,pg_temp as $$
declare v_result jsonb;
begin
  -- Reserve a dispatch attempt before calling GitHub. An ambiguous network result
  -- may be retried after two minutes; only one resulting machine can claim the job.
  with picked as (
    select j.id from private.observer_jobs j join private.observer_installations i on i.organization=j.organization
    where j.status in ('queued','dispatched') and i.enabled and j.expires_at>now() and j.dispatch_count<3
      and (j.last_dispatch_at is null or j.last_dispatch_at<now()-interval '2 minutes')
    order by j.created_at for update of j skip locked limit greatest(1,least(coalesce(p_limit,10),20))
  ), reserved as (
    update private.observer_jobs j set dispatch_count=dispatch_count+1,last_dispatch_at=now()
    from picked p where j.id=p.id returning j.id,j.kind,j.organization,j.workflow_sha,j.encrypted_nonce
  )
  select coalesce(jsonb_agg(to_jsonb(reserved)),'[]') into v_result from reserved;
  return v_result;
end $$;

create function public.observer_runner_configuration()
returns jsonb language sql stable security definer set search_path = public,pg_temp as $$
  select coalesce(jsonb_agg(to_jsonb(i)),'[]') from private.observer_installations i where i.enabled
$$;

create function public.observer_dispatch_error(p_job uuid,p_error text)
returns void language plpgsql security definer set search_path = public,pg_temp as $$
begin
  -- An ambiguous GitHub response is not proof that no workflow started. Keep the
  -- job claimable until its dispatch lease expires; the reconciler decides expiry.
  update private.observer_jobs set error=left(coalesce(p_error,'dispatch_failed'),1000)
    where id=p_job and status in ('queued','dispatched');
end $$;

create function public.observer_reconcile_jobs()
returns integer language plpgsql security definer set search_path = public,pg_temp as $$
declare v private.observer_jobs; v_batch uuid; n integer:=0;
begin
  for v in select * from private.observer_jobs
    where status in ('queued','dispatched','claimed') and expires_at<=now()
    order by expires_at for update skip locked limit 100
  loop
    update private.observer_jobs set status='failed',error='job_expired',finished_at=now() where id=v.id;
    if v.revision_id is not null then
      update public.observer_revisions set status='failed',error='Project preparation did not finish. Please retry.'
        where id=v.revision_id and status in ('queued','preparing');
    else
      update public.observer_runs set status='failed',error='evaluation_job_expired',finished_at=now()
        where id=v.run_id and status in ('queued','starting','ready','running') returning batch_id into v_batch;
      if v_batch is not null then perform private.observer_finalize_batch(v_batch); end if;
    end if;
    n:=n+1;
  end loop;
  return n;
end $$;

do $$
declare f record;
begin
  for f in select p.oid::regprocedure as signature from pg_proc p join pg_namespace n on n.oid=p.pronamespace
    where n.nspname='public' and p.proname in ('observer_enqueue_job','observer_job_identity','observer_mark_dispatched',
      'observer_claim_job','observer_finish_job','observer_pending_jobs','observer_runner_configuration',
      'observer_dispatch_error','observer_reconcile_jobs')
  loop
    execute format('revoke all on function %s from public,anon,authenticated',f.signature);
    execute format('grant execute on function %s to service_role',f.signature);
  end loop;
end $$;

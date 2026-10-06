-- Evaluation hardening, two organizer switches (both on by default):
--
-- restricted_egress  Colocated engine jobs start the participant container on a
--                    per-run internal Docker network whose only exit is a
--                    forwarder to the observer-model proxy (project_platform/
--                    egress.py). The scheduler copies the switch into each new
--                    engine job payload; running jobs keep what they started with.
-- rescore            A finished formal project run is scored from the engine's
--                    summary as before, then an independent 'score' job (a fresh
--                    runner, no participant code) recomputes the score from the
--                    committed trace: decisions.csv bound by the digest given at
--                    finish, v4 replayed from its recorded actions. A different
--                    total replaces the reported one ('corrected', evidence kept);
--                    a trace that is not the committed one voids the run
--                    ('rejected').
--
-- One-click rollback: select public.observer_set_hardening(false,false);
-- (or either switch alone; null leaves a switch unchanged).

create table private.observer_hardening(
  id boolean primary key default true check (id),
  restricted_egress boolean not null default true,
  rescore boolean not null default true,
  updated_at timestamptz not null default now()
);
insert into private.observer_hardening(id) values(true) on conflict do nothing;
revoke all on private.observer_hardening from public,anon,authenticated;

create function public.observer_hardening()
returns jsonb language sql stable security definer set search_path=public,pg_temp as $$
  select coalesce((select jsonb_build_object('restricted_egress',h.restricted_egress,'rescore',h.rescore)
    from private.observer_hardening h where h.id),
    jsonb_build_object('restricted_egress',false,'rescore',false))
$$;

create function public.observer_set_hardening(p_restricted_egress boolean default null,p_rescore boolean default null)
returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$
begin
  insert into private.observer_hardening(id) values(true) on conflict do nothing;
  update private.observer_hardening set restricted_egress=coalesce(p_restricted_egress,restricted_egress),
    rescore=coalesce(p_rescore,rescore),updated_at=now() where id;
  if p_rescore is false then
    -- Runs not yet handed to a score job will not be checked: no stale 'pending'.
    update public.observer_runs r set score_check=null where r.score_check='pending'
      and not exists(select 1 from private.observer_jobs j where j.run_id=r.id and j.kind='score');
  end if;
  perform private.audit('observer.hardening',public.observer_hardening());
  return public.observer_hardening();
end $$;

-- Verification state of a run's score: null (not checked: switch off at finish,
-- local, preview or calibrated run), pending, verified, corrected (recomputed
-- total adopted), rejected (stored trace is not the committed one; run voided)
-- or unverified (the score job could not run; the reported score stands).
alter table public.observer_runs add column score_check text
  check (score_check in ('pending','verified','corrected','rejected','unverified'));

-- Evidence for each check: the engine's own report and the recomputation.
create table private.observer_score_checks(
  run_id uuid primary key references public.observer_runs(id),
  job_id uuid not null,
  outcome text not null check (outcome in ('verified','corrected','rejected','unverified')),
  reported_score double precision,
  reported_summary jsonb,
  recomputed jsonb,
  checked_at timestamptz not null default now()
);
revoke all on private.observer_score_checks from public,anon,authenticated;

alter table private.observer_jobs drop constraint observer_jobs_kind_check;
alter table private.observer_jobs add constraint observer_jobs_kind_check
  check (kind in ('prepare','execute','engine','score'));

-- Unchanged except for score_check: a formal project run finished under the
-- rescore switch waits for its score job. A repeated finish of a corrected run
-- is compared with the summary the engine originally reported.
create or replace function public.observer_finish_run(p_run uuid,p_token text,p_summary jsonb,p_decisions_digest text,p_result_path text)
returns void language plpgsql security definer set search_path = public, pg_temp as $$
declare v private.observer_sessions; v_run public.observer_runs; v_batch public.observer_batches; v_score double precision;
begin
  select * into v from private.observer_sessions where run_id=p_run for update;
  if not found or p_token is null or v.engine_hash is distinct from sha256(convert_to(p_token,'UTF8'))
    or v.expires_at<=clock_timestamp() then raise exception 'invalid_or_expired_capability'; end if;
  select * into v_run from public.observer_runs where id=p_run for update;
  if p_summary is null or jsonb_typeof(p_summary)<>'object' or octet_length(p_summary::text)>65536
    or p_decisions_digest is null or p_decisions_digest !~ '^[0-9a-f]{64}$'
    or p_result_path is null or length(p_result_path) not between 1 and 1024 then raise exception 'invalid_result'; end if;
  if v_run.status in ('scored','awaiting_csv') then
    if coalesce((select c.reported_summary from private.observer_score_checks c where c.run_id=p_run),
        v_run.score_summary) is distinct from p_summary or v_run.decisions_digest is distinct from p_decisions_digest
      or v_run.result_path is distinct from p_result_path then raise exception 'result_conflict'; end if;
    return;
  end if;
  if v_run.status<>'running' then raise exception 'run_not_running'; end if;
  v_score:=(p_summary->'score'->>'total')::double precision;
  if v_score is null or v_score in ('NaN'::double precision,'Infinity'::double precision,'-Infinity'::double precision) then
    raise exception 'invalid_score'; end if;
  select * into v_batch from public.observer_batches where id=v_run.batch_id;
  update public.observer_runs set status=case when v_batch.mode='local' then 'awaiting_csv' else 'scored' end,
    score=v_score,score_summary=p_summary,decisions_digest=p_decisions_digest,result_path=p_result_path,finished_at=now(),
    score_check=case when v_batch.mode='project' and v_batch.purpose='formal' and p_result_path like 'github:%'
      and not exists(select 1 from private.observer_scenario_instances i where i.run_id=p_run)
      and coalesce((select h.rescore from private.observer_hardening h where h.id),false) then 'pending' end
    where id=p_run;
  perform private.observer_finalize_batch(v_run.batch_id);
end $$;

-- Runs waiting for their score job, with what the job needs. The score job
-- goes to the organization that ran the engine, or the least loaded one if that
-- organization is no longer enabled (dispatch failover still applies).
create function public.observer_pending_score_runs(p_limit integer default 5)
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
    left join private.observer_installations i on i.organization=e.organization and i.enabled
    where r.status='scored' and r.score_check='pending'
      and coalesce((select h.rescore from private.observer_hardening h where h.id),false)
      and not exists(select 1 from private.observer_jobs j where j.run_id=r.id and j.kind='score')
    order by r.finished_at,r.id limit greatest(1,least(coalesce(p_limit,5),20))) x
$$;

-- Settle a run's score check. Called with the score job's receipt (outcome
-- verified/corrected decided here) or with a failure outcome.
create function private.observer_settle_score_check(p_job uuid,p_outcome text,p_recomputed jsonb)
returns void language plpgsql security definer set search_path=public,pg_temp as $$
declare j private.observer_jobs; r public.observer_runs; b public.observer_batches; v_total double precision;
  v_outcome text:=p_outcome;
begin
  select * into j from private.observer_jobs where id=p_job and kind='score';
  if not found then raise exception 'job_unavailable'; end if;
  select * into r from public.observer_runs where id=j.run_id for update;
  if r.status<>'scored' or r.score_check is distinct from 'pending' then return; end if;
  if v_outcome='scored' then
    if jsonb_typeof(p_recomputed) is distinct from 'object' or p_recomputed->>'run_id' is distinct from r.id::text
      or jsonb_typeof(p_recomputed->'score') is distinct from 'object' or octet_length(p_recomputed::text)>65536 then
      raise exception 'invalid_job_result'; end if;
    -- The job proved its trace against this digest; re-check the binding so a
    -- receipt can never pair a score with a different trace.
    if p_recomputed->>'decisions_digest' is distinct from r.decisions_digest then
      raise exception 'invalid_job_result'; end if;
    v_total:=(p_recomputed->'score'->>'total')::double precision;
    if v_total is null or v_total in ('NaN'::double precision,'Infinity'::double precision,'-Infinity'::double precision) then
      raise exception 'invalid_job_result'; end if;
    v_outcome:=case when abs(v_total-r.score)<=1e-6*greatest(1,abs(v_total)) then 'verified' else 'corrected' end;
  end if;
  if v_outcome not in ('verified','corrected','rejected','unverified') then raise exception 'invalid_job_result'; end if;
  -- Switched off while the job ran: record the evidence, change nothing.
  if v_outcome in ('corrected','rejected') and not coalesce((select h.rescore from private.observer_hardening h where h.id),false) then
    insert into private.observer_score_checks(run_id,job_id,outcome,reported_score,recomputed)
      values(r.id,j.id,'unverified',r.score,p_recomputed) on conflict(run_id) do nothing;
    update public.observer_runs set score_check='unverified' where id=r.id;
    return;
  end if;
  insert into private.observer_score_checks(run_id,job_id,outcome,reported_score,reported_summary,recomputed)
    values(r.id,j.id,v_outcome,r.score,case when v_outcome in ('corrected','rejected') then r.score_summary end,p_recomputed)
    on conflict(run_id) do nothing;
  if v_outcome in ('verified','unverified') then
    update public.observer_runs set score_check=v_outcome where id=r.id;
    return;
  end if;
  select * into b from public.observer_batches where id=r.batch_id for update;
  if v_outcome='corrected' then
    update public.observer_runs set score=v_total,score_check='corrected',
      score_summary=jsonb_set(r.score_summary,'{score}',p_recomputed->'score',true)
        ||jsonb_build_object('rescore',jsonb_build_object('reported_total',r.score,'recomputed_total',v_total))
      where id=r.id;
    if b.status='scored' then
      update public.observer_batches set score=(select avg(score) from public.observer_runs where batch_id=b.id)
        where id=b.id;
    end if;
    perform private.audit('observer.score_corrected',jsonb_build_object('run_id',r.id,'job_id',j.id,
      'reported',r.score,'recomputed',v_total));
  else
    update public.observer_runs set status='failed',error='score_verification_failed',score=null,
      score_check='rejected' where id=r.id;
    -- A voided trace is the team's run, never a platform failure: no refund and
    -- no automatic retry (private.observer_participant_failure below).
    if b.status='scored' then
      update public.observer_batches set status='failed',score=null,quota_refunded=false where id=b.id;
    else
      perform private.observer_finalize_batch(b.id);
    end if;
    perform private.audit('observer.score_rejected',jsonb_build_object('run_id',r.id,'job_id',j.id,
      'reported',r.score));
  end if;
end $$;

-- Unchanged except for the score kind: a receipt settles the run's check; a
-- failure never touches the run's status except for tamper evidence.
create or replace function public.observer_finish_job(p_job uuid, p_github_run text, p_attempt text, p_result jsonb, p_error text DEFAULT ''::text)
returns void language plpgsql security definer set search_path = public, pg_temp as $$
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
  if v.kind='score' then
    begin
      perform private.observer_settle_score_check(p_job,
        case when coalesce(p_error,'')='' then 'scored'
          when p_result->'diagnostics'->>'code'='score_trace_mismatch' then 'rejected' else 'unverified' end,
        case when coalesce(p_error,'')='' then p_result end);
    exception when raise_exception then
      -- A receipt that does not fit its run (e.g. another digest) changes no
      -- score; the run keeps its reported one.
      perform private.observer_settle_score_check(p_job,'unverified',null);
    end;
    return;
  end if;
  if coalesce(p_error,'')<>'' then
    -- A failed executor must release its run immediately; otherwise its paired
    -- engine waits for the full clock and may consume hours of shared capacity.
    -- Job failure may stop its own run but can never create/replace a score.
    if v.revision_id is not null then
      update public.observer_revisions set status='failed',error=case
          when p_result->'diagnostics'->>'code'='project_error' and coalesce(p_result->'diagnostics'->>'log','')<>''
          then 'Project preparation failed: '||left(p_result->'diagnostics'->>'log',400)
          else 'Project preparation failed. Please retry.' end
        where id=v.revision_id and status in ('queued','preparing');
    else
      update public.observer_runs set status='failed',error=v.kind||'_job_failed',finished_at=now()
        where id=v.run_id and status in ('queued','starting','ready','running') returning batch_id into v_batch;
      if v_batch is not null then perform private.observer_finalize_batch(v_batch); end if;
    end if;
  end if;
end $$;

-- Unchanged except that an expired score job leaves its run 'unverified'.
create or replace function public.observer_reconcile_jobs()
returns integer language plpgsql security definer set search_path = public, pg_temp as $$
declare v private.observer_jobs; v_batch uuid; n integer:=0;
begin
  for v in select * from private.observer_jobs
    where status in ('queued','dispatched','claimed') and expires_at<=now()
    order by expires_at for update skip locked limit 100
  loop
    update private.observer_jobs set status='failed',error='job_expired',finished_at=now() where id=v.id;
    if v.kind='score' then
      perform private.observer_settle_score_check(v.id,'unverified',null);
    elsif v.revision_id is not null then
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

-- Unchanged except that a run waiting for its score job also wakes the dispatcher.
create or replace function private.observer_tick()
returns bigint language plpgsql security definer set search_path to 'pg_catalog', 'pg_temp' as $$
declare c private.observer_dispatch_config; capability text; request_id bigint;
begin
  select * into c from private.observer_dispatch_config where id and enabled for update skip locked;
  if not found or c.last_enqueued_at>clock_timestamp()-interval '50 seconds' then return null; end if;
  if not exists(select 1 from public.observer_revisions where status in ('queued','preparing'))
     and not exists(select 1 from public.observer_runs where status in ('queued','starting','ready','running'))
     and not exists(select 1 from private.observer_jobs where status in ('queued','dispatched','claimed'))
     and public.observer_pending_score_runs(1)='[]'::jsonb
     and public.observer_expired_uploads(1)='[]'::jsonb then return null; end if;
  if not exists(select 1 from pg_extension where extname='pg_net')
     or to_regclass('vault.decrypted_secrets') is null then return null; end if;
  execute 'select decrypted_secret from vault.decrypted_secrets where id=$1' into capability using c.secret_id;
  if capability is null or length(capability)<40 then return null; end if;
  select net.http_post(url:=c.endpoint,
    headers:=jsonb_build_object('Content-Type','application/json','Authorization','Bearer '||capability),
    body:='{}'::jsonb,timeout_milliseconds:=120000) into request_id;
  update private.observer_dispatch_config set last_enqueued_at=clock_timestamp(),last_request_id=request_id where id;
  return request_id;
end $$;

-- Unchanged except that a run voided by its score check is the team's failure.
create or replace function private.observer_participant_failure(p_run uuid)
returns boolean language sql stable security definer set search_path to 'public', 'pg_temp' as $$
  select exists(select 1 from public.observer_runs r join public.observer_batches b on b.id=r.batch_id
    where r.id=p_run and r.status='failed' and (r.error='local_runner_stopped'
      or r.error='score_verification_failed'
      or (r.error='evaluation_expired' and b.mode='local')
      or (r.error in ('execute_job_failed','engine_job_failed') and exists(select 1 from private.observer_jobs j
        where j.run_id=r.id and j.status='failed' and j.result->'diagnostics'->>'code'='project_operation_failed'
          and (j.kind='execute' or (j.kind='engine' and j.result->'diagnostics'->>'stage'='execute'))))))
$$;

-- Unchanged except that a score job moving organization never moves its team's
-- placement: it only re-reads a finished run's trace.
create or replace function public.observer_failover_job(p_job uuid, p_organization text)
returns void language plpgsql security definer set search_path to 'public', 'pg_temp' as $$
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
  if v.kind='score' then return; end if;
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

revoke all on function public.observer_hardening(),public.observer_set_hardening(boolean,boolean),
  public.observer_pending_score_runs(integer),private.observer_settle_score_check(uuid,text,jsonb)
  from public,anon,authenticated;
grant execute on function public.observer_hardening(),public.observer_set_hardening(boolean,boolean),
  public.observer_pending_score_runs(integer) to service_role;
notify pgrst,'reload schema';

-- Contestants must never see "failed" for something we did, not them: DB
-- timeouts, runner crashes, GitHub Actions hiccups, lease expiry and other
-- infra errors are retried automatically (with backoff, up to a ~2-hour
-- budget per preparation/run) before anything reaches a terminal state. If
-- the budget runs out the subject is "parked" in its last non-failed status
-- (the contestant keeps seeing "queued"/"running") and an incident row pages
-- the organizers, who can clear it with observer_admin_requeue(). A failure
-- that is genuinely the contestant's own (their build, their protocol
-- violation, their public-test interface) is unchanged: immediate, specific,
-- no retry.
--
-- All of this plugs into existing machinery: observer_pending_preparations /
-- observer_pending_runs already debounce+lease every minute via observer_tick,
-- and the platform/contestant split already exists for quota refund
-- (private.observer_participant_failure) -- this migration reuses that same
-- split to decide retry vs. immediate failure, and only ever widens an
-- existing lease/attempts row; no table is dropped or narrowed.

-- ---------------------------------------------------------------------------
-- Incidents: the organizer-facing alert a parked subject raises instead of
-- ever showing the contestant a failure.
-- ---------------------------------------------------------------------------
create table private.observer_incidents (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  subject_type text not null check (subject_type in ('revision','run')),
  subject_id uuid not null,
  reason text not null,
  detail jsonb not null default '{}'::jsonb,
  resolved_at timestamptz,
  resolved_by uuid references public.profiles(id)
);
create index observer_incidents_subject_idx on private.observer_incidents(subject_type,subject_id);
-- At most one open incident per subject: raising one while another is
-- already open (every retry tick, while parked) is a no-op, not a new row.
create unique index observer_incidents_open_unique on private.observer_incidents(subject_type,subject_id)
  where resolved_at is null;
revoke all on private.observer_incidents from public,anon,authenticated;

create or replace function private.observer_raise_incident(p_subject_type text,p_subject_id uuid,p_reason text,
  p_detail jsonb default '{}'::jsonb)
returns void language sql security definer set search_path=public,pg_temp as $$
  insert into private.observer_incidents(subject_type,subject_id,reason,detail)
    values(p_subject_type,p_subject_id,p_reason,p_detail)
    on conflict (subject_type,subject_id) where resolved_at is null do nothing
$$;
revoke all on function private.observer_raise_incident(text,uuid,text,jsonb) from public,anon,authenticated;

-- ---------------------------------------------------------------------------
-- Retry bookkeeping: a backoff-capped lease duration and a wall-clock budget
-- per lease/attempts row, plus a "parked" marker that stops further retries
-- (but never flips status to 'failed') once the budget is exhausted.
-- ---------------------------------------------------------------------------
alter table private.observer_preparations
  add column if not exists first_leased_at timestamptz not null default now(),
  add column if not exists paused_at timestamptz;
alter table private.observer_run_leases
  add column if not exists first_leased_at timestamptz not null default now(),
  add column if not exists paused_at timestamptz;

-- ---------------------------------------------------------------------------
-- Preparation scheduling lease: give up after a ~2h budget (not after a
-- fixed attempt count), with the lease window backing off 2/4/8/16/20min
-- instead of a flat 2 minutes, and park (not fail) when the budget is spent.
-- ---------------------------------------------------------------------------
create or replace function public.observer_pending_preparations(p_limit integer default 3)
returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$
declare r record;c private.observer_preparation_config;l private.observer_preparations;result jsonb:='[]';v_gameplay text;v_minutes integer;
begin
  select x.* into c from private.observer_preparation_config x
    join public.scenarios s on s.id=x.scenario_id
    join public.observer_phase_settings f on f.phase_id=x.phase_id
    where x.enabled and f.projects_enabled and s.is_active and s.weather_public and s.events_public and s.forecasts_public;
  if not found then return result; end if;
  v_gameplay:=case when exists(select 1 from public.scenarios where id=c.scenario_id and contract='v4-score-v1') then 'v4' else 'v3' end;
  for r in select rev.id,p.owner_id,p.team_id,rev.source_kind,rev.source_location
    from public.observer_revisions rev join public.observer_projects p on p.id=rev.project_id
    join public.profiles u on u.id=p.owner_id and u.team_id=p.team_id and not u.is_banned
    left join private.observer_preparations prep on prep.revision_id=rev.id
    -- A parked row stays out of this scan entirely: no new lease until an
    -- organizer clears it via observer_admin_requeue.
    where rev.status='queued' and (prep.revision_id is null or (prep.expires_at<=now() and prep.paused_at is null))
    order by rev.created_at,rev.id for update of rev skip locked limit greatest(1,least(coalesce(p_limit,3),10))
  loop
    select * into l from private.observer_preparations where revision_id=r.id;
    if found and l.expires_at>now() then continue; end if;
    if found and now()-l.first_leased_at>interval '2 hours' then
      update private.observer_preparations set paused_at=now() where revision_id=r.id;
      perform private.observer_raise_incident('revision',r.id,'preparation_retry_budget_exhausted',
        jsonb_build_object('attempts',l.attempts,'team_id',r.team_id,'owner_id',r.owner_id));
      continue;
    end if;
    v_minutes:=case when found then (least(20,2^greatest(l.attempts,1)))::int else 2 end;
    insert into private.observer_preparations(revision_id,lease,expires_at,phase_id,scenario_id,model)
      values(r.id,gen_random_uuid(),now()+(v_minutes||' minutes')::interval,c.phase_id,c.scenario_id,c.model)
      on conflict(revision_id) do update set lease=excluded.lease,expires_at=excluded.expires_at,
        attempts=private.observer_preparations.attempts+1 returning * into l;
    result:=result || jsonb_build_array(to_jsonb(r)||jsonb_build_object('lease',l.lease,'model_run_id',l.model_run_id,'model',l.model,
      'gameplay',v_gameplay));
  end loop;
  return result;
end $$;

-- Unchanged except that it no longer fails the run after the Nth scheduling
-- attempt: the periodic budget check in observer_pending_runs below owns that
-- decision now, uniformly with every other give-up path here.
create or replace function public.observer_run_schedule_error(p_run uuid,p_lease uuid,p_error text)
returns void language plpgsql security definer set search_path=public,pg_temp as $$
declare r public.observer_runs;l private.observer_run_leases;
begin
  select * into r from public.observer_runs where id=p_run for update;
  select * into l from private.observer_run_leases where run_id=p_run;
  if r.status<>'queued' or l.lease is distinct from p_lease then return; end if;
  update private.observer_run_leases set error=left(coalesce(p_error,'schedule_unavailable'),100) where run_id=p_run;
end $$;

-- Run scheduling lease: same ~2h budget/backoff/park as preparations, for the
-- lease that turns a queued run into dispatched jobs.
create or replace function public.observer_pending_runs(p_limit integer default 5)
returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$
declare r record; result jsonb:='[]'; v_lease uuid; v_minutes integer; v_old private.observer_run_leases;
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
      m.archive_ref,m.digest as materialized_digest,rev.manifest
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
    order by run.created_at,run.id for update of run skip locked limit greatest(1,least(coalesce(p_limit,5),10))
  loop
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
    result:=result || jsonb_build_array(to_jsonb(r)||jsonb_build_object('lease',v_lease));
  end loop;
  return result;
end $$;

-- ---------------------------------------------------------------------------
-- Job-level failures: distinguish the contestant's own project (immediate,
-- specific, unchanged) from everything else (requeue through the same lease
-- the job came from, instead of failing). The classification mirrors
-- private.observer_participant_failure exactly, so a failure that still
-- reaches 'failed' here is, by construction, the contestant's own.
-- ---------------------------------------------------------------------------
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
      if p_result->'diagnostics'->>'code'='project_error' and coalesce(p_result->'diagnostics'->>'log','')<>'' then
        -- The contestant's own adaptation/build/entry-point failed: show it
        -- plainly, no retry.
        update public.observer_revisions set status='failed',
          error='Project preparation failed: '||left(p_result->'diagnostics'->>'log',400)
          where id=v.revision_id and status in ('queued','preparing');
      else
        -- Runner crash / GitHub Actions hiccup / infra error, not the
        -- contestant's code: requeue through the preparation lease instead of
        -- ever showing "failed". The adaptation run this attempt held is
        -- revoked (same as observer_reconcile_preparations already does for
        -- every terminated prepare job) and model_run_id is rotated -- it is
        -- single-use once a job has actually run under it.
        update public.observer_revisions set status='queued',error='' where id=v.revision_id and status in ('queued','preparing');
        update public.observer_runs set status='cancelled',finished_at=now()
          where id=(select model_run_id from private.observer_preparations where revision_id=v.revision_id)
            and status in ('starting','ready','running') returning batch_id into v_batch;
        if v_batch is not null then perform private.observer_finalize_batch(v_batch); end if;
        update private.observer_preparations set model_run_id=gen_random_uuid(),expires_at=now() where revision_id=v.revision_id;
      end if;
    elsif v.kind in ('execute','engine') and p_result->'diagnostics'->>'code'='project_operation_failed'
      and (v.kind='execute' or p_result->'diagnostics'->>'stage'='execute') then
      -- The contestant's own project crashed, timed out or violated the
      -- protocol inside the sandbox: show it plainly, no retry.
      update public.observer_runs set status='failed',error=v.kind||'_job_failed',finished_at=now()
        where id=v.run_id and status in ('queued','starting','ready','running') returning batch_id into v_batch;
      if v_batch is not null then perform private.observer_finalize_batch(v_batch); end if;
    else
      -- Platform-side failure: requeue the run through its scheduling lease
      -- (which may place it on a different organization or the public pool
      -- next time around) instead of failing it.
      update public.observer_runs set status='queued',error='' where id=v.run_id and status in ('queued','starting','ready','running');
      update private.observer_run_leases set expires_at=now() where run_id=v.run_id;
    end if;
  end if;
end $$;

-- Unchanged except that a dispatch/claim-lease timeout -- always ours, never
-- the contestant's (no participant code ever ran) -- requeues instead of
-- failing, for both prepare and execute/engine jobs. A score job's expiry is
-- unchanged: it only leaves its already-scored run 'unverified'.
create or replace function public.observer_reconcile_jobs()
returns integer language plpgsql security definer set search_path = public, pg_temp as $$
declare v private.observer_jobs; n integer:=0; v_batch uuid;
begin
  for v in select * from private.observer_jobs
    where status in ('queued','dispatched','claimed') and expires_at<=now()
    order by expires_at for update skip locked limit 100
  loop
    update private.observer_jobs set status='failed',error='job_expired',finished_at=now() where id=v.id;
    if v.kind='score' then
      perform private.observer_settle_score_check(v.id,'unverified',null);
    elsif v.revision_id is not null then
      update public.observer_revisions set status='queued',error='' where id=v.revision_id and status in ('queued','preparing');
      update public.observer_runs set status='cancelled',finished_at=now()
        where id=(select model_run_id from private.observer_preparations where revision_id=v.revision_id)
          and status in ('starting','ready','running') returning batch_id into v_batch;
      if v_batch is not null then perform private.observer_finalize_batch(v_batch); end if;
      update private.observer_preparations set model_run_id=gen_random_uuid(),expires_at=now() where revision_id=v.revision_id;
    else
      update public.observer_runs set status='queued',error='' where id=v.run_id and status in ('queued','starting','ready','running');
      update private.observer_run_leases set expires_at=now() where run_id=v.run_id;
    end if;
    n:=n+1;
  end loop;
  return n;
end $$;

-- Unchanged except that a malformed prepare-job result (a runtime bug, not
-- the contestant's fault) and a public-test scenario disabled mid-flight
-- (an organizer action) requeue instead of failing the revision. "Public test
-- failed" is untouched: that is the contestant's own interface not passing,
-- and (now that platform-caused run failures no longer reach 'failed') the
-- run.status='failed' check here is only ever true for that reason.
CREATE OR REPLACE FUNCTION public.observer_reconcile_preparations()
 RETURNS integer
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public', 'pg_temp'
AS $function$
declare r record;v jsonb;b uuid;preview uuid;n integer:=0;run public.observer_runs;
begin
  for r in select rev.id,rev.status as revision_status,rev.repository,prep.model_run_id,prep.preview_run_id,prep.phase_id,prep.scenario_id,
      p.owner_id,p.team_id,j.status as job_status,j.result
    from public.observer_revisions rev join private.observer_preparations prep on prep.revision_id=rev.id
    join public.observer_projects p on p.id=rev.project_id
    join private.observer_jobs j on j.revision_id=rev.id and j.kind='prepare'
    where (rev.status='preparing' or (rev.status='failed' and exists(select 1 from public.observer_runs
      where id=prep.model_run_id and status in ('starting','ready','running')))) and j.status in ('succeeded','failed')
      and (prep.preview_run_id is null or rev.status='preparing')
    order by rev.created_at for update of rev skip locked limit 20
  loop
    -- Revoke adaptation capability as soon as its trusted job terminates.
    update public.observer_runs set status='cancelled',finished_at=now()
      where id=r.model_run_id and status in ('starting','ready','running') returning batch_id into b;
    if b is not null then perform private.observer_finalize_batch(b); end if;
    if r.job_status='failed' or r.revision_status='failed' then continue; end if;
    if r.preview_run_id is null then
      v:=r.result;
      if v->>'status' is distinct from 'awaiting_public_test' or v->>'revision_id' is distinct from r.id::text or
        v->>'repository' is distinct from r.repository or
        not coalesce(v->>'source_digest' ~ '^[0-9a-f]{64}$',false) or
        not coalesce(v->>'source_commit' ~ '^[0-9a-f]{40}$',false) or
        not coalesce(v->>'materialized_digest' ~ '^[0-9a-f]{64}$',false) or
        not coalesce(v->>'approval_digest' ~ '^[0-9a-f]{64}$',false) or
        jsonb_typeof(v->'manifest') is distinct from 'object' or
        not coalesce(v->'manifest'->>'image' ~ '@sha256:[0-9a-f]{64}$',false) or
        jsonb_typeof(v->'adapter_files') is distinct from 'object' or
        left(v->>'preview_path',length('github:'||r.repository||'@')) is distinct from 'github:'||r.repository||'@' or
        length(v->>'preview_path') is distinct from length('github:'||r.repository||'@')+40 or
        not coalesce(v->>'preview_path' ~ '@[0-9a-f]{40}$',false) then
        update public.observer_revisions set status='queued',error='' where id=r.id;
        update private.observer_preparations set model_run_id=gen_random_uuid(),expires_at=now() where revision_id=r.id;
        continue;
      end if;
      if not exists(select 1 from public.scenarios where id=r.scenario_id and is_active and weather_public and events_public and forecasts_public) then
        update public.observer_revisions set status='queued',error='' where id=r.id;
        update private.observer_preparations set model_run_id=gen_random_uuid(),expires_at=now() where revision_id=r.id;
        continue;
      end if;
      insert into private.observer_materializations(revision_id,archive_ref,digest)
        values(r.id,v->>'preview_path',v->>'materialized_digest');
      update public.observer_revisions set source_digest=v->>'source_digest',source_commit=v->>'source_commit',
        manifest=v->'manifest',adapter_files=v->'adapter_files',approval_digest=v->>'approval_digest',
        explanation=left(coalesce(v->>'explanation',''),8000),public_test='{"status":"queued","passed":false}' where id=r.id;
      insert into public.observer_batches(team_id,user_id,phase_id,revision_id,mode,purpose)
        values(r.team_id,r.owner_id,r.phase_id,r.id,'project','preview') returning id into b;
      insert into public.observer_runs(batch_id,scenario_id) values(b,r.scenario_id) returning id into preview;
      update private.observer_preparations set preview_run_id=preview where revision_id=r.id;
    else
      select * into run from public.observer_runs where id=r.preview_run_id;
      if run.status in ('failed','cancelled') or (run.status='scored' and not coalesce(
          (run.score_summary->>'termination_reason' in ('survey_complete','global_wallclock_expired')
            -- v4 cards: an agent may end its own run with the "finish" action.
            or (run.score_summary->>'termination_reason'='agent_finished'
                and exists(select 1 from public.scenarios where id=run.scenario_id and contract='v4-score-v1')))
          and (run.score_summary->>'committed_action_count') ~ '^[1-9][0-9]*$',false)) or exists(select 1 from private.observer_jobs
          where run_id=run.id and status='failed') then
        update public.observer_revisions set status='failed',error='Public test failed. Check the project interface and submit again.',
          public_test=jsonb_build_object('passed',false,'status','failed','run_id',run.id) where id=r.id;
      elsif run.status='scored' and exists(select 1 from private.observer_jobs
          where run_id=run.id and kind='engine' and status='succeeded') and not exists(select 1 from private.observer_jobs
          where run_id=run.id and kind in ('execute','engine') and status<>'succeeded') then
        update public.observer_revisions set status='reviewable',
          public_test=jsonb_build_object('passed',true,'status','passed','run_id',run.id,'score',run.score) where id=r.id;
      end if;
    end if;
    n:=n+1;
  end loop;
  return n;
end $function$;

-- ---------------------------------------------------------------------------
-- One-off (and idempotent -- safe to call again) catch-up: revisions/batches
-- already stuck 'failed' for purely platform reasons, from before this retry
-- mechanism existed, get a fresh retry window instead of staying failed
-- forever. Mixed-cause batches (any run actually the contestant's fault) are
-- left untouched -- they already show the right thing.
-- ---------------------------------------------------------------------------
create or replace function private.observer_requeue_stuck()
returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$
declare n_rev integer:=0; n_batch integer:=0; rec record;
begin
  for rec in select r.id from public.observer_revisions r
    where r.status='failed' and r.archived_at is null
      and r.error in ('Project preparation failed. Please retry.','Project preparation did not finish. Please retry.',
        'Project scheduling failed. Please retry.','Invalid preparation result. Please retry.','Public test scenario is unavailable.')
  loop
    update public.observer_revisions set status='queued',error='' where id=rec.id;
    update private.observer_preparations set expires_at=now(),attempts=1,first_leased_at=now(),paused_at=null,
      model_run_id=gen_random_uuid(),error='' where revision_id=rec.id;
    n_rev:=n_rev+1;
  end loop;
  for rec in select b.id as batch_id from public.observer_batches b
    -- 'adaptation'/'preview' batches are internal preparation bookkeeping,
    -- never shown to a contestant (observer-portal's list only ever reads
    -- purpose='formal'); only a real, contestant-visible evaluation batch
    -- is worth requeuing here.
    where b.status='failed' and b.purpose='formal'
      and exists(select 1 from public.observer_runs r where r.batch_id=b.id)
      and not exists(select 1 from public.observer_runs r where r.batch_id=b.id and r.status<>'failed')
      and not exists(select 1 from public.observer_runs r where r.batch_id=b.id and private.observer_participant_failure(r.id))
  loop
    update public.observer_runs set status='queued',error='',finished_at=null where batch_id=rec.batch_id;
    update public.observer_batches set status='queued',finished_at=null,quota_refunded=false where id=rec.batch_id;
    update private.observer_run_leases set expires_at=now(),attempts=1,first_leased_at=now(),paused_at=null
      where run_id in (select id from public.observer_runs where batch_id=rec.batch_id);
    n_batch:=n_batch+1;
  end loop;
  return jsonb_build_object('revisions_requeued',n_rev,'batches_requeued',n_batch);
end $$;
revoke all on function private.observer_requeue_stuck() from public,anon,authenticated;

-- ---------------------------------------------------------------------------
-- Organizer-facing surface: incident counts (safe for the anon key, used by
-- db-watchdog's log), the admin incidents/retrying lists, and the one action
-- an organizer takes -- requeue a parked subject right now.
-- ---------------------------------------------------------------------------
create or replace function public.observer_incident_summary()
returns jsonb language sql stable security definer set search_path=public,pg_temp as $$
  select jsonb_build_object('open_incidents',(select count(*) from private.observer_incidents where resolved_at is null),
    'parked_revisions',(select count(*) from private.observer_preparations where paused_at is not null),
    'parked_runs',(select count(*) from private.observer_run_leases where paused_at is not null))
$$;

create or replace function public.observer_admin_incidents()
returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$
begin
  if not public.is_admin() then raise exception 'admin_only'; end if;
  return coalesce((select jsonb_agg(jsonb_build_object('id',i.id,'created_at',i.created_at,'subject_type',i.subject_type,
      'subject_id',i.subject_id,'reason',i.reason,'detail',i.detail,'team_name',coalesce(t1.name,t2.name))
      order by i.created_at desc)
    from private.observer_incidents i
    left join public.observer_revisions rv on i.subject_type='revision' and rv.id=i.subject_id
    left join public.observer_projects pr on pr.id=rv.project_id
    left join public.teams t1 on t1.id=pr.team_id
    left join public.observer_runs ru on i.subject_type='run' and ru.id=i.subject_id
    left join public.observer_batches ba on ba.id=ru.batch_id
    left join public.teams t2 on t2.id=ba.team_id
    where i.resolved_at is null),'[]'::jsonb);
end $$;

create or replace function public.observer_admin_retrying()
returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$
begin
  if not public.is_admin() then raise exception 'admin_only'; end if;
  return jsonb_build_object(
    'revisions',coalesce((select jsonb_agg(jsonb_build_object('revision_id',p.revision_id,'attempts',p.attempts,
        'first_leased_at',p.first_leased_at,'paused_at',p.paused_at,'team_name',t.name) order by p.first_leased_at)
      from private.observer_preparations p
      join public.observer_revisions r on r.id=p.revision_id
      join public.observer_projects pr on pr.id=r.project_id
      join public.teams t on t.id=pr.team_id
      where r.status in ('queued','preparing') and p.attempts>1),'[]'::jsonb),
    'runs',coalesce((select jsonb_agg(jsonb_build_object('run_id',l.run_id,'attempts',l.attempts,
        'first_leased_at',l.first_leased_at,'paused_at',l.paused_at,'team_name',t.name) order by l.first_leased_at)
      from private.observer_run_leases l
      join public.observer_runs ru on ru.id=l.run_id
      join public.observer_batches b on b.id=ru.batch_id
      join public.teams t on t.id=b.team_id
      where ru.status in ('queued','starting','ready','running') and l.attempts>1),'[]'::jsonb));
end $$;

create or replace function public.observer_admin_requeue(p_subject_type text,p_subject_id uuid)
returns void language plpgsql security definer set search_path=public,pg_temp as $$
begin
  if not public.is_admin() then raise exception 'admin_only'; end if;
  if p_subject_type='revision' then
    update public.observer_revisions set status='queued',error='' where id=p_subject_id and status in ('queued','preparing','failed');
    update private.observer_preparations set expires_at=now(),attempts=1,first_leased_at=now(),paused_at=null,
      model_run_id=gen_random_uuid(),error='' where revision_id=p_subject_id;
  elsif p_subject_type='run' then
    update public.observer_runs set status='queued',error='',finished_at=null
      where id=p_subject_id and status in ('queued','starting','ready','running','failed');
    update private.observer_run_leases set expires_at=now(),attempts=1,first_leased_at=now(),paused_at=null
      where run_id=p_subject_id;
  else
    raise exception 'unknown_subject_type';
  end if;
  update private.observer_incidents set resolved_at=now(),resolved_by=auth.uid()
    where subject_type=p_subject_type and subject_id=p_subject_id and resolved_at is null;
  perform private.audit('admin.incident.requeue',jsonb_build_object('subject_type',p_subject_type,'subject_id',p_subject_id));
end $$;

-- A one-time run right now: pick up anything already stuck failed for
-- platform reasons before this migration existed.
select private.observer_requeue_stuck();

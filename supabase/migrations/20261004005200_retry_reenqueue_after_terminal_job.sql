-- Root cause of 17 revisions permanently stuck queued/preparing (reported as "zip source
-- deleted", which it was not -- see 20261003003000's PR for that unrelated, already-fixed
-- bug): private.observer_jobs has unique(revision_id,kind) and unique(run_id,kind), added
-- 2026-09-25 back when a revision/run only ever got one job of a given kind, ever. The
-- retry/backoff system (platform_failure_resilience, 20261003000100) requeues a revision or
-- run after a platform-caused job failure and mints a brand-new job id on the next attempt
-- (observer-prepare.ts / observer-orchestrate.ts), but observer_enqueue_job still does a
-- plain insert -- so every second-or-later attempt for the same revision/run+kind hit the
-- old row's unique constraint ("duplicate key value violates unique constraint
-- observer_jobs_revision_id_kind_key"), confirmed live via a temporary diagnostic (removed)
-- that logged the underlying Postgres error the dispatcher's generic "preparation_unavailable"
-- was hiding. The lease kept re-granting (attempts climbed normally) but no job row was ever
-- created again, so the revision/run retried for the full ~2h budget and then parked forever,
-- never reaching the contestant as a failure and never actually progressing.
--
-- Fix: once a job has reached a terminal state (succeeded/failed), a fresh retry attempt for
-- the same (revision_id,kind) or (run_id,kind) may reuse that slot -- the old terminal row is
-- removed immediately before the new one is inserted. Two still-active jobs (queued/
-- dispatched/claimed) for the same revision/run+kind continue to collide exactly as before
-- (observer_enqueue_job's existing by-id idempotency check is untouched, so a genuine retried
-- call with the *same* job id is still a no-op); this only unblocks a *new* id's first insert
-- after the previous attempt is done. No column dropped, no constraint removed, no privilege
-- narrowed.
create or replace function public.observer_enqueue_job(p_id uuid,p_kind text,p_run uuid,p_revision uuid,
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
  if p_revision is not null then
    delete from private.observer_jobs where revision_id=p_revision and kind=p_kind and status in ('succeeded','failed');
  elsif p_run is not null then
    delete from private.observer_jobs where run_id=p_run and kind=p_kind and status in ('succeeded','failed');
  end if;
  insert into private.observer_jobs(id,kind,run_id,revision_id,organization,repository_id,organization_id,
    workflow_sha,nonce_hash,encrypted_input,encrypted_nonce)
  values(p_id,p_kind,p_run,p_revision,p_organization,v_install.repository_id,v_install.organization_id,
    v_install.approved_sha,sha256(convert_to(p_nonce,'UTF8')),p_encrypted_input,p_encrypted_nonce);
  return p_id;
end $$;

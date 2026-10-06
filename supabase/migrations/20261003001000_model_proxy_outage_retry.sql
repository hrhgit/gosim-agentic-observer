-- A model-proxy outage during preparation (project_platform/model_client.py
-- exhausting its own internal retries against *our* observer-model proxy --
-- "Model service is unavailable." for a network-level failure reaching it,
-- or "Model call failed (HTTP ###)." for a non-provider error status from
-- it) is reported with the same diagnostics.code='project_error' as a
-- genuine contestant mistake (bad manifest, missing entry point, their own
-- model account not set up -- those keep the message text from a different,
-- clearly-contestant branch in model_client.py: "No model API is set up for
-- your team...", "The model provider rejected the request...", etc). Only
-- these two exact proxy-outage messages are platform-caused; everything
-- else under project_error is unchanged (contestant's own build/account,
-- immediate, specific, no retry).
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
      perform private.observer_settle_score_check(p_job,'unverified',null);
    end;
    return;
  end if;
  if coalesce(p_error,'')<>'' then
    if v.revision_id is not null then
      if p_result->'diagnostics'->>'code'='project_error' and coalesce(p_result->'diagnostics'->>'log','')<>''
        and p_result->'diagnostics'->>'log' !~ '^(Model service is unavailable\.|Model call failed \(HTTP \d+\)\.)$' then
        update public.observer_revisions set status='failed',
          error='Project preparation failed: '||left(p_result->'diagnostics'->>'log',400)
          where id=v.revision_id and status in ('queued','preparing');
      else
        update public.observer_revisions set status='queued',error='' where id=v.revision_id and status in ('queued','preparing');
        update public.observer_runs set status='cancelled',finished_at=now()
          where id=(select model_run_id from private.observer_preparations where revision_id=v.revision_id)
            and status in ('starting','ready','running') returning batch_id into v_batch;
        if v_batch is not null then perform private.observer_finalize_batch(v_batch); end if;
        update private.observer_preparations set model_run_id=gen_random_uuid(),expires_at=now() where revision_id=v.revision_id;
      end if;
    elsif v.kind in ('execute','engine') and p_result->'diagnostics'->>'code'='project_operation_failed'
      and (v.kind='execute' or p_result->'diagnostics'->>'stage'='execute') then
      update public.observer_runs set status='failed',error=v.kind||'_job_failed',finished_at=now()
        where id=v.run_id and status in ('queued','starting','ready','running') returning batch_id into v_batch;
      if v_batch is not null then perform private.observer_finalize_batch(v_batch); end if;
    else
      update public.observer_runs set status='queued',error='' where id=v.run_id and status in ('queued','starting','ready','running');
      update private.observer_run_leases set expires_at=now() where run_id=v.run_id;
    end if;
  end if;
end $$;

-- One-time catch-up for the same gap: revisions already stuck 'failed' by
-- the pre-existing (and the first retry migration's) misclassification of a
-- model-proxy outage as the contestant's fault get a fresh retry window.
do $catchup$
declare rec record; n integer:=0;
begin
  for rec in select r.id from public.observer_revisions r
    where r.status='failed' and r.archived_at is null
      and r.error ~ '^Project preparation failed: (Model service is unavailable\.|Model call failed \(HTTP [0-9]+\)\.)$'
  loop
    update public.observer_revisions set status='queued',error='' where id=rec.id;
    update private.observer_preparations set expires_at=now(),attempts=1,first_leased_at=now(),paused_at=null,
      model_run_id=gen_random_uuid(),error='' where revision_id=rec.id;
    n:=n+1;
  end loop;
  raise notice 'model_proxy_outage_catchup_requeued=%',n;
end $catchup$;

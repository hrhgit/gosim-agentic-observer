-- Show the participant-facing reason when preparation fails with a known project
-- error (e.g. the automatic adapter could not find an entry point), instead of a
-- generic retry message. The reason is fixed platform wording, never project output.
CREATE OR REPLACE FUNCTION public.observer_finish_job(p_job uuid, p_github_run text, p_attempt text, p_result jsonb, p_error text DEFAULT ''::text)
 RETURNS void
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public', 'pg_temp'
AS $function$
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
end $function$

;

-- Colocated project runs (20260927000900) are scheduled with a single engine job.
-- The scheduler already sends one job; admission must accept it.
CREATE OR REPLACE FUNCTION public.observer_schedule_run(p_run uuid, p_lease uuid, p_organization text, p_participant_token text, p_engine_token text, p_local_credential text, p_jobs jsonb)
 RETURNS void
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public', 'pg_temp'
AS $function$
declare v_colocated boolean:=public.observer_run_colocated(p_run); r public.observer_runs;b public.observer_batches;l private.observer_run_leases;j jsonb;
begin
  select * into r from public.observer_runs where id=p_run for update;
  select * into l from private.observer_run_leases where run_id=p_run;
  if r.id is null or l.lease is distinct from p_lease then raise exception 'run_lease_invalid'; end if;
  -- A repeated acknowledgement cannot create new jobs or rotate capabilities.
  if r.status<>'queued' and exists(select 1 from private.observer_jobs where run_id=p_run) then return; end if;
  if r.status<>'queued' or l.expires_at<=clock_timestamp() then raise exception 'run_lease_invalid'; end if;
  select * into b from public.observer_batches where id=r.batch_id;
  if b.status not in ('queued','running') or not exists(select 1 from public.profiles
    where id=b.user_id and team_id=b.team_id and not is_banned) then raise exception 'run_not_eligible'; end if;
  if not exists(select 1 from public.observer_phase_settings c where c.phase_id=b.phase_id
    and (case when b.mode='local' then c.local_sessions_enabled else c.projects_enabled end)) then
    raise exception 'run_not_eligible'; end if;
  if p_jobs is null or jsonb_typeof(p_jobs)<>'array' or jsonb_array_length(p_jobs)<>
      (case when b.mode='local' or v_colocated then 1 else 2 end) then raise exception 'invalid_run_jobs'; end if;
  if (select count(*) from jsonb_array_elements(p_jobs) x where x->>'kind'='engine')<>1 or
    (select count(*) from jsonb_array_elements(p_jobs) x where x->>'kind'='execute')<>
      (case when b.mode='local' or v_colocated then 0 else 1 end) then raise exception 'invalid_run_jobs'; end if;
  if b.mode='local' then
    perform public.observer_open_local_session(p_run,p_participant_token,p_engine_token,p_local_credential);
  else
    if p_local_credential is not null then raise exception 'invalid_run_jobs'; end if;
    perform public.observer_open_session(p_run,p_participant_token,p_engine_token);
  end if;
  for j in select value from jsonb_array_elements(p_jobs) loop
    perform public.observer_enqueue_job((j->>'id')::uuid,j->>'kind',p_run,null,p_organization,
      j->>'nonce',j->>'encrypted_input',j->>'encrypted_nonce');
  end loop;
end $function$
;

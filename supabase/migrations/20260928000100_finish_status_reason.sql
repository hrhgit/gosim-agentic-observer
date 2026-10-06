-- The graceful finish message needs the run's termination reason on the
-- execute host: expose it (and nothing else) once the run is scored.
create or replace function public.observer_run_status(p_run uuid,p_token text)
returns jsonb language plpgsql security definer set search_path = public, pg_temp as $$
declare v private.observer_sessions; v_run public.observer_runs; v_hash bytea;
begin
  select * into v from private.observer_sessions where run_id=p_run;
  v_hash:=sha256(convert_to(p_token,'UTF8'));
  if not found or p_token is null or (v_hash<>v.engine_hash and v_hash<>v.participant_hash) then
    raise exception 'invalid_or_expired_capability'; end if;
  select * into v_run from public.observer_runs where id=p_run;
  -- Status is the only readable field after termination/expiration; no model
  -- use, observations, results, or writable capability is returned. The
  -- termination reason is the participant's own result metadata.
  return jsonb_build_object('status',v_run.status,'expired',v.expires_at<=clock_timestamp(),
    'termination_reason',case when v_run.status in ('scored','awaiting_csv')
      then v_run.score_summary->>'termination_reason' else null end);
end $$;

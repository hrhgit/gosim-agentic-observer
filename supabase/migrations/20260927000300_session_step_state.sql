-- Cheap, lock-free step state for server-side long polling. The session edge
-- function checks this next to the database instead of each runner polling
-- across the Pacific, then returns the full observer_poll result once ready.
create function public.observer_step_state(p_run uuid,p_token text,p_scope text)
returns jsonb language plpgsql stable security definer set search_path=public,pg_temp as $$
declare v private.observer_sessions; v_message private.observer_messages;
begin
  select s.* into v from private.observer_sessions s join public.observer_runs r on r.id=s.run_id
    where s.run_id=p_run and s.expires_at>clock_timestamp() and r.status in ('starting','ready','running');
  if not found or p_token is null or length(p_token)>200 or p_scope is null or p_scope not in ('participant','engine')
     or sha256(convert_to(p_token,'UTF8')) is distinct from
        (case when p_scope='engine' then v.engine_hash else v.participant_hash end) then
    raise exception 'invalid_or_expired_capability';
  end if;
  if v.deadline_at is not null and v.deadline_at<=clock_timestamp() then raise exception 'session_deadline'; end if;
  select * into v_message from private.observer_messages where run_id=p_run and sequence=v.next_sequence;
  return jsonb_build_object('ready',v.ready_at is not null,'sequence',v.next_sequence,
    'observed',v_message.observation is not null,'answered',v_message.response is not null);
end $$;
revoke all on function public.observer_step_state(uuid,text,text) from public,anon,authenticated;
grant execute on function public.observer_step_state(uuid,text,text) to service_role;

-- observer_poll only reads, but its capability check took FOR UPDATE on the
-- session row. While the engine stores a multi-MB catalog (publish_initial holds
-- that row for tens of seconds), the executor's poll hit the lock timeout
-- (SQLSTATE 55P03), failed three times and failed the run. Check the same
-- capability without locking.
create or replace function private.observer_capability_read(p_run uuid,p_token text,p_scope text)
returns private.observer_sessions language plpgsql stable security definer set search_path=public,pg_temp as $$
declare v private.observer_sessions;
begin
  select s.* into v from private.observer_sessions s join public.observer_runs r on r.id=s.run_id
    where s.run_id=p_run and s.expires_at>clock_timestamp() and r.status in ('starting','ready','running');
  if not found or p_token is null or length(p_token)>200 or p_scope is null or p_scope not in ('participant','engine')
     or sha256(convert_to(p_token,'UTF8')) is distinct from
        (case when p_scope='engine' then v.engine_hash else v.participant_hash end) then
    raise exception 'invalid_or_expired_capability';
  end if;
  if v.deadline_at is not null and v.deadline_at<=clock_timestamp() then raise exception 'session_deadline'; end if;
  return v;
end $$;
revoke all on function private.observer_capability_read(uuid,text,text) from public,anon,authenticated;

create or replace function public.observer_poll(p_run uuid,p_token text,p_scope text default 'participant',p_initialized boolean default false)
returns jsonb language plpgsql security definer set search_path=public,pg_temp set statement_timeout='60s' as $$
declare v private.observer_sessions; v_message private.observer_messages;
begin
  v:=private.observer_capability_read(p_run,p_token,p_scope);
  select * into v_message from private.observer_messages where run_id=p_run and sequence=v.next_sequence;
  if p_scope='engine' then
    return jsonb_build_object('ready',v.ready_at is not null,'sequence',v.next_sequence,'response',v_message.response);
  end if;
  return jsonb_build_object('publication',case when p_initialized then null else v.publication end,'sequence',v.next_sequence,
    'observation',v_message.observation,'action_received',v_message.response is not null,
    'deadline_at',v.deadline_at);
end $$;

create or replace function public.observer_step_state(p_run uuid,p_token text,p_scope text)
returns jsonb language plpgsql stable security definer set search_path=public,pg_temp as $$
declare v private.observer_sessions; v_message private.observer_messages;
begin
  v:=private.observer_capability_read(p_run,p_token,p_scope);
  select * into v_message from private.observer_messages where run_id=p_run and sequence=v.next_sequence;
  return jsonb_build_object('ready',v.ready_at is not null,'sequence',v.next_sequence,
    'observed',v_message.observation is not null,'answered',v_message.response is not null);
end $$;
notify pgrst,'reload schema';

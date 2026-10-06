-- Local participants use the same live session as cloud projects. Only the
-- participant capability is deliverable through the authenticated portal.
create table private.observer_local_credentials (
  run_id uuid primary key references public.observer_runs(id),
  encrypted_credential text not null,
  created_at timestamptz not null default now()
);
revoke all on private.observer_local_credentials from public,anon,authenticated;

create function public.observer_open_local_session(p_run uuid,p_participant_token text,p_engine_token text,p_encrypted text)
returns void language plpgsql security definer set search_path=public,pg_temp as $$
begin
  if not exists(select 1 from public.observer_runs r join public.observer_batches b on b.id=r.batch_id
    where r.id=p_run and b.mode='local') or p_encrypted is null or length(p_encrypted)<20 then
    raise exception 'invalid_local_session'; end if;
  perform public.observer_open_session(p_run,p_participant_token,p_engine_token);
  insert into private.observer_local_credentials(run_id,encrypted_credential) values(p_run,p_encrypted);
end $$;

create function public.observer_local_access(p_run uuid,p_user uuid)
returns jsonb language sql stable security definer set search_path=public,pg_temp as $$
  select jsonb_build_object('encrypted_credential',c.encrypted_credential,'expires_at',s.expires_at)
  from private.observer_local_credentials c join private.observer_sessions s on s.run_id=c.run_id
  join public.observer_runs r on r.id=c.run_id join public.observer_batches b on b.id=r.batch_id
  join public.profiles p on p.team_id=b.team_id
  where r.id=p_run and p.id=p_user and not p.is_banned and b.mode='local'
    and r.status in ('starting','ready','running') and s.expires_at>now()
$$;

create function public.observer_export_decisions(p_run uuid,p_token text)
returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$
declare v private.observer_sessions; r public.observer_runs; rows jsonb;
begin
  select * into v from private.observer_sessions where run_id=p_run;
  if not found or p_token is null or length(p_token)>200 or
    v.participant_hash is distinct from sha256(convert_to(p_token,'UTF8')) then raise exception 'invalid_or_expired_capability'; end if;
  select * into r from public.observer_runs where id=p_run;
  if r.status not in ('awaiting_csv','scored') then raise exception 'session_not_finished'; end if;
  if r.finished_at<now()-interval '7 days' then raise exception 'invalid_or_expired_capability'; end if;
  -- Completed, officially committed CSV rows only: no observation, future data,
  -- model response, uncommitted action, engine key or private scenario content.
  select coalesce(jsonb_agg(item.value order by m.sequence,item.ordinality),'[]') into rows
  from private.observer_messages m cross join lateral jsonb_array_elements(m.committed->'rows') with ordinality item
  where m.run_id=p_run and m.committed is not null;
  if octet_length(rows::text)>16700000 then raise exception 'decisions_export_too_large'; end if;
  return jsonb_build_object('rows',rows,'decisions_digest',r.decisions_digest);
end $$;

create function public.observer_abort_local(p_run uuid,p_token text)
returns void language plpgsql security definer set search_path=public,pg_temp as $$
declare v private.observer_sessions;v_batch uuid;
begin
  v:=private.observer_capability(p_run,p_token,'participant',false);
  select b.id into v_batch from public.observer_runs r join public.observer_batches b on b.id=r.batch_id
    where r.id=p_run and b.mode='local';
  if not found then raise exception 'invalid_local_session'; end if;
  update public.observer_runs set status='failed',error='local_runner_stopped',finished_at=now()
    where id=p_run and status in ('starting','ready','running');
  perform private.observer_finalize_batch(v_batch);
end $$;

revoke all on function public.observer_open_local_session(uuid,text,text,text),public.observer_local_access(uuid,uuid),
  public.observer_export_decisions(uuid,text),public.observer_abort_local(uuid,text) from public,anon,authenticated;
grant execute on function public.observer_open_local_session(uuid,text,text,text),public.observer_local_access(uuid,uuid),
  public.observer_export_decisions(uuid,text),public.observer_abort_local(uuid,text) to service_role;

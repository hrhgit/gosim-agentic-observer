-- Each team chooses how formal runs reach its model API:
--   stored (default): the key is saved encrypted and the model proxy calls the
--     provider server-side, so no page has to stay open;
--   relay: the existing ephemeral path; the key stays in the open page only.
-- The Edge portal encrypts a saved key with the application key (AES-GCM, bound
-- to the provider ID) before it reaches the database. No function returns the
-- key or its ciphertext to a participant. Organizer credits are never used for
-- formal runs in either mode; other runs are unchanged.

create table private.observer_team_model_modes (
  team_id uuid primary key references public.teams(id),
  mode text not null check (mode in ('stored','relay')),
  updated_by uuid references public.profiles(id) on delete set null,
  updated_at timestamptz not null default clock_timestamp()
);
-- One saved model API per team. The ciphertext stays in private.observer_providers
-- so the existing reservation, settlement and reconciliation accounting applies.
create table private.observer_team_models (
  team_id uuid primary key references public.teams(id),
  provider_id uuid not null unique references private.observer_providers(id),
  -- At most the last four key characters, for recognition only.
  key_hint text not null default '' check (key_hint ~ '^[!-~]{0,4}$'),
  saved_by uuid references public.profiles(id) on delete set null,
  saved_at timestamptz not null default clock_timestamp()
);
revoke all on private.observer_team_model_modes,private.observer_team_models from public,anon,authenticated;

create function private.observer_team_model_mode(p_team uuid)
returns text language sql stable security definer set search_path=public,pg_temp as $$
  select coalesce((select mode from private.observer_team_model_modes where team_id=p_team),'stored')
$$;
create function private.observer_run_model_mode(p_run uuid)
returns text language sql stable security definer set search_path=public,pg_temp as $$
  select private.observer_team_model_mode(b.team_id) from public.observer_runs r
    join public.observer_batches b on b.id=r.batch_id where r.id=p_run
$$;

-- Wipes one stored key. A row referenced by call receipts keeps only its
-- non-secret audit metadata; an unreferenced row is removed.
create function private.observer_forget_provider_key(p_provider uuid)
returns boolean language plpgsql security definer set search_path=public,pg_temp as $$
declare v_key text;
begin
  -- Waits for an in-flight reservation on this row, then sees its receipt.
  select encrypted_key into v_key from private.observer_providers where id=p_provider for update;
  if not found then return false; end if;
  delete from private.observer_team_models where provider_id=p_provider;
  if exists(select 1 from private.observer_model_calls where provider_id=p_provider)
    or exists(select 1 from private.observer_provider_usage where provider_id=p_provider) then
    update private.observer_providers set encrypted_key='',enabled=false where id=p_provider;
  else
    delete from private.observer_providers where id=p_provider;
  end if;
  return v_key<>'';
end $$;

-- Called only by the portal after it has verified the user and encrypted the key.
-- Saving a key also selects the stored mode.
create function public.observer_save_team_model(p_user uuid,p_provider uuid,p_base text,p_model text,
  p_encrypted_key text,p_key_hint text)
returns void language plpgsql security definer set search_path=public,pg_temp as $$
declare v_team uuid; v_old uuid;
begin
  select team_id into v_team from public.profiles where id=p_user and not is_banned;
  if v_team is null then raise exception 'team_required'; end if;
  if p_provider is null or p_base is null or length(p_base)>1000
    or p_base !~ '^https://[A-Za-z0-9.-]+(:[0-9]{1,5})?(/[A-Za-z0-9._~/-]*)?$'
    or p_model is null or length(p_model) not between 1 and 256 or p_model ~ '[[:cntrl:]]'
    or p_encrypted_key is null or length(p_encrypted_key)>16384
    or p_encrypted_key !~ '^v1[.][A-Za-z0-9+/]+=*[.][A-Za-z0-9+/]+=*$'
    or p_key_hint is null or p_key_hint !~ '^[!-~]{0,4}$' then raise exception 'invalid_team_model'; end if;
  -- One change at a time per team; a replaced key is forgotten in the same transaction.
  perform 1 from public.teams where id=v_team for update;
  insert into private.observer_team_model_modes(team_id,mode,updated_by) values(v_team,'stored',p_user)
    on conflict(team_id) do update set mode='stored',updated_by=excluded.updated_by,updated_at=clock_timestamp();
  select provider_id into v_old from private.observer_team_models where team_id=v_team for update;
  insert into private.observer_providers(id,team_id,name,base_url,encrypted_key,models,allow_http,enabled,daily_token_limit)
    values(p_provider,v_team,'Team model API',p_base,p_encrypted_key,array[p_model],false,true,0);
  insert into private.observer_team_models(team_id,provider_id,key_hint,saved_by,saved_at)
    values(v_team,p_provider,p_key_hint,p_user,clock_timestamp())
    on conflict(team_id) do update set provider_id=excluded.provider_id,key_hint=excluded.key_hint,
      saved_by=excluded.saved_by,saved_at=excluded.saved_at;
  if v_old is not null then perform private.observer_forget_provider_key(v_old); end if;
  perform private.audit('observer.team_model_saved',jsonb_build_object('team_id',v_team,'user_id',p_user,'base_url',p_base));
end $$;

create function public.observer_delete_team_model()
returns boolean language plpgsql security definer set search_path=public,pg_temp as $$
declare v_team uuid; v_provider uuid;
begin
  perform private.assert_not_banned();
  select team_id into v_team from public.profiles where id=auth.uid();
  if v_team is null then raise exception 'team_required'; end if;
  perform 1 from public.teams where id=v_team for update;
  select provider_id into v_provider from private.observer_team_models where team_id=v_team for update;
  if v_provider is null then return false; end if;
  perform private.observer_forget_provider_key(v_provider);
  perform private.audit('observer.team_model_deleted',jsonb_build_object('team_id',v_team));
  return true;
end $$;

-- Choosing the relay (not saving a key) deletes any saved key at once.
create function public.observer_set_team_model_mode(p_mode text)
returns text language plpgsql security definer set search_path=public,pg_temp as $$
declare v_team uuid; v_provider uuid;
begin
  perform private.assert_not_banned();
  if p_mode is null or p_mode not in ('stored','relay') then raise exception 'invalid_team_model_mode'; end if;
  select team_id into v_team from public.profiles where id=auth.uid();
  if v_team is null then raise exception 'team_required'; end if;
  perform 1 from public.teams where id=v_team for update;
  insert into private.observer_team_model_modes(team_id,mode,updated_by) values(v_team,p_mode,auth.uid())
    on conflict(team_id) do update set mode=excluded.mode,updated_by=excluded.updated_by,updated_at=clock_timestamp();
  if p_mode='relay' then
    select provider_id into v_provider from private.observer_team_models where team_id=v_team for update;
    if v_provider is not null then perform private.observer_forget_provider_key(v_provider); end if;
  end if;
  perform private.audit('observer.team_model_mode',jsonb_build_object('team_id',v_team,'mode',p_mode,
    'saved_key_deleted',v_provider is not null));
  return p_mode;
end $$;

-- Only what a team member needs: the chosen mode and how to recognize a saved
-- API. Never the key or its ciphertext. A saved key is shown and used until it
-- is deleted; the former providers' enabled flag does not apply to it.
create function public.observer_team_model()
returns jsonb language sql stable security definer set search_path=public,pg_temp as $$
  select jsonb_build_object('mode',private.observer_team_model_mode(u.team_id),'saved',
    (select jsonb_build_object('base_url',p.base_url,'model',p.models[1],'key_hint',m.key_hint,'saved_at',m.saved_at)
      from private.observer_team_models m join private.observer_providers p on p.id=m.provider_id
      where m.team_id=u.team_id and p.encrypted_key<>''))
  from public.profiles u where u.id=auth.uid() and not u.is_banned and u.team_id is not null
$$;

-- Trusted model proxy only. A formal run in stored mode may use only its own
-- team's saved API.
create function public.observer_reserve_team_model(p_run uuid,p_token text,p_call uuid,p_digest text,p_tokens bigint)
returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$
declare v private.observer_sessions; v_call private.observer_model_calls; v_provider private.observer_providers;
  v_team uuid; v_day date:=(clock_timestamp() at time zone 'UTC')::date;
begin
  v:=private.observer_capability(p_run,p_token,'participant');
  if not private.observer_personal_models_only(p_run) then raise exception 'team_model_not_enabled'; end if;
  if p_call is null or p_digest is null or p_digest !~ '^[0-9a-f]{64}$' or p_tokens is null
    or p_tokens not between 1 and 1000000 then raise exception 'invalid_reservation'; end if;
  select * into v_call from private.observer_model_calls where id=p_call;
  if found then
    if v_call.run_id<>p_run or v_call.request_digest<>p_digest or v_call.reserved_tokens<>p_tokens then
      raise exception 'request_id_conflict'; end if;
    -- Never send upstream a second time, even if the first HTTP response was lost.
    return jsonb_build_object('reserved',false,'status',v_call.status);
  end if;
  select b.team_id into v_team from public.observer_runs r join public.observer_batches b on b.id=r.batch_id where r.id=p_run;
  select p.* into v_provider from private.observer_team_models m join private.observer_providers p on p.id=m.provider_id
    where m.team_id=v_team and p.team_id=v_team and p.encrypted_key<>'' and not p.allow_http
      and private.observer_team_model_mode(v_team)='stored'
    for share of m,p;
  -- No organizer, shared or other-team provider is ever substituted.
  if not found then raise exception 'team_model_not_configured'; end if;
  -- Bounded calls and tokens per run, and one outstanding call at a time.
  if v.tokens_used+v.tokens_reserved+p_tokens>v.token_limit or v.calls_used>=v.call_limit
    or v.calls_active>=1 then raise exception 'run_model_quota'; end if;
  insert into private.observer_provider_usage(provider_id,usage_day) values(v_provider.id,v_day) on conflict do nothing;
  insert into private.observer_model_calls(id,run_id,provider_id,usage_day,request_digest,reserved_tokens)
    values(p_call,p_run,v_provider.id,v_day,p_digest,p_tokens);
  update private.observer_sessions set tokens_reserved=tokens_reserved+p_tokens,
    calls_used=calls_used+1,calls_active=calls_active+1 where run_id=p_run;
  update private.observer_provider_usage set tokens_reserved=tokens_reserved+p_tokens
    where provider_id=v_provider.id and usage_day=v_day;
  return jsonb_build_object('reserved',true,'provider_id',v_provider.id,'base_url',v_provider.base_url,
    'model',v_provider.models[1],'encrypted_key',v_provider.encrypted_key);
end $$;

-- The route now also names the team's mode. Relay behaviour is unchanged.
create or replace function public.observer_model_route(p_run uuid,p_token text)
returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$
begin
  perform private.observer_capability(p_run,p_token,'participant');
  if not private.observer_personal_models_only(p_run) then return jsonb_build_object('personal',false); end if;
  if private.observer_run_model_mode(p_run)='stored' then
    return jsonb_build_object('personal',true,'mode','stored');
  end if;
  insert into private.observer_personal_model_channels(run_id) values(p_run) on conflict do nothing;
  return (select jsonb_build_object('personal',true,'mode','relay','topic',topic)
    from private.observer_personal_model_channels where run_id=p_run);
end $$;

-- Only teams that chose not to save a key relay calls through their open page.
create or replace function public.observer_personal_model_routes()
returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$
declare v_team uuid;
begin
  perform private.assert_not_banned();
  select team_id into v_team from public.profiles where id=auth.uid();
  if v_team is null then raise exception 'team_required'; end if;
  if private.observer_team_model_mode(v_team)<>'relay' then return '[]'::jsonb; end if;
  insert into private.observer_personal_model_channels(run_id)
    select r.id from public.observer_runs r join public.observer_batches b on b.id=r.batch_id
    where b.team_id=v_team and r.status in ('queued','starting','ready','running') and private.observer_personal_models_only(r.id)
    on conflict do nothing;
  return coalesce((select jsonb_agg(jsonb_build_object('run_id',r.id,'topic',c.topic))
    from public.observer_runs r join public.observer_batches b on b.id=r.batch_id
    join private.observer_personal_model_channels c on c.run_id=r.id
    where b.team_id=v_team and r.status in ('queued','starting','ready','running')),'[]'::jsonb);
end $$;

create or replace function public.observer_request_personal_model(p_run uuid,p_token text,p_call uuid,p_digest text)
returns boolean language plpgsql security definer set search_path=public,pg_temp as $$
declare v private.observer_sessions; old private.observer_personal_model_calls;
begin
  v:=private.observer_capability(p_run,p_token,'participant');
  if not private.observer_personal_models_only(p_run) or private.observer_run_model_mode(p_run)<>'relay' then
    raise exception 'personal_model_not_enabled'; end if;
  if p_call is null or p_digest is null or p_digest !~ '^[0-9a-f]{64}$' then raise exception 'invalid_reservation'; end if;
  select * into old from private.observer_personal_model_calls where id=p_call;
  if found then
    if (old.run_id,old.request_digest) is distinct from (p_run,p_digest) then raise exception 'request_id_conflict'; end if;
    return false;
  end if;
  if (select count(*) from private.observer_personal_model_calls where run_id=p_run)>=10000 or
    exists(select 1 from private.observer_personal_model_calls where run_id=p_run and status in ('awaiting','running')
      and created_at>clock_timestamp()-interval '150 seconds') then raise exception 'run_model_quota'; end if;
  insert into private.observer_personal_model_calls(id,run_id,request_digest) values(p_call,p_run,p_digest);
  return true;
end $$;

-- Replaces the relay-only rule: a formal run may record server-side calls only
-- against its own team's saved key; a saved key never funds any other run.
create or replace function private.observer_disallow_stored_formal_model()
returns trigger language plpgsql security definer set search_path=public,pg_temp as $$
begin
  if private.observer_personal_models_only(new.run_id) then
    if not exists(select 1 from private.observer_team_models m
      join public.observer_batches b on b.team_id=m.team_id
      join public.observer_runs r on r.batch_id=b.id
      where m.provider_id=new.provider_id and r.id=new.run_id) then raise exception 'personal_api_required'; end if;
  elsif exists(select 1 from private.observer_team_models where provider_id=new.provider_id) then
    raise exception 'model_not_available';
  end if;
  return new;
end $$;

-- Organizer step after the results are verified. Wipes every participant key
-- (saved team keys and keys retained from the former provider settings);
-- organizer providers are untouched. Returns the number of keys deleted.
create function public.observer_purge_provider_keys()
returns integer language plpgsql security definer set search_path=public,pg_temp as $$
declare v record; n integer:=0;
begin
  for v in select id from private.observer_providers where team_id is not null and encrypted_key<>'' order by id loop
    if private.observer_forget_provider_key(v.id) then n:=n+1; end if;
  end loop;
  perform private.audit('observer.provider_keys_purged',jsonb_build_object('count',n));
  return n;
end $$;

-- Saved-key formal runs get explicit per-run bounds again (the relay migration
-- set them to zero): 10,000 calls, 10,000,000 tokens, one call at a time. The
-- relay path keeps its own bounds (10,000 calls, one outstanding call).
update public.observer_phase_settings set model_token_limit=10000000,model_call_limit=10000,model_concurrency=1
  where phase_id in (select id from public.phases where counts_for_final or slug='online');

revoke all on function private.observer_team_model_mode(uuid),private.observer_run_model_mode(uuid),
  private.observer_forget_provider_key(uuid) from public,anon,authenticated;
revoke all on function public.observer_save_team_model(uuid,uuid,text,text,text,text),public.observer_delete_team_model(),
  public.observer_set_team_model_mode(text),public.observer_team_model(),
  public.observer_reserve_team_model(uuid,text,uuid,text,bigint),public.observer_purge_provider_keys()
  from public,anon,authenticated;
grant execute on function public.observer_delete_team_model(),public.observer_set_team_model_mode(text),
  public.observer_team_model() to authenticated;
grant execute on function public.observer_save_team_model(uuid,uuid,text,text,text,text),public.observer_delete_team_model(),
  public.observer_set_team_model_mode(text),public.observer_team_model(),
  public.observer_reserve_team_model(uuid,text,uuid,text,bigint),public.observer_purge_provider_keys() to service_role;
notify pgrst,'reload schema';

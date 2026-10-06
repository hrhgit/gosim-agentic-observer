-- Runs that use the team's own model key (formal 'online' and final phases,
-- 'practice-projects', internal 'observer-acceptance-*' phases) get looser
-- per-run bounds: the team pays for its own tokens, so the token cap is set out
-- of reach; every call still passes through the platform, so calls stay capped.
--   model_token_limit 1,000,000,000   model_call_limit 100,000   model_concurrency 4
-- Phases that spend organizer keys keep their settings. Safe to re-run.

alter table public.observer_phase_settings
  drop constraint if exists observer_phase_settings_model_token_limit_check,
  drop constraint if exists observer_phase_settings_model_call_limit_check,
  drop constraint if exists observer_phase_settings_model_concurrency_check;
alter table public.observer_phase_settings
  add constraint observer_phase_settings_model_token_limit_check check (model_token_limit between 0 and 1000000000),
  add constraint observer_phase_settings_model_call_limit_check check (model_call_limit between 0 and 100000),
  add constraint observer_phase_settings_model_concurrency_check check (model_concurrency between 1 and 4);

update public.observer_phase_settings set model_token_limit=1000000000,model_call_limit=100000,model_concurrency=4
  where phase_id in (select id from public.phases where counts_for_final or slug='online'
      or slug='practice-projects' or slug like 'observer-acceptance-%')
    and (model_token_limit,model_call_limit,model_concurrency) is distinct from (1000000000,100000,4);

-- Saved-key path: honour the run's model_concurrency instead of one call at a time.
create or replace function public.observer_reserve_team_model(p_run uuid,p_token text,p_call uuid,p_digest text,p_tokens bigint)
returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$
declare v private.observer_sessions; v_call private.observer_model_calls; v_provider private.observer_providers;
  v_team uuid; v_day date:=(clock_timestamp() at time zone 'UTC')::date;
begin
  -- Locks the run's session row, so concurrent reservations are counted one by one.
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
  -- Bounded calls and tokens per run, and at most model_concurrency calls outstanding.
  if v.tokens_used+v.tokens_reserved+p_tokens>v.token_limit or v.calls_used>=v.call_limit
    or v.calls_active>=v.concurrency_limit then raise exception 'run_model_quota'; end if;
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

-- Relay path (key kept in the open page): the same per-run call cap and
-- concurrency as the saved-key path instead of its own fixed 10,000 / one call.
-- Tokens are not metered on the relay; the team's key pays for them.
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
  if (select count(*) from private.observer_personal_model_calls where run_id=p_run)>=v.call_limit or
    (select count(*) from private.observer_personal_model_calls where run_id=p_run and status in ('awaiting','running')
      and created_at>clock_timestamp()-interval '150 seconds')>=v.concurrency_limit then raise exception 'run_model_quota'; end if;
  insert into private.observer_personal_model_calls(id,run_id,request_digest) values(p_call,p_run,p_digest);
  return true;
end $$;

notify pgrst,'reload schema';

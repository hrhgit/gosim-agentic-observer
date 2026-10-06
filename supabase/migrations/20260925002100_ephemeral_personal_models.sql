-- Only relay addresses and call receipts persist. No provider key or ciphertext
-- is accepted by these functions or stored in these tables.
create table private.observer_personal_model_channels (
  run_id uuid primary key references public.observer_runs(id) on delete cascade,
  topic text not null unique default ('observer-model-'||replace(gen_random_uuid()::text,'-','')||replace(gen_random_uuid()::text,'-',''))
);
create table private.observer_personal_model_calls (
  id uuid primary key,
  run_id uuid not null references public.observer_runs(id) on delete cascade,
  request_digest text not null check(request_digest ~ '^[0-9a-f]{64}$'),
  status text not null default 'awaiting' check(status in ('awaiting','running','done','failed','timeout')),
  created_at timestamptz not null default clock_timestamp(),
  updated_at timestamptz not null default clock_timestamp()
);
create index on private.observer_personal_model_calls(run_id,created_at);
revoke all on private.observer_personal_model_channels,private.observer_personal_model_calls from public,anon,authenticated;

create function private.observer_personal_models_only(p_run uuid)
returns boolean language sql stable security definer set search_path=public,pg_temp as $$
  select exists(select 1 from public.observer_runs r join public.observer_batches b on b.id=r.batch_id
    join public.phases p on p.id=b.phase_id where r.id=p_run and (p.counts_for_final or p.slug='online'
      -- Preparation uses its own restricted phase, even during the competition.
      -- That shared phase must not silently spend organizer credits either.
      or exists(select 1 from private.observer_site_mode where id and mode='competition')))
$$;

create function public.observer_model_route(p_run uuid,p_token text)
returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$
begin
  perform private.observer_capability(p_run,p_token,'participant');
  if not private.observer_personal_models_only(p_run) then return jsonb_build_object('personal',false); end if;
  insert into private.observer_personal_model_channels(run_id) values(p_run) on conflict do nothing;
  return (select jsonb_build_object('personal',true,'topic',topic) from private.observer_personal_model_channels where run_id=p_run);
end $$;

create function public.observer_personal_model_routes()
returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$
declare v_team uuid;
begin
  perform private.assert_not_banned();
  select team_id into v_team from public.profiles where id=auth.uid();
  if v_team is null then raise exception 'team_required'; end if;
  insert into private.observer_personal_model_channels(run_id)
    select r.id from public.observer_runs r join public.observer_batches b on b.id=r.batch_id
    where b.team_id=v_team and r.status in ('queued','starting','ready','running') and private.observer_personal_models_only(r.id)
    on conflict do nothing;
  return coalesce((select jsonb_agg(jsonb_build_object('run_id',r.id,'topic',c.topic))
    from public.observer_runs r join public.observer_batches b on b.id=r.batch_id
    join private.observer_personal_model_channels c on c.run_id=r.id
    where b.team_id=v_team and r.status in ('queued','starting','ready','running')),'[]'::jsonb);
end $$;

create function public.observer_request_personal_model(p_run uuid,p_token text,p_call uuid,p_digest text)
returns boolean language plpgsql security definer set search_path=public,pg_temp as $$
declare v private.observer_sessions; old private.observer_personal_model_calls;
begin
  v:=private.observer_capability(p_run,p_token,'participant');
  if not private.observer_personal_models_only(p_run) then raise exception 'personal_model_not_enabled'; end if;
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

create function public.observer_claim_personal_model(p_user uuid,p_run uuid,p_call uuid,p_digest text)
returns text language plpgsql security definer set search_path=public,pg_temp as $$
declare v private.observer_personal_model_calls; v_topic text;
begin
  if not exists(select 1 from public.profiles u join public.observer_batches b on b.team_id=u.team_id
    join public.observer_runs r on r.batch_id=b.id join private.observer_sessions s on s.run_id=r.id
    where u.id=p_user and not u.is_banned and r.id=p_run and s.expires_at>clock_timestamp()
      and (s.deadline_at is null or s.deadline_at>clock_timestamp()) and r.status in ('starting','ready','running')) then raise exception 'run_not_found'; end if;
  select * into v from private.observer_personal_model_calls where id=p_call for update;
  if not found or (v.run_id,v.request_digest) is distinct from (p_run,p_digest) then raise exception 'request_id_conflict'; end if;
  if v.status<>'awaiting' or v.created_at<clock_timestamp()-interval '130 seconds' then raise exception 'model_request_already_received'; end if;
  update private.observer_personal_model_calls set status='running',updated_at=clock_timestamp() where id=p_call;
  select topic into v_topic from private.observer_personal_model_channels where run_id=p_run;
  if v_topic is null then raise exception 'run_not_found'; end if;
  return v_topic;
end $$;

create function public.observer_finish_personal_model(p_call uuid,p_status text)
returns void language plpgsql security definer set search_path=public,pg_temp as $$
begin
  if p_status not in ('done','failed','timeout') or p_status is null then raise exception 'invalid_status'; end if;
  update private.observer_personal_model_calls set status=p_status,updated_at=clock_timestamp()
    where id=p_call and status in ('awaiting','running');
end $$;

-- A stored provider must never bypass the personal, non-persistent path.
create function private.observer_disallow_stored_formal_model()
returns trigger language plpgsql security definer set search_path=public,pg_temp as $$
begin
  if private.observer_personal_models_only(new.run_id) then raise exception 'personal_api_required'; end if;
  return new;
end $$;
create trigger a_disallow_stored_formal_model before insert on private.observer_model_calls
  for each row execute function private.observer_disallow_stored_formal_model();
update public.observer_phase_settings set model_token_limit=0,model_call_limit=0
  where phase_id in (select id from public.phases where counts_for_final or slug='online');

revoke all on function private.observer_personal_models_only(uuid),private.observer_disallow_stored_formal_model() from public,anon,authenticated;
revoke all on function public.observer_model_route(uuid,text),public.observer_request_personal_model(uuid,text,uuid,text),
  public.observer_claim_personal_model(uuid,uuid,uuid,text),public.observer_finish_personal_model(uuid,text),public.observer_personal_model_routes() from public,anon,authenticated;
grant execute on function public.observer_personal_model_routes() to authenticated;
grant execute on function public.observer_model_route(uuid,text),public.observer_request_personal_model(uuid,text,uuid,text),
  public.observer_claim_personal_model(uuid,uuid,uuid,text),public.observer_finish_personal_model(uuid,text) to service_role;

-- Old clients cannot reintroduce persistent personal-key storage. Existing
-- provider records remain intact for audit and can still be disabled by owners.
create or replace function public.observer_save_provider(p_user uuid,p_id uuid,p_name text,p_base text,p_encrypted_key text,
  p_models text[],p_limit bigint,p_http boolean)
returns void language plpgsql security definer set search_path=public,pg_temp as $$
begin raise exception 'ephemeral_credentials_required'; end $$;

-- Credits are not issued during the formal competition. Existing codes remain.
create or replace function public.claim_redeem_code(p_provider text)
returns jsonb language plpgsql security definer set search_path = public as $$
declare v_uid uuid := auth.uid(); v_team uuid; v_row public.redeem_codes;
begin
  perform private.assert_not_banned();
  if exists(select 1 from private.observer_site_mode where mode='competition') then raise exception 'personal_api_required'; end if;
  select team_id into v_team from public.profiles where id = v_uid;
  if v_team is null then raise exception 'need_team'; end if;
  select * into v_row from public.redeem_codes where provider = p_provider and status = 'assigned' and team_id = v_team limit 1;
  if found then return jsonb_build_object('provider', v_row.provider, 'code', v_row.code, 'note', v_row.note, 'already', true); end if;
  select * into v_row from public.redeem_codes where provider = p_provider and status = 'available'
    order by id limit 1 for update skip locked;
  if not found then raise exception 'no_codes_left'; end if;
  update public.redeem_codes set status = 'assigned', team_id = v_team, assigned_by = v_uid, assigned_at = now() where id = v_row.id;
  perform private.audit('redeem.claim', jsonb_build_object('provider', p_provider, 'team_id', v_team));
  return jsonb_build_object('provider', v_row.provider, 'code', v_row.code, 'note', v_row.note, 'already', false);
end $$;

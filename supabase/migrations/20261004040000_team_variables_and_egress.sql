-- Team variables and allowed domains ("Keys and network").
--
-- A team stores named variables (API keys, base URLs, model names) and a short
-- list of public domains. At run time the variables become the project's
-- environment and the project container reaches only those domains on port 443,
-- through the pinned forwarder sidecar (project_platform/team_egress.py); the
-- platform no longer relays model traffic for such runs.
--
-- * Secret values are encrypted by the portal Edge function (AES-GCM, bound to
--   the row id) before they reach the database; plain values (base URLs, model
--   names) are stored as they are and shown to the team.
-- * The scheduler reads a run's variables and domains with
--   observer_run_team_egress and puts them into the encrypted job input, only
--   while observer_hardening.team_egress is on (off by default: rollout switch).
-- * Backfill: every team's saved model API becomes variables
--   (OPENAI_* or ANTHROPIC_* by its protocol: API key, base URL, model) plus the
--   base's host as an allowed domain. The key's ciphertext is reused as is: it is
--   bound to the provider id, which becomes the variable's id.
-- * Secret variables are deleted automatically on the same schedule as saved
--   model keys (observer_key_purge_after).
-- Idempotent; alters no existing table except one switch column.

create table if not exists private.observer_team_variables(
  id uuid primary key,
  team_id uuid not null references public.teams(id) on delete cascade,
  name text not null,
  secret boolean not null default true,
  encrypted_value text,
  plain_value text,
  hint text not null default '',
  updated_by uuid,
  updated_at timestamptz not null default clock_timestamp(),
  unique(team_id,name),
  check (name ~ '^[A-Z][A-Z0-9_]{0,63}$'),
  check ((secret and encrypted_value is not null and plain_value is null)
      or (not secret and plain_value is not null and encrypted_value is null)),
  check (encrypted_value is null or (length(encrypted_value)<=16384
      and encrypted_value ~ '^v1[.][A-Za-z0-9+/]+=*[.][A-Za-z0-9+/]+=*$')),
  check (plain_value is null or octet_length(plain_value)<=8192),
  check (hint ~ '^[!-~]{0,4}$')
);
create table if not exists private.observer_team_domains(
  team_id uuid not null references public.teams(id) on delete cascade,
  host text not null,
  added_at timestamptz not null default clock_timestamp(),
  primary key(team_id,host)
);
revoke all on private.observer_team_variables,private.observer_team_domains from public,anon,authenticated;

alter table private.observer_hardening add column if not exists team_egress boolean not null default false;

-- Same rules as project_platform/team_egress.py.
create or replace function private.observer_variable_name_ok(p_name text)
returns boolean language sql immutable as $$
  select p_name ~ '^[A-Z][A-Z0-9_]{0,63}$' and p_name !~ '^(OBSERVER_|SAC_)' and p_name !~ '_PROXY$'
    and p_name not in ('HTTPS_PROXY','HTTP_PROXY','ALL_PROXY','NO_PROXY','NODE_USE_ENV_PROXY','PATH','HOME',
      'HOSTNAME','LD_PRELOAD','LD_LIBRARY_PATH','PYTHONPATH','NODE_OPTIONS')
$$;
create or replace function private.observer_domain_ok(p_host text)
returns boolean language sql immutable as $$
  select p_host is not null and length(p_host)<=253
    and p_host ~ '^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$'
    and p_host !~ '^[0-9.]+$'
    and p_host !~ '(^|\.)(localhost|local|internal|intranet|lan|home\.arpa|arpa|test|example|invalid|onion)$'
$$;

create or replace function private.observer_team_of(p_user uuid)
returns uuid language sql stable security definer set search_path=public,pg_temp as $$
  select team_id from public.profiles where id=p_user and not is_banned
$$;

-- What a team member sees: names, plain values, the last characters of secrets, domains.
create or replace function public.observer_team_environment()
returns jsonb language sql stable security definer set search_path=public,pg_temp as $$
  select jsonb_build_object(
    'variables',coalesce((select jsonb_agg(jsonb_build_object('name',v.name,'secret',v.secret,'hint',v.hint,
        'value',v.plain_value,'updated_at',v.updated_at) order by v.name)
      from private.observer_team_variables v where v.team_id=t.team_id),'[]'::jsonb),
    'domains',coalesce((select jsonb_agg(d.host order by d.host) from private.observer_team_domains d
      where d.team_id=t.team_id),'[]'::jsonb),
    -- Teams that kept their key only in an open page ("do not save") must enter it here.
    'relay_key_missing',private.observer_team_model_mode(t.team_id)='relay'
      and not exists(select 1 from private.observer_team_variables v where v.team_id=t.team_id and v.secret),
    'limits',jsonb_build_object('variables',20,'domains',10,'value_bytes',8192))
  from (select private.observer_team_of(auth.uid()) team_id) t where t.team_id is not null
$$;

-- Portal only (service role): the portal has encrypted a secret value with p_id.
create or replace function public.observer_save_team_variable(p_user uuid,p_id uuid,p_name text,p_secret boolean,
  p_encrypted text,p_plain text,p_hint text)
returns void language plpgsql security definer set search_path=public,pg_temp as $$
declare v_team uuid:=private.observer_team_of(p_user);
begin
  if v_team is null then raise exception 'team_required'; end if;
  if p_id is null or p_secret is null or not coalesce(private.observer_variable_name_ok(p_name),false)
    or (p_secret and (p_encrypted is null or p_plain is not null))
    or (not p_secret and (p_plain is null or p_encrypted is not null)) then
    raise exception 'invalid_team_variable'; end if;
  perform 1 from public.teams where id=v_team for update;
  delete from private.observer_team_variables where team_id=v_team and name=p_name;
  if (select count(*) from private.observer_team_variables where team_id=v_team)>=20 then
    raise exception 'team_variable_limit'; end if;
  insert into private.observer_team_variables(id,team_id,name,secret,encrypted_value,plain_value,hint,updated_by)
    values(p_id,v_team,p_name,p_secret,p_encrypted,p_plain,coalesce(p_hint,''),p_user);
  perform private.audit('observer.team_variable_saved',jsonb_build_object('team_id',v_team,'user_id',p_user,
    'name',p_name,'secret',p_secret));
end $$;

create or replace function public.observer_delete_team_variable(p_name text)
returns boolean language plpgsql security definer set search_path=public,pg_temp as $$
declare v_team uuid; n integer;
begin
  perform private.assert_not_banned();
  v_team:=private.observer_team_of(auth.uid());
  if v_team is null then raise exception 'team_required'; end if;
  delete from private.observer_team_variables where team_id=v_team and name=p_name;
  get diagnostics n=row_count;
  if n>0 then
    perform private.audit('observer.team_variable_deleted',jsonb_build_object('team_id',v_team,'user_id',auth.uid(),
      'name',p_name));
  end if;
  return n>0;
end $$;

-- Portal only (service role): the portal has checked that every name resolves publicly.
create or replace function public.observer_set_team_domains(p_user uuid,p_hosts jsonb)
returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$
declare v_team uuid:=private.observer_team_of(p_user); v_hosts text[];
begin
  if v_team is null then raise exception 'team_required'; end if;
  if p_hosts is null or jsonb_typeof(p_hosts)<>'array' or jsonb_array_length(p_hosts)>10
    or exists(select 1 from jsonb_array_elements(p_hosts) e where jsonb_typeof(e)<>'string') then
    raise exception 'invalid_team_domains'; end if;
  select coalesce(array_agg(e order by o),'{}') into v_hosts from jsonb_array_elements_text(p_hosts) with ordinality x(e,o);
  if exists(select 1 from unnest(v_hosts) h where not coalesce(private.observer_domain_ok(h),false))
    or (select count(distinct h) from unnest(v_hosts) h)<>cardinality(v_hosts) then
    raise exception 'invalid_team_domains'; end if;
  perform 1 from public.teams where id=v_team for update;
  delete from private.observer_team_domains where team_id=v_team and host<>all(v_hosts);
  insert into private.observer_team_domains(team_id,host) select v_team,h from unnest(v_hosts) h
    on conflict do nothing;
  perform private.audit('observer.team_domains_saved',jsonb_build_object('team_id',v_team,'user_id',p_user,
    'domains',p_hosts));
  return p_hosts;
end $$;

-- Scheduler only (service role): a run's variables (ciphertext for secrets) and domains.
create or replace function public.observer_run_team_egress(p_run uuid)
returns jsonb language sql stable security definer set search_path=public,pg_temp as $$
  select jsonb_build_object(
    'variables',coalesce((select jsonb_agg(jsonb_build_object('id',v.id,'name',v.name,'secret',v.secret,
        'encrypted_value',v.encrypted_value,'plain_value',v.plain_value) order by v.name)
      from private.observer_team_variables v where v.team_id=b.team_id),'[]'::jsonb),
    'domains',coalesce((select jsonb_agg(d.host order by d.host) from private.observer_team_domains d
      where d.team_id=b.team_id),'[]'::jsonb))
  from public.observer_runs r join public.observer_batches b on b.id=r.batch_id where r.id=p_run
$$;

create or replace function public.observer_hardening()
returns jsonb language sql stable security definer set search_path=public,pg_temp as $$
  select coalesce((select jsonb_build_object('restricted_egress',h.restricted_egress,'rescore',h.rescore,
      'team_egress',h.team_egress)
    from private.observer_hardening h where h.id),
    jsonb_build_object('restricted_egress',false,'rescore',false,'team_egress',false))
$$;

-- Backfill: each saved model API becomes variables and one allowed domain.
create or replace function private.observer_backfill_team_variables()
returns integer language plpgsql security definer set search_path=public,pg_temp as $$
declare r record; n integer:=0; prefix text; host text;
begin
  for r in select m.team_id,p.id,p.base_url,p.models[1] model,p.encrypted_key,coalesce(m.key_hint,'') hint,m.saved_by
      from private.observer_team_models m join private.observer_providers p on p.id=m.provider_id
      where p.team_id=m.team_id and p.encrypted_key<>''
        and not exists(select 1 from private.observer_team_variables v where v.team_id=m.team_id) loop
    prefix:=case private.observer_team_model_protocol(r.team_id) when 'anthropic' then 'ANTHROPIC' else 'OPENAI' end;
    insert into private.observer_team_variables(id,team_id,name,secret,encrypted_value,hint,updated_by)
      values(r.id,r.team_id,prefix||'_API_KEY',true,r.encrypted_key,
        case when r.hint ~ '^[!-~]{0,4}$' then r.hint else '' end,r.saved_by)
      on conflict do nothing;
    insert into private.observer_team_variables(id,team_id,name,secret,plain_value,updated_by)
      values(gen_random_uuid(),r.team_id,prefix||'_BASE_URL',false,r.base_url,r.saved_by) on conflict do nothing;
    if r.model is not null and r.model<>'' then
      insert into private.observer_team_variables(id,team_id,name,secret,plain_value,updated_by)
        values(gen_random_uuid(),r.team_id,prefix||'_MODEL',false,r.model,r.saved_by) on conflict do nothing;
    end if;
    host:=lower(substring(r.base_url from '^https://([^/:]+)'));
    if private.observer_domain_ok(host) then
      insert into private.observer_team_domains(team_id,host) values(r.team_id,host) on conflict do nothing;
    end if;
    n:=n+1;
  end loop;
  return n;
end $$;
select private.observer_backfill_team_variables();

-- Automatic deletion of secret values, on the saved-key schedule.
create or replace function private.observer_auto_purge_team_variables()
returns integer language plpgsql security definer set search_path=public,pg_temp as $$
declare cfg private.observer_key_retention; t record; n integer:=0; k integer;
begin
  select * into cfg from private.observer_key_retention where id;
  if not found or not cfg.enabled then return 0; end if;
  for t in select v.team_id,max(v.updated_at) saved from private.observer_team_variables v where v.secret
      group by v.team_id order by v.team_id loop
    perform 1 from public.teams where id=t.team_id for update;
    if private.observer_team_model_busy(t.team_id) then continue; end if;
    if private.observer_key_purge_after(t.team_id,t.saved)<=clock_timestamp() then
      delete from private.observer_team_variables where team_id=t.team_id and secret;
      get diagnostics k=row_count;
      n:=n+k;
    end if;
  end loop;
  if n>0 then perform private.audit('observer.team_variables_auto_purged',jsonb_build_object('count',n)); end if;
  return n;
end $$;

revoke all on function private.observer_variable_name_ok(text),private.observer_domain_ok(text),
  private.observer_team_of(uuid),private.observer_backfill_team_variables(),
  private.observer_auto_purge_team_variables() from public,anon,authenticated;
revoke all on function public.observer_team_environment(),
  public.observer_save_team_variable(uuid,uuid,text,boolean,text,text,text),
  public.observer_delete_team_variable(text),public.observer_set_team_domains(uuid,jsonb),
  public.observer_run_team_egress(uuid) from public,anon,authenticated;
grant execute on function public.observer_team_environment(),public.observer_delete_team_variable(text)
  to authenticated;
grant execute on function public.observer_save_team_variable(uuid,uuid,text,boolean,text,text,text),
  public.observer_set_team_domains(uuid,jsonb),public.observer_run_team_egress(uuid) to service_role;

do $cron$
begin
  if exists(select 1 from pg_available_extensions where name='pg_cron') then
    create extension if not exists pg_cron;
    perform cron.unschedule(jobid) from cron.job where jobname='observer-purge-team-variables';
    perform cron.schedule('observer-purge-team-variables','23 * * * *','select private.observer_auto_purge_team_variables()');
  end if;
end $cron$;

notify pgrst,'reload schema';

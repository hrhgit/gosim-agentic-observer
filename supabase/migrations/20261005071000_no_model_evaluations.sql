-- Evaluations without a model ("本次不提供模型") and team variables switched off without deleting.
--
-- * observer_batches.model_disabled: chosen when a team starts an ordinary evaluation (or a
--   self-check set). For its runs observer_run_team_egress leaves out every team variable
--   tagged "model", so the values never leave the database for that run, and reports
--   model_disabled=true; the scheduler then sets OBSERVER_MODEL_DISABLED=1 in the project's
--   environment. Network policy is unchanged. The hidden final is created by the organizers
--   (observer_run_hidden_final) and never carries the flag; sealed phases refuse it.
-- * observer_team_variables.model: the "model" tag. Set automatically for model-looking
--   names (OPENAI_*, ANTHROPIC_*, *_API_KEY, *_BASE_URL, *_MODEL, *_MODEL_NAME, *_PROTOCOL,
--   *LLM_*) and for variables created by 「添加模型服务」; the team can change it.
-- * observer_team_variables.disabled: kept (still encrypted) but never given to a run or a
--   preparation job.
-- * private.stats_llm_usage_daily gains model_disabled (part of the key) for paired comparisons.
--
-- Additive: two columns on observer_team_variables, one on observer_batches, one on the stats
-- table; functions keep their argument lists apart from one new trailing argument with a default
-- (observer_create_batch, observer_create_repeat_batches, observer_save_team_variable), so older
-- callers keep working. Rollback: none needed (flags default to false; old edge functions ignore them).

set local lock_timeout = '5s';

alter table public.observer_batches add column if not exists model_disabled boolean not null default false;
alter table private.observer_team_variables add column if not exists model boolean not null default false;
alter table private.observer_team_variables add column if not exists disabled boolean not null default false;

-- Model-looking names (tagged automatically; the team can change the tag).
create or replace function private.observer_variable_model_name(p_name text)
returns boolean language sql immutable as $$
  select coalesce(p_name ~ '^(OPENAI|ANTHROPIC)_' or p_name ~ '_(API_KEY|BASE_URL|MODEL|MODEL_NAME|PROTOCOL)$'
    or p_name ~ '(^|_)LLM_', false)
$$;
revoke all on function private.observer_variable_model_name(text) from public,anon,authenticated;

update private.observer_team_variables set model=true
  where not model and private.observer_variable_model_name(name);

-- What a team member sees: as before plus each variable's tag and switch.
create or replace function public.observer_team_environment()
returns jsonb language sql stable security definer set search_path=public,pg_temp as $$
  select jsonb_build_object(
    'enabled',private.observer_team_egress_on(t.team_id),
    'open',private.observer_open_egress_on(t.team_id),
    'variables',coalesce((select jsonb_agg(jsonb_build_object('name',v.name,'secret',v.secret,'hint',v.hint,
        'value',v.plain_value,'updated_at',v.updated_at,'model',v.model,'disabled',v.disabled) order by v.name)
      from private.observer_team_variables v where v.team_id=t.team_id),'[]'::jsonb),
    'domains',coalesce((select jsonb_agg(d.host order by d.host) from private.observer_team_domains d
      where d.team_id=t.team_id),'[]'::jsonb),
    'relay_key_missing',private.observer_team_model_mode(t.team_id)='relay'
      and not exists(select 1 from private.observer_team_variables v where v.team_id=t.team_id and v.secret),
    'limits',jsonb_build_object('variables',20,'domains',10,'value_bytes',8192),
    'egress_route',jsonb_build_object(
      'available',private.observer_egress_routes_on(t.team_id) and private.observer_open_egress_on(t.team_id),
      'route',coalesce((select r.route from private.observer_team_egress_route r where r.team_id=t.team_id),'direct'),
      'auto_fallback',coalesce((select r.auto_fallback from private.observer_team_egress_route r
        where r.team_id=t.team_id),true)))
  from (select private.observer_team_of(auth.uid()) team_id) t where t.team_id is not null
$$;

-- Portal only (service role). p_model: the tag (null = keep the saved tag, or by name for a new variable).
-- Saving a new value keeps the variable's tag and switch.
drop function if exists public.observer_save_team_variable(uuid,uuid,text,boolean,text,text,text);
create or replace function public.observer_save_team_variable(p_user uuid,p_id uuid,p_name text,p_secret boolean,
  p_encrypted text,p_plain text,p_hint text,p_model boolean default null)
returns void language plpgsql security definer set search_path=public,pg_temp as $$
declare v_team uuid:=private.observer_team_of(p_user); v_old private.observer_team_variables;
begin
  if v_team is null then raise exception 'team_required'; end if;
  if p_id is null or p_secret is null or not coalesce(private.observer_variable_name_ok(p_name),false)
    or (p_secret and (p_encrypted is null or p_plain is not null))
    or (not p_secret and (p_plain is null or p_encrypted is not null)) then
    raise exception 'invalid_team_variable'; end if;
  perform 1 from public.teams where id=v_team for update;
  select * into v_old from private.observer_team_variables where team_id=v_team and name=p_name;
  delete from private.observer_team_variables where team_id=v_team and name=p_name;
  if (select count(*) from private.observer_team_variables where team_id=v_team)>=20 then
    raise exception 'team_variable_limit'; end if;
  insert into private.observer_team_variables(id,team_id,name,secret,encrypted_value,plain_value,hint,updated_by,model,disabled)
    values(p_id,v_team,p_name,p_secret,p_encrypted,p_plain,coalesce(p_hint,''),p_user,
      coalesce(p_model,v_old.model,private.observer_variable_model_name(p_name)),coalesce(v_old.disabled,false));
  perform private.audit('observer.team_variable_saved',jsonb_build_object('team_id',v_team,'user_id',p_user,
    'name',p_name,'secret',p_secret));
end $$;

-- A team member: tag a variable as model-related or not, switch it off or on (null = unchanged).
create or replace function public.observer_set_team_variable_flags(p_name text,p_model boolean default null,
  p_disabled boolean default null)
returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$
declare v_team uuid; n integer;
begin
  perform private.assert_not_banned();
  v_team:=private.observer_team_of(auth.uid());
  if v_team is null then raise exception 'team_required'; end if;
  update private.observer_team_variables set model=coalesce(p_model,model),disabled=coalesce(p_disabled,disabled)
    where team_id=v_team and name=p_name;
  get diagnostics n=row_count;
  if n=0 then raise exception 'team_variable_not_found'; end if;
  perform private.audit('observer.team_variable_flags',jsonb_build_object('team_id',v_team,'user_id',auth.uid(),
    'name',p_name,'model',p_model,'disabled',p_disabled));
  return public.observer_team_environment();
end $$;

-- Scheduler only: switched-off variables never; model variables not for an evaluation without a model.
create or replace function public.observer_run_team_egress(p_run uuid)
returns jsonb language sql stable security definer set search_path=public,pg_temp as $$
  select jsonb_build_object(
    'enabled',private.observer_team_egress_on(b.team_id),
    'open',private.observer_open_egress_on(b.team_id),
    'model_disabled',b.model_disabled,
    'variables',coalesce((select jsonb_agg(jsonb_build_object('id',v.id,'name',v.name,'secret',v.secret,
        'encrypted_value',v.encrypted_value,'plain_value',v.plain_value) order by v.name)
      from private.observer_team_variables v
      where v.team_id=b.team_id and not v.disabled and not (b.model_disabled and v.model)),'[]'::jsonb),
    'domains',coalesce((select jsonb_agg(d.host order by d.host) from private.observer_team_domains d
      where d.team_id=b.team_id),'[]'::jsonb),
    'route',private.observer_team_route(b.team_id))
  from public.observer_runs r join public.observer_batches b on b.id=r.batch_id where r.id=p_run
$$;

create or replace function public.observer_preparation_team_egress(p_revision uuid)
returns jsonb language sql stable security definer set search_path=public,pg_temp as $$
  select jsonb_build_object(
    'enabled',coalesce((select h.prepare_direct_model from private.observer_hardening h where h.id),false)
      and private.observer_team_egress_on(p.team_id),
    'open',private.observer_open_egress_on(p.team_id),
    'variables',coalesce((select jsonb_agg(jsonb_build_object('id',v.id,'name',v.name,'secret',v.secret,
        'encrypted_value',v.encrypted_value,'plain_value',v.plain_value) order by v.name)
      from private.observer_team_variables v where v.team_id=p.team_id and not v.disabled),'[]'::jsonb),
    'domains',coalesce((select jsonb_agg(d.host order by d.host) from private.observer_team_domains d
      where d.team_id=p.team_id),'[]'::jsonb))
  from public.observer_revisions r join public.observer_projects p on p.id=r.project_id where r.id=p_revision
$$;

-- The retired platform model proxy (still the team-egress rollback path) refuses runs without a model.
create or replace function public.observer_model_route(p_run uuid, p_token text)
returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$
declare v_team uuid; v_off boolean;
begin
  perform private.observer_capability(p_run,p_token,'participant');
  select b.team_id,b.model_disabled into v_team,v_off from public.observer_runs r
    join public.observer_batches b on b.id=r.batch_id where r.id=p_run;
  if v_off then raise exception 'model_disabled'; end if;
  -- Retired for teams on team egress (they call their provider directly).
  if private.observer_model_proxy_retired(v_team) then raise exception 'model_proxy_retired'; end if;
  if not private.observer_personal_models_only(p_run) then return jsonb_build_object('personal',false); end if;
  if private.observer_run_model_mode(p_run)='stored' then
    return jsonb_build_object('personal',true,'mode','stored','protocol',private.observer_team_model_protocol(v_team));
  end if;
  insert into private.observer_personal_model_channels(run_id) values(p_run) on conflict do nothing;
  return (select jsonb_build_object('personal',true,'mode','relay','topic',topic,
      'protocol',private.observer_team_model_protocol(v_team))
    from private.observer_personal_model_channels where run_id=p_run);
end $$;

-- Evaluations: p_no_model = "本次不提供模型" (project versions in open, non-sealed phases only).
drop function if exists public.observer_create_repeat_batches(uuid,uuid,boolean);
drop function if exists public.observer_create_batch(uuid,uuid,boolean);
create or replace function public.observer_create_batch(p_phase uuid, p_revision uuid default null,
  p_confirm_repeat boolean default false, p_no_model boolean default false)
returns uuid language plpgsql security definer set search_path=public,pg_temp as $$
declare v_team uuid; v_config public.observer_phase_settings; v_phase public.phases; v_id uuid;
begin
  perform private.assert_not_banned();
  select team_id into v_team from public.profiles where id=auth.uid();
  if v_team is null then raise exception 'team_required'; end if;
  perform 1 from public.teams where id=v_team for update;
  select * into v_phase from public.phases where id=p_phase;
  select * into v_config from public.observer_phase_settings where phase_id=p_phase;
  if not found or not v_phase.is_active or (v_phase.starts_at is not null and now()<v_phase.starts_at)
     or (v_phase.ends_at is not null and now()>=v_phase.ends_at) then raise exception 'phase_closed'; end if;
  if coalesce(p_no_model,false) and (p_revision is null or coalesce(v_config.sealed,false)) then
    raise exception 'no_model_not_available'; end if;
  if p_revision is null then
    if not v_config.local_sessions_enabled then raise exception 'local_sessions_disabled'; end if;
  else
    if not v_config.projects_enabled then raise exception 'projects_not_enabled'; end if;
    if exists(select 1 from public.observer_revisions r join public.observer_projects p on p.id=r.project_id
      where r.id=p_revision and p.team_id=v_team and r.archived_at is not null) then raise exception 'revision_withdrawn'; end if;
    if not exists(select 1 from public.observer_revisions r join public.observer_projects p on p.id=r.project_id
      where r.id=p_revision and r.status='approved' and p.team_id=v_team) then raise exception 'revision_not_approved'; end if;
  end if;
  if private.observer_batches_used(v_team,p_phase) >= v_config.daily_batches then
    raise exception 'daily_limit';
  end if;
  -- Up to max_active_evaluations of the team's evaluations in flight (a self-check set counts once);
  -- the organizers' sealed final evaluations do not count.
  if private.observer_active_evaluations(v_team) >= coalesce(v_config.max_active_evaluations,4) then
    raise exception 'batch_already_active';
  end if;
  if not exists(select 1 from public.phase_scenarios where phase_id=p_phase) then raise exception 'no_scenarios'; end if;
  -- Another evaluation of the same version uses another daily evaluation; the
  -- team must ask for it explicitly. A refunded (platform-failed) try does not count.
  if p_revision is not null and not coalesce(p_confirm_repeat,false) and exists(select 1 from public.observer_batches
    where revision_id=p_revision and phase_id=p_phase and purpose='formal' and not quota_refunded) then
    raise exception 'revision_already_evaluated';
  end if;
  insert into public.observer_batches(team_id,user_id,phase_id,revision_id,mode,model_disabled)
    values(v_team,auth.uid(),p_phase,p_revision,case when p_revision is null then 'local' else 'project' end,
      coalesce(p_no_model,false))
    returning id into v_id;
  insert into public.observer_runs(batch_id,scenario_id)
    select v_id,scenario_id from public.phase_scenarios where phase_id=p_phase;
  return v_id;
end $$;

create or replace function public.observer_create_repeat_batches(p_phase uuid, p_revision uuid,
  p_confirm_repeat boolean default false, p_no_model boolean default false)
returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$
declare v_team uuid; v_config public.observer_phase_settings; v_runs integer:=private.observer_self_check_runs();
  v_group uuid:=gen_random_uuid(); v_first uuid; v_id uuid; v_ids jsonb; i integer;
begin
  perform private.assert_not_banned();
  select team_id into v_team from public.profiles where id=auth.uid();
  if v_team is null then raise exception 'team_required'; end if;
  if p_revision is null then raise exception 'revision_not_approved'; end if;
  perform 1 from public.teams where id=v_team for update;
  select * into v_config from public.observer_phase_settings where phase_id=p_phase;
  -- Checked before the first evaluation is created: all three or none.
  if found and private.observer_batches_used(v_team,p_phase)+v_runs > v_config.daily_batches then
    raise exception 'repeat_daily_limit';
  end if;
  -- At most one self-check set in flight per team.
  if exists(select 1 from public.observer_batches where team_id=v_team and purpose='formal' and repeat_group is not null
    and status in ('queued','running')) then raise exception 'repeat_already_active'; end if;
  -- Every other check (phase open, confirmed version, evaluations in flight, repeat confirmation) as usual.
  v_first:=public.observer_create_batch(p_phase,p_revision,p_confirm_repeat,coalesce(p_no_model,false));
  update public.observer_batches set repeat_group=v_group,repeat_runs=v_runs where id=v_first;
  v_ids:=jsonb_build_array(v_first);
  for i in 2..v_runs loop
    insert into public.observer_batches(team_id,user_id,phase_id,revision_id,mode,repeat_group,repeat_runs,created_at,
        model_disabled)
      values(v_team,auth.uid(),p_phase,p_revision,'project',v_group,v_runs,clock_timestamp(),coalesce(p_no_model,false))
      returning id into v_id;
    insert into public.observer_runs(batch_id,scenario_id,created_at)
      select v_id,scenario_id,clock_timestamp() from public.phase_scenarios where phase_id=p_phase order by scenario_id;
    v_ids:=v_ids||jsonb_build_array(v_id);
  end loop;
  perform private.audit('observer.repeat_evaluation',jsonb_build_object('phase_id',p_phase,'revision_id',p_revision,
    'repeat_group',v_group,'batches',v_ids,'model_disabled',coalesce(p_no_model,false)));
  return jsonb_build_object('repeat_group',v_group,'batch_ids',v_ids);
end $$;

revoke all on function public.observer_save_team_variable(uuid,uuid,text,boolean,text,text,text,boolean),
  public.observer_set_team_variable_flags(text,boolean,boolean),
  public.observer_create_batch(uuid,uuid,boolean,boolean),
  public.observer_create_repeat_batches(uuid,uuid,boolean,boolean) from public,anon,authenticated;
grant execute on function public.observer_save_team_variable(uuid,uuid,text,boolean,text,text,text,boolean) to service_role;
grant execute on function public.observer_set_team_variable_flags(text,boolean,boolean),
  public.observer_create_batch(uuid,uuid,boolean,boolean),
  public.observer_create_repeat_batches(uuid,uuid,boolean,boolean) to authenticated,service_role;

-- Stats: the flag is part of each aggregate row (paired with/without-model comparisons).
alter table private.stats_llm_usage_daily add column if not exists model_disabled boolean not null default false;
alter table private.stats_llm_usage_daily drop constraint if exists stats_llm_usage_daily_pkey;
alter table private.stats_llm_usage_daily
  add primary key(day_cst, team_id, phase, purpose, card, family, vendor, model_disabled);

create or replace function private.stats_refresh() returns void
language plpgsql set search_path = pg_catalog, public as $$
declare t0 timestamptz := clock_timestamp(); n1 integer; n2 integer;
begin
  -- one refresh at a time; never queue behind locks held by live evaluations
  if not pg_try_advisory_xact_lock(hashtext('private.stats_refresh')) then return; end if;
  perform set_config('lock_timeout', '5s', true);

  delete from private.stats_llm_usage_daily;
  insert into private.stats_llm_usage_daily(day_cst, team_id, team_name, phase, purpose, card, family, vendor,
    runs, scored_runs, failed_runs, llm_runs, proxy_calls, proxy_zero_token_calls, proxy_tokens, proxy_latency_sum_s,
    egress_connections, egress_refused, egress_bytes_up, egress_bytes_down, score_avg, score_min, score_max,
    model_disabled)
  with rt as (
    select id, name from public.teams
    where not is_hidden and name not ilike '%(test)%'
      and leader_id not in (select jsonb_array_elements_text(value)::uuid from public.site_settings where key = 'excluded_accounts')),
  calls as (
    select c.run_id, count(*) calls, count(*) filter (where c.actual_tokens = 0) zero_calls,
           coalesce(sum(c.actual_tokens), 0) tokens,
           coalesce(sum(extract(epoch from c.settled_at - c.created_at)), 0) lat,
           string_agg(distinct regexp_replace(p.base_url, '^https?://([^/:]+).*', '\1'), ',') phost,
           string_agg(distinct array_to_string(p.models, '/'), ',') pmodel
    from private.observer_model_calls c left join private.observer_providers p on p.id = c.provider_id
    group by 1),
  eg as (
    select run_id, sum(connections) conns, sum(refused) refused, sum(bytes_up) up, sum(bytes_down) down,
           string_agg(distinct host, ',') ehost
    from private.observer_run_egress group by 1),
  runs as (
    select r.id, b.team_id, t.name team, ph.slug phase, b.purpose, s.slug card, r.status, r.score,
           (r.created_at at time zone 'Asia/Shanghai')::date day_cst, b.model_disabled,
           c.calls, c.zero_calls, c.tokens, c.lat, c.pmodel, e.conns, e.refused, e.up, e.down,
           coalesce(c.phost, e.ehost) host
    from public.observer_runs r
    join public.observer_batches b on b.id = r.batch_id
    join rt t on t.id = b.team_id
    join public.phases ph on ph.id = b.phase_id
    join public.scenarios s on s.id = r.scenario_id
    left join calls c on c.run_id = r.id
    left join eg e on e.run_id = r.id),
  v as (
    select *,
      case when coalesce(pmodel, '') ~* 'telemetry' or host ~ 'telemetry' then 'non-LLM (telemetry)'
           when host ~ 'kimi|moonshot' or coalesce(pmodel, '') ~* 'kimi|^k3' then 'Kimi/Moonshot'
           when host ~ 'deepseek|taotoken|siliconflow|paratera' or coalesce(pmodel, '') ~* 'deepseek' then 'DeepSeek'
           when host ~ 'bigmodel|bafang' or coalesce(pmodel, '') ~* 'glm' then 'GLM'
           when coalesce(pmodel, '') ~* 'gpt' then 'GPT (relay)'
           when coalesce(pmodel, '') ~* 'grok' then 'Grok (relay)'
           when coalesce(pmodel, '') ~* 'claude' or host ~ 'anthropic' then 'Claude'
           when host ~ 'openai' then 'OpenAI'
           when host is null then '' else 'other' end family,
      case when host is null then ''
           when host ~ 'telemetry' then 'non-LLM (telemetry)'
           when host ~ 'kimi\.com|kimi\.ai' then 'Kimi Coding Plan'
           when host ~ 'moonshot' then 'Moonshot API'
           when host ~ 'deepseek\.com' then 'DeepSeek official'
           when host ~ 'bigmodel' then 'GLM official'
           when host ~ 'openai\.com' then 'OpenAI official'
           when host ~ 'anthropic\.com' then 'Anthropic official'
           else 'relay: ' || host end vendor
    from runs)
  select day_cst, team_id, team, phase, purpose, card, family, vendor,
         count(*), count(*) filter (where status = 'scored'), count(*) filter (where status = 'failed'),
         count(*) filter (where family not in ('', 'non-LLM (telemetry)')),
         coalesce(sum(calls), 0), coalesce(sum(zero_calls), 0), coalesce(sum(tokens), 0), coalesce(sum(lat), 0),
         coalesce(sum(conns), 0), coalesce(sum(refused), 0), coalesce(sum(up), 0), coalesce(sum(down), 0),
         avg(score) filter (where status = 'scored'), min(score) filter (where status = 'scored'),
         max(score) filter (where status = 'scored'), model_disabled
  from v group by 1, 2, 3, 4, 5, 6, 7, 8, model_disabled;
  get diagnostics n1 = row_count;

  delete from private.stats_egress_hosts_daily;
  insert into private.stats_egress_hosts_daily
  select (r.created_at at time zone 'Asia/Shanghai')::date, t.id, t.name, e.host, e.port,
         count(distinct e.run_id), sum(e.connections), sum(e.refused), sum(e.bytes_up), sum(e.bytes_down)
  from private.observer_run_egress e
  join public.observer_runs r on r.id = e.run_id
  join public.observer_batches b on b.id = r.batch_id
  join public.teams t on t.id = b.team_id and not t.is_hidden and t.name not ilike '%(test)%'
  group by 1, 2, 3, 4, 5;
  get diagnostics n2 = row_count;

  insert into private.stats_meta values (true, now(), extract(epoch from clock_timestamp() - t0) * 1000, n1, n2)
  on conflict (id) do update set refreshed_at = excluded.refreshed_at, duration_ms = excluded.duration_ms,
    usage_rows = excluded.usage_rows, egress_rows = excluded.egress_rows;
end $$;
revoke all on function private.stats_refresh() from public, anon, authenticated;

notify pgrst,'reload schema';

-- Aggregate LLM-usage / outcome statistics, refreshed hourly by pg_cron.
-- Additive only: new private tables + one refresh function + one cron job.
-- Aggregates only: no secrets, no keys, no message contents, no per-call rows.
-- Real teams = not hidden, name without "(test)", leader not in site_settings.excluded_accounts.
-- A run "uses an LLM" when it has platform model-proxy calls (before 2026-10-04 09:43Z)
-- or outbound connections in private.observer_run_egress (direct calls afterwards).
-- Proxy calls settled with actual_tokens = 0 are counted as zero-token (most likely failed) calls.

create table if not exists private.stats_llm_usage_daily(
  day_cst date not null,
  team_id uuid not null,
  team_name text not null,
  phase text not null,
  purpose text not null,
  card text not null,
  family text not null,          -- model family; '' = no LLM traffic
  vendor text not null,          -- channel (Kimi Coding Plan, DeepSeek official, relay, ...); '' = none
  runs integer not null,
  scored_runs integer not null,
  failed_runs integer not null,
  llm_runs integer not null,     -- runs with real LLM traffic (excludes non-LLM endpoints behind the proxy)
  proxy_calls bigint not null,
  proxy_zero_token_calls bigint not null,
  proxy_tokens bigint not null,
  proxy_latency_sum_s double precision not null,
  egress_connections bigint not null,
  egress_refused bigint not null,
  egress_bytes_up bigint not null,
  egress_bytes_down bigint not null,
  score_avg double precision,
  score_min double precision,
  score_max double precision,
  primary key(day_cst, team_id, phase, purpose, card, family, vendor));

create table if not exists private.stats_egress_hosts_daily(
  day_cst date not null,
  team_id uuid not null,
  team_name text not null,
  host text not null,
  port integer not null,
  runs integer not null,
  connections bigint not null,
  refused bigint not null,
  bytes_up bigint not null,
  bytes_down bigint not null,
  primary key(day_cst, team_id, host, port));

create table if not exists private.stats_meta(
  id boolean primary key default true check (id),
  refreshed_at timestamptz not null,
  duration_ms double precision not null,
  usage_rows integer not null,
  egress_rows integer not null);

revoke all on private.stats_llm_usage_daily, private.stats_egress_hosts_daily, private.stats_meta
  from public, anon, authenticated;

create or replace function private.stats_refresh() returns void
language plpgsql set search_path = pg_catalog, public as $$
declare t0 timestamptz := clock_timestamp(); n1 integer; n2 integer;
begin
  -- one refresh at a time; never queue behind locks held by live evaluations
  if not pg_try_advisory_xact_lock(hashtext('private.stats_refresh')) then return; end if;
  perform set_config('lock_timeout', '5s', true);

  delete from private.stats_llm_usage_daily;
  insert into private.stats_llm_usage_daily
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
           (r.created_at at time zone 'Asia/Shanghai')::date day_cst,
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
         max(score) filter (where status = 'scored')
  from v group by 1, 2, 3, 4, 5, 6, 7, 8;
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

do $$ begin
  if exists(select 1 from pg_extension where extname = 'pg_cron') then
    perform cron.unschedule(jobid) from cron.job where jobname = 'stats-llm-refresh';
    perform cron.schedule('stats-llm-refresh', '41 * * * *', 'select private.stats_refresh()');
  end if;
end $$;
select private.stats_refresh();

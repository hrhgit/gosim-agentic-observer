-- Additive project platform. No existing phase, score, submission or rule is changed.
-- Participant code never receives service credentials; these RPCs are mediated by
-- the Edge API. Security-definer functions below are explicitly granted at the end.

create table public.observer_phase_settings (
  phase_id uuid primary key references public.phases(id),
  projects_enabled boolean not null default false,
  local_sessions_enabled boolean not null default false,
  runtime_seconds integer not null default 7200 check (runtime_seconds between 10 and 18000),
  daily_batches integer not null default 3 check (daily_batches between 1 and 100),
  model_token_limit bigint not null default 0 check (model_token_limit between 0 and 10000000),
  model_call_limit integer not null default 0 check (model_call_limit between 0 and 10000),
  model_concurrency integer not null default 1 check (model_concurrency between 1 and 4)
);

create table public.observer_projects (
  id uuid primary key default gen_random_uuid(),
  team_id uuid not null references public.teams(id),
  owner_id uuid not null references public.profiles(id),
  title text not null check (length(title) between 1 and 100),
  created_at timestamptz not null default now()
);

create table public.observer_revisions (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.observer_projects(id),
  source_kind text not null check (source_kind in ('repository','zip')),
  source_location text not null check (length(source_location) between 1 and 1024),
  source_digest text check (source_digest ~ '^[0-9a-f]{64}$'),
  source_commit text check (source_commit ~ '^[0-9a-f]{40,64}$'),
  repository text,
  status text not null default 'queued' check (status in ('queued','preparing','reviewable','approved','failed')),
  manifest jsonb,
  adapter_files jsonb not null default '{}'::jsonb,
  approval_digest text check (approval_digest ~ '^[0-9a-f]{64}$'),
  public_test jsonb not null default '{}'::jsonb,
  explanation text not null default '',
  error text not null default '',
  approved_by uuid references public.profiles(id),
  approved_at timestamptz,
  created_at timestamptz not null default now(),
  check (status not in ('reviewable','approved') or (
    source_digest is not null and approval_digest is not null and manifest is not null
    and coalesce(manifest->>'image' ~ '@sha256:[0-9a-f]{64}$', false)
    and coalesce(public_test->>'passed' = 'true', false)
  )),
  check (status <> 'approved' or (approved_at is not null and approved_by is not null))
);

create table public.observer_batches (
  id uuid primary key default gen_random_uuid(),
  team_id uuid not null references public.teams(id),
  user_id uuid not null references public.profiles(id),
  phase_id uuid not null references public.phases(id),
  revision_id uuid references public.observer_revisions(id),
  purpose text not null default 'formal' check (purpose in ('formal','adaptation','preview')),
  mode text not null check (mode in ('project','local')),
  status text not null default 'queued' check (status in ('queued','running','scored','failed','cancelled')),
  score double precision,
  created_at timestamptz not null default now(),
  finished_at timestamptz,
  check ((mode = 'project') = (revision_id is not null)),
  check (status <> 'scored' or (score is not null and finished_at is not null))
);

create table public.observer_runs (
  id uuid primary key default gen_random_uuid(),
  batch_id uuid not null references public.observer_batches(id),
  scenario_id uuid not null references public.scenarios(id),
  status text not null default 'queued' check (status in ('queued','starting','ready','running','awaiting_csv','scored','failed','cancelled')),
  error text not null default '',
  score double precision,
  result_path text,
  decisions_digest text check (decisions_digest ~ '^[0-9a-f]{64}$'),
  score_summary jsonb,
  created_at timestamptz not null default now(),
  started_at timestamptz,
  finished_at timestamptz,
  unique(batch_id, scenario_id)
);
create index observer_runs_queue on public.observer_runs(created_at) where status = 'queued';
create index observer_batches_team on public.observer_batches(team_id, phase_id, created_at);
create index observer_revisions_project on public.observer_revisions(project_id, created_at);

-- Private job identity, capabilities, protocol data and budgets are never selectable
-- by participants. Only hashes of random capabilities are retained.
create table private.observer_sessions (
  run_id uuid primary key references public.observer_runs(id),
  participant_hash bytea not null unique,
  engine_hash bytea not null unique,
  expires_at timestamptz not null,
  deadline_at timestamptz,
  ready_at timestamptz,
  publication jsonb,
  next_sequence integer not null default 1,
  token_limit bigint not null,
  call_limit integer not null,
  concurrency_limit integer not null,
  tokens_used bigint not null default 0,
  tokens_reserved bigint not null default 0,
  calls_used integer not null default 0,
  calls_active integer not null default 0
);
create table private.observer_messages (
  run_id uuid not null references private.observer_sessions(run_id),
  sequence integer not null check (sequence > 0),
  observation jsonb not null,
  response jsonb,
  committed jsonb,
  created_at timestamptz not null default now(),
  primary key(run_id, sequence)
);
create table private.observer_providers (
  id uuid primary key default gen_random_uuid(),
  team_id uuid references public.teams(id),
  name text not null,
  base_url text not null,
  -- AES-GCM ciphertext, encrypted by the Edge application key; never returned to a project.
  encrypted_key text not null,
  models text[] not null,
  allow_http boolean not null default false,
  enabled boolean not null default false,
  daily_token_limit bigint not null default 0 check (daily_token_limit >= 0),
  created_at timestamptz not null default now()
);
create table private.observer_provider_usage (
  provider_id uuid not null references private.observer_providers(id),
  usage_day date not null,
  tokens_used bigint not null default 0,
  tokens_reserved bigint not null default 0,
  primary key(provider_id, usage_day)
);
create table private.observer_model_calls (
  id uuid primary key,
  run_id uuid not null references private.observer_sessions(run_id),
  provider_id uuid not null references private.observer_providers(id),
  usage_day date not null,
  request_digest text not null check (request_digest ~ '^[0-9a-f]{64}$'),
  reserved_tokens bigint not null check (reserved_tokens > 0),
  actual_tokens bigint,
  status text not null default 'reserved' check (status in ('reserved','settled')),
  created_at timestamptz not null default now(),
  settled_at timestamptz
);

create function public.observer_team(p_team uuid) returns boolean
language sql stable security definer set search_path = public, pg_temp as $$
  select exists(select 1 from public.profiles where id = auth.uid()
    and team_id = p_team and not is_banned)
$$;

alter table public.observer_phase_settings enable row level security;
alter table public.observer_projects enable row level security;
alter table public.observer_revisions enable row level security;
alter table public.observer_batches enable row level security;
alter table public.observer_runs enable row level security;
create policy observer_phase_read on public.observer_phase_settings for select using (true);
create policy observer_project_read on public.observer_projects for select to authenticated
  using (public.observer_team(team_id) or public.is_admin());
create policy observer_revision_read on public.observer_revisions for select to authenticated
  using (exists(select 1 from public.observer_projects p where p.id = project_id));
create policy observer_batch_read on public.observer_batches for select to authenticated
  using (public.observer_team(team_id) or public.is_admin());
create policy observer_run_read on public.observer_runs for select to authenticated
  using (exists(select 1 from public.observer_batches b where b.id = batch_id));
revoke all on public.observer_phase_settings, public.observer_projects, public.observer_revisions,
  public.observer_batches, public.observer_runs from public, anon, authenticated;
grant select on public.observer_phase_settings to anon, authenticated;
grant select on public.observer_projects, public.observer_revisions,
  public.observer_batches, public.observer_runs to authenticated;
grant all on public.observer_phase_settings, public.observer_projects, public.observer_revisions,
  public.observer_batches, public.observer_runs to service_role;
revoke all on private.observer_sessions, private.observer_messages, private.observer_providers,
  private.observer_provider_usage, private.observer_model_calls from public, anon, authenticated;

create function public.observer_create_project(p_title text, p_source_kind text, p_source_location text)
returns uuid language plpgsql security definer set search_path = public, pg_temp as $$
declare v_team uuid; v_project uuid; v_revision uuid;
begin
  perform private.assert_not_banned();
  select team_id into v_team from public.profiles where id = auth.uid() for update;
  if v_team is null then raise exception 'team_required'; end if;
  if not exists(select 1 from public.observer_phase_settings c join public.phases p on p.id = c.phase_id
    where c.projects_enabled and p.is_active and (p.ends_at is null or now() < p.ends_at)) then
    raise exception 'projects_not_enabled';
  end if;
  if p_source_kind = 'repository' then
    if p_source_location is null or p_source_location !~ '^https://github.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/?$' then
      raise exception 'invalid_repository_url';
    end if;
  elsif p_source_kind = 'zip' then
    if p_source_location is null or p_source_location !~ ('^' || v_team::text || '/[0-9a-f-]{36}/source[.]zip$') then
      raise exception 'invalid_upload_path';
    end if;
  else raise exception 'invalid_source_kind';
  end if;
  -- Serialize team-level admission; a pending queue cannot grow without bound.
  perform 1 from public.teams where id = v_team for update;
  if (select count(*) from public.observer_revisions r join public.observer_projects p on p.id=r.project_id
      where p.team_id=v_team and r.created_at >= (date_trunc('day',now() at time zone 'UTC') at time zone 'UTC')) >= 10 then
    raise exception 'preparation_daily_limit';
  end if;
  if (select count(*) from public.observer_revisions r join public.observer_projects p on p.id=r.project_id
      where p.team_id=v_team and r.status in ('queued','preparing')) >= 3 then
    raise exception 'preparation_limit';
  end if;
  insert into public.observer_projects(team_id, owner_id, title)
    values(v_team, auth.uid(), trim(p_title)) returning id into v_project;
  insert into public.observer_revisions(project_id, source_kind, source_location)
    values(v_project, p_source_kind, p_source_location) returning id into v_revision;
  return v_revision;
end $$;

create function public.observer_approve_revision(p_revision uuid, p_digest text)
returns void language plpgsql security definer set search_path = public, pg_temp as $$
declare v public.observer_revisions;
begin
  perform private.assert_not_banned();
  select r.* into v from public.observer_revisions r join public.observer_projects p on p.id=r.project_id
    where r.id=p_revision and public.observer_team(p.team_id) for update of r;
  if not found then raise exception 'revision_not_found'; end if;
  if p_digest is null or v.approval_digest is distinct from p_digest then raise exception 'stale_approval'; end if;
  if v.status = 'approved' then return; end if;
  if v.status <> 'reviewable' then raise exception 'revision_not_ready'; end if;
  update public.observer_revisions set status='approved', approved_by=auth.uid(), approved_at=now() where id=p_revision;
  perform private.audit('observer.approve', jsonb_build_object('revision_id',p_revision,'digest',p_digest));
end $$;

-- Approved revisions are immutable, including for backend retries.
create function private.observer_revision_immutable() returns trigger language plpgsql as $$
begin
  if old.status = 'approved' and new is distinct from old then raise exception 'approved_revision_immutable'; end if;
  return new;
end $$;
create trigger observer_revision_immutable before update on public.observer_revisions
  for each row execute function private.observer_revision_immutable();

create function public.observer_create_batch(p_phase uuid, p_revision uuid default null)
returns uuid language plpgsql security definer set search_path = public, pg_temp as $$
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
  if p_revision is null then
    if not v_config.local_sessions_enabled then raise exception 'local_sessions_disabled'; end if;
  else
    if not v_config.projects_enabled then raise exception 'projects_not_enabled'; end if;
    if not exists(select 1 from public.observer_revisions r join public.observer_projects p on p.id=r.project_id
      where r.id=p_revision and r.status='approved' and p.team_id=v_team) then raise exception 'revision_not_approved'; end if;
  end if;
  if (select count(*) from public.observer_batches where team_id=v_team and phase_id=p_phase and purpose='formal'
      and created_at >= (date_trunc('day', now() at time zone 'UTC') at time zone 'UTC')) >= v_config.daily_batches then
    raise exception 'daily_limit';
  end if;
  if exists(select 1 from public.observer_batches where team_id=v_team and purpose='formal' and status in ('queued','running')) then
    raise exception 'batch_already_active';
  end if;
  if not exists(select 1 from public.phase_scenarios where phase_id=p_phase) then raise exception 'no_scenarios'; end if;
  insert into public.observer_batches(team_id,user_id,phase_id,revision_id,mode)
    values(v_team,auth.uid(),p_phase,p_revision,case when p_revision is null then 'local' else 'project' end)
    returning id into v_id;
  insert into public.observer_runs(batch_id,scenario_id)
    select v_id,scenario_id from public.phase_scenarios where phase_id=p_phase;
  return v_id;
end $$;

-- Service-only capability management. Tokens come from a CSPRNG in the API and
-- are delivered once to the matching authenticated participant/job.
create function public.observer_open_session(p_run uuid, p_participant_token text, p_engine_token text)
returns void language plpgsql security definer set search_path = public, pg_temp as $$
declare v_run public.observer_runs; v_config public.observer_phase_settings; v_purpose text;
begin
  if p_participant_token is null or length(p_participant_token) < 40 or length(p_participant_token)>200
     or p_engine_token is null or length(p_engine_token)<40 or length(p_engine_token)>200
     or p_participant_token = p_engine_token then raise exception 'invalid_capability'; end if;
  select * into v_run from public.observer_runs where id=p_run for update;
  if not found or v_run.status <> 'queued' then raise exception 'run_not_queued'; end if;
  select c.* into v_config from public.observer_phase_settings c join public.observer_batches b on b.phase_id=c.phase_id
    where b.id=v_run.batch_id;
  select purpose into v_purpose from public.observer_batches where id=v_run.batch_id;
  if v_purpose='adaptation' then
    v_config.runtime_seconds:=1800;v_config.model_token_limit:=65536;v_config.model_call_limit:=1;v_config.model_concurrency:=1;
  elsif v_purpose='preview' then
    v_config.runtime_seconds:=least(v_config.runtime_seconds,300);
  end if;
  insert into private.observer_sessions(run_id,participant_hash,engine_hash,expires_at,token_limit,call_limit,concurrency_limit)
    values(p_run,sha256(convert_to(p_participant_token,'UTF8')),sha256(convert_to(p_engine_token,'UTF8')),
      now()+make_interval(secs=>v_config.runtime_seconds+1800),v_config.model_token_limit,
      v_config.model_call_limit,v_config.model_concurrency);
  update public.observer_runs set status='starting' where id=p_run;
  update public.observer_batches set status='running' where id=v_run.batch_id;
end $$;

create function private.observer_capability(p_run uuid, p_token text, p_scope text,p_enforce_deadline boolean default true)
returns private.observer_sessions language plpgsql security definer set search_path = public, pg_temp as $$
declare v private.observer_sessions;
begin
  select s.* into v from private.observer_sessions s join public.observer_runs r on r.id=s.run_id
    where s.run_id=p_run and s.expires_at>clock_timestamp()
      and r.status in ('starting','ready','running') for update of s;
  if not found or p_token is null or length(p_token)>200 or p_scope is null or p_scope not in ('participant','engine')
     or sha256(convert_to(p_token,'UTF8')) is distinct from
        (case when p_scope='engine' then v.engine_hash else v.participant_hash end) then
    raise exception 'invalid_or_expired_capability';
  end if;
  if p_enforce_deadline and v.deadline_at is not null and v.deadline_at<=clock_timestamp() then raise exception 'session_deadline'; end if;
  return v;
end $$;

create function public.observer_publish_initial(p_run uuid,p_token text,p_publication jsonb)
returns void language plpgsql security definer set search_path = public, pg_temp as $$
declare v private.observer_sessions;
begin
  v:=private.observer_capability(p_run,p_token,'engine');
  if p_publication is null or jsonb_typeof(p_publication)<>'object' or octet_length(p_publication::text)>16777216 then
    raise exception 'invalid_publication'; end if;
  if v.publication is not null and v.publication is distinct from p_publication then raise exception 'publication_conflict'; end if;
  update private.observer_sessions set publication=p_publication where run_id=p_run;
end $$;

create function public.observer_ready(p_run uuid,p_token text)
returns void language plpgsql security definer set search_path = public, pg_temp as $$
declare v private.observer_sessions;
begin
  v:=private.observer_capability(p_run,p_token,'participant');
  if v.publication is null then raise exception 'publication_not_ready'; end if;
  update private.observer_sessions set ready_at=coalesce(ready_at,clock_timestamp()) where run_id=p_run;
  update public.observer_runs set status='ready' where id=p_run and status='starting';
end $$;

create function public.observer_begin(p_run uuid,p_token text)
returns timestamptz language plpgsql security definer set search_path = public, pg_temp as $$
declare v private.observer_sessions; v_seconds integer; v_deadline timestamptz;
begin
  v:=private.observer_capability(p_run,p_token,'engine');
  if v.ready_at is null then raise exception 'participant_not_ready'; end if;
  if v.deadline_at is not null then return v.deadline_at; end if;
  select case when b.purpose='preview' then least(c.runtime_seconds,300) else c.runtime_seconds end
    into v_seconds from public.observer_runs r join public.observer_batches b on b.id=r.batch_id
    join public.observer_phase_settings c on c.phase_id=b.phase_id where r.id=p_run;
  v_deadline:=least(clock_timestamp()+make_interval(secs=>v_seconds),v.expires_at);
  update private.observer_sessions set deadline_at=v_deadline where run_id=p_run;
  update public.observer_runs set status='running',started_at=clock_timestamp() where id=p_run;
  return v_deadline;
end $$;

create function public.observer_publish_step(p_run uuid,p_token text,p_sequence integer,p_observation jsonb)
returns void language plpgsql security definer set search_path = public, pg_temp as $$
declare v private.observer_sessions; v_previous jsonb;
begin
  v:=private.observer_capability(p_run,p_token,'engine');
  if v.deadline_at is null or p_sequence is distinct from v.next_sequence then raise exception 'step_out_of_order'; end if;
  if p_observation is null or jsonb_typeof(p_observation)<>'object' or octet_length(p_observation::text)>16777216 then
    raise exception 'invalid_observation'; end if;
  select observation into v_previous from private.observer_messages where run_id=p_run and sequence=p_sequence;
  if found then
    if v_previous is distinct from p_observation then raise exception 'observation_conflict'; end if;
    return;
  end if;
  insert into private.observer_messages(run_id,sequence,observation) values(p_run,p_sequence,p_observation);
end $$;

create function public.observer_poll(p_run uuid,p_token text,p_scope text default 'participant',p_initialized boolean default false)
returns jsonb language plpgsql security definer set search_path = public, pg_temp as $$
declare v private.observer_sessions; v_message private.observer_messages;
begin
  v:=private.observer_capability(p_run,p_token,p_scope);
  select * into v_message from private.observer_messages where run_id=p_run and sequence=v.next_sequence;
  if p_scope='engine' then
    return jsonb_build_object('ready',v.ready_at is not null,'sequence',v.next_sequence,'response',v_message.response);
  end if;
  return jsonb_build_object('publication',case when p_initialized then null else v.publication end,'sequence',v.next_sequence,
    'observation',v_message.observation,'action_received',v_message.response is not null,
    'deadline_at',v.deadline_at);
end $$;

create function public.observer_respond(p_run uuid,p_token text,p_sequence integer,p_response jsonb)
returns void language plpgsql security definer set search_path = public, pg_temp as $$
declare v private.observer_sessions; v_message private.observer_messages;
begin
  v:=private.observer_capability(p_run,p_token,'participant');
  if p_sequence is null or p_response is null or jsonb_typeof(p_response)<>'object'
     or octet_length(p_response::text)>524288
     or p_response->>'protocol_version' is distinct from 'participant-agent-protocol-v2'
     or p_response->>'message_type' is distinct from 'decision_response'
     or p_response->'decision_sequence' is distinct from to_jsonb(p_sequence) then raise exception 'invalid_response'; end if;
  select * into v_message from private.observer_messages where run_id=p_run and sequence=p_sequence;
  if not found then raise exception 'step_not_published'; end if;
  -- A retried identical response remains successful after the engine advances.
  if v_message.response is not null then
    if v_message.response is distinct from p_response then raise exception 'response_conflict'; end if;
    return;
  end if;
  if p_sequence<>v.next_sequence then raise exception 'step_out_of_order'; end if;
  update private.observer_messages set response=p_response where run_id=p_run and sequence=p_sequence;
end $$;

create function public.observer_commit_step(p_run uuid,p_token text,p_sequence integer,p_committed jsonb)
returns void language plpgsql security definer set search_path = public, pg_temp as $$
declare v private.observer_sessions; v_message private.observer_messages;
begin
  -- The trusted simulator may persist a decision made before the deadline after
  -- that deadline. No participant action is accepted through this endpoint.
  v:=private.observer_capability(p_run,p_token,'engine',false);
  select * into v_message from private.observer_messages where run_id=p_run and sequence=p_sequence;
  if not found or v_message.response is null or p_committed is null or jsonb_typeof(p_committed)<>'object'
    or octet_length(p_committed::text)>524288 then raise exception 'invalid_commit'; end if;
  if v_message.committed is not null then
    if v_message.committed is distinct from p_committed then raise exception 'commit_conflict'; end if;
    return;
  end if;
  if p_sequence<>v.next_sequence then raise exception 'step_out_of_order'; end if;
  update private.observer_messages set committed=p_committed where run_id=p_run and sequence=p_sequence;
  update private.observer_sessions set next_sequence=next_sequence+1 where run_id=p_run;
end $$;

create function public.observer_reserve_model(p_run uuid,p_token text,p_call uuid,p_provider uuid,
  p_model text,p_digest text,p_tokens bigint)
returns jsonb language plpgsql security definer set search_path = public, pg_temp as $$
declare v private.observer_sessions; v_provider private.observer_providers; v_call private.observer_model_calls;
  v_usage private.observer_provider_usage; v_day date := (clock_timestamp() at time zone 'UTC')::date; v_team uuid;
begin
  v:=private.observer_capability(p_run,p_token,'participant');
  if p_call is null or p_digest is null or p_digest !~ '^[0-9a-f]{64}$' or p_tokens is null
    or p_tokens not between 1 and 1000000 then raise exception 'invalid_reservation'; end if;
  select * into v_call from private.observer_model_calls where id=p_call;
  if found then
    if v_call.run_id<>p_run or v_call.provider_id<>p_provider or v_call.request_digest<>p_digest
       or v_call.reserved_tokens<>p_tokens then raise exception 'request_id_conflict'; end if;
    -- Never send upstream a second time, even if the first HTTP response was lost.
    return jsonb_build_object('reserved',false,'status',v_call.status);
  end if;
  select b.team_id into v_team from public.observer_batches b join public.observer_runs r on r.batch_id=b.id where r.id=p_run;
  select * into v_provider from private.observer_providers where id=p_provider and enabled
    and (team_id is null or team_id=v_team) and p_model=any(models);
  if not found then raise exception 'model_not_available'; end if;
  if v.tokens_used+v.tokens_reserved+p_tokens>v.token_limit or v.calls_used>=v.call_limit
    or v.calls_active>=v.concurrency_limit then raise exception 'run_model_quota'; end if;
  insert into private.observer_provider_usage(provider_id,usage_day) values(p_provider,v_day) on conflict do nothing;
  select * into v_usage from private.observer_provider_usage where provider_id=p_provider and usage_day=v_day for update;
  if v_usage.tokens_used+v_usage.tokens_reserved+p_tokens>v_provider.daily_token_limit then raise exception 'provider_model_quota'; end if;
  insert into private.observer_model_calls(id,run_id,provider_id,usage_day,request_digest,reserved_tokens)
    values(p_call,p_run,p_provider,v_day,p_digest,p_tokens);
  update private.observer_sessions set tokens_reserved=tokens_reserved+p_tokens,
    calls_used=calls_used+1,calls_active=calls_active+1 where run_id=p_run;
  update private.observer_provider_usage set tokens_reserved=tokens_reserved+p_tokens where provider_id=p_provider and usage_day=v_day;
  return jsonb_build_object('reserved',true,'base_url',v_provider.base_url,
    'encrypted_key',v_provider.encrypted_key,'allow_http',v_provider.allow_http);
end $$;

-- This endpoint must only be called by the trusted proxy, never by a job/participant.
-- NULL usage conservatively consumes the entire reservation after timeout/network loss.
create function public.observer_settle_model(p_call uuid,p_actual_tokens bigint default null)
returns void language plpgsql security definer set search_path = public, pg_temp as $$
declare v private.observer_model_calls; v_actual bigint;
begin
  select * into v from private.observer_model_calls where id=p_call;
  if not found then raise exception 'call_not_found'; end if;
  -- Same lock ordering as reservation: session, then provider. Reload after locking.
  perform 1 from private.observer_sessions where run_id=v.run_id for update;
  select * into v from private.observer_model_calls where id=p_call for update;
  if v.status='settled' then return; end if;
  v_actual:=coalesce(p_actual_tokens,v.reserved_tokens);
  if v_actual<0 or v_actual>v.reserved_tokens then raise exception 'invalid_actual_usage'; end if;
  update private.observer_sessions set tokens_reserved=tokens_reserved-v.reserved_tokens,
    tokens_used=tokens_used+v_actual,calls_active=calls_active-1 where run_id=v.run_id;
  update private.observer_provider_usage set tokens_reserved=tokens_reserved-v.reserved_tokens,
    tokens_used=tokens_used+v_actual where provider_id=v.provider_id and usage_day=v.usage_day;
  update private.observer_model_calls set status='settled',actual_tokens=v_actual,settled_at=now() where id=p_call;
end $$;

create function private.observer_finalize_batch(p_batch uuid)
returns void language plpgsql security definer set search_path = public, pg_temp as $$
begin
  perform 1 from public.observer_batches where id=p_batch for update;
  if exists(select 1 from public.observer_runs where batch_id=p_batch and status in ('failed','cancelled')) then
    update public.observer_batches set status='failed',finished_at=now() where id=p_batch and status in ('queued','running');
  elsif exists(select 1 from public.observer_runs where batch_id=p_batch)
    and not exists(select 1 from public.observer_runs where batch_id=p_batch and status<>'scored') then
    update public.observer_batches set status='scored',
      score=(select avg(score) from public.observer_runs where batch_id=p_batch),finished_at=now()
      where id=p_batch and status in ('queued','running');
  end if;
end $$;

create function public.observer_finish_run(p_run uuid,p_token text,p_summary jsonb,p_decisions_digest text,p_result_path text)
returns void language plpgsql security definer set search_path = public, pg_temp as $$
declare v private.observer_sessions; v_run public.observer_runs; v_mode text; v_score double precision;
begin
  select * into v from private.observer_sessions where run_id=p_run for update;
  if not found or p_token is null or v.engine_hash is distinct from sha256(convert_to(p_token,'UTF8'))
    or v.expires_at<=clock_timestamp() then raise exception 'invalid_or_expired_capability'; end if;
  select * into v_run from public.observer_runs where id=p_run for update;
  if p_summary is null or jsonb_typeof(p_summary)<>'object' or octet_length(p_summary::text)>65536
    or p_decisions_digest is null or p_decisions_digest !~ '^[0-9a-f]{64}$'
    or p_result_path is null or length(p_result_path) not between 1 and 1024 then raise exception 'invalid_result'; end if;
  if v_run.status in ('scored','awaiting_csv') then
    if v_run.score_summary is distinct from p_summary or v_run.decisions_digest is distinct from p_decisions_digest
      or v_run.result_path is distinct from p_result_path then raise exception 'result_conflict'; end if;
    return;
  end if;
  if v_run.status<>'running' then raise exception 'run_not_running'; end if;
  v_score:=(p_summary->'score'->>'total')::double precision;
  if v_score is null or v_score in ('NaN'::double precision,'Infinity'::double precision,'-Infinity'::double precision) then
    raise exception 'invalid_score'; end if;
  select mode into v_mode from public.observer_batches where id=v_run.batch_id;
  update public.observer_runs set status=case when v_mode='local' then 'awaiting_csv' else 'scored' end,
    score=v_score,score_summary=p_summary,decisions_digest=p_decisions_digest,result_path=p_result_path,finished_at=now()
    where id=p_run;
  perform private.observer_finalize_batch(v_run.batch_id);
end $$;

-- The Edge endpoint hashes the bounded uploaded CSV bytes. It never accepts a
-- user-supplied digest as evidence of an uploaded file.
create function public.observer_accept_csv(p_run uuid,p_user uuid,p_digest text)
returns void language plpgsql security definer set search_path = public, pg_temp as $$
declare v public.observer_runs; v_batch public.observer_batches;
begin
  select * into v from public.observer_runs where id=p_run for update;
  select * into v_batch from public.observer_batches where id=v.batch_id;
  if not found or v_batch.mode<>'local' or not exists(select 1 from public.profiles where id=p_user
    and team_id=v_batch.team_id and not is_banned) then raise exception 'run_not_found'; end if;
  if p_digest is null or v.decisions_digest is distinct from p_digest then raise exception 'csv_does_not_match_session'; end if;
  if v.status='scored' then return; end if;
  if v.status<>'awaiting_csv' then raise exception 'session_not_finished'; end if;
  update public.observer_runs set status='scored' where id=p_run;
  perform private.observer_finalize_batch(v.batch_id);
end $$;

create function public.observer_fail_run(p_run uuid,p_token text,p_error text)
returns void language plpgsql security definer set search_path = public, pg_temp as $$
declare v private.observer_sessions; v_batch uuid;
begin
  select * into v from private.observer_sessions where run_id=p_run for update;
  if not found or p_token is null or v.engine_hash is distinct from sha256(convert_to(p_token,'UTF8'))
    or v.expires_at<=clock_timestamp() then raise exception 'invalid_or_expired_capability'; end if;
  update public.observer_runs set status='failed',error=left(coalesce(p_error,'evaluation_failed'),1000),finished_at=now()
    where id=p_run and status in ('starting','ready','running') returning batch_id into v_batch;
  if v_batch is not null then perform private.observer_finalize_batch(v_batch); end if;
end $$;

create function public.observer_run_status(p_run uuid,p_token text)
returns jsonb language plpgsql security definer set search_path = public, pg_temp as $$
declare v private.observer_sessions; v_run public.observer_runs; v_hash bytea;
begin
  select * into v from private.observer_sessions where run_id=p_run;
  v_hash:=sha256(convert_to(p_token,'UTF8'));
  if not found or p_token is null or (v_hash<>v.engine_hash and v_hash<>v.participant_hash) then
    raise exception 'invalid_or_expired_capability'; end if;
  select * into v_run from public.observer_runs where id=p_run;
  -- Status is the only readable field after termination/expiration; no model
  -- use, observations, results, or writable capability is returned.
  return jsonb_build_object('status',v_run.status,'expired',v.expires_at<=clock_timestamp());
end $$;

create function public.observer_reconcile_sessions()
returns integer language plpgsql security definer set search_path = public, pg_temp as $$
declare v record; n integer:=0;
begin
  for v in select id from private.observer_model_calls where status='reserved'
    and created_at<now()-interval '5 minutes' order by created_at limit 100
  loop
    perform public.observer_settle_model(v.id,null);
    n:=n+1;
  end loop;
  -- Queued/never-dispatched runs and missing job callbacks must not lock a team
  -- out forever. Expiration is an infrastructure failure, never a scored zero.
  for v in select r.id,r.batch_id from public.observer_runs r
    left join private.observer_sessions s on s.run_id=r.id
    where (r.status='queued' and r.created_at<now()-interval '30 minutes')
      or (r.status in ('starting','ready','running') and s.expires_at<=now())
    order by r.created_at limit 100
  loop
    update public.observer_runs set status='failed',error='evaluation_expired',finished_at=now()
      where id=v.id and status in ('queued','starting','ready','running');
    perform private.observer_finalize_batch(v.batch_id);
    n:=n+1;
  end loop;
  return n;
end $$;

create function public.observer_leaderboard(p_phase uuid,p_limit integer default 100)
returns table(rank bigint,team_id uuid,team_name text,batch_id uuid,score double precision,finished_at timestamptz)
language sql stable security definer set search_path = public, pg_temp as $$
  with best as (
    select distinct on(b.team_id) b.team_id,t.name,b.id,b.score,b.finished_at,b.created_at
    from public.observer_batches b join public.teams t on t.id=b.team_id join public.phases p on p.id=b.phase_id
    where b.phase_id=p_phase and b.purpose='formal' and b.status='scored' and p.is_active
      and (public.is_admin() or (not t.is_hidden and p.leaderboard_mode in ('live','published')))
    order by b.team_id,b.score desc,b.created_at
  )
  select rank() over(order by b.score desc),b.team_id,b.name,b.id,b.score,b.finished_at
    from best b order by b.score desc,b.created_at limit greatest(1,least(coalesce(p_limit,100),1000))
$$;

-- No default PUBLIC execute privileges on these APIs, including future PostgREST
-- schema exposure changes. Only the three participant RPCs accept user JWTs.
do $$
declare f record;
begin
  for f in select p.oid::regprocedure as signature from pg_proc p join pg_namespace n on n.oid=p.pronamespace
    where n.nspname='public' and p.proname like 'observer_%'
  loop
    execute format('revoke all on function %s from public, anon, authenticated',f.signature);
    execute format('grant execute on function %s to service_role',f.signature);
  end loop;
end $$;
grant execute on function public.observer_create_project(text,text,text),
  public.observer_approve_revision(uuid,text), public.observer_create_batch(uuid,uuid),
  public.observer_team(uuid) to authenticated;
grant execute on function public.observer_leaderboard(uuid,integer) to anon,authenticated;

revoke all on function private.observer_capability(uuid,text,text,boolean),
  private.observer_revision_immutable(),private.observer_finalize_batch(uuid) from public, anon, authenticated;

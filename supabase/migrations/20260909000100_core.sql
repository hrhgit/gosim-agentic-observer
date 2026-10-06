-- Agent Observer competition platform: core schema.
-- Apply with `supabase db push` (or paste into the SQL editor). Idempotent where practical.

-- gen_random_uuid() is built into PostgreSQL 13+; no extension needed.
create schema if not exists private;
revoke all on schema private from public;

-- ---------------------------------------------------------------------------
-- enums
-- ---------------------------------------------------------------------------
do $$ begin
  create type public.submission_kind as enum ('results', 'agent');
exception when duplicate_object then null; end $$;
do $$ begin
  create type public.submission_status as enum ('queued', 'running', 'scored', 'invalid', 'failed', 'cancelled');
exception when duplicate_object then null; end $$;
do $$ begin
  create type public.leaderboard_mode as enum ('live', 'frozen', 'hidden', 'published');
exception when duplicate_object then null; end $$;

-- ---------------------------------------------------------------------------
-- helpers
-- ---------------------------------------------------------------------------
create or replace function private.new_invite_code() returns text language sql volatile as $$
  select string_agg(substr('ABCDEFGHJKLMNPQRSTUVWXYZ23456789', (floor(random() * 32))::int + 1, 1), '')
  from generate_series(1, 8);
$$;

create or replace function private.slugify(p text) returns text language sql immutable as $$
  select coalesce(nullif(trim(both '-' from regexp_replace(lower(p), '[^a-z0-9一-鿿]+', '-', 'g')), ''), substr(md5(random()::text), 1, 6));
$$;

-- ---------------------------------------------------------------------------
-- tables
-- ---------------------------------------------------------------------------
create table if not exists public.site_settings (
  key text primary key,
  value jsonb not null default '{}'::jsonb
);

create table if not exists public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  email text,
  name text not null default '',
  github text not null default '',
  affiliation text not null default '',
  role text not null default '',
  looking_for_team boolean not null default false,
  locale text not null default 'zh',
  is_admin boolean not null default false,
  is_banned boolean not null default false,
  created_at timestamptz not null default now()
);

create table if not exists public.teams (
  id uuid primary key default gen_random_uuid(),
  name text not null unique,
  slug text not null unique,
  leader_id uuid not null references public.profiles(id),
  invite_code text not null unique default private.new_invite_code(),
  project_idea text not null default '',
  github_repo text not null default '',
  max_size integer not null default 4 check (max_size between 1 and 8),
  is_locked boolean not null default false,
  is_hidden boolean not null default false,
  created_at timestamptz not null default now()
);

alter table public.profiles add column if not exists team_id uuid references public.teams(id) on delete set null;
create index if not exists profiles_team_idx on public.profiles(team_id);

create table if not exists public.scenarios (
  id uuid primary key default gen_random_uuid(),
  slug text not null unique,
  name text not null,
  description text not null default '',
  weather_public boolean not null default true,
  tiles_public boolean not null default true,
  is_active boolean not null default true,
  n_slots integer not null default 0,
  n_nights integer not null default 0,
  n_tiles integer not null default 0,
  seed integer,
  checksum text not null default '',
  created_at timestamptz not null default now()
);

create table if not exists public.phases (
  id uuid primary key default gen_random_uuid(),
  slug text not null unique,
  name_en text not null,
  name_zh text not null,
  description_en text not null default '',
  description_zh text not null default '',
  sort_order integer not null default 0,
  starts_at timestamptz,
  ends_at timestamptz,
  allow_results boolean not null default true,
  allow_agents boolean not null default true,
  daily_limit integer not null default 10,
  leaderboard_mode public.leaderboard_mode not null default 'live',
  counts_for_final boolean not null default false,
  is_active boolean not null default true,
  created_at timestamptz not null default now()
);

create table if not exists public.phase_scenarios (
  phase_id uuid not null references public.phases(id) on delete cascade,
  scenario_id uuid not null references public.scenarios(id) on delete cascade,
  primary key (phase_id, scenario_id)
);

create table if not exists public.submissions (
  id bigint generated always as identity primary key,
  team_id uuid not null references public.teams(id),
  user_id uuid not null references public.profiles(id),
  phase_id uuid not null references public.phases(id),
  scenario_id uuid references public.scenarios(id),
  kind public.submission_kind not null,
  title text not null default '',
  notes text not null default '',
  storage_path text not null,
  original_filename text not null default '',
  sha256 text not null default '',
  status public.submission_status not null default 'queued',
  score double precision,
  science_score double precision,
  completion double precision,
  uniformity double precision,
  metrics jsonb not null default '{}'::jsonb,
  error text not null default '',
  is_excluded boolean not null default false,
  claimed_by text not null default '',
  created_at timestamptz not null default now(),
  started_at timestamptz,
  finished_at timestamptz
);
create index if not exists submissions_team_idx on public.submissions(team_id, created_at desc);
create index if not exists submissions_phase_status_idx on public.submissions(phase_id, status);
create index if not exists submissions_queue_idx on public.submissions(created_at) where status = 'queued';

create table if not exists public.evaluations (
  id bigint generated always as identity primary key,
  submission_id bigint not null references public.submissions(id) on delete cascade,
  scenario_id uuid not null references public.scenarios(id),
  status public.submission_status not null default 'queued',
  score double precision,
  science_score double precision,
  completion double precision,
  uniformity double precision,
  report_path text not null default '',
  decisions_path text not null default '',
  log_path text not null default '',
  summary jsonb not null default '{}'::jsonb,
  error text not null default '',
  runtime_seconds double precision,
  created_at timestamptz not null default now(),
  finished_at timestamptz,
  unique (submission_id, scenario_id)
);

create table if not exists public.announcements (
  id bigint generated always as identity primary key,
  title_en text not null,
  title_zh text not null,
  body_en text not null default '',
  body_zh text not null default '',
  level text not null default 'info' check (level in ('info', 'warning', 'success')),
  is_pinned boolean not null default false,
  is_published boolean not null default true,
  created_at timestamptz not null default now()
);

create table if not exists public.audit_log (
  id bigint generated always as identity primary key,
  user_id uuid,
  action text not null,
  detail jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- auth helpers
-- ---------------------------------------------------------------------------
create or replace function public.is_admin() returns boolean
language sql stable security definer set search_path = public as $$
  select coalesce((select p.is_admin and not p.is_banned from public.profiles p where p.id = auth.uid()), false);
$$;

create or replace function public.my_team_id() returns uuid
language sql stable security definer set search_path = public as $$
  select p.team_id from public.profiles p where p.id = auth.uid();
$$;

create or replace function private.audit(p_action text, p_detail jsonb default '{}'::jsonb) returns void
language sql security definer set search_path = public as $$
  insert into public.audit_log (user_id, action, detail) values (auth.uid(), p_action, coalesce(p_detail, '{}'::jsonb));
$$;

-- new auth user -> profile (admin if listed in site_settings.admin_emails)
create or replace function public.handle_new_user() returns trigger
language plpgsql security definer set search_path = public as $$
declare
  v_admins jsonb := coalesce((select value from public.site_settings where key = 'admin_emails'), '[]'::jsonb);
begin
  insert into public.profiles (id, email, name, github, affiliation, looking_for_team, locale, is_admin)
  values (
    new.id, new.email,
    coalesce(new.raw_user_meta_data->>'name', split_part(coalesce(new.email, ''), '@', 1)),
    coalesce(new.raw_user_meta_data->>'github', ''),
    coalesce(new.raw_user_meta_data->>'affiliation', ''),
    coalesce((new.raw_user_meta_data->>'looking_for_team')::boolean, false),
    coalesce(new.raw_user_meta_data->>'locale', 'zh'),
    v_admins ? lower(coalesce(new.email, ''))
  )
  on conflict (id) do nothing;
  return new;
end $$;
drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created after insert on auth.users for each row execute function public.handle_new_user();

-- phase status helper
create or replace function public.phase_status(p public.phases) returns text language sql stable as $$
  select case
    when not p.is_active then 'disabled'
    when p.starts_at is not null and now() < p.starts_at then 'upcoming'
    when p.ends_at is not null and now() > p.ends_at then 'closed'
    else 'open' end;
$$;

-- ---------------------------------------------------------------------------
-- row level security
-- ---------------------------------------------------------------------------
alter table public.site_settings enable row level security;
alter table public.profiles enable row level security;
alter table public.teams enable row level security;
alter table public.scenarios enable row level security;
alter table public.phases enable row level security;
alter table public.phase_scenarios enable row level security;
alter table public.submissions enable row level security;
alter table public.evaluations enable row level security;
alter table public.announcements enable row level security;
alter table public.audit_log enable row level security;

drop policy if exists "settings public keys" on public.site_settings;
create policy "settings public keys" on public.site_settings for select to anon, authenticated
  using (key in ('registration_open', 'event') or public.is_admin());
drop policy if exists "settings admin write" on public.site_settings;
create policy "settings admin write" on public.site_settings for all to authenticated using (public.is_admin()) with check (public.is_admin());

drop policy if exists "profiles read" on public.profiles;
create policy "profiles read" on public.profiles for select to authenticated
  using (id = auth.uid() or (team_id is not null and team_id = public.my_team_id()) or public.is_admin());
drop policy if exists "profiles update own" on public.profiles;
create policy "profiles update own" on public.profiles for update to authenticated
  using (id = auth.uid() or public.is_admin()) with check (id = auth.uid() or public.is_admin());
revoke update on public.profiles from authenticated;
grant update (name, github, affiliation, role, looking_for_team, locale) on public.profiles to authenticated;
-- admins change flags through admin_set_user()

drop policy if exists "teams read" on public.teams;
create policy "teams read" on public.teams for select to authenticated using (true);
-- all writes go through RPCs (security definer); no insert/update/delete grants for participants
revoke insert, update, delete on public.teams from authenticated;

drop policy if exists "scenarios read" on public.scenarios;
create policy "scenarios read" on public.scenarios for select to anon, authenticated using (is_active or public.is_admin());
drop policy if exists "scenarios admin write" on public.scenarios;
create policy "scenarios admin write" on public.scenarios for all to authenticated using (public.is_admin()) with check (public.is_admin());

drop policy if exists "phases read" on public.phases;
create policy "phases read" on public.phases for select to anon, authenticated using (is_active or public.is_admin());
drop policy if exists "phases admin write" on public.phases;
create policy "phases admin write" on public.phases for all to authenticated using (public.is_admin()) with check (public.is_admin());

drop policy if exists "phase_scenarios read" on public.phase_scenarios;
create policy "phase_scenarios read" on public.phase_scenarios for select to anon, authenticated using (true);
drop policy if exists "phase_scenarios admin write" on public.phase_scenarios;
create policy "phase_scenarios admin write" on public.phase_scenarios for all to authenticated using (public.is_admin()) with check (public.is_admin());

drop policy if exists "submissions read" on public.submissions;
create policy "submissions read" on public.submissions for select to authenticated
  using (team_id = public.my_team_id() or public.is_admin());
drop policy if exists "submissions admin update" on public.submissions;
create policy "submissions admin update" on public.submissions for update to authenticated using (public.is_admin()) with check (public.is_admin());
revoke insert, delete on public.submissions from authenticated;

drop policy if exists "evaluations read" on public.evaluations;
create policy "evaluations read" on public.evaluations for select to authenticated
  using (exists (select 1 from public.submissions s where s.id = submission_id and (s.team_id = public.my_team_id() or public.is_admin())));
revoke insert, update, delete on public.evaluations from authenticated;

drop policy if exists "announcements read" on public.announcements;
create policy "announcements read" on public.announcements for select to anon, authenticated using (is_published or public.is_admin());
drop policy if exists "announcements admin write" on public.announcements;
create policy "announcements admin write" on public.announcements for all to authenticated using (public.is_admin()) with check (public.is_admin());

drop policy if exists "audit admin read" on public.audit_log;
create policy "audit admin read" on public.audit_log for select to authenticated using (public.is_admin());
revoke insert, update, delete on public.audit_log from authenticated, anon;

-- ---------------------------------------------------------------------------
-- team workflow (transactional, capacity-checked)
-- ---------------------------------------------------------------------------
create or replace function public.create_team(p_name text, p_max_size integer default 4, p_project_idea text default '', p_github_repo text default '')
returns uuid language plpgsql security definer set search_path = public as $$
declare v_uid uuid := auth.uid(); v_team uuid; v_slug text; v_name text := trim(coalesce(p_name, ''));
begin
  if v_uid is null then raise exception 'not_authenticated'; end if;
  if (select team_id from public.profiles where id = v_uid for update) is not null then raise exception 'already_in_team'; end if;
  if length(v_name) < 2 or length(v_name) > 60 then raise exception 'name_length'; end if;
  if exists (select 1 from public.teams where lower(name) = lower(v_name)) then raise exception 'name_taken'; end if;
  if p_max_size is null or p_max_size < 1 or p_max_size > 8 then raise exception 'bad_size'; end if;
  v_slug := private.slugify(v_name);
  if exists (select 1 from public.teams where slug = v_slug) then v_slug := v_slug || '-' || substr(md5(random()::text), 1, 4); end if;
  insert into public.teams (name, slug, leader_id, max_size, project_idea, github_repo)
  values (v_name, v_slug, v_uid, p_max_size, left(coalesce(p_project_idea, ''), 2000), left(coalesce(p_github_repo, ''), 255))
  returning id into v_team;
  update public.profiles set team_id = v_team, looking_for_team = false where id = v_uid;
  perform private.audit('team.create', jsonb_build_object('team_id', v_team));
  return v_team;
end $$;

create or replace function public.join_team(p_invite_code text)
returns uuid language plpgsql security definer set search_path = public as $$
declare v_uid uuid := auth.uid(); v_team public.teams; v_count integer;
begin
  if v_uid is null then raise exception 'not_authenticated'; end if;
  if (select team_id from public.profiles where id = v_uid for update) is not null then raise exception 'already_in_team'; end if;
  select * into v_team from public.teams where invite_code = upper(trim(coalesce(p_invite_code, ''))) for update;
  if not found then raise exception 'bad_code'; end if;
  if v_team.is_locked then raise exception 'locked'; end if;
  select count(*) into v_count from public.profiles where team_id = v_team.id;
  if v_count >= v_team.max_size then raise exception 'full'; end if;
  update public.profiles set team_id = v_team.id, looking_for_team = false where id = v_uid;
  perform private.audit('team.join', jsonb_build_object('team_id', v_team.id));
  return v_team.id;
end $$;

create or replace function public.leave_team()
returns void language plpgsql security definer set search_path = public as $$
declare v_uid uuid := auth.uid(); v_team public.teams; v_count integer;
begin
  if v_uid is null then raise exception 'not_authenticated'; end if;
  select t.* into v_team from public.teams t join public.profiles p on p.team_id = t.id where p.id = v_uid for update of t;
  if not found then raise exception 'not_in_team'; end if;
  select count(*) into v_count from public.profiles where team_id = v_team.id;
  if v_team.leader_id = v_uid and v_count > 1 then raise exception 'leader_must_transfer'; end if;
  if v_team.leader_id = v_uid and exists (select 1 from public.submissions where team_id = v_team.id) then raise exception 'has_submissions'; end if;
  update public.profiles set team_id = null where id = v_uid;
  if v_count <= 1 then delete from public.teams where id = v_team.id; end if;
  perform private.audit('team.leave', jsonb_build_object('team_id', v_team.id));
end $$;

create or replace function public.disband_team()
returns void language plpgsql security definer set search_path = public as $$
declare v_uid uuid := auth.uid(); v_team public.teams;
begin
  select t.* into v_team from public.teams t where t.leader_id = v_uid for update;
  if not found then raise exception 'leader_only'; end if;
  if exists (select 1 from public.submissions where team_id = v_team.id) then raise exception 'has_submissions'; end if;
  update public.profiles set team_id = null, looking_for_team = true where team_id = v_team.id;
  delete from public.teams where id = v_team.id;
  perform private.audit('team.disband', jsonb_build_object('team_id', v_team.id));
end $$;

create or replace function public.update_team(p_project_idea text, p_github_repo text, p_max_size integer, p_is_locked boolean)
returns void language plpgsql security definer set search_path = public as $$
declare v_uid uuid := auth.uid(); v_team public.teams; v_count integer;
begin
  select * into v_team from public.teams where leader_id = v_uid for update;
  if not found then raise exception 'leader_only'; end if;
  select count(*) into v_count from public.profiles where team_id = v_team.id;
  update public.teams set
    project_idea = left(coalesce(p_project_idea, project_idea), 2000),
    github_repo = left(coalesce(p_github_repo, github_repo), 255),
    max_size = greatest(v_count, least(8, coalesce(p_max_size, max_size))),
    is_locked = coalesce(p_is_locked, is_locked)
  where id = v_team.id;
end $$;

create or replace function public.transfer_leadership(p_user_id uuid)
returns void language plpgsql security definer set search_path = public as $$
declare v_team public.teams;
begin
  select * into v_team from public.teams where leader_id = auth.uid() for update;
  if not found then raise exception 'leader_only'; end if;
  if not exists (select 1 from public.profiles where id = p_user_id and team_id = v_team.id) then raise exception 'not_member'; end if;
  update public.teams set leader_id = p_user_id where id = v_team.id;
  perform private.audit('team.transfer', jsonb_build_object('team_id', v_team.id, 'to', p_user_id));
end $$;

create or replace function public.remove_member(p_user_id uuid)
returns void language plpgsql security definer set search_path = public as $$
declare v_team public.teams;
begin
  select * into v_team from public.teams where leader_id = auth.uid() for update;
  if not found then raise exception 'leader_only'; end if;
  if p_user_id = v_team.leader_id then raise exception 'cannot_remove_leader'; end if;
  update public.profiles set team_id = null where id = p_user_id and team_id = v_team.id;
  perform private.audit('team.remove_member', jsonb_build_object('team_id', v_team.id, 'user', p_user_id));
end $$;

create or replace function public.regenerate_invite_code()
returns text language plpgsql security definer set search_path = public as $$
declare v_code text;
begin
  update public.teams set invite_code = private.new_invite_code() where leader_id = auth.uid() returning invite_code into v_code;
  if v_code is null then raise exception 'leader_only'; end if;
  return v_code;
end $$;

create or replace function public.open_teams()
returns table (id uuid, name text, member_count bigint, max_size integer, created_at timestamptz)
language sql stable security definer set search_path = public as $$
  select t.id, t.name, count(p.id), t.max_size, t.created_at
  from public.teams t left join public.profiles p on p.team_id = t.id
  where not t.is_locked and not t.is_hidden
  group by t.id having count(p.id) < t.max_size
  order by t.created_at desc limit 50;
$$;

create or replace function public.team_members(p_team_id uuid)
returns table (id uuid, name text, github text, affiliation text, is_leader boolean)
language sql stable security definer set search_path = public as $$
  select p.id, p.name, p.github, p.affiliation, (t.leader_id = p.id)
  from public.profiles p join public.teams t on t.id = p.team_id
  where p.team_id = p_team_id and (p_team_id = public.my_team_id() or public.is_admin())
  order by (t.leader_id = p.id) desc, p.created_at;
$$;

-- ---------------------------------------------------------------------------
-- submissions
-- ---------------------------------------------------------------------------
create or replace function public.team_daily_count(p_phase_slug text)
returns integer language sql stable security definer set search_path = public as $$
  select count(*)::integer from public.submissions s join public.phases p on p.id = s.phase_id
  where s.team_id = public.my_team_id() and p.slug = p_phase_slug and s.status <> 'cancelled'
    and s.created_at >= date_trunc('day', now() at time zone 'utc') at time zone 'utc';
$$;

create or replace function public.create_submission(
  p_phase_slug text, p_kind public.submission_kind, p_scenario_slug text, p_storage_path text,
  p_filename text default '', p_sha256 text default '', p_title text default '', p_notes text default '')
returns bigint language plpgsql security definer set search_path = public as $$
declare
  v_uid uuid := auth.uid(); v_team uuid; v_phase public.phases; v_scn public.scenarios; v_id bigint; v_admin boolean := public.is_admin();
begin
  if v_uid is null then raise exception 'not_authenticated'; end if;
  select team_id into v_team from public.profiles where id = v_uid and not is_banned;
  if v_team is null then raise exception 'need_team'; end if;
  select * into v_phase from public.phases where slug = p_phase_slug and is_active;
  if not found then raise exception 'bad_phase'; end if;
  if public.phase_status(v_phase) <> 'open' and not v_admin then raise exception 'phase_closed'; end if;
  if p_kind = 'results' and not v_phase.allow_results then raise exception 'results_not_allowed'; end if;
  if p_kind = 'agent' and not v_phase.allow_agents then raise exception 'agents_not_allowed'; end if;
  if p_kind = 'results' then
    select s.* into v_scn from public.scenarios s join public.phase_scenarios ps on ps.scenario_id = s.id
    where s.slug = p_scenario_slug and ps.phase_id = v_phase.id and s.is_active;
    if not found then raise exception 'bad_scenario'; end if;
    if not v_scn.weather_public and not v_admin then raise exception 'scenario_hidden'; end if;
  end if;
  if p_storage_path is null or position(v_team::text || '/' in p_storage_path) <> 1 then raise exception 'bad_storage_path'; end if;
  if not v_admin and public.team_daily_count(p_phase_slug) >= v_phase.daily_limit then raise exception 'daily_limit'; end if;
  insert into public.submissions (team_id, user_id, phase_id, scenario_id, kind, title, notes, storage_path, original_filename, sha256)
  values (v_team, v_uid, v_phase.id, v_scn.id, p_kind, left(coalesce(p_title, ''), 160), left(coalesce(p_notes, ''), 2000), p_storage_path, left(coalesce(p_filename, ''), 255), coalesce(p_sha256, ''))
  returning id into v_id;
  perform private.audit('submission.create', jsonb_build_object('submission_id', v_id, 'kind', p_kind, 'phase', p_phase_slug));
  return v_id;
end $$;

create or replace function public.cancel_submission(p_id bigint)
returns void language plpgsql security definer set search_path = public as $$
begin
  update public.submissions set status = 'cancelled', finished_at = now()
  where id = p_id and status = 'queued' and (team_id = public.my_team_id() or public.is_admin());
  if not found then raise exception 'cannot_cancel'; end if;
end $$;

-- ---------------------------------------------------------------------------
-- leaderboard
-- ---------------------------------------------------------------------------
create or replace function public.leaderboard(p_phase_slug text default null, p_limit integer default 500)
returns table (
  rank integer, team_id uuid, team_name text, team_slug text, total_score double precision, science_score double precision,
  completion_rate double precision, uniformity_score double precision, submission_count bigint, best_submission_id bigint,
  kind public.submission_kind, scored_at timestamptz)
language plpgsql stable security definer set search_path = public as $$
declare v_phase public.phases; v_admin boolean := public.is_admin();
begin
  if p_phase_slug is null then
    select * into v_phase from public.phases p where p.is_active
    order by (p.counts_for_final and public.phase_status(p) in ('open', 'closed')) desc, (public.phase_status(p) = 'open') desc, p.sort_order limit 1;
  else
    select * into v_phase from public.phases p where p.slug = p_phase_slug and (p.is_active or v_admin);
  end if;
  if v_phase.id is null then return; end if;
  if v_phase.leaderboard_mode = 'hidden' and not v_admin then return; end if;
  return query
  with scored as (
    select s.*, t.name as tname, t.slug as tslug, t.is_hidden
    from public.submissions s join public.teams t on t.id = s.team_id
    where s.phase_id = v_phase.id and s.status = 'scored' and not s.is_excluded and s.score is not null and (not t.is_hidden or v_admin)
  ), best as (
    select distinct on (s.team_id) s.team_id, s.id, s.tname, s.tslug, s.score, s.science_score, s.completion, s.uniformity, s.kind, s.finished_at, s.created_at
    from scored s order by s.team_id, s.score desc, s.created_at asc
  ), counts as (
    select s.team_id, count(*) as n from scored s group by s.team_id
  )
  select (rank() over (order by b.score desc))::integer, b.team_id, b.tname, b.tslug, b.score, b.science_score, b.completion, b.uniformity,
         c.n, b.id, b.kind, coalesce(b.finished_at, b.created_at)
  from best b join counts c on c.team_id = b.team_id
  order by b.score desc, b.created_at asc
  limit greatest(1, least(coalesce(p_limit, 500), 1000));
end $$;

grant execute on function public.leaderboard(text, integer) to anon, authenticated;

-- ---------------------------------------------------------------------------
-- worker / service-role API (evaluation pipeline). Not callable by participants.
-- ---------------------------------------------------------------------------
create or replace function public.claim_submission(p_worker text, p_kinds public.submission_kind[] default array['results', 'agent']::public.submission_kind[])
returns public.submissions language plpgsql security definer set search_path = public as $$
declare v_row public.submissions;
begin
  update public.submissions set status = 'running', claimed_by = p_worker, started_at = now(), error = ''
  where id = (select id from public.submissions where status = 'queued' and kind = any(p_kinds) order by created_at limit 1 for update skip locked)
  returning * into v_row;
  return v_row;
end $$;
revoke execute on function public.claim_submission(text, public.submission_kind[]) from public, anon, authenticated;

create or replace function public.claim_submission_by_id(p_id bigint, p_worker text)
returns public.submissions language plpgsql security definer set search_path = public as $$
declare v_row public.submissions;
begin
  update public.submissions set status = 'running', claimed_by = p_worker, started_at = now(), error = ''
  where id = p_id and status = 'queued' returning * into v_row;
  return v_row;
end $$;
revoke execute on function public.claim_submission_by_id(bigint, text) from public, anon, authenticated;

-- requeue rows stuck in 'running' for longer than p_minutes (worker crash)
create or replace function public.requeue_stale(p_minutes integer default 30)
returns integer language plpgsql security definer set search_path = public as $$
declare n integer;
begin
  update public.submissions set status = 'queued', claimed_by = '' where status = 'running' and started_at < now() - make_interval(mins => p_minutes);
  get diagnostics n = row_count; return n;
end $$;
revoke execute on function public.requeue_stale(integer) from public, anon, authenticated;

-- ---------------------------------------------------------------------------
-- admin actions
-- ---------------------------------------------------------------------------
create or replace function public.admin_set_user(p_user_id uuid, p_action text)
returns void language plpgsql security definer set search_path = public as $$
begin
  if not public.is_admin() then raise exception 'admin_only'; end if;
  if p_user_id = auth.uid() and p_action in ('toggle_admin', 'toggle_ban') then raise exception 'cannot_change_self'; end if;
  case p_action
    when 'toggle_admin' then update public.profiles set is_admin = not is_admin where id = p_user_id;
    when 'toggle_ban' then update public.profiles set is_banned = not is_banned where id = p_user_id;
    when 'remove_from_team' then update public.profiles set team_id = null where id = p_user_id;
    else raise exception 'unknown_action';
  end case;
  perform private.audit('admin.user.' || p_action, jsonb_build_object('user_id', p_user_id));
end $$;

create or replace function public.admin_set_team(p_team_id uuid, p_action text, p_value text default null)
returns void language plpgsql security definer set search_path = public as $$
begin
  if not public.is_admin() then raise exception 'admin_only'; end if;
  case p_action
    when 'toggle_hidden' then update public.teams set is_hidden = not is_hidden where id = p_team_id;
    when 'toggle_locked' then update public.teams set is_locked = not is_locked where id = p_team_id;
    when 'rename' then update public.teams set name = left(trim(p_value), 60) where id = p_team_id and length(trim(coalesce(p_value, ''))) >= 2;
    else raise exception 'unknown_action';
  end case;
  perform private.audit('admin.team.' || p_action, jsonb_build_object('team_id', p_team_id));
end $$;

create or replace function public.admin_submission_action(p_id bigint, p_action text)
returns void language plpgsql security definer set search_path = public as $$
begin
  if not public.is_admin() then raise exception 'admin_only'; end if;
  case p_action
    when 'rescore' then update public.submissions set status = 'queued', error = '', score = null, claimed_by = '' where id = p_id;
    when 'exclude' then update public.submissions set is_excluded = not is_excluded where id = p_id;
    when 'cancel' then update public.submissions set status = 'cancelled', finished_at = now() where id = p_id and status in ('queued', 'running');
    else raise exception 'unknown_action';
  end case;
  perform private.audit('admin.submission.' || p_action, jsonb_build_object('submission_id', p_id));
end $$;

create or replace function public.admin_rescore_phase(p_phase_slug text)
returns integer language plpgsql security definer set search_path = public as $$
declare n integer;
begin
  if not public.is_admin() then raise exception 'admin_only'; end if;
  update public.submissions s set status = 'queued', error = '', score = null, claimed_by = ''
  from public.phases p where p.id = s.phase_id and p.slug = p_phase_slug and s.status in ('scored', 'failed', 'invalid');
  get diagnostics n = row_count;
  perform private.audit('admin.phase.rescore', jsonb_build_object('phase', p_phase_slug, 'n', n));
  return n;
end $$;

create or replace function public.admin_stats()
returns jsonb language sql stable security definer set search_path = public as $$
  select case when public.is_admin() then jsonb_build_object(
    'users', (select count(*) from public.profiles),
    'teams', (select count(*) from public.teams),
    'submissions', (select count(*) from public.submissions),
    'queued', (select count(*) from public.submissions where status in ('queued', 'running')),
    'scored', (select count(*) from public.submissions where status = 'scored'),
    'failed', (select count(*) from public.submissions where status in ('failed', 'invalid'))
  ) else '{}'::jsonb end;
$$;

-- profile of the caller (joins team) in one call
create or replace function public.me()
returns jsonb language sql stable security definer set search_path = public as $$
  select case when auth.uid() is null then null else (
    select jsonb_build_object(
      'id', p.id, 'email', p.email, 'name', p.name, 'github', p.github, 'affiliation', p.affiliation, 'role', p.role,
      'looking_for_team', p.looking_for_team, 'locale', p.locale, 'is_admin', p.is_admin, 'is_banned', p.is_banned,
      'team', case when t.id is null then null else jsonb_build_object(
        'id', t.id, 'name', t.name, 'slug', t.slug, 'leader_id', t.leader_id, 'invite_code', t.invite_code,
        'project_idea', t.project_idea, 'github_repo', t.github_repo, 'max_size', t.max_size, 'is_locked', t.is_locked,
        'member_count', (select count(*) from public.profiles m where m.team_id = t.id)) end)
    from public.profiles p left join public.teams t on t.id = p.team_id where p.id = auth.uid()) end;
$$;

-- public admin list of profiles with emails (admin only, returns empty otherwise)
create or replace function public.admin_users(p_query text default null)
returns table (id uuid, email text, name text, github text, affiliation text, team_name text, is_admin boolean, is_banned boolean, created_at timestamptz)
language sql stable security definer set search_path = public as $$
  select p.id, p.email, p.name, p.github, p.affiliation, t.name, p.is_admin, p.is_banned, p.created_at
  from public.profiles p left join public.teams t on t.id = p.team_id
  where public.is_admin() and (p_query is null or p.email ilike '%' || p_query || '%' or p.name ilike '%' || p_query || '%')
  order by p.created_at desc limit 500;
$$;

create or replace function public.admin_teams()
returns table (id uuid, name text, slug text, leader_id uuid, invite_code text, max_size integer, is_locked boolean, is_hidden boolean, github_repo text, member_count bigint, submission_count bigint, created_at timestamptz)
language sql stable security definer set search_path = public as $$
  select t.id, t.name, t.slug, t.leader_id, t.invite_code, t.max_size, t.is_locked, t.is_hidden, t.github_repo,
         (select count(*) from public.profiles p where p.team_id = t.id), (select count(*) from public.submissions s where s.team_id = t.id), t.created_at
  from public.teams t where public.is_admin() order by t.created_at desc;
$$;

-- ---------------------------------------------------------------------------
-- default settings
-- ---------------------------------------------------------------------------
insert into public.site_settings (key, value) values ('registration_open', 'true'::jsonb) on conflict (key) do nothing;
insert into public.site_settings (key, value) values ('admin_emails', '[]'::jsonb) on conflict (key) do nothing;

-- ---------------------------------------------------------------------------
-- realtime: teams watch their submissions change status
-- ---------------------------------------------------------------------------
do $$ begin
  if exists (select 1 from pg_publication where pubname = 'supabase_realtime') then
    alter publication supabase_realtime add table public.submissions;
    alter publication supabase_realtime add table public.evaluations;
    alter publication supabase_realtime add table public.announcements;
  end if;
exception when duplicate_object then null; end $$;

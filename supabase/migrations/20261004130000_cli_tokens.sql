-- Personal API tokens for the survey26 command-line tool.
--
-- A contestant creates a token on the profile page; only its SHA-256 is stored. The edge
-- function survey26-cli checks the token on every request (cli_token_authenticate), then
-- acts as that user with an ordinary user session that never leaves the server, and only
-- for the allow-listed contestant actions. Permissions, quotas and limits are therefore
-- exactly those of the website; organizer actions are never reachable with a token.
--
-- Rollout switch (private.cli_config.enabled): false = off, true = everyone,
-- {"users": ["<uuid>", ...]} = only these accounts. Rollback, one line:
--   update private.cli_config set enabled = 'false';

create table if not exists private.cli_config (
  id boolean primary key default true check (id),
  enabled jsonb not null default 'false'::jsonb
);
insert into private.cli_config (id) values (true) on conflict (id) do nothing;

create table if not exists private.cli_tokens (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  name text not null check (char_length(name) between 1 and 60),
  token_hash text not null unique check (token_hash ~ '^[0-9a-f]{64}$'),
  token_hint text not null,
  created_at timestamptz not null default now(),
  last_used_at timestamptz,
  use_count bigint not null default 0,
  revoked_at timestamptz
);
create index if not exists cli_tokens_user on private.cli_tokens(user_id) where revoked_at is null;

-- Requests per user per minute, across all of the user's tokens.
create table if not exists private.cli_rate (
  user_id uuid primary key references auth.users(id) on delete cascade,
  window_start timestamptz not null,
  count integer not null
);

-- The server-side user session the edge function acts with (tokens AES-GCM encrypted by the function).
create table if not exists private.cli_sessions (
  user_id uuid primary key references auth.users(id) on delete cascade,
  encrypted_session text not null,
  expires_at timestamptz not null,
  updated_at timestamptz not null default now()
);

revoke all on private.cli_config, private.cli_tokens, private.cli_rate, private.cli_sessions from public, anon, authenticated;

create or replace function private.cli_tokens_on(p_user uuid)
returns boolean language sql stable security definer set search_path = public, pg_temp as $$
  select coalesce((select case
      when jsonb_typeof(c.enabled) = 'boolean' then c.enabled = 'true'::jsonb
      when jsonb_typeof(c.enabled) = 'object' then coalesce(c.enabled -> 'users', '[]'::jsonb) ? p_user::text
      else false end
    from private.cli_config c where c.id), false)
$$;
revoke all on function private.cli_tokens_on(uuid) from public, anon, authenticated;

-- Profile page: whether tokens are available to the caller, and the caller's active tokens.
create or replace function public.my_cli_tokens()
returns jsonb language sql stable security definer set search_path = public, pg_temp as $$
  select case when auth.uid() is null then null else jsonb_build_object(
    'enabled', private.cli_tokens_on(auth.uid()),
    'limit', 5,
    'tokens', coalesce((select jsonb_agg(jsonb_build_object('id', t.id, 'name', t.name, 'hint', t.token_hint,
        'created_at', t.created_at, 'last_used_at', t.last_used_at) order by t.created_at desc)
      from private.cli_tokens t where t.user_id = auth.uid() and t.revoked_at is null), '[]'::jsonb)) end
$$;

-- Creates a token and returns it once. 244 random bits from two v4 UUIDs, hashed to 64 hex digits.
create or replace function public.create_cli_token(p_name text)
returns jsonb language plpgsql volatile security definer set search_path = public, pg_temp as $$
declare
  v_user uuid := auth.uid();
  v_name text := btrim(coalesce(p_name, ''));
  v_token text;
  v_id uuid;
begin
  if v_user is null then raise exception 'login_required'; end if;
  if exists (select 1 from public.profiles p where p.id = v_user and p.is_banned) then raise exception 'account_banned'; end if;
  if not private.cli_tokens_on(v_user) then raise exception 'cli_tokens_disabled'; end if;
  if char_length(v_name) < 1 or char_length(v_name) > 60 then raise exception 'invalid_token_name'; end if;
  perform pg_advisory_xact_lock(hashtextextended('cli_tokens:' || v_user::text, 0));
  if (select count(*) from private.cli_tokens t where t.user_id = v_user and t.revoked_at is null) >= 5 then
    raise exception 'token_limit';
  end if;
  v_token := 's26_' || encode(sha256(convert_to(gen_random_uuid()::text || gen_random_uuid()::text, 'UTF8')), 'hex');
  insert into private.cli_tokens (user_id, name, token_hash, token_hint)
  values (v_user, v_name, encode(sha256(convert_to(v_token, 'UTF8')), 'hex'), right(v_token, 4))
  returning id into v_id;
  insert into public.audit_log (user_id, action, detail)
  values (v_user, 'cli_token_created', jsonb_build_object('token_id', v_id, 'name', v_name));
  return jsonb_build_object('id', v_id, 'name', v_name, 'token', v_token, 'hint', right(v_token, 4), 'created_at', now());
end $$;

create or replace function public.revoke_cli_token(p_id uuid)
returns boolean language plpgsql volatile security definer set search_path = public, pg_temp as $$
declare v_user uuid := auth.uid();
begin
  if v_user is null then raise exception 'login_required'; end if;
  update private.cli_tokens set revoked_at = now() where id = p_id and user_id = v_user and revoked_at is null;
  if not found then raise exception 'token_not_found'; end if;
  insert into public.audit_log (user_id, action, detail) values (v_user, 'cli_token_revoked', jsonb_build_object('token_id', p_id));
  -- Without an active token the cached server session is not needed any more.
  if not exists (select 1 from private.cli_tokens t where t.user_id = v_user and t.revoked_at is null) then
    delete from private.cli_sessions where user_id = v_user;
  end if;
  return true;
end $$;

revoke all on function public.my_cli_tokens(), public.create_cli_token(text), public.revoke_cli_token(uuid) from public, anon;
grant execute on function public.my_cli_tokens(), public.create_cli_token(text), public.revoke_cli_token(uuid) to authenticated;

-- Edge function only: the token's owner, after the switch, ban and rate checks; records the use.
create or replace function public.cli_token_authenticate(p_hash text, p_limit integer default 120)
returns jsonb language plpgsql volatile security definer set search_path = public, pg_temp as $$
declare
  v record;
  v_count integer;
begin
  select t.id, t.user_id, u.email into v
    from private.cli_tokens t join auth.users u on u.id = t.user_id
   where t.token_hash = p_hash and t.revoked_at is null;
  if not found then raise exception 'invalid_token'; end if;
  if not private.cli_tokens_on(v.user_id) then raise exception 'cli_tokens_disabled'; end if;
  if exists (select 1 from public.profiles p where p.id = v.user_id and p.is_banned) then raise exception 'account_banned'; end if;
  insert into private.cli_rate as r (user_id, window_start, count) values (v.user_id, date_trunc('minute', now()), 1)
  on conflict (user_id) do update set
    count = case when r.window_start = date_trunc('minute', now()) then r.count + 1 else 1 end,
    window_start = date_trunc('minute', now())
  returning count into v_count;
  if v_count > greatest(p_limit, 1) then raise exception 'rate_limited'; end if;
  update private.cli_tokens set last_used_at = now(), use_count = use_count + 1 where id = v.id;
  return jsonb_build_object('user_id', v.user_id, 'token_id', v.id, 'email', v.email);
end $$;

create or replace function public.cli_session_get(p_user uuid)
returns jsonb language sql stable security definer set search_path = public, pg_temp as $$
  select jsonb_build_object('encrypted_session', s.encrypted_session, 'expires_at', s.expires_at)
    from private.cli_sessions s where s.user_id = p_user
$$;

create or replace function public.cli_session_put(p_user uuid, p_encrypted text, p_expires_at timestamptz)
returns void language sql volatile security definer set search_path = public, pg_temp as $$
  insert into private.cli_sessions (user_id, encrypted_session, expires_at, updated_at)
  values (p_user, p_encrypted, p_expires_at, now())
  on conflict (user_id) do update set encrypted_session = excluded.encrypted_session,
    expires_at = excluded.expires_at, updated_at = now()
$$;

revoke all on function public.cli_token_authenticate(text, integer), public.cli_session_get(uuid),
  public.cli_session_put(uuid, text, timestamptz) from public, anon, authenticated;
grant execute on function public.cli_token_authenticate(text, integer), public.cli_session_get(uuid),
  public.cli_session_put(uuid, text, timestamptz) to service_role;

notify pgrst, 'reload schema';

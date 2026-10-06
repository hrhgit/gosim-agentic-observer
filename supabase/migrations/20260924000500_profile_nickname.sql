-- Optional display nicknames. Existing names, IDs, team membership, and public opt-in stay unchanged.
alter table public.profiles add column if not exists nickname text not null default '';
alter table public.profiles add constraint profiles_nickname_length check (char_length(nickname) <= 40);

-- Match JavaScript String.trim(), including Unicode spaces, before checking the code-point limit.
create or replace function private.normalize_profile_nickname() returns trigger
language plpgsql set search_path = public as $$
begin
  new.nickname := btrim(coalesce(new.nickname, ''), U&'\0009\000A\000B\000C\000D\0020\00A0\1680\2000\2001\2002\2003\2004\2005\2006\2007\2008\2009\200A\2028\2029\202F\205F\3000\FEFF');
  return new;
end $$;
create trigger normalize_profile_nickname
before insert or update of nickname on public.profiles
for each row execute function private.normalize_profile_nickname();

-- Existing row policies still limit normal participants to their own profile.
grant update (nickname) on public.profiles to authenticated;

-- Preserve the registration deadline and all existing sign-up metadata handling.
create or replace function public.handle_new_user() returns trigger
language plpgsql security definer set search_path = public as $$
declare
  v_admins jsonb := coalesce((select value from public.site_settings where key = 'admin_emails'), '[]'::jsonb);
  v_meta jsonb := coalesce(new.raw_user_meta_data, '{}'::jsonb);
  v_seeking text := '';
  v_seek_n integer := 1;
begin
  if not public.registration_effectively_open() then
    raise exception 'registration_closed';
  end if;
  if v_meta->>'seeking' in ('astro', 'ai') then v_seeking := v_meta->>'seeking'; end if;
  if coalesce(v_meta->>'seeking_count', '') ~ '^[0-9]$' then v_seek_n := (v_meta->>'seeking_count')::integer; end if;
  insert into public.profiles (id, email, name, nickname, github, affiliation, looking_for_team, locale, is_admin,
                               astro_level, ai_level, city, contact, heard_from, long_term, blurb, show_on_wall, role,
                               seeking, seeking_count)
  values (
    new.id, new.email,
    coalesce(v_meta->>'name', split_part(coalesce(new.email, ''), '@', 1)),
    coalesce(v_meta->>'nickname', ''),
    coalesce(v_meta->>'github', ''),
    coalesce(v_meta->>'affiliation', ''),
    v_seeking <> '',
    coalesce(v_meta->>'locale', 'zh'),
    v_admins ? lower(coalesce(new.email, '')),
    case when coalesce(v_meta->>'astro_level', '') ~ '^[0-3]$' then (v_meta->>'astro_level')::smallint else 0 end,
    case when coalesce(v_meta->>'ai_level', '') ~ '^[0-3]$' then (v_meta->>'ai_level')::smallint else 0 end,
    left(coalesce(v_meta->>'city', ''), 120),
    left(coalesce(v_meta->>'contact', ''), 200),
    left(coalesce(v_meta->>'heard_from', ''), 120),
    coalesce(v_meta->>'long_term', '') = 'true',
    left(coalesce(v_meta->>'blurb', ''), 160),
    coalesce(v_meta->>'show_on_wall', '') = 'true',
    left(coalesce(v_meta->>'role', ''), 120),
    v_seeking,
    case when v_seeking = '' then 0 else least(greatest(v_seek_n, 1), 9) end
  )
  on conflict (id) do nothing;
  return new;
end $$;

-- Keep the account name separate from the optional display nickname.
create or replace function public.me()
returns jsonb language sql stable security definer set search_path = public as $$
  select case when auth.uid() is null then null else (
    select jsonb_build_object(
      'id', p.id, 'email', p.email, 'name', p.name, 'nickname', p.nickname, 'github', p.github, 'affiliation', p.affiliation, 'role', p.role,
      'looking_for_team', p.looking_for_team, 'seeking', p.seeking, 'seeking_count', p.seeking_count,
      'locale', p.locale, 'is_admin', p.is_admin, 'is_banned', p.is_banned,
      'astro_level', p.astro_level, 'ai_level', p.ai_level, 'city', p.city, 'contact', p.contact,
      'heard_from', p.heard_from, 'blurb', p.blurb, 'show_on_wall', p.show_on_wall,
      'team', case when t.id is null then null else jsonb_build_object(
        'id', t.id, 'name', t.name, 'slug', t.slug, 'leader_id', t.leader_id, 'invite_code', t.invite_code,
        'project_idea', t.project_idea, 'github_repo', t.github_repo, 'max_size', t.max_size, 'is_locked', t.is_locked,
        'member_count', (select count(*) from public.profiles m where m.team_id = t.id)) end)
    from public.profiles p left join public.teams t on t.id = p.team_id where p.id = auth.uid()) end;
$$;

-- Keep the public response shape compatible; only the display name is exposed.
create or replace function public.participants_wall(p_limit int default 60)
returns table (
  id uuid, name text, role text, affiliation text, city text, blurb text,
  astro_level smallint, ai_level smallint, looking_for_team boolean, team_name text, joined_at timestamptz,
  github text, seeking text, seeking_count smallint
) language sql stable security definer set search_path = public as $$
  select p.id, coalesce(nullif(p.nickname, ''), p.name), p.role, p.affiliation, p.city, p.blurb,
         p.astro_level, p.ai_level,
         (p.looking_for_team and p.team_id is null) as looking_for_team,
         t.name as team_name, p.created_at as joined_at,
         p.github, p.seeking, p.seeking_count
  from public.profiles p
  left join public.teams t on t.id = p.team_id
  where p.show_on_wall and not p.is_banned and coalesce(nullif(p.nickname, ''), p.name) <> ''
  order by p.created_at desc
  limit least(greatest(coalesce(p_limit, 60), 1), 200);
$$;
grant execute on function public.participants_wall(int) to anon, authenticated;

-- Team members use the same display name without changing team visibility.
create or replace function public.team_members(p_team_id uuid)
returns table (id uuid, name text, github text, affiliation text, is_leader boolean, astro_level smallint, ai_level smallint)
language sql stable security definer set search_path = public as $$
  select p.id, coalesce(nullif(p.nickname, ''), p.name), p.github, p.affiliation, (t.leader_id = p.id), p.astro_level, p.ai_level
  from public.profiles p join public.teams t on t.id = p.team_id
  where p.team_id = p_team_id and (p_team_id = public.my_team_id() or public.is_admin())
  order by (t.leader_id = p.id) desc, p.created_at;
$$;
grant execute on function public.team_members(uuid) to authenticated;

notify pgrst, 'reload schema';

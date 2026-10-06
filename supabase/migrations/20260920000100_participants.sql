-- Participant identity: skill tiers, recruiting fields, and the public participants wall.
-- The wall is opt-in (show_on_wall); contact details are only revealed to logged-in participants.

alter table public.profiles
  add column if not exists astro_level smallint not null default 0,
  add column if not exists ai_level smallint not null default 0,
  add column if not exists city text not null default '',
  add column if not exists contact text not null default '',
  add column if not exists heard_from text not null default '',
  add column if not exists long_term boolean not null default false,
  add column if not exists blurb text not null default '',
  add column if not exists show_on_wall boolean not null default false;

alter table public.profiles drop constraint if exists profiles_astro_level_range;
alter table public.profiles add constraint profiles_astro_level_range check (astro_level between 0 and 3);
alter table public.profiles drop constraint if exists profiles_ai_level_range;
alter table public.profiles add constraint profiles_ai_level_range check (ai_level between 0 and 3);

grant update (astro_level, ai_level, city, contact, heard_from, long_term, blurb, show_on_wall)
  on public.profiles to authenticated;

-- Carry the sign-up answers from auth metadata into the profile. Metadata comes from the
-- browser, so every value is validated or clamped rather than cast blindly.
create or replace function public.handle_new_user() returns trigger
language plpgsql security definer set search_path = public as $$
declare
  v_admins jsonb := coalesce((select value from public.site_settings where key = 'admin_emails'), '[]'::jsonb);
  v_meta jsonb := coalesce(new.raw_user_meta_data, '{}'::jsonb);
begin
  insert into public.profiles (id, email, name, github, affiliation, looking_for_team, locale, is_admin,
                               astro_level, ai_level, city, contact, heard_from, long_term, blurb, show_on_wall, role)
  values (
    new.id, new.email,
    coalesce(v_meta->>'name', split_part(coalesce(new.email, ''), '@', 1)),
    coalesce(v_meta->>'github', ''),
    coalesce(v_meta->>'affiliation', ''),
    coalesce(v_meta->>'looking_for_team', '') = 'true',
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
    left(coalesce(v_meta->>'role', ''), 120)
  )
  on conflict (id) do nothing;
  return new;
end $$;

-- me() now returns the participant fields so the profile page can prefill them.
create or replace function public.me()
returns jsonb language sql stable security definer set search_path = public as $$
  select case when auth.uid() is null then null else (
    select jsonb_build_object(
      'id', p.id, 'email', p.email, 'name', p.name, 'github', p.github, 'affiliation', p.affiliation, 'role', p.role,
      'looking_for_team', p.looking_for_team, 'locale', p.locale, 'is_admin', p.is_admin, 'is_banned', p.is_banned,
      'astro_level', p.astro_level, 'ai_level', p.ai_level, 'city', p.city, 'contact', p.contact,
      'heard_from', p.heard_from, 'long_term', p.long_term, 'blurb', p.blurb, 'show_on_wall', p.show_on_wall,
      'team', case when t.id is null then null else jsonb_build_object(
        'id', t.id, 'name', t.name, 'slug', t.slug, 'leader_id', t.leader_id, 'invite_code', t.invite_code,
        'project_idea', t.project_idea, 'github_repo', t.github_repo, 'max_size', t.max_size, 'is_locked', t.is_locked,
        'member_count', (select count(*) from public.profiles m where m.team_id = t.id)) end)
    from public.profiles p left join public.teams t on t.id = p.team_id where p.id = auth.uid()) end;
$$;

-- Public participants wall: opted-in, not banned, named. No e-mail, no contact handle.
create or replace function public.participants_wall(p_limit int default 60)
returns table (
  id uuid, name text, role text, affiliation text, city text, blurb text,
  astro_level smallint, ai_level smallint, looking_for_team boolean, team_name text, joined_at timestamptz
) language sql stable security definer set search_path = public as $$
  select p.id, p.name, p.role, p.affiliation, p.city, p.blurb,
         p.astro_level, p.ai_level,
         (p.looking_for_team and p.team_id is null) as looking_for_team,
         t.name as team_name, p.created_at as joined_at
  from public.profiles p
  left join public.teams t on t.id = p.team_id
  where p.show_on_wall and not p.is_banned and p.name <> ''
  order by p.created_at desc
  limit least(greatest(coalesce(p_limit, 60), 1), 200);
$$;
grant execute on function public.participants_wall(int) to anon, authenticated;

-- Headline numbers for the homepage: everyone registered counts, wall and matching are subsets.
create or replace function public.participants_stats()
returns jsonb language sql stable security definer set search_path = public as $$
  select jsonb_build_object(
    'total', (select count(*) from public.profiles where not is_banned),
    'on_wall', (select count(*) from public.profiles where show_on_wall and not is_banned),
    'looking', (select count(*) from public.profiles where show_on_wall and not is_banned and looking_for_team and team_id is null),
    'teams', (select count(*) from public.teams));
$$;
grant execute on function public.participants_stats() to anon, authenticated;

-- Contact reveal: only for logged-in participants, only for people on the wall.
create or replace function public.teammate_contact(p_id uuid)
returns jsonb language sql stable security definer set search_path = public as $$
  select case when auth.uid() is null then null else (
    select jsonb_build_object('contact', p.contact, 'github', p.github)
    from public.profiles p
    where p.id = p_id and p.show_on_wall and not p.is_banned) end;
$$;
revoke execute on function public.teammate_contact(uuid) from public, anon;
grant execute on function public.teammate_contact(uuid) to authenticated;

-- Team pages show member tiers; the return type changes, so the function is dropped first.
drop function if exists public.team_members(uuid);
create function public.team_members(p_team_id uuid)
returns table (id uuid, name text, github text, affiliation text, is_leader boolean, astro_level smallint, ai_level smallint)
language sql stable security definer set search_path = public as $$
  select p.id, p.name, p.github, p.affiliation, (t.leader_id = p.id), p.astro_level, p.ai_level
  from public.profiles p join public.teams t on t.id = p.team_id
  where p.team_id = p_team_id and (p_team_id = public.my_team_id() or public.is_admin())
  order by (t.leader_id = p.id) desc, p.created_at;
$$;
grant execute on function public.team_members(uuid) to authenticated;

notify pgrst, 'reload schema';

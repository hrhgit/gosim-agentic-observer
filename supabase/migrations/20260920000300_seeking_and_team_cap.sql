-- Team-up intent replaces the single "looking for a team" checkbox, the team cap drops to 3,
-- and the public wall gains github (avatar source) plus the seeking fields.
--   profiles.seeking       : '' (no teammates wanted) | 'astro' | 'ai'
--   profiles.seeking_count : how many teammates they hope to add (0 when not seeking)

alter table public.profiles
  add column if not exists seeking text not null default '',
  add column if not exists seeking_count smallint not null default 0;
alter table public.profiles drop constraint if exists profiles_seeking_check;
alter table public.profiles add constraint profiles_seeking_check check (seeking in ('', 'astro', 'ai'));
alter table public.profiles drop constraint if exists profiles_seeking_count_check;
alter table public.profiles add constraint profiles_seeking_count_check check (seeking_count between 0 and 9);

-- Cap: 3 people per team. Existing larger caps clamp down; memberships are untouched.
update public.teams set max_size = 3 where max_size > 3;
alter table public.teams alter column max_size set default 3;
alter table public.teams drop constraint if exists teams_max_size_check;
alter table public.teams add constraint teams_max_size_check check (max_size between 1 and 3);

create or replace function public.create_team(p_name text, p_max_size integer default 3, p_project_idea text default '', p_github_repo text default '')
returns uuid language plpgsql security definer set search_path = public as $$
declare v_uid uuid := auth.uid(); v_team uuid; v_slug text; v_name text := trim(coalesce(p_name, ''));
begin
  if v_uid is null then raise exception 'not_authenticated'; end if;
  if (select team_id from public.profiles where id = v_uid for update) is not null then raise exception 'already_in_team'; end if;
  if length(v_name) < 2 or length(v_name) > 60 then raise exception 'name_length'; end if;
  if exists (select 1 from public.teams where lower(name) = lower(v_name)) then raise exception 'name_taken'; end if;
  if p_max_size is null or p_max_size < 1 or p_max_size > 3 then raise exception 'bad_size'; end if;
  v_slug := private.slugify(v_name);
  if exists (select 1 from public.teams where slug = v_slug) then v_slug := v_slug || '-' || substr(md5(random()::text), 1, 4); end if;
  insert into public.teams (name, slug, leader_id, max_size, project_idea, github_repo)
  values (v_name, v_slug, v_uid, p_max_size, left(coalesce(p_project_idea, ''), 2000), left(coalesce(p_github_repo, ''), 255))
  returning id into v_team;
  update public.profiles set team_id = v_team, looking_for_team = false where id = v_uid;
  perform private.audit('team.create', jsonb_build_object('team_id', v_team));
  return v_team;
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
    max_size = greatest(v_count, least(3, coalesce(p_max_size, max_size))),
    is_locked = coalesce(p_is_locked, is_locked)
  where id = v_team.id;
end $$;

-- Sign-up trigger: parse the seeking fields, derive looking_for_team, keep the deadline guard.
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
  insert into public.profiles (id, email, name, github, affiliation, looking_for_team, locale, is_admin,
                               astro_level, ai_level, city, contact, heard_from, long_term, blurb, show_on_wall, role,
                               seeking, seeking_count)
  values (
    new.id, new.email,
    coalesce(v_meta->>'name', split_part(coalesce(new.email, ''), '@', 1)),
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

-- me(): expose the seeking fields (long_term retires from the payload).
create or replace function public.me()
returns jsonb language sql stable security definer set search_path = public as $$
  select case when auth.uid() is null then null else (
    select jsonb_build_object(
      'id', p.id, 'email', p.email, 'name', p.name, 'github', p.github, 'affiliation', p.affiliation, 'role', p.role,
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

-- Public wall: add github (avatars) and the seeking fields. Return type changes, so drop first.
drop function if exists public.participants_wall(int);
create function public.participants_wall(p_limit int default 60)
returns table (
  id uuid, name text, role text, affiliation text, city text, blurb text,
  astro_level smallint, ai_level smallint, looking_for_team boolean, team_name text, joined_at timestamptz,
  github text, seeking text, seeking_count smallint
) language sql stable security definer set search_path = public as $$
  select p.id, p.name, p.role, p.affiliation, p.city, p.blurb,
         p.astro_level, p.ai_level,
         (p.looking_for_team and p.team_id is null) as looking_for_team,
         t.name as team_name, p.created_at as joined_at,
         p.github, p.seeking, p.seeking_count
  from public.profiles p
  left join public.teams t on t.id = p.team_id
  where p.show_on_wall and not p.is_banned and p.name <> ''
  order by p.created_at desc
  limit least(greatest(coalesce(p_limit, 60), 1), 200);
$$;
grant execute on function public.participants_wall(int) to anon, authenticated;

notify pgrst, 'reload schema';

-- Avatar uploads: a public "avatars" storage bucket, one image per user under
-- their own auth.uid() folder, and a small RPC so profiles RLS never grants a
-- direct column update (the URL is validated server-side before it is stored).

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('avatars', 'avatars', true, 2097152, array['image/png','image/jpeg','image/webp'])
on conflict (id) do update set public = excluded.public, file_size_limit = excluded.file_size_limit,
  allowed_mime_types = excluded.allowed_mime_types;

drop policy if exists "avatars read" on storage.objects;
create policy "avatars read" on storage.objects for select to anon, authenticated
using (bucket_id = 'avatars');

drop policy if exists "avatars upload own" on storage.objects;
create policy "avatars upload own" on storage.objects for insert to authenticated
with check (bucket_id = 'avatars' and (storage.foldername(name))[1] = auth.uid()::text);

drop policy if exists "avatars update own" on storage.objects;
create policy "avatars update own" on storage.objects for update to authenticated
using (bucket_id = 'avatars' and (storage.foldername(name))[1] = auth.uid()::text)
with check (bucket_id = 'avatars' and (storage.foldername(name))[1] = auth.uid()::text);

drop policy if exists "avatars delete own" on storage.objects;
create policy "avatars delete own" on storage.objects for delete to authenticated
using (bucket_id = 'avatars' and (storage.foldername(name))[1] = auth.uid()::text);

alter table public.profiles add column if not exists avatar_url text not null default '';
alter table public.profiles drop constraint if exists profiles_avatar_url_length;
alter table public.profiles add constraint profiles_avatar_url_length check (char_length(avatar_url) <= 500);

-- Participants update their own avatar through this RPC only: it checks the
-- URL actually names an object in the avatars bucket under the caller's own
-- folder before storing it, so RLS never needs to grant a direct avatar_url
-- column update.
create or replace function public.set_my_avatar(p_url text) returns void
language plpgsql security definer set search_path = public, pg_temp as $$
declare
  v_path text;
begin
  perform private.assert_not_banned();
  if p_url is null or length(p_url) > 500 then raise exception 'avatar_invalid_url'; end if;
  v_path := (regexp_match(p_url, '/storage/v1/object/public/avatars/([^?#]+)$'))[1];
  if v_path is null or (storage.foldername(v_path))[1] is distinct from auth.uid()::text
     or v_path !~ '\.(png|jpe?g|webp)$' then
    raise exception 'avatar_invalid_url';
  end if;
  if not exists (select 1 from storage.objects where bucket_id = 'avatars' and name = v_path) then
    raise exception 'avatar_not_found';
  end if;
  update public.profiles set avatar_url = p_url where id = auth.uid();
end $$;

create or replace function public.clear_my_avatar() returns void
language plpgsql security definer set search_path = public, pg_temp as $$
begin
  perform private.assert_not_banned();
  update public.profiles set avatar_url = '' where id = auth.uid();
end $$;

revoke all on function public.set_my_avatar(text), public.clear_my_avatar() from public, anon;
grant execute on function public.set_my_avatar(text), public.clear_my_avatar() to authenticated;

-- Re-publish avatar_url through the existing read surfaces (unchanged otherwise).
create or replace function public.me()
returns jsonb language sql stable security definer set search_path = public as $$
  select case when auth.uid() is null then null else (
    select jsonb_build_object(
      'id', p.id, 'email', p.email, 'name', p.name, 'nickname', p.nickname, 'github', p.github, 'affiliation', p.affiliation, 'role', p.role,
      'looking_for_team', p.looking_for_team, 'seeking', p.seeking, 'seeking_count', p.seeking_count,
      'locale', p.locale, 'is_admin', p.is_admin, 'is_banned', p.is_banned,
      'astro_level', p.astro_level, 'ai_level', p.ai_level, 'city', p.city, 'contact', p.contact,
      'heard_from', p.heard_from, 'blurb', p.blurb, 'show_on_wall', p.show_on_wall, 'avatar_url', p.avatar_url,
      'team', case when t.id is null then null else jsonb_build_object(
        'id', t.id, 'name', t.name, 'slug', t.slug, 'leader_id', t.leader_id, 'invite_code', t.invite_code,
        'project_idea', t.project_idea, 'github_repo', t.github_repo, 'max_size', t.max_size, 'is_locked', t.is_locked,
        'member_count', (select count(*) from public.profiles m where m.team_id = t.id)) end)
    from public.profiles p left join public.teams t on t.id = p.team_id where p.id = auth.uid()) end;
$$;

-- Adding a column changes the OUT-parameter row type, which create or replace
-- cannot do in place.
drop function if exists public.participants_wall(int);
create or replace function public.participants_wall(p_limit int default 60)
returns table (
  id uuid, name text, role text, affiliation text, city text, blurb text,
  astro_level smallint, ai_level smallint, looking_for_team boolean, team_name text, joined_at timestamptz,
  github text, seeking text, seeking_count smallint, avatar_url text
) language sql stable security definer set search_path = public as $$
  select p.id, coalesce(nullif(p.nickname, ''), p.name), p.role, p.affiliation, p.city, p.blurb,
         p.astro_level, p.ai_level,
         (p.looking_for_team and p.team_id is null) as looking_for_team,
         t.name as team_name, p.created_at as joined_at,
         p.github, p.seeking, p.seeking_count, p.avatar_url
  from public.profiles p
  left join public.teams t on t.id = p.team_id
  where p.show_on_wall and not p.is_banned and coalesce(nullif(p.nickname, ''), p.name) <> ''
  order by p.created_at desc
  limit least(greatest(coalesce(p_limit, 60), 1), 200);
$$;
grant execute on function public.participants_wall(int) to anon, authenticated;

drop function if exists public.team_members(uuid);
create or replace function public.team_members(p_team_id uuid)
returns table (id uuid, name text, github text, affiliation text, is_leader boolean, astro_level smallint, ai_level smallint, avatar_url text)
language sql stable security definer set search_path = public as $$
  select p.id, coalesce(nullif(p.nickname, ''), p.name), p.github, p.affiliation, (t.leader_id = p.id), p.astro_level, p.ai_level, p.avatar_url
  from public.profiles p join public.teams t on t.id = p.team_id
  where p.team_id = p_team_id and (p_team_id = public.my_team_id() or public.is_admin())
  order by (t.leader_id = p.id) desc, p.created_at;
$$;
grant execute on function public.team_members(uuid) to authenticated;

notify pgrst, 'reload schema';

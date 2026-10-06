-- A permanent 9-digit UID per participant, like a game account number: 100000001 for the earliest
-- registered profile, then ascending by registration order (profiles.created_at, ties by id). New
-- profiles get the next number from a sequence. Shown only to the user themselves through me().
--
-- Additive only: one sequence, one private table, one trigger on profiles and me() gains a 'uid' key.
-- No existing row or column changes. The UIDs live in a private table (not a profiles column) so no
-- public select, view or leaderboard can ever expose them. A UID is never reused: a deleted profile
-- keeps its number retired (on delete cascade removes the row, the sequence never goes back).

-- Hold new registrations for the moment between the backfill and the trigger, so none is missed.
-- Reads are not blocked; profile writes wait only for this short transaction (creating the trigger takes this lock anyway).
lock table public.profiles in share row exclusive mode;

create sequence if not exists private.profile_uid_seq as bigint minvalue 100000001 maxvalue 999999999 start 100000001;
revoke all on sequence private.profile_uid_seq from public, anon, authenticated;

create table if not exists private.profile_uids(
  profile_id uuid primary key references public.profiles(id) on delete cascade,
  uid bigint not null unique default nextval('private.profile_uid_seq') check (uid between 100000001 and 999999999),
  assigned_at timestamptz not null default now()
);
revoke all on private.profile_uids from public, anon, authenticated;

-- Backfill in registration order, numbered explicitly (not by insert order), then continue the sequence after it.
insert into private.profile_uids(profile_id, uid)
select p.id, coalesce((select max(uid) from private.profile_uids), 100000000)
             + row_number() over (order by p.created_at, p.id)
from public.profiles p
where not exists (select 1 from private.profile_uids u where u.profile_id = p.id)
order by p.created_at, p.id;

select setval('private.profile_uid_seq', coalesce((select max(uid) from private.profile_uids), 100000001),
              (select max(uid) from private.profile_uids) is not null);

-- Every new profile gets the next number in the same transaction that creates it.
create or replace function private.assign_profile_uid() returns trigger
language plpgsql security definer set search_path = public, pg_temp as $$
begin
  insert into private.profile_uids(profile_id) values (new.id) on conflict (profile_id) do nothing;
  return new;
end $$;
revoke all on function private.assign_profile_uid() from public, anon, authenticated;

drop trigger if exists profiles_assign_uid on public.profiles;
create trigger profiles_assign_uid after insert on public.profiles
  for each row execute function private.assign_profile_uid();

-- me(): unchanged except for the caller's own 'uid'.
create or replace function public.me()
returns jsonb language sql stable security definer set search_path = public as $$
  select case when auth.uid() is null then null else (
    select jsonb_build_object(
      'id', p.id, 'email', p.email, 'name', p.name, 'nickname', p.nickname, 'github', p.github, 'affiliation', p.affiliation, 'role', p.role,
      'looking_for_team', p.looking_for_team, 'seeking', p.seeking, 'seeking_count', p.seeking_count,
      'locale', p.locale, 'is_admin', p.is_admin, 'is_banned', p.is_banned,
      'astro_level', p.astro_level, 'ai_level', p.ai_level, 'city', p.city, 'contact', p.contact,
      'heard_from', p.heard_from, 'blurb', p.blurb, 'show_on_wall', p.show_on_wall, 'avatar_url', p.avatar_url,
      'uid', (select u.uid from private.profile_uids u where u.profile_id = p.id),
      'team', case when t.id is null then null else jsonb_build_object(
        'id', t.id, 'name', t.name, 'slug', t.slug, 'leader_id', t.leader_id, 'invite_code', t.invite_code,
        'project_idea', t.project_idea, 'github_repo', t.github_repo, 'max_size', t.max_size, 'is_locked', t.is_locked,
        'member_count', (select count(*) from public.profiles m where m.team_id = t.id)) end)
    from public.profiles p left join public.teams t on t.id = p.team_id where p.id = auth.uid()) end;
$$;

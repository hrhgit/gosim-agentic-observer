-- Organizer decision 2026-09-27: team registration closes automatically at 150 teams.
--
-- site_settings.team_limit (a JSON number; absent means 150) caps the number of teams that
-- count: hidden teams (organizers, acceptance and test teams) do not. Only creating a
-- team is refused ('team_limit_reached'); signing up an account and joining an existing
-- team through an invitation stay open. Admins bypass the cap.
--
-- create_team also regains the banned-account guard that 20260909000600 added and
-- 20260920000300 accidentally dropped when it redefined the function.

-- No row is inserted: an absent team_limit means 150, and existing data stays untouched.

-- The configured cap; a missing or malformed value falls back to 150.
create or replace function private.team_limit() returns integer
language sql stable security definer set search_path = public as $$
  select coalesce((
    select case
      when jsonb_typeof(value) = 'number' and (value #>> '{}') ~ '^[0-9]{1,6}$' then (value #>> '{}')::integer
      when jsonb_typeof(value) = 'string' and (value #>> '{}') ~ '^[0-9]{1,6}$' then (value #>> '{}')::integer
    end
    from public.site_settings where key = 'team_limit'), 150)
$$;

create or replace function private.counted_teams() returns integer
language sql stable security definer set search_path = public as $$
  select count(*)::integer from public.teams where not is_hidden
$$;
revoke all on function private.team_limit(), private.counted_teams() from public, anon, authenticated;

-- Public: how many team places remain. The count of visible teams is already public
-- through the team directory.
create or replace function public.team_capacity() returns jsonb
language sql stable security definer set search_path = public as $$
  select jsonb_build_object('limit', l, 'teams', n, 'remaining', greatest(l - n, 0), 'full', n >= l)
  from (select private.team_limit() as l, private.counted_teams() as n) x
$$;
grant execute on function public.team_capacity() to anon, authenticated;

create or replace function public.create_team(p_name text, p_max_size integer default 3, p_project_idea text default '', p_github_repo text default '')
returns uuid language plpgsql security definer set search_path = public as $$
declare v_uid uuid := auth.uid(); v_team uuid; v_slug text; v_name text := trim(coalesce(p_name, ''));
begin
  perform private.assert_not_banned();
  if (select team_id from public.profiles where id = v_uid for update) is not null then raise exception 'already_in_team'; end if;
  if length(v_name) < 2 or length(v_name) > 60 then raise exception 'name_length'; end if;
  if exists (select 1 from public.teams where lower(name) = lower(v_name)) then raise exception 'name_taken'; end if;
  if p_max_size is null or p_max_size < 1 or p_max_size > 3 then raise exception 'bad_size'; end if;
  if not public.is_admin() then
    -- Serialize creations so concurrent requests cannot overshoot the cap.
    perform pg_advisory_xact_lock(hashtextextended('public.create_team/team_limit', 0));
    if private.counted_teams() >= private.team_limit() then raise exception 'team_limit_reached'; end if;
  end if;
  v_slug := private.slugify(v_name);
  if exists (select 1 from public.teams where slug = v_slug) then v_slug := v_slug || '-' || substr(md5(random()::text), 1, 4); end if;
  insert into public.teams (name, slug, leader_id, max_size, project_idea, github_repo)
  values (v_name, v_slug, v_uid, p_max_size, left(coalesce(p_project_idea, ''), 2000), left(coalesce(p_github_repo, ''), 255))
  returning id into v_team;
  update public.profiles set team_id = v_team, looking_for_team = false where id = v_uid;
  perform private.audit('team.create', jsonb_build_object('team_id', v_team));
  return v_team;
end $$;

-- team_limit is public configuration, like registration_open.
drop policy if exists "settings public keys" on public.site_settings;
create policy "settings public keys" on public.site_settings for select to anon, authenticated
  using (key in ('registration_open', 'event', 'mechanics_public', 'registration_deadline', 'worker_heartbeat', 'credits_note', 'team_limit')
         or public.is_admin());

notify pgrst, 'reload schema';

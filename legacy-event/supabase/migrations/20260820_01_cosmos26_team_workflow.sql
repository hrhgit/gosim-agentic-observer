-- Transactional team workflow for the dedicated Cosmos26 Supabase project.
-- Apply after 20260820_00_cosmos26_registration.sql.

create or replace function public.create_cosmos26_team(
  p_name text,
  p_project_idea text default null,
  p_github_repo text default null,
  p_max_size integer default 4
) returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  v_user_id uuid := auth.uid();
  v_current_team uuid;
  v_team_id uuid;
begin
  if v_user_id is null then
    raise exception 'You must be logged in.';
  end if;
  if nullif(trim(p_name), '') is null then
    raise exception 'Team name is required.';
  end if;
  if p_max_size is null or p_max_size < 1 or p_max_size > 8 then
    raise exception 'Team size must be between 1 and 8.';
  end if;

  select team_id into v_current_team
  from public.profiles
  where id = v_user_id
  for update;

  if not found then
    raise exception 'Complete your participant profile first.';
  end if;
  if v_current_team is not null then
    raise exception 'You already belong to a team.';
  end if;

  insert into public.teams (name, leader_id, project_idea, github_repo, max_size)
  values (trim(p_name), v_user_id, nullif(trim(p_project_idea), ''), nullif(trim(p_github_repo), ''), p_max_size)
  returning id into v_team_id;

  update public.profiles
  set team_id = v_team_id, looking_for_team = false
  where id = v_user_id;

  return v_team_id;
end;
$$;

create or replace function public.join_cosmos26_team(p_team_id uuid)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  v_user_id uuid := auth.uid();
  v_current_team uuid;
  v_max_size integer;
  v_member_count integer;
begin
  if v_user_id is null then
    raise exception 'You must be logged in.';
  end if;

  select team_id into v_current_team
  from public.profiles
  where id = v_user_id
  for update;

  if not found then
    raise exception 'Complete your participant profile first.';
  end if;
  if v_current_team is not null then
    raise exception 'Leave your current team before joining another.';
  end if;

  select max_size into v_max_size
  from public.teams
  where id = p_team_id
  for update;

  if not found then
    raise exception 'Team not found.';
  end if;

  select count(*) into v_member_count
  from public.profiles
  where team_id = p_team_id;

  if v_max_size is not null and v_member_count >= v_max_size then
    raise exception 'This team is full.';
  end if;

  update public.profiles
  set team_id = p_team_id, looking_for_team = false
  where id = v_user_id;
end;
$$;

create or replace function public.leave_cosmos26_team()
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  v_user_id uuid := auth.uid();
  v_team_id uuid;
  v_leader_id uuid;
begin
  if v_user_id is null then
    raise exception 'You must be logged in.';
  end if;

  select team_id into v_team_id
  from public.profiles
  where id = v_user_id
  for update;

  if v_team_id is null then
    raise exception 'You are not on a team.';
  end if;

  select leader_id into v_leader_id
  from public.teams
  where id = v_team_id
  for update;

  if v_leader_id = v_user_id then
    raise exception 'Team leaders must disband the team instead.';
  end if;

  update public.profiles
  set team_id = null, looking_for_team = true
  where id = v_user_id;
end;
$$;

create or replace function public.disband_cosmos26_team(p_team_id uuid)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  v_user_id uuid := auth.uid();
begin
  if v_user_id is null then
    raise exception 'You must be logged in.';
  end if;

  perform 1
  from public.teams
  where id = p_team_id and leader_id = v_user_id
  for update;

  if not found then
    raise exception 'Only the team leader can disband this team.';
  end if;

  update public.profiles
  set team_id = null, looking_for_team = true
  where team_id = p_team_id;

  delete from public.teams where id = p_team_id;
end;
$$;

revoke all on function public.create_cosmos26_team(text, text, text, integer) from public;
revoke all on function public.join_cosmos26_team(uuid) from public;
revoke all on function public.leave_cosmos26_team() from public;
revoke all on function public.disband_cosmos26_team(uuid) from public;

grant execute on function public.create_cosmos26_team(text, text, text, integer) to authenticated;
grant execute on function public.join_cosmos26_team(uuid) to authenticated;
grant execute on function public.leave_cosmos26_team() to authenticated;
grant execute on function public.disband_cosmos26_team(uuid) to authenticated;

-- Membership changes go through the functions above, so clients cannot bypass
-- team capacity checks by directly changing profiles.team_id.
revoke update on public.profiles from authenticated;
grant update (name, github_id, role, discord, looking_for_team) on public.profiles to authenticated;
revoke insert on public.teams from authenticated;

-- Banned accounts must not be able to create or join teams (create_submission already checks is_banned).
create or replace function private.assert_not_banned() returns void language plpgsql security definer set search_path = public as $$
begin
  if auth.uid() is null then raise exception 'not_authenticated'; end if;
  if coalesce((select is_banned from public.profiles where id = auth.uid()), false) then raise exception 'banned'; end if;
end $$;

create or replace function public.create_team(p_name text, p_max_size integer default 4, p_project_idea text default '', p_github_repo text default '')
returns uuid language plpgsql security definer set search_path = public as $$
declare v_uid uuid := auth.uid(); v_team uuid; v_slug text; v_name text := trim(coalesce(p_name, ''));
begin
  perform private.assert_not_banned();
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
  perform private.assert_not_banned();
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

-- Daily project preparations per team: 10 -> 40 (matches the 40/day evaluation quota). Already applied to production.
CREATE OR REPLACE FUNCTION public.observer_create_project(p_title text, p_source_kind text, p_source_location text)
 RETURNS uuid
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public', 'pg_temp'
AS $function$
declare v_team uuid; v_project uuid; v_revision uuid;
begin
  perform private.assert_not_banned();
  select team_id into v_team from public.profiles where id = auth.uid() for update;
  if v_team is null then raise exception 'team_required'; end if;
  if not exists(select 1 from public.observer_phase_settings c join public.phases p on p.id = c.phase_id
    where c.projects_enabled and p.is_active and (p.ends_at is null or now() < p.ends_at)) then
    raise exception 'projects_not_enabled';
  end if;
  if p_source_kind = 'repository' then
    if p_source_location is null or p_source_location !~ '^https://github.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/?$' then
      raise exception 'invalid_repository_url';
    end if;
  elsif p_source_kind = 'zip' then
    if p_source_location is null or p_source_location !~ ('^' || v_team::text || '/[0-9a-f-]{36}/source[.]zip$') then
      raise exception 'invalid_upload_path';
    end if;
  else raise exception 'invalid_source_kind';
  end if;
  -- Serialize team-level admission; a pending queue cannot grow without bound.
  perform 1 from public.teams where id = v_team for update;
  if (select count(*) from public.observer_revisions r join public.observer_projects p on p.id=r.project_id
      where p.team_id=v_team and r.created_at >= (date_trunc('day',now() at time zone 'UTC') at time zone 'UTC')) >= 40 then
    raise exception 'preparation_daily_limit';
  end if;
  if (select count(*) from public.observer_revisions r join public.observer_projects p on p.id=r.project_id
      where p.team_id=v_team and r.status in ('queued','preparing')) >= 3 then
    raise exception 'preparation_limit';
  end if;
  insert into public.observer_projects(team_id, owner_id, title)
    values(v_team, auth.uid(), trim(p_title)) returning id into v_project;
  insert into public.observer_revisions(project_id, source_kind, source_location)
    values(v_project, p_source_kind, p_source_location) returning id into v_revision;
  return v_revision;
end $function$
;

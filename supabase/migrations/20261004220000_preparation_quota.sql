-- Daily project preparations follow the evaluation quota (owner decision 2026-10-04), replacing the
-- hard-coded number of 20261004200000. Function replacements only; no table or row changes.
--
--   limit  = the largest daily_batches among the contestant phases this team can prepare projects for
--            (active, projects enabled, not ended, not sealed, open to everyone or to this team);
--            10 (the original default) only if there is none
--   window = from the later of the start of the UTC day and the latest organizer quota reset of those
--            phases (private.observer_quota_resets), exactly like the evaluation count
--
-- private.observer_preparation_quota is the single definition used by admission (observer_create_project)
-- and display (three extra fields on every row of observer_evaluation_quota, which the website and both
-- CLIs already read through the portal "list" action: preparations_daily, preparations_used,
-- preparations_remaining).

create or replace function private.observer_preparation_quota(p_team uuid)
returns table(daily_limit integer, used integer, remaining integer, window_start timestamptz)
language sql stable security definer set search_path=public,pg_temp as $$
  with phases as (
    select s.phase_id, s.daily_batches from public.observer_phase_settings s join public.phases p on p.id=s.phase_id
    where s.projects_enabled and p.is_active and (p.ends_at is null or now() < p.ends_at) and not s.sealed
      and (s.access_team_id is null or s.access_team_id = p_team)
  ), q as (
    select coalesce((select max(daily_batches) from phases), 10)::integer as daily_limit,
      greatest(date_trunc('day', now() at time zone 'UTC') at time zone 'UTC',
        (select max(private.observer_quota_window_start(phase_id)) from phases)) as window_start
  )
  select q.daily_limit, u.used, greatest(0, q.daily_limit - u.used), q.window_start
  from q cross join lateral (select count(*)::integer as used from public.observer_revisions r
    join public.observer_projects p on p.id=r.project_id where p.team_id=p_team and r.created_at >= q.window_start) u
$$;
revoke all on function private.observer_preparation_quota(uuid) from public, anon, authenticated;

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
  if (select remaining from private.observer_preparation_quota(v_team)) <= 0 then
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
end $function$;

create or replace function public.observer_evaluation_quota()
returns jsonb language sql stable security definer set search_path=public,pg_temp as $$
  select coalesce(jsonb_agg(jsonb_build_object('phase_id',s.phase_id,'daily_batches',s.daily_batches,'used',q.used,
      'remaining',greatest(0,s.daily_batches-q.used),
      'resets_at',(date_trunc('day', now() at time zone 'UTC') at time zone 'UTC')+interval '1 day',
      'preparations_daily',pq.daily_limit,'preparations_used',pq.used,'preparations_remaining',pq.remaining)),'[]'::jsonb)
  from public.profiles u join public.observer_phase_settings s on public.observer_phase_visible(s.phase_id)
  cross join lateral (select private.observer_batches_used(u.team_id,s.phase_id) as used) q
  cross join lateral private.observer_preparation_quota(u.team_id) pq
  where u.id=auth.uid() and u.team_id is not null and not u.is_banned
$$;

notify pgrst,'reload schema';

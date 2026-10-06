-- Practice stays open while the online competition runs. In competition mode the RPC also names the open
-- complete-project practice board under its own key, so clients that read project_phase_id (or phase_id) as
-- "the entry phase" keep the online phase as their default. Practice mode answers exactly as before.
create or replace function public.current_competition()
returns jsonb language sql stable security definer set search_path=public,pg_temp as $$
  select jsonb_build_object('mode',m.mode,'phase_id',coalesce(m.phase_id,
    (select id from public.phases where slug=case when m.mode='practice' then 'practice' else 'online' end)))
    || coalesce((select jsonb_build_object(case when m.mode='practice' then 'project_phase_id' else 'practice_phase_id' end,p.id)
      from public.phases p join public.observer_phase_settings s on s.phase_id=p.id
      where p.slug='practice-projects' and p.is_active and s.projects_enabled and s.access_team_id is null
        and (p.starts_at is null or now()>=p.starts_at) and (p.ends_at is null or now()<p.ends_at)),'{}'::jsonb)
  from private.observer_site_mode m where m.id
$$;

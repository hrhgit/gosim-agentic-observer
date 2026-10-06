-- After the online phase ends, practice stays usable indefinitely and becomes the default entry.
-- In competition mode the RPC keeps answering practice_phase_id; once the competition phase has ended it also
-- answers project_phase_id (the key clients already prefer over phase_id), so the website and every installed
-- CLI default to practice without a client update. Before the end, and in practice mode, nothing changes.
create or replace function public.current_competition()
returns jsonb language sql stable security definer set search_path=public,pg_temp as $$
  with m as (select m.mode,coalesce(m.phase_id,(select id from public.phases
      where slug=case when m.mode='practice' then 'practice' else 'online' end)) phase_id
    from private.observer_site_mode m where m.id),
  practice as (select p.id from public.phases p join public.observer_phase_settings s on s.phase_id=p.id
    where p.slug='practice-projects' and p.is_active and s.projects_enabled and s.access_team_id is null
      and (p.starts_at is null or now()>=p.starts_at) and (p.ends_at is null or now()<p.ends_at))
  select jsonb_build_object('mode',m.mode,'phase_id',m.phase_id)
    || coalesce((select case when m.mode='practice' then jsonb_build_object('project_phase_id',p.id)
        else jsonb_build_object('practice_phase_id',p.id)
          || case when exists(select 1 from public.phases o where o.id=m.phase_id and o.ends_at is not null and now()>=o.ends_at)
             then jsonb_build_object('project_phase_id',p.id) else '{}'::jsonb end end
      from practice p),'{}'::jsonb)
  from m
$$;

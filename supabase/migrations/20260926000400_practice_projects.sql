-- Playground complete-project board. While the site is in practice mode, an
-- active phase with the slug 'practice-projects' runs next to CSV practice:
-- teams choose CSV (the practice phase) or a complete project (this phase),
-- each with its own board. Its runs use the team's own model key only.
create or replace function public.current_competition()
returns jsonb language sql stable security definer set search_path=public,pg_temp as $$
  select jsonb_build_object('mode',m.mode,'phase_id',coalesce(m.phase_id,
    (select id from public.phases where slug=case when m.mode='practice' then 'practice' else 'online' end)))
    -- The key only appears when a board is open, so existing clients see the same object as before.
    || coalesce((select jsonb_build_object('project_phase_id',p.id) from public.phases p
      join public.observer_phase_settings s on s.phase_id=p.id
      where m.mode='practice' and p.slug='practice-projects' and p.is_active and s.projects_enabled and s.access_team_id is null
        and (p.starts_at is null or now()>=p.starts_at) and (p.ends_at is null or now()<p.ends_at)),'{}'::jsonb)
  from private.observer_site_mode m where m.id
$$;

create or replace function private.observer_personal_models_only(p_run uuid)
returns boolean language sql stable security definer set search_path=public,pg_temp as $$
  select exists(select 1 from public.observer_runs r join public.observer_batches b on b.id=r.batch_id
    join public.phases p on p.id=b.phase_id where r.id=p_run and (p.counts_for_final or p.slug='online'
      or p.slug like 'observer-acceptance-%' or p.slug='practice-projects'
      -- Preparation uses its own restricted phase, even during the competition.
      -- That shared phase must not silently spend organizer credits either.
      or exists(select 1 from private.observer_site_mode where id and mode='competition')))
$$;
revoke all on function private.observer_personal_models_only(uuid) from public,anon,authenticated;

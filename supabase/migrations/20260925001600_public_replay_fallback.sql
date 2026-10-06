-- Online evaluations keep their scenario data and detailed results private.
-- The homepage must retain a public legacy replay when such a phase opens,
-- rather than asking the legacy board for an online phase with no legacy rows.
create or replace function public.champion_run()
returns jsonb language sql stable security definer set search_path = public as $$
  select jsonb_build_object(
    'team_id', l.team_id,
    'team_name', l.team_name,
    'submission_id', l.best_submission_id,
    'leader_github', l.leader_github,
    'scenario_slug', c.scen,
    'report_path', c.path,
    'score', l.total_score,
    'scored_at', l.scored_at)
  from public.phases p
  cross join lateral public.leaderboard(p.slug, 1) l
  cross join lateral (
    select (storage.foldername(o.name))[3] as scen, o.name as path
    from storage.objects o
    join public.scenarios sc on sc.slug = (storage.foldername(o.name))[3]
    where o.bucket_id = 'results'
      and (storage.foldername(o.name))[2] = 'sub-' || l.best_submission_id
      and storage.filename(o.name) = 'report.json'
      and sc.is_active and sc.weather_public and sc.tiles_public
    order by sc.n_slots asc
    limit 1
  ) c
  where p.is_active and p.leaderboard_mode <> 'hidden'
    and not exists (
      select 1 from public.observer_phase_settings settings
      where settings.phase_id = p.id
        and (settings.projects_enabled or settings.local_sessions_enabled)
    )
  order by (p.counts_for_final and public.phase_status(p) in ('open', 'closed')) desc,
    (public.phase_status(p) = 'open') desc, p.sort_order, p.id
  limit 1;
$$;

notify pgrst, 'reload schema';

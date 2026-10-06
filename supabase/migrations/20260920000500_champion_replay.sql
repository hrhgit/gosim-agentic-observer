-- The homepage console renders the CURRENT champion's real run, not a canned demo.
--   champion_run()          : who leads the default board right now, their leader's GitHub handle,
--                             and where the score report of their best submission lives — picking the
--                             smallest fully-public scenario of that submission so the client can also
--                             fetch the matching weather and tiles.
--   champion_report_path()  : the one results-bucket object the public may read.
--   storage policies        : results bucket opens exactly that one report to everyone; the scenarios
--                             bucket now matches what the resources page promises (config and reference
--                             data always downloadable, weather/forecasts/events per scenario flags).

create or replace function public.champion_run()
returns jsonb language sql stable security definer set search_path = public as $$
  select jsonb_build_object(
    'team_id', l.team_id,
    'team_name', l.team_name,
    'submission_id', l.best_submission_id,
    'leader_github', (select p.github from public.teams t join public.profiles p on p.id = t.leader_id where t.id = l.team_id),
    'scenario_slug', c.scen,
    'report_path', c.path,
    'score', l.total_score,
    'scored_at', l.scored_at)
  from (select * from public.leaderboard(null, 1) limit 1) l
  left join lateral (
    select (storage.foldername(o.name))[3] as scen, o.name as path
    from storage.objects o
    join public.scenarios sc on sc.slug = (storage.foldername(o.name))[3]
    where o.bucket_id = 'results'
      and (storage.foldername(o.name))[2] = 'sub-' || l.best_submission_id
      and storage.filename(o.name) = 'report.json'
      and sc.is_active and sc.weather_public and sc.tiles_public
    order by sc.n_slots asc
    limit 1
  ) c on true
  where l.best_submission_id is not null;
$$;
grant execute on function public.champion_run() to anon, authenticated;

create or replace function public.champion_report_path()
returns text language sql stable security definer set search_path = public as $$
  select public.champion_run() ->> 'report_path';
$$;
grant execute on function public.champion_report_path() to anon, authenticated;

drop policy if exists "champion report public" on storage.objects;
create policy "champion report public" on storage.objects for select to anon, authenticated
using (bucket_id = 'results' and name = public.champion_report_path());

-- Scenario files: configuration and reference data are always downloadable (as the resources page
-- states); weather, forecasts and events follow the per-scenario visibility flags.
drop policy if exists "scenario files read" on storage.objects;
create policy "scenario files read" on storage.objects for select to anon, authenticated
using (
  bucket_id = 'scenarios' and (
    public.is_admin() or exists (
      select 1 from public.scenarios s
      where s.slug = (storage.foldername(objects.name))[1] and s.is_active and (
        (storage.foldername(objects.name))[2] = 'config'
        or storage.filename(objects.name) in (
          'night_calendar.csv', 'slots.csv', 'targets.csv', 'tile_windows.csv',
          'observation_requests.csv', 'observation_request_tiles.csv',
          'scenario_manifest.json', 'calendar_metadata.json', 'catalog_metadata.json',
          'observation_request_metadata.json', 'score_config.json')
        or (s.tiles_public and storage.filename(objects.name) = 'tiles.csv')
        or (s.weather_public and storage.filename(objects.name) in ('weather.csv', 'weather_metadata.json'))
        or (s.forecasts_public and storage.filename(objects.name) = 'weather_forecasts.csv')
        or (s.events_public and storage.filename(objects.name) = 'weather_events.csv'))))
);

notify pgrst, 'reload schema';

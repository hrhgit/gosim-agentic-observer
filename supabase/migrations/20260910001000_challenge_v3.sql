-- Challenge environment v3 (example3 contract): scenario file sets, score-report-v3 metrics, replay artifacts.

-- scenarios: a scenario is now a directory (config/*.json + outputs/reference/*.csv). Per-file visibility flags.
alter table public.scenarios add column if not exists global_wallclock_seconds integer not null default 7200;
alter table public.scenarios add column if not exists events_public boolean not null default false;
alter table public.scenarios add column if not exists forecasts_public boolean not null default true;
alter table public.scenarios add column if not exists n_requests integer not null default 0;
alter table public.scenarios add column if not exists n_targets integer not null default 0;
alter table public.scenarios add column if not exists manifest jsonb not null default '{}'::jsonb;
alter table public.scenarios add column if not exists contract text not null default 'challenge-score-v3';

-- submissions / evaluations: score breakdown of score-report-v3
alter table public.submissions add column if not exists base_science double precision;
alter table public.submissions add column if not exists program_bonus double precision;
alter table public.submissions add column if not exists request_reward double precision;
alter table public.submissions add column if not exists penalty_total double precision;
alter table public.submissions add column if not exists completed_tiles integer;
alter table public.submissions add column if not exists required_missing integer;
alter table public.submissions add column if not exists flexible_shortfall integer;
alter table public.submissions add column if not exists termination_reason text not null default '';

alter table public.evaluations add column if not exists base_science double precision;
alter table public.evaluations add column if not exists program_bonus double precision;
alter table public.evaluations add column if not exists request_reward double precision;
alter table public.evaluations add column if not exists penalty_total double precision;
alter table public.evaluations add column if not exists completed_tiles integer;
alter table public.evaluations add column if not exists required_missing integer;
alter table public.evaluations add column if not exists flexible_shortfall integer;
alter table public.evaluations add column if not exists termination_reason text not null default '';
alter table public.evaluations add column if not exists accounted_wallclock_seconds double precision;
alter table public.evaluations add column if not exists replay_path text not null default '';
alter table public.evaluations add column if not exists workflow_path text not null default '';

-- storage: per-file visibility inside scenarios/<slug>/config/* and scenarios/<slug>/outputs/reference/*
drop policy if exists "scenario files read" on storage.objects;
create policy "scenario files read" on storage.objects for select to anon, authenticated
using (
  bucket_id = 'scenarios' and (
    public.is_admin() or exists (
      select 1 from public.scenarios s
      where s.slug = (storage.foldername(objects.name))[1] and s.is_active and (
        (storage.foldername(objects.name))[2] = 'config'
        or storage.filename(objects.name) in ('night_calendar.csv', 'slots.csv', 'tiles.csv', 'targets.csv', 'tile_windows.csv',
                                              'observation_requests.csv', 'observation_request_tiles.csv', 'scenario_manifest.json',
                                              'calendar_metadata.json', 'catalog_metadata.json', 'observation_request_metadata.json')
        or (storage.filename(objects.name) = 'weather.csv' and s.weather_public)
        or (storage.filename(objects.name) = 'weather_metadata.json' and s.weather_public)
        or (storage.filename(objects.name) = 'weather_forecasts.csv' and s.forecasts_public)
        or (storage.filename(objects.name) = 'weather_events.csv' and s.events_public))))
);

-- leaderboard with the v3 breakdown (keeps the old column names for compatibility and adds the new ones)
drop function if exists public.leaderboard(text, integer);
create or replace function public.leaderboard(p_phase_slug text default null, p_limit integer default 500)
returns table (
  rank integer, team_id uuid, team_name text, team_slug text, total_score double precision, science_score double precision,
  completion_rate double precision, uniformity_score double precision, base_science double precision, program_bonus double precision,
  request_reward double precision, penalty_total double precision, completed_tiles integer, required_missing integer,
  submission_count bigint, best_submission_id bigint, kind public.submission_kind, scored_at timestamptz)
language plpgsql stable security definer set search_path = public as $$
declare v_phase public.phases; v_admin boolean := public.is_admin();
begin
  if p_phase_slug is null then
    select * into v_phase from public.phases p where p.is_active
    order by (p.counts_for_final and public.phase_status(p) in ('open', 'closed')) desc, (public.phase_status(p) = 'open') desc, p.sort_order limit 1;
  else
    select * into v_phase from public.phases p where p.slug = p_phase_slug and (p.is_active or v_admin);
  end if;
  if v_phase.id is null then return; end if;
  if v_phase.leaderboard_mode = 'hidden' and not v_admin then return; end if;
  return query
  with scored as (
    select s.*, t.name as tname, t.slug as tslug
    from public.submissions s join public.teams t on t.id = s.team_id
    where s.phase_id = v_phase.id and s.status = 'scored' and not s.is_excluded and s.score is not null and (not t.is_hidden or v_admin)
  ), best as (
    select distinct on (s.team_id) s.* from scored s order by s.team_id, s.score desc, s.created_at asc
  ), counts as (
    select s.team_id, count(*) as n from scored s group by s.team_id
  )
  select (rank() over (order by b.score desc))::integer, b.team_id, b.tname, b.tslug, b.score, b.science_score, b.completion, b.uniformity,
         b.base_science, b.program_bonus, b.request_reward, b.penalty_total, b.completed_tiles, b.required_missing,
         c.n, b.id, b.kind, coalesce(b.finished_at, b.created_at)
  from best b join counts c on c.team_id = b.team_id
  order by b.score desc, b.created_at asc
  limit greatest(1, least(coalesce(p_limit, 500), 1000));
end $$;
grant execute on function public.leaderboard(text, integer) to anon, authenticated;

-- realtime for evaluations already added; nothing else.

-- Downloadable inputs of v4 cards (organizer decision 2026-09-28).
--   practice cards (alpha, beta): their full public file set in the 'scenarios'
--     bucket (config/, public/ and, when weather, forecasts and events are all
--     public, truth/) is readable by anyone while the card is in an active,
--     started, public non-final phase (practice-projects);
--   formal cards (A-D): their public-input files (config/, public/) are readable
--     only while the card is in the active, started, public 'online' phase AND
--     the site is in competition mode (from the 2026-10-04 16:00 UTC switch);
--   hidden cards (E-H): never. A card linked to any sealed phase is never
--     released, whatever the list says.
-- Which files are released is an explicit per-file list,
-- private.observer_scenario_public_files, written by
-- scripts/configure-v4-phases.py; it is empty for every v3 scenario, so v3
-- visibility is unchanged. The private evaluation bundle lives in another
-- bucket and is never listed here.
create table if not exists private.observer_scenario_public_files (
  scenario_id uuid not null references public.scenarios(id),
  path text not null check (path ~ '^(config|public|truth)/[A-Za-z0-9_.-]+$' and path !~ '\.\.'),
  release text not null check (release in ('practice','competition')),
  primary key (scenario_id, path)
);
revoke all on private.observer_scenario_public_files from public,anon,authenticated;

create or replace function public.observer_released_scenario_file(p_name text)
returns boolean language sql stable security definer set search_path=public,pg_temp as $$
  select exists(
    select 1 from public.scenarios s join private.observer_scenario_public_files f on f.scenario_id=s.id
    where s.slug=split_part(p_name,'/',1) and p_name=s.slug||'/'||f.path and s.is_active
      and not exists(select 1 from public.phase_scenarios ps join public.observer_phase_settings c on c.phase_id=ps.phase_id
        where ps.scenario_id=s.id and c.sealed)
      and case f.release
        when 'practice' then exists(select 1 from public.phase_scenarios ps join public.phases p on p.id=ps.phase_id
            left join public.observer_phase_settings c on c.phase_id=p.id
            where ps.scenario_id=s.id and p.is_active and not p.counts_for_final and p.slug<>'online'
              and c.access_team_id is null and (p.starts_at is null or now()>=p.starts_at))
        when 'competition' then exists(select 1 from public.phase_scenarios ps join public.phases p on p.id=ps.phase_id
            left join public.observer_phase_settings c on c.phase_id=p.id
            join private.observer_site_mode m on m.id and m.mode='competition' and (m.phase_id is null or m.phase_id=p.id)
            where ps.scenario_id=s.id and p.slug='online' and p.is_active and c.access_team_id is null
              and p.starts_at is not null and now()>=p.starts_at)
        else false end)
$$;
revoke all on function public.observer_released_scenario_file(text) from public;
grant execute on function public.observer_released_scenario_file(text) to anon,authenticated,service_role;

-- Permissive: released files are readable (the v3 policy "scenario files read"
-- only knows the v3 file names).
drop policy if exists "released scenario files read" on storage.objects;
create policy "released scenario files read" on storage.objects for select to anon,authenticated
  using (bucket_id='scenarios' and public.observer_released_scenario_file(name));

-- Restrictive (20260927000100): formal and sealed sources stay private, except
-- a file released above. Identical otherwise.
drop policy if exists "formal source files remain private" on storage.objects;
create policy "formal source files remain private" on storage.objects
as restrictive for select to anon,authenticated
using (bucket_id<>'scenarios' or public.is_admin()
       or not public.observer_formal_source((storage.foldername(name))[1])
       or public.observer_released_scenario_file(name));

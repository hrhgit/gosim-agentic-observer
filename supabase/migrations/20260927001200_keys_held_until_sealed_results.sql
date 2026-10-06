-- Saved model keys stay until the hidden final has been run and its results
-- published.
--
-- Rule (2026-09-27): after the online phase freezes, organizers run each team's
-- final version once on the hidden scenario of the sealed phase ('final-hidden').
-- A team whose program calls a model must use stored mode for that run.
--
-- The automatic purge (20260926000600) already counts the sealed phase: it is
-- active and counts_for_final, so keys are not purged at the online ends_at but
-- only retention (7 days) after the latest such phase end ('final-hidden' ends
-- 2026-10-17 15:59Z). The hidden run itself does not check the sealed phase's
-- ends_at, though, so a late run or a verification re-run could still find the
-- keys gone. From now on a sealed phase that can use the team's key holds every
-- key of the team until its results are published (leaderboard_mode
-- 'published', i.e. verified); the retention period still counts from its
-- ends_at. Only private.observer_key_purge_after changes; the manual
-- public.observer_purge_provider_keys() is unchanged.

create or replace function private.observer_key_purge_after(p_team uuid,p_saved timestamptz)
returns timestamptz language sql stable security definer set search_path=public,pg_temp as $$
  with competition as (select exists(select 1 from private.observer_site_mode where id and mode='competition') on_),
  phases as (
    select p.id,p.ends_at from public.phases p
      left join public.observer_phase_settings s on s.phase_id=p.id, competition c
     where p.is_active and (s.access_team_id is null or s.access_team_id=p_team)
       and (p.counts_for_final or p.slug='online' or p.slug like 'observer-acceptance-%'
            or p.slug='practice-projects' or (c.on_ and s.phase_id is not null)))
  select case when not exists(select 1 from phases) or exists(select 1 from phases where ends_at is null)
      or exists(select 1 from phases where public.observer_phase_sealed(id)) then null
    else greatest((select max(ends_at) from phases),p_saved)
      +(select retention from private.observer_key_retention where id) end
$$;
revoke all on function private.observer_key_purge_after(uuid,timestamptz) from public,anon,authenticated;

notify pgrst,'reload schema';

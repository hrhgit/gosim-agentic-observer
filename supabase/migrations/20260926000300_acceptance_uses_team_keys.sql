-- Internal acceptance phases (observer-acceptance-*, created by
-- scripts/configure-observer-acceptance.py) test the formal path end to end, so
-- their runs use the acceptance team's own model key exactly like formal runs
-- and never spend organizer credits. Every other condition is unchanged.
create or replace function private.observer_personal_models_only(p_run uuid)
returns boolean language sql stable security definer set search_path=public,pg_temp as $$
  select exists(select 1 from public.observer_runs r join public.observer_batches b on b.id=r.batch_id
    join public.phases p on p.id=b.phase_id where r.id=p_run and (p.counts_for_final or p.slug='online'
      or p.slug like 'observer-acceptance-%'
      -- Preparation uses its own restricted phase, even during the competition.
      -- That shared phase must not silently spend organizer credits either.
      or exists(select 1 from private.observer_site_mode where id and mode='competition')))
$$;
revoke all on function private.observer_personal_models_only(uuid) from public,anon,authenticated;

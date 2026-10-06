-- Hidden-weather scenarios leaked their generation parameters: the "scenarios read" policy is row level, so
-- anon (the key embedded in the site bundle) could read seed, checksum and manifest for eval-a/eval-b and
-- rebuild the hidden weather locally. manifest.files carries a per-file sha256, which also makes a six-digit
-- seed brute-forceable offline. Move all three columns off the participant-visible surface with column grants
-- and hand them to admins through a dedicated RPC instead.
--
-- Row-level policies still apply on top of this; service_role (the worker) is unaffected and keeps reading
-- checksum for its scenario cache.

revoke select on public.scenarios from anon, authenticated;
grant select (
  id, slug, name, description,
  weather_public, forecasts_public, events_public, tiles_public, is_active,
  n_slots, n_nights, n_tiles, n_targets, n_requests,
  global_wallclock_seconds, contract, created_at
) on public.scenarios to anon, authenticated;

-- Admin console reads the withheld columns here. Returns nothing at all for non-admins.
create or replace function public.admin_scenarios()
returns table (
  id uuid, slug text, name text, description text,
  weather_public boolean, forecasts_public boolean, events_public boolean, tiles_public boolean,
  is_active boolean, n_slots integer, n_nights integer, n_tiles integer, n_targets integer, n_requests integer,
  global_wallclock_seconds integer, contract text, created_at timestamptz,
  seed integer, checksum text, manifest jsonb
)
language sql stable security definer set search_path = public as $$
  select s.id, s.slug, s.name, s.description,
         s.weather_public, s.forecasts_public, s.events_public, s.tiles_public,
         s.is_active, s.n_slots, s.n_nights, s.n_tiles, s.n_targets, s.n_requests,
         s.global_wallclock_seconds, s.contract, s.created_at,
         s.seed, s.checksum, s.manifest
  from public.scenarios s
  where public.is_admin()
  order by s.slug;
$$;
revoke execute on function public.admin_scenarios() from public, anon;
grant execute on function public.admin_scenarios() to authenticated;

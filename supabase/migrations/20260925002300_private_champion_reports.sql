-- A score report contains the full action trace and can reconstruct decisions.csv.
-- Being first on a public board must never make a team's private artifacts public.
drop policy if exists "champion report public" on storage.objects;

-- Keep old clients compatible while they age out: no artifact location or run is
-- advertised. The public homepage uses the bundled organizer demonstration.
create or replace function public.champion_run()
returns jsonb language sql stable security definer set search_path = public as $$
  select null::jsonb;
$$;
create or replace function public.champion_report_path()
returns text language sql stable security definer set search_path = public as $$
  select null::text;
$$;

-- Existing owner-team/admin storage policies and all scores remain unchanged.
notify pgrst, 'reload schema';

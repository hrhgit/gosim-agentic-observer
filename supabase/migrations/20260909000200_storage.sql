-- Storage buckets and object policies.
--   scenarios   : <slug>/weather.csv | tiles.csv | score_config.json   (written by the worker; public per scenario flags)
--   submissions : <team_id>/<file>                                     (uploaded by team members)
--   results     : <team_id>/sub-<id>/<scenario>/report.json|decisions.csv|agent.log (written by the worker / scorer function)

insert into storage.buckets (id, name, public, file_size_limit)
values ('scenarios', 'scenarios', false, 52428800), ('submissions', 'submissions', false, 20971520), ('results', 'results', false, 52428800)
on conflict (id) do update set public = excluded.public, file_size_limit = excluded.file_size_limit;

-- scenario files: config always readable; weather/tiles per scenario flags; admins everything
drop policy if exists "scenario files read" on storage.objects;
create policy "scenario files read" on storage.objects for select to anon, authenticated
using (
  bucket_id = 'scenarios' and (
    public.is_admin() or exists (
      select 1 from public.scenarios s
      where s.slug = (storage.foldername(objects.name))[1] and s.is_active and (
        storage.filename(objects.name) = 'score_config.json'
        or (storage.filename(objects.name) = 'weather.csv' and s.weather_public)
        or (storage.filename(objects.name) = 'tiles.csv' and s.tiles_public))))
);

-- submissions: team members upload into their own folder and read it back; admins read all
drop policy if exists "submission upload own team" on storage.objects;
create policy "submission upload own team" on storage.objects for insert to authenticated
with check (bucket_id = 'submissions' and (storage.foldername(name))[1] = public.my_team_id()::text);
drop policy if exists "submission read own team" on storage.objects;
create policy "submission read own team" on storage.objects for select to authenticated
using (bucket_id = 'submissions' and ((storage.foldername(name))[1] = public.my_team_id()::text or public.is_admin()));

-- results: read-only for the owning team and admins (written with the service role)
drop policy if exists "results read own team" on storage.objects;
create policy "results read own team" on storage.objects for select to authenticated
using (bucket_id = 'results' and ((storage.foldername(name))[1] = public.my_team_id()::text or public.is_admin()));

-- admins may upload scenario files from the dashboard (the worker uses the service role)
drop policy if exists "scenario files admin write" on storage.objects;
create policy "scenario files admin write" on storage.objects for insert to authenticated
with check (bucket_id = 'scenarios' and public.is_admin());
drop policy if exists "scenario files admin update" on storage.objects;
create policy "scenario files admin update" on storage.objects for update to authenticated
using (bucket_id = 'scenarios' and public.is_admin()) with check (bucket_id = 'scenarios' and public.is_admin());

-- Fix: inside the EXISTS subquery an unqualified `name` resolved to scenarios.name. Qualify the object name.
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

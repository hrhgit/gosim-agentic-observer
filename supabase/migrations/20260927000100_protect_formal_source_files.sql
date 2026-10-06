-- Source bundles contain generation seeds in configs, metadata and manifests.
-- Hiding scenario rows until starts_at is not sufficient: after opening, the
-- old Storage policy would expose those files again. Formal participants get
-- public observations from the session engine, never raw generator inputs.
create function public.observer_formal_source(p_slug text)
returns boolean language sql stable security definer set search_path=public,pg_temp as $$
  select exists (
    select 1 from public.scenarios s
    join public.phase_scenarios ps on ps.scenario_id=s.id
    join public.phases p on p.id=ps.phase_id
    where s.slug=p_slug and (p.counts_for_final or p.slug='online')
  )
$$;
revoke all on function public.observer_formal_source(text) from public;
grant execute on function public.observer_formal_source(text) to anon,authenticated,service_role;

-- Restrictive so a visibility flag, another permissive policy, or the scheduled
-- publication of legacy weather cannot reopen the source bundle. Keep originals
-- for trusted scoring/reproduction and preserve every existing score and object.
create policy "formal source files remain private" on storage.objects
as restrictive for select to anon,authenticated
using (bucket_id<>'scenarios' or public.is_admin()
       or not public.observer_formal_source((storage.foldername(name))[1]));

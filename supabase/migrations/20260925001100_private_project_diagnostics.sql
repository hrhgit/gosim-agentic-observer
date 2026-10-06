-- Expose only bounded diagnostics to the current team. Job payloads, OIDC
-- metadata, capabilities and trusted scenario inputs remain private.
create function public.observer_diagnostics(p_revision uuid default null,p_run uuid default null)
returns jsonb language plpgsql stable security definer set search_path=public,pg_temp as $$
declare team uuid; result jsonb;
begin
  select team_id into team from public.profiles where id=auth.uid() and not is_banned;
  if team is null or (p_revision is null)=(p_run is null) then raise exception 'diagnostics_not_found';end if;
  if p_revision is not null and not exists(select 1 from public.observer_revisions r
    join public.observer_projects p on p.id=r.project_id where r.id=p_revision and p.team_id=team)
    then raise exception 'diagnostics_not_found';end if;
  if p_run is not null and not exists(select 1 from public.observer_runs r
    join public.observer_batches b on b.id=r.batch_id where r.id=p_run and b.team_id=team)
    then raise exception 'diagnostics_not_found';end if;
  select coalesce(jsonb_agg(jsonb_build_object('kind',j.kind,'status',j.status,
    'code',left(coalesce(j.result->'diagnostics'->>'code',nullif(j.error,''),j.status),100),
    'log',left(coalesce(j.result->'diagnostics'->>'log',''),32768),'finished_at',j.finished_at)
    order by j.created_at),'[]'::jsonb) into result
  from private.observer_jobs j
  left join public.observer_runs r on r.id=j.run_id
  left join public.observer_batches b on b.id=r.batch_id
  where (p_run is not null and j.run_id=p_run)
    or (p_revision is not null and (j.revision_id=p_revision or (b.revision_id=p_revision and b.purpose='preview')));
  return result;
end $$;
revoke all on function public.observer_diagnostics(uuid,uuid) from public,anon;
grant execute on function public.observer_diagnostics(uuid,uuid) to authenticated;

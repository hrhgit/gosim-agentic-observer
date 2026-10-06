-- A long evaluation obtains a fresh repository-scoped credential when it needs
-- to store its result, rather than retaining an expired installation token.
create function public.observer_job_artifact_target(p_job uuid,p_github_run text,p_attempt text)
returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$
declare j private.observer_jobs;v_user uuid;v_id uuid;
begin
  select * into j from private.observer_jobs where id=p_job;
  if not found or j.status<>'claimed' or j.expires_at<=now() or j.github_run_id is distinct from p_github_run
    or j.github_run_attempt is distinct from p_attempt or j.kind not in ('prepare','engine') then
    raise exception 'artifact_access_denied'; end if;
  if not exists(select 1 from private.observer_installations where organization=j.organization and enabled) then
    raise exception 'artifact_access_denied'; end if;
  if j.kind='prepare' then
    select p.owner_id into v_user from public.observer_revisions r join public.observer_projects p on p.id=r.project_id
      where r.id=j.revision_id;
    v_id:=j.revision_id;
  else
    select b.user_id into v_user from public.observer_runs r join public.observer_batches b on b.id=r.batch_id where r.id=j.run_id;
    v_id:=j.run_id;
  end if;
  if v_user is null then raise exception 'artifact_access_denied'; end if;
  return jsonb_build_object('user_id',v_user,'artifact_id',v_id,'kind',j.kind);
end $$;
revoke all on function public.observer_job_artifact_target(uuid,text,text) from public,anon,authenticated;
grant execute on function public.observer_job_artifact_target(uuid,text,text) to service_role;

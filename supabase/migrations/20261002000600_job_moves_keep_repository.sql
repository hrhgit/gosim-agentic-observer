-- A job that moves to another organization keeps the owner's placement when
-- its participant repository must not change underneath it.
--
-- observer-job builds a preparation job's repository credential at claim time
-- from the owner's placement and refuses the claim when that repository is not
-- the one the job was scheduled with (invalid_job_payload). Moving the
-- placement together with a preparation job therefore made the moved job
-- unclaimable: the claim itself was already recorded, so the job then held its
-- slot until it expired.
--
-- observer_fallback_job no longer moves the placement at all: the self-hosted
-- fallback is a last resort for this one job, and the job's participant
-- repository (and the artifacts of its run) stay where they are; GitHub-hosted
-- jobs of the owner keep going to their organization.
-- observer_failover_job no longer moves it for preparation jobs (as already
-- for score jobs); runs keep the earlier behaviour.

create or replace function public.observer_fallback_job(p_job uuid,p_avoid text[] default '{}')
returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$
declare v private.observer_jobs; v_partner private.observer_jobs; v_target private.observer_installations;
  v_need integer;
begin
  -- One fallback decision at a time keeps the slot count exact.
  perform pg_advisory_xact_lock(hashtext('observer-fallback'));
  select * into v from private.observer_jobs where id=p_job for update;
  if not found or v.status not in ('queued','dispatched') or v.expires_at<=now() then
    raise exception 'job_unavailable'; end if;
  if v.runner='self-hosted' then raise exception 'job_conflict'; end if;
  select * into v_partner from private.observer_jobs p
    where v.run_id is not null and p.run_id=v.run_id and p.id<>v.id and p.runner='github-hosted'
      and p.status in ('queued','dispatched') and p.expires_at>now() for update;
  v_need:=case when v_partner.id is null then 1 else 2 end;
  select i.* into v_target from private.observer_installations i
  where i.enabled and not (i.organization=any(coalesce(p_avoid,'{}')))
    and i.fallback_capacity-(select count(*) from private.observer_jobs j
      where j.organization=i.organization and j.runner='self-hosted'
        and j.status in ('queued','dispatched','claimed') and j.expires_at>now())>=v_need
  order by i.organization=v.organization, substring(i.organization from '[0-9]+$')::integer
  limit 1;
  if not found then raise exception 'fallback_unavailable'; end if;
  update private.observer_jobs set organization=v_target.organization,repository_id=v_target.repository_id,
    organization_id=v_target.organization_id,workflow_sha=v_target.approved_sha,runner='self-hosted',
    status='queued',error='',dispatch_count=1,last_dispatch_at=now(),
    expires_at=greatest(expires_at,now()+interval '30 minutes')
    where id=p_job;
  if v_partner.id is not null then
    update private.observer_jobs set organization=v_target.organization,repository_id=v_target.repository_id,
      organization_id=v_target.organization_id,workflow_sha=v_target.approved_sha,runner='self-hosted',
      status='queued',error='',dispatch_count=0,last_dispatch_at=null,
      expires_at=greatest(expires_at,now()+interval '30 minutes')
      where id=v_partner.id;
  end if;
  update private.observer_sessions s set expires_at=s.expires_at+(now()-v.created_at)
    where s.expires_at>now() and s.deadline_at is null and s.run_id in
      (v.run_id,(select p.model_run_id from private.observer_preparations p where p.revision_id=v.revision_id));
  return jsonb_build_object('organization',v_target.organization,'approved_sha',v_target.approved_sha,
    'partner',v_partner.id);
end $$;

create or replace function public.observer_failover_job(p_job uuid, p_organization text)
returns void language plpgsql security definer set search_path to 'public', 'pg_temp' as $$
declare v private.observer_jobs; v_target private.observer_installations; v_owner uuid;
begin
  select * into v_target from private.observer_installations where organization=p_organization and enabled;
  if not found then raise exception 'runner_not_configured'; end if;
  select * into v from private.observer_jobs where id=p_job for update;
  if not found or v.status not in ('queued','dispatched') or v.expires_at<=now() then
    raise exception 'job_unavailable'; end if;
  if v.organization=p_organization then raise exception 'job_conflict'; end if;
  update private.observer_jobs set organization=p_organization,repository_id=v_target.repository_id,
    organization_id=v_target.organization_id,workflow_sha=v_target.approved_sha,error='',dispatch_count=0
    where id=p_job;
  if v.kind in ('score','prepare') then return; end if;
  select coalesce(b.user_id,pr.owner_id) into v_owner
    from private.observer_jobs j
    left join public.observer_runs r on r.id=j.run_id
    left join public.observer_batches b on b.id=r.batch_id
    left join public.observer_revisions v2 on v2.id=j.revision_id
    left join public.observer_projects pr on pr.id=v2.project_id
  where j.id=p_job;
  if v_owner is not null then
    update private.observer_placements set organization=p_organization
      where user_id=v_owner and organization=v.organization;
  end if;
end $$;

revoke all on function public.observer_fallback_job(uuid,text[]),public.observer_failover_job(uuid,text)
  from public,anon,authenticated;
grant execute on function public.observer_fallback_job(uuid,text[]),public.observer_failover_job(uuid,text) to service_role;

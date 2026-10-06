-- Up to twelve runner organizations share the GitHub Actions budget.
-- A participant's private repository and every job stay in one organization.
-- Placement is now recorded once and never recomputed: participants who already
-- have jobs keep their current organization; new participants go to the enabled
-- organization with the fewest placed participants.
alter table private.observer_installations drop constraint observer_installations_organization_check;
alter table private.observer_installations add constraint observer_installations_organization_check
  check (organization ~ '^AGENTIC-OBSERVER26-runner-([1-9]|1[0-2])$');
alter table private.observer_materializations drop constraint observer_materializations_archive_ref_check;
alter table private.observer_materializations add constraint observer_materializations_archive_ref_check
  check (archive_ref ~ '^github:AGENTIC-OBSERVER26-runner-([1-9]|1[0-2])/participant-[0-9a-f]{32}@[0-9a-f]{40}$');

create table private.observer_placements (
  user_id uuid primary key,
  organization text not null references private.observer_installations(organization),
  created_at timestamptz not null default now()
);
revoke all on private.observer_placements from public,anon,authenticated;

-- The organization of a participant's existing jobs (their repository lives there).
create function private.observer_job_organization(p_user uuid)
returns text language sql stable set search_path=public,pg_temp as $$
  select j.organization from private.observer_jobs j
    left join public.observer_runs r on r.id=j.run_id
    left join public.observer_batches b on b.id=r.batch_id
    left join public.observer_revisions v on v.id=j.revision_id
    left join public.observer_projects p on p.id=v.project_id
  where coalesce(b.user_id,p.owner_id)=p_user
  order by j.created_at,j.id limit 1
$$;
revoke all on function private.observer_job_organization(uuid) from public,anon,authenticated;

insert into private.observer_placements(user_id,organization)
  select distinct on (u) u,organization from (
    select coalesce(b.user_id,p.owner_id) u,j.organization,j.created_at,j.id from private.observer_jobs j
      left join public.observer_runs r on r.id=j.run_id
      left join public.observer_batches b on b.id=r.batch_id
      left join public.observer_revisions v on v.id=j.revision_id
      left join public.observer_projects p on p.id=v.project_id) x
  where u is not null order by u,created_at,id
  on conflict(user_id) do nothing;

create function public.observer_placement(p_user uuid)
returns text language plpgsql security definer set search_path=public,pg_temp as $$
declare v text;
begin
  if p_user is null then raise exception 'invalid_participant'; end if;
  select organization into v from private.observer_placements where user_id=p_user;
  if found then return v; end if;
  -- One placement decision at a time keeps the least-loaded choice consistent.
  perform pg_advisory_xact_lock(hashtext('observer-placement'));
  select organization into v from private.observer_placements where user_id=p_user;
  if found then return v; end if;
  -- A participant with jobs from before this migration keeps that organization.
  v:=private.observer_job_organization(p_user);
  if v is null then
    select i.organization into v from private.observer_installations i where i.enabled
      order by (select count(*) from private.observer_placements p where p.organization=i.organization),
        substring(i.organization from '[0-9]+$')::integer
      limit 1;
  end if;
  if v is null then raise exception 'runner_not_configured'; end if;
  insert into private.observer_placements(user_id,organization) values(p_user,v);
  return v;
end $$;
revoke all on function public.observer_placement(uuid) from public,anon,authenticated;
grant execute on function public.observer_placement(uuid) to service_role;

-- Placement by participant count alone concentrated heavy teams: in one week
-- runner-1 ran 1543 minutes of jobs while runner-8 ran 11. New participants now
-- go to the enabled organization with the fewest active jobs, then the least
-- Actions usage over the last seven days, then the fewest recorded placements.
-- Recorded placements are unchanged: a participant's private repository and
-- every later job stay in the organization that already hosts them.
create or replace function public.observer_placement(p_user uuid)
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
      order by
        (select count(*) from private.observer_jobs j where j.organization=i.organization
          and j.status in ('queued','dispatched','claimed') and j.expires_at>now()),
        (select coalesce(sum(extract(epoch from j.finished_at-j.claimed_at)),0) from private.observer_jobs j
          where j.organization=i.organization and j.claimed_at is not null
            and j.finished_at is not null and j.finished_at>now()-interval '7 days'),
        (select count(*) from private.observer_placements p where p.organization=i.organization),
        substring(i.organization from '[0-9]+$')::integer
      limit 1;
  end if;
  if v is null then raise exception 'runner_not_configured'; end if;
  insert into private.observer_placements(user_id,organization) values(p_user,v);
  return v;
end $$;
revoke all on function public.observer_placement(uuid) from public,anon,authenticated;
grant execute on function public.observer_placement(uuid) to service_role;

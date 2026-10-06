-- Every repository submission is pinned and preserved at submission time (owner
-- requirement "every version must be preserved").
--
-- Before: a repository-kind revision recorded only its URL until preparation forked it,
-- so a failed or still-queued revision had no commit and the team could later rewrite
-- or delete the repository. (observer_revisions.source_commit is the commit of the
-- team's private snapshot repository written by preparation, not the upstream commit.)
--
-- Now, while private.observer_source_snapshot_config.enabled (portal submit_repository):
--   1. the exact upstream commit of the default branch is resolved (GitHub App),
--   2. that commit's zipball is stored in the private bucket observer-sources
--      (<user>/<commit>-<uuid>.zip; nothing ever deletes from this bucket),
--   3. the revision is created, then observer_record_source_snapshot records the
--      commit, path, size and SHA-256 (private.observer_source_snapshots, never purged,
--      no foreign key so it outlives any revision cleanup) and pins
--      observer_revisions.submitted_commit,
--   4. preparation forks and materializes exactly submitted_commit instead of whatever
--      the default branch points at by then.
-- If resolving or storing fails the submission is refused (the team retries); a
-- private repository is refused with private_source_requires_zip.
--
-- One-line switches:
--   select public.observer_set_source_snapshots(true);    -- enable
--   select public.observer_set_source_snapshots(false);   -- rollback (URL-only as before)

insert into storage.buckets(id,name,public) values('observer-sources','observer-sources',false)
  on conflict (id) do nothing;

create table if not exists private.observer_source_snapshot_config(
  id boolean primary key default true check (id),
  enabled boolean not null default false,
  updated_at timestamptz not null default now()
);
insert into private.observer_source_snapshot_config(id) values(true) on conflict do nothing;
revoke all on private.observer_source_snapshot_config from public,anon,authenticated;

alter table public.observer_revisions add column if not exists submitted_commit text
  check (submitted_commit ~ '^[0-9a-f]{40}$');

create table if not exists private.observer_source_snapshots(
  revision_id uuid primary key,
  source_location text not null,
  source_commit text not null check (source_commit ~ '^[0-9a-f]{40}$'),
  bucket text not null default 'observer-sources' check (bucket='observer-sources'),
  storage_path text not null unique,
  bytes bigint not null check (bytes>0),
  sha256 text not null check (sha256 ~ '^[0-9a-f]{64}$'),
  created_at timestamptz not null default now()
);
revoke all on private.observer_source_snapshots from public,anon,authenticated;

create or replace function public.observer_source_snapshots_enabled()
returns boolean language sql stable security definer set search_path=public,pg_temp as $$
  select coalesce((select enabled from private.observer_source_snapshot_config where id),false)
$$;

create or replace function public.observer_set_source_snapshots(p_enabled boolean)
returns boolean language plpgsql security definer set search_path=public,pg_temp as $$
begin
  update private.observer_source_snapshot_config set enabled=coalesce(p_enabled,enabled),updated_at=now() where id;
  perform private.audit('observer.source_snapshots',jsonb_build_object('enabled',p_enabled));
  return public.observer_source_snapshots_enabled();
end $$;

create or replace function public.observer_record_source_snapshot(p_revision uuid,p_commit text,p_path text,
  p_bytes bigint,p_sha256 text)
returns void language plpgsql security definer set search_path=public,pg_temp as $$
declare v public.observer_revisions;
begin
  select * into v from public.observer_revisions where id=p_revision for update;
  if not found or v.source_kind<>'repository' then raise exception 'revision_not_found'; end if;
  if p_commit is null or p_commit !~ '^[0-9a-f]{40}$' or p_path is null
    or p_path !~ ('^[0-9a-f-]{36}/'||p_commit||'-[0-9a-f-]{36}[.]zip$') then raise exception 'invalid_source_snapshot'; end if;
  insert into private.observer_source_snapshots(revision_id,source_location,source_commit,storage_path,bytes,sha256)
    values(p_revision,v.source_location,p_commit,p_path,p_bytes,p_sha256);
  update public.observer_revisions set submitted_commit=p_commit where id=p_revision and submitted_commit is null;
end $$;

-- Preparation receives the pinned commit (only change: rev.submitted_commit in the row).
CREATE OR REPLACE FUNCTION public.observer_pending_preparations(p_limit integer DEFAULT 3)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public', 'pg_temp'
AS $function$
declare r record;c private.observer_preparation_config;l private.observer_preparations;result jsonb:='[]';v_gameplay text;v_minutes integer;
begin
  select x.* into c from private.observer_preparation_config x
    join public.scenarios s on s.id=x.scenario_id
    join public.observer_phase_settings f on f.phase_id=x.phase_id
    where x.enabled and f.projects_enabled and s.is_active and s.weather_public and s.events_public and s.forecasts_public;
  if not found then return result; end if;
  v_gameplay:=case when exists(select 1 from public.scenarios where id=c.scenario_id and contract='v4-score-v1') then 'v4' else 'v3' end;
  for r in select rev.id,p.owner_id,p.team_id,rev.source_kind,rev.source_location,rev.submitted_commit
    from public.observer_revisions rev join public.observer_projects p on p.id=rev.project_id
    join public.profiles u on u.id=p.owner_id and u.team_id=p.team_id and not u.is_banned
    left join private.observer_preparations prep on prep.revision_id=rev.id
    -- A parked row stays out of this scan entirely: no new lease until an
    -- organizer clears it via observer_admin_requeue.
    where rev.status='queued' and (prep.revision_id is null or (prep.expires_at<=now() and prep.paused_at is null))
    order by coalesce(prep.attempts,0),rev.created_at,rev.id for update of rev skip locked limit greatest(1,least(coalesce(p_limit,3),10))
  loop
    select * into l from private.observer_preparations where revision_id=r.id;
    if found and l.expires_at>now() then continue; end if;
    if found and now()-l.first_leased_at>interval '2 hours' then
      update private.observer_preparations set paused_at=now() where revision_id=r.id;
      perform private.observer_raise_incident('revision',r.id,'preparation_retry_budget_exhausted',
        jsonb_build_object('attempts',l.attempts,'team_id',r.team_id,'owner_id',r.owner_id));
      continue;
    end if;
    v_minutes:=case when found then (least(20,2^greatest(l.attempts,1)))::int else 2 end;
    insert into private.observer_preparations(revision_id,lease,expires_at,phase_id,scenario_id,model)
      values(r.id,gen_random_uuid(),now()+(v_minutes||' minutes')::interval,c.phase_id,c.scenario_id,c.model)
      on conflict(revision_id) do update set lease=excluded.lease,expires_at=excluded.expires_at,
        attempts=private.observer_preparations.attempts+1 returning * into l;
    result:=result || jsonb_build_array(to_jsonb(r)||jsonb_build_object('lease',l.lease,'model_run_id',l.model_run_id,'model',l.model,
      'gameplay',v_gameplay));
  end loop;
  return result;
end $function$;

do $$
declare f record;
begin
  for f in select p.oid::regprocedure as signature from pg_proc p join pg_namespace n on n.oid=p.pronamespace
    where n.nspname='public' and p.proname in ('observer_source_snapshots_enabled','observer_set_source_snapshots',
      'observer_record_source_snapshot','observer_pending_preparations')
  loop
    execute format('revoke all on function %s from public,anon,authenticated',f.signature);
    execute format('grant execute on function %s to service_role',f.signature);
  end loop;
end $$;

notify pgrst,'reload schema';

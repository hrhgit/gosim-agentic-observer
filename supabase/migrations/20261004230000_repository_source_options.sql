-- Repository submissions may name a branch, tag or commit and a project folder
-- (GitHub /tree/<ref>/<folder> and /commit/<sha> links, or the portal fields
-- "branch" and "subdir"). Builds on 20261004072000_repository_source_snapshots.
--
--   * The portal resolves the named ref (default branch when none) to its exact commit
--     and stores that commit's zipball in observer-sources; with a folder, only that
--     folder is stored (under the archive's single top folder), so the stored snapshot
--     is exactly the submitted project.
--   * observer_record_source_snapshot also records the ref and folder, on the snapshot
--     and on the revision (source_ref, source_subdir: shown on the website and CLIs and
--     reused by "prepare again"). source_location stays the plain repository URL.
--   * Preparation reads the stored snapshot (observer_preparation_source) instead of
--     forking the public repository, so the prepared project root is the chosen folder
--     of the pinned commit. Revisions without a snapshot still fork as before.
--
-- Additive only: two nullable columns on each table, one replaced and one new
-- service-role function. Rollback: the previous edge functions ignore all of it.

alter table public.observer_revisions
  add column if not exists source_ref text check (source_ref is null or length(source_ref) between 1 and 200),
  add column if not exists source_subdir text check (source_subdir is null or length(source_subdir) between 1 and 300);
alter table private.observer_source_snapshots
  add column if not exists source_ref text,
  add column if not exists source_subdir text;

drop function if exists public.observer_record_source_snapshot(uuid,text,text,bigint,text);
create or replace function public.observer_record_source_snapshot(p_revision uuid,p_commit text,p_path text,
  p_bytes bigint,p_sha256 text,p_ref text default null,p_subdir text default null)
returns void language plpgsql security definer set search_path=public,pg_temp as $$
declare v public.observer_revisions;
begin
  select * into v from public.observer_revisions where id=p_revision for update;
  if not found or v.source_kind<>'repository' then raise exception 'revision_not_found'; end if;
  if p_commit is null or p_commit !~ '^[0-9a-f]{40}$' or p_path is null
    or p_path !~ ('^[0-9a-f-]{36}/'||p_commit||'-[0-9a-f-]{36}[.]zip$')
    or (p_ref is not null and p_ref !~ '^[A-Za-z0-9._/+@-]{1,200}$')
    or (p_subdir is not null and (p_subdir !~ '^[^/\\]{1,255}(/[^/\\]{1,255})*$' or length(p_subdir)>300
      or p_subdir ~ '(^|/)\.\.?(/|$)')) then
    raise exception 'invalid_source_snapshot';
  end if;
  insert into private.observer_source_snapshots(revision_id,source_location,source_commit,storage_path,bytes,sha256,
      source_ref,source_subdir)
    values(p_revision,v.source_location,p_commit,p_path,p_bytes,p_sha256,p_ref,p_subdir);
  update public.observer_revisions set submitted_commit=p_commit,source_ref=p_ref,source_subdir=p_subdir
    where id=p_revision and submitted_commit is null;
end $$;

-- The stored snapshot a repository revision is prepared from (null: none, fork as before).
create or replace function public.observer_preparation_source(p_revision uuid)
returns text language sql stable security definer set search_path=public,pg_temp as $$
  select s.storage_path from private.observer_source_snapshots s
    join public.observer_revisions r on r.id=s.revision_id and r.source_kind='repository'
  where s.revision_id=p_revision
$$;

do $$
declare f record;
begin
  for f in select p.oid::regprocedure as signature from pg_proc p join pg_namespace n on n.oid=p.pronamespace
    where n.nspname='public' and p.proname in ('observer_record_source_snapshot','observer_preparation_source')
  loop
    execute format('revoke all on function %s from public,anon,authenticated',f.signature);
    execute format('grant execute on function %s to service_role',f.signature);
  end loop;
end $$;

notify pgrst,'reload schema';

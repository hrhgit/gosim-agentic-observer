-- Race: temporary_upload_lifecycle (observer_expired_uploads / observer_upload_cleaned,
-- run from private.observer_tick) already required a 'source' upload's revision to have
-- a private.observer_materializations row before purging it. That check has no grace
-- period: the object is deleted the same tick it becomes eligible, and
-- observer_schedule_preparation's own "is the source still there" guard only checks that
-- an observer_uploads row with consumed_at is not null still exists by path -- it never
-- checks cleaned_at. So once 20261002160500_upload_reservation_short_lived.sql cut the
-- default reservation window from 1 day to 15 minutes, any revision whose preparation
-- retries past that window (now bounded to ~2h by platform_failure_resilience's budget,
-- then parked, never failed) could have its *consumed, not-yet-materialized* upload swept
-- up by a narrow timing window around materialization, after which scheduling kept
-- reporting the source as "available" (the DB row was still there, just cleaned_at-tagged)
-- while every download attempt failed against storage -- a platform-classified error that
-- retries forever and is never surfaced to the contestant. Belt-and-suspenders fix:
--   1. observer_schedule_preparation also requires cleaned_at is null, so a cleaned source
--      hard-fails scheduling immediately instead of silently retrying against a dead path.
--   2. observer_expired_uploads adds an explicit "revision is not queued/preparing" guard
--      and a 7-day grace period after materialization (and after a scored run, for csv)
--      before the object is eligible, instead of purging the instant it qualifies.
-- Additive only: no column dropped, no existing row deleted, no privilege narrowed.

alter table private.observer_materializations add column if not exists created_at timestamptz not null default now();

create or replace function public.observer_schedule_preparation(p_revision uuid,p_lease uuid,p_organization text,
  p_participant_token text,p_engine_token text,p_job jsonb)
returns void language plpgsql security definer set search_path=public,pg_temp as $$
declare r public.observer_revisions;l private.observer_preparations;p public.observer_projects;b uuid;
begin
  select * into r from public.observer_revisions where id=p_revision for update;
  select * into l from private.observer_preparations where revision_id=p_revision;
  if r.id is null or l.lease is distinct from p_lease then raise exception 'preparation_lease_invalid'; end if;
  if r.status<>'queued' and exists(select 1 from private.observer_jobs where revision_id=p_revision) then return; end if;
  if r.status<>'queued' or l.expires_at<=clock_timestamp() then raise exception 'preparation_lease_invalid'; end if;
  select * into p from public.observer_projects where id=r.project_id;
  if not exists(select 1 from public.profiles where id=p.owner_id and team_id=p.team_id and not is_banned) or
    not exists(select 1 from public.scenarios where id=l.scenario_id and is_active and weather_public and events_public and forecasts_public) or
    not exists(select 1 from public.observer_phase_settings where phase_id=l.phase_id and projects_enabled) then
    raise exception 'preparation_not_eligible'; end if;
  if r.source_kind='zip' and not exists(select 1 from private.observer_uploads where revision_id=r.id
    and path=r.source_location and consumed_at is not null and cleaned_at is null) then raise exception 'source_upload_unavailable'; end if;
  if p_job is null or p_job->>'kind' is distinct from 'prepare' then raise exception 'invalid_preparation_job'; end if;
  insert into public.observer_batches(team_id,user_id,phase_id,revision_id,mode,purpose)
    values(p.team_id,p.owner_id,l.phase_id,r.id,'project','adaptation') returning id into b;
  insert into public.observer_runs(id,batch_id,scenario_id) values(l.model_run_id,b,l.scenario_id);
  perform public.observer_open_session(l.model_run_id,p_participant_token,p_engine_token);
  perform public.observer_enqueue_job((p_job->>'id')::uuid,'prepare',null,r.id,p_organization,
    p_job->>'nonce',p_job->>'encrypted_input',p_job->>'encrypted_nonce');
  update public.observer_revisions set status='preparing',
    repository=p_organization||'/participant-'||replace(p.owner_id::text,'-','') where id=r.id;
end $$;

create or replace function public.observer_expired_uploads(p_limit integer default 50)
returns jsonb language sql stable security definer set search_path=public,pg_temp as $$
 select coalesce(jsonb_agg(jsonb_build_object('id',u.id,'path',u.path)),'[]') from (
   select u.id,u.path from private.observer_uploads u
   where u.cleaned_at is null and u.expires_at<now()
     and (u.consumed_at is null
       or (u.purpose='source'
         and not exists(select 1 from public.observer_revisions rv where rv.id=u.revision_id and rv.status in ('queued','preparing'))
         and exists(select 1 from private.observer_materializations m
           where m.revision_id=u.revision_id and m.created_at<now()-interval '7 days'))
       or (u.purpose='csv' and exists(select 1 from public.observer_runs r where r.id=u.run_id
         and r.status='scored' and r.result_path like 'github:%' and r.finished_at<now()-interval '7 days')))
   order by u.expires_at limit greatest(1,least(coalesce(p_limit,50),100))
 ) u
$$;

-- Organizer action for the specific, unrecoverable case: a revision's zip was deleted
-- before materialization (the race above). Unlike observer_admin_requeue, retrying is
-- useless here -- the source is permanently gone -- so this fails the revision with a
-- fixed, non-blaming message (mapped to Chinese in web/src/lib/projectText.ts, and the
-- existing zip-reupload hint already appends for any failed+zip revision) and refunds
-- any formal batch the revision had reached, instead of ever requeuing it.
create or replace function public.observer_admin_mark_source_missing(p_revision uuid)
returns void language plpgsql security definer set search_path=public,pg_temp as $$
declare r public.observer_revisions;
begin
  if not public.is_admin() then raise exception 'admin_only'; end if;
  select * into r from public.observer_revisions where id=p_revision for update;
  if not found then raise exception 'revision_not_found'; end if;
  if r.source_kind<>'zip' then raise exception 'not_a_zip_revision'; end if;
  if r.status not in ('queued','preparing') then raise exception 'revision_not_stuck'; end if;
  if not exists(select 1 from private.observer_uploads where revision_id=p_revision and path=r.source_location and cleaned_at is not null) then
    raise exception 'source_still_available'; end if;
  update public.observer_revisions set status='failed',
    error='The uploaded file is no longer available. Please re-upload this version; it will not count against your submission quota.'
    where id=p_revision;
  update private.observer_preparations set paused_at=now() where revision_id=p_revision and paused_at is null;
  update private.observer_incidents set resolved_at=now(),resolved_by=auth.uid()
    where subject_type='revision' and subject_id=p_revision and resolved_at is null;
  update public.observer_batches set status='failed',finished_at=now(),quota_refunded=true
    where revision_id=p_revision and purpose='formal' and not quota_refunded;
  perform private.audit('admin.revision.source_missing',jsonb_build_object('revision_id',p_revision));
end $$;
revoke all on function public.observer_admin_mark_source_missing(uuid) from public,anon;
grant execute on function public.observer_admin_mark_source_missing(uuid) to authenticated;

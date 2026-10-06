-- One explicitly configured PUBLIC scenario is used for adaptation smoke tests.
-- No formal scenario is selected by a model or by submitted project contents.
create table private.observer_preparation_config (
  id boolean primary key default true check(id),
  phase_id uuid not null references public.observer_phase_settings(phase_id),
  scenario_id uuid not null references private.observer_scenario_bundles(scenario_id),
  model text not null check(length(model) between 1 and 256),
  enabled boolean not null default false
);
create table private.observer_preparations (
  revision_id uuid primary key references public.observer_revisions(id),
  lease uuid not null,
  expires_at timestamptz not null,
  attempts integer not null default 1,
  model_run_id uuid not null unique default gen_random_uuid(),
  preview_run_id uuid unique references public.observer_runs(id),
  phase_id uuid not null references public.phases(id),
  scenario_id uuid not null references public.scenarios(id),
  model text not null,
  error text not null default ''
);
revoke all on private.observer_preparation_config,private.observer_preparations from public,anon,authenticated;

create function public.observer_pending_preparations(p_limit integer default 3)
returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$
declare r record;c private.observer_preparation_config;l private.observer_preparations;result jsonb:='[]';
begin
  select x.* into c from private.observer_preparation_config x
    join public.scenarios s on s.id=x.scenario_id
    join public.observer_phase_settings f on f.phase_id=x.phase_id
    where x.enabled and f.projects_enabled and s.is_active and s.weather_public and s.events_public and s.forecasts_public;
  if not found then return result; end if;
  for r in select rev.id,p.owner_id,p.team_id,rev.source_kind,rev.source_location
    from public.observer_revisions rev join public.observer_projects p on p.id=rev.project_id
    join public.profiles u on u.id=p.owner_id and u.team_id=p.team_id and not u.is_banned
    left join private.observer_preparations prep on prep.revision_id=rev.id
    where rev.status='queued' and (prep.revision_id is null or prep.expires_at<=now())
    order by rev.created_at,rev.id for update of rev skip locked limit greatest(1,least(coalesce(p_limit,3),10))
  loop
    select * into l from private.observer_preparations where revision_id=r.id;
    if found and l.expires_at>now() then continue; end if;
    if found and l.attempts>=5 then
      update public.observer_revisions set status='failed',error='Project scheduling failed. Please retry.' where id=r.id;
      continue;
    end if;
    insert into private.observer_preparations(revision_id,lease,expires_at,phase_id,scenario_id,model)
      values(r.id,gen_random_uuid(),now()+interval '2 minutes',c.phase_id,c.scenario_id,c.model)
      on conflict(revision_id) do update set lease=excluded.lease,expires_at=excluded.expires_at,
        attempts=private.observer_preparations.attempts+1 returning * into l;
    result:=result || jsonb_build_array(to_jsonb(r)||jsonb_build_object('lease',l.lease,'model_run_id',l.model_run_id,'model',l.model));
  end loop;
  return result;
end $$;

create function public.observer_schedule_preparation(p_revision uuid,p_lease uuid,p_organization text,
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
    and path=r.source_location and consumed_at is not null) then raise exception 'source_upload_unavailable'; end if;
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

create function public.observer_preparation_error(p_revision uuid,p_lease uuid,p_error text)
returns void language plpgsql security definer set search_path=public,pg_temp as $$
begin
  update private.observer_preparations set error=left(coalesce(p_error,'preparation_unavailable'),100)
    where revision_id=p_revision and lease=p_lease;
end $$;

create function public.observer_materialized_project(p_revision uuid,p_user uuid)
returns text language sql stable security definer set search_path=public,pg_temp as $$
  select m.archive_ref from private.observer_materializations m
    join public.observer_revisions r on r.id=m.revision_id
    join public.observer_projects p on p.id=r.project_id
    join public.profiles u on u.team_id=p.team_id
  where r.id=p_revision and r.status in ('reviewable','approved','failed') and u.id=p_user and not u.is_banned
$$;

create function public.observer_reconcile_preparations()
returns integer language plpgsql security definer set search_path=public,pg_temp as $$
declare r record;v jsonb;b uuid;preview uuid;n integer:=0;run public.observer_runs;
begin
  for r in select rev.id,rev.status as revision_status,rev.repository,prep.model_run_id,prep.preview_run_id,prep.phase_id,prep.scenario_id,
      p.owner_id,p.team_id,j.status as job_status,j.result
    from public.observer_revisions rev join private.observer_preparations prep on prep.revision_id=rev.id
    join public.observer_projects p on p.id=rev.project_id
    join private.observer_jobs j on j.revision_id=rev.id and j.kind='prepare'
    where (rev.status='preparing' or (rev.status='failed' and exists(select 1 from public.observer_runs
      where id=prep.model_run_id and status in ('starting','ready','running')))) and j.status in ('succeeded','failed')
      and (prep.preview_run_id is null or rev.status='preparing')
    order by rev.created_at for update of rev skip locked limit 20
  loop
    -- Revoke adaptation capability as soon as its trusted job terminates.
    update public.observer_runs set status='cancelled',finished_at=now()
      where id=r.model_run_id and status in ('starting','ready','running') returning batch_id into b;
    if b is not null then perform private.observer_finalize_batch(b); end if;
    if r.job_status='failed' or r.revision_status='failed' then continue; end if;
    if r.preview_run_id is null then
      v:=r.result;
      if v->>'status' is distinct from 'awaiting_public_test' or v->>'revision_id' is distinct from r.id::text or
        v->>'repository' is distinct from r.repository or
        not coalesce(v->>'source_digest' ~ '^[0-9a-f]{64}$',false) or
        not coalesce(v->>'source_commit' ~ '^[0-9a-f]{40}$',false) or
        not coalesce(v->>'materialized_digest' ~ '^[0-9a-f]{64}$',false) or
        not coalesce(v->>'approval_digest' ~ '^[0-9a-f]{64}$',false) or
        jsonb_typeof(v->'manifest') is distinct from 'object' or
        not coalesce(v->'manifest'->>'image' ~ '@sha256:[0-9a-f]{64}$',false) or
        jsonb_typeof(v->'adapter_files') is distinct from 'object' or
        left(v->>'preview_path',length('github:'||r.repository||'@')) is distinct from 'github:'||r.repository||'@' or
        length(v->>'preview_path') is distinct from length('github:'||r.repository||'@')+40 or
        not coalesce(v->>'preview_path' ~ '@[0-9a-f]{40}$',false) then
        update public.observer_revisions set status='failed',error='Invalid preparation result. Please retry.' where id=r.id;
        continue;
      end if;
      if not exists(select 1 from public.scenarios where id=r.scenario_id and is_active and weather_public and events_public and forecasts_public) then
        update public.observer_revisions set status='failed',error='Public test scenario is unavailable.' where id=r.id;
        continue;
      end if;
      insert into private.observer_materializations(revision_id,archive_ref,digest)
        values(r.id,v->>'preview_path',v->>'materialized_digest');
      update public.observer_revisions set source_digest=v->>'source_digest',source_commit=v->>'source_commit',
        manifest=v->'manifest',adapter_files=v->'adapter_files',approval_digest=v->>'approval_digest',
        explanation=left(coalesce(v->>'explanation',''),8000),public_test='{"status":"queued","passed":false}' where id=r.id;
      insert into public.observer_batches(team_id,user_id,phase_id,revision_id,mode,purpose)
        values(r.team_id,r.owner_id,r.phase_id,r.id,'project','preview') returning id into b;
      insert into public.observer_runs(batch_id,scenario_id) values(b,r.scenario_id) returning id into preview;
      update private.observer_preparations set preview_run_id=preview where revision_id=r.id;
    else
      select * into run from public.observer_runs where id=r.preview_run_id;
      if run.status in ('failed','cancelled') or exists(select 1 from private.observer_jobs
          where run_id=run.id and status='failed') then
        update public.observer_revisions set status='failed',error='Public test failed. Check the project interface and submit again.',
          public_test=jsonb_build_object('passed',false,'status','failed','run_id',run.id) where id=r.id;
      elsif run.status='scored' and (select count(*) from private.observer_jobs
          where run_id=run.id and kind in ('execute','engine') and status='succeeded')=2 then
        update public.observer_revisions set status='reviewable',
          public_test=jsonb_build_object('passed',true,'status','passed','run_id',run.id,'score',run.score) where id=r.id;
      end if;
    end if;
    n:=n+1;
  end loop;
  return n;
end $$;

revoke all on function public.observer_pending_preparations(integer),
  public.observer_schedule_preparation(uuid,uuid,text,text,text,jsonb),public.observer_preparation_error(uuid,uuid,text),
  public.observer_reconcile_preparations() from public,anon,authenticated;
grant execute on function public.observer_pending_preparations(integer),
  public.observer_schedule_preparation(uuid,uuid,text,text,text,jsonb),public.observer_preparation_error(uuid,uuid,text),
  public.observer_reconcile_preparations() to service_role;
revoke all on function public.observer_materialized_project(uuid,uuid) from public,anon,authenticated;
grant execute on function public.observer_materialized_project(uuid,uuid) to service_role;

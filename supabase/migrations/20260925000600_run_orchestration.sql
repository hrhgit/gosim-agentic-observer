-- Internal scheduling data is not part of participant-readable scenario records.
create table private.observer_scenario_bundles (
  scenario_id uuid primary key references public.scenarios(id),
  storage_path text not null check (storage_path ~ '^[A-Za-z0-9_/-]+[.]zip$'),
  digest text not null check (digest ~ '^[0-9a-f]{64}$')
);
insert into storage.buckets(id,name,public,file_size_limit,allowed_mime_types)
  values('observer-scenarios','observer-scenarios',false,104857600,array['application/zip'])
  on conflict(id) do nothing;

create table private.observer_materializations (
  revision_id uuid primary key references public.observer_revisions(id),
  archive_ref text not null check (archive_ref ~ '^github:AGENTIC-OBSERVER26-runner-[1-6]/participant-[0-9a-f]{32}@[0-9a-f]{40}$'),
  digest text not null check (digest ~ '^[0-9a-f]{64}$')
);
create table private.observer_run_leases (
  run_id uuid primary key references public.observer_runs(id),
  lease uuid not null,
  expires_at timestamptz not null,
  attempts integer not null default 1,
  error text not null default ''
);
revoke all on private.observer_scenario_bundles,private.observer_materializations,
  private.observer_run_leases from public,anon,authenticated;

create function public.observer_pending_runs(p_limit integer default 5)
returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$
declare r record; result jsonb:='[]'; v_lease uuid;
begin
  for r in select run.id,run.batch_id from public.observer_runs run
    join private.observer_run_leases l on l.run_id=run.id
    where run.status='queued' and l.attempts>=5 and l.expires_at<=now()
    for update of run skip locked limit 100
  loop
    update public.observer_runs set status='failed',error='evaluation_schedule_failed',finished_at=now() where id=r.id;
    perform private.observer_finalize_batch(r.batch_id);
  end loop;
  -- Serialize reservations, including first insert, on the public run row. A
  -- crashed scheduler leaves a short lease, never a half-open session.
  for r in select run.id,b.user_id,b.mode,
      case when b.purpose='preview' then least(c.runtime_seconds,300) else c.runtime_seconds end as runtime_seconds,
      s.storage_path,s.digest as scenario_digest,
      m.archive_ref,m.digest as materialized_digest,rev.manifest
    from public.observer_runs run join public.observer_batches b on b.id=run.batch_id
    join public.observer_phase_settings c on c.phase_id=b.phase_id
    join private.observer_scenario_bundles s on s.scenario_id=run.scenario_id
    join public.profiles p on p.id=b.user_id and not p.is_banned and p.team_id=b.team_id
    left join public.observer_revisions rev on rev.id=b.revision_id
    left join private.observer_materializations m on m.revision_id=rev.id
    left join private.observer_run_leases l on l.run_id=run.id
    where run.status='queued' and b.purpose in ('formal','preview') and b.status in ('queued','running')
      and ((b.mode='local' and c.local_sessions_enabled) or
        (b.mode='project' and c.projects_enabled and m.revision_id is not null and
          ((b.purpose='formal' and rev.status='approved') or (b.purpose='preview' and rev.status='preparing'))))
      -- A local user starts one scenario at a time. Do not spend hosted engine
      -- minutes waiting for the other scenarios while their first CLI is busy.
      and (b.mode<>'local' or not exists(select 1 from public.observer_runs other
        where other.batch_id=run.batch_id and other.id<>run.id and
          (other.status in ('starting','ready','running') or
            (other.status='queued' and (other.created_at,other.id)<(run.created_at,run.id)))))
      and (l.run_id is null or (l.expires_at<=now() and l.attempts<5))
    order by run.created_at,run.id for update of run skip locked limit greatest(1,least(coalesce(p_limit,5),10))
  loop
    -- The join snapshot may predate a concurrently committed first lease even
    -- though this row lock was acquired afterwards. Re-read under the lock.
    if exists(select 1 from private.observer_run_leases where run_id=r.id
      and (expires_at>now() or attempts>=5)) then continue; end if;
    v_lease:=gen_random_uuid();
    insert into private.observer_run_leases(run_id,lease,expires_at)
      values(r.id,v_lease,now()+interval '2 minutes')
      on conflict(run_id) do update set lease=excluded.lease,expires_at=excluded.expires_at,
        attempts=private.observer_run_leases.attempts+1;
    result:=result || jsonb_build_array(to_jsonb(r)||jsonb_build_object('lease',v_lease));
  end loop;
  return result;
end $$;

create function public.observer_schedule_run(p_run uuid,p_lease uuid,p_organization text,
  p_participant_token text,p_engine_token text,p_local_credential text,p_jobs jsonb)
returns void language plpgsql security definer set search_path=public,pg_temp as $$
declare r public.observer_runs;b public.observer_batches;l private.observer_run_leases;j jsonb;
begin
  select * into r from public.observer_runs where id=p_run for update;
  select * into l from private.observer_run_leases where run_id=p_run;
  if r.id is null or l.lease is distinct from p_lease then raise exception 'run_lease_invalid'; end if;
  -- A repeated acknowledgement cannot create new jobs or rotate capabilities.
  if r.status<>'queued' and exists(select 1 from private.observer_jobs where run_id=p_run) then return; end if;
  if r.status<>'queued' or l.expires_at<=clock_timestamp() then raise exception 'run_lease_invalid'; end if;
  select * into b from public.observer_batches where id=r.batch_id;
  if b.status not in ('queued','running') or not exists(select 1 from public.profiles
    where id=b.user_id and team_id=b.team_id and not is_banned) then raise exception 'run_not_eligible'; end if;
  if not exists(select 1 from public.observer_phase_settings c where c.phase_id=b.phase_id
    and (case when b.mode='local' then c.local_sessions_enabled else c.projects_enabled end)) then
    raise exception 'run_not_eligible'; end if;
  if p_jobs is null or jsonb_typeof(p_jobs)<>'array' or jsonb_array_length(p_jobs)<>
      (case when b.mode='local' then 1 else 2 end) then raise exception 'invalid_run_jobs'; end if;
  if (select count(*) from jsonb_array_elements(p_jobs) x where x->>'kind'='engine')<>1 or
    (select count(*) from jsonb_array_elements(p_jobs) x where x->>'kind'='execute')<>
      (case when b.mode='local' then 0 else 1 end) then raise exception 'invalid_run_jobs'; end if;
  if b.mode='local' then
    perform public.observer_open_local_session(p_run,p_participant_token,p_engine_token,p_local_credential);
  else
    if p_local_credential is not null then raise exception 'invalid_run_jobs'; end if;
    perform public.observer_open_session(p_run,p_participant_token,p_engine_token);
  end if;
  for j in select value from jsonb_array_elements(p_jobs) loop
    perform public.observer_enqueue_job((j->>'id')::uuid,j->>'kind',p_run,null,p_organization,
      j->>'nonce',j->>'encrypted_input',j->>'encrypted_nonce');
  end loop;
end $$;

create function public.observer_run_schedule_error(p_run uuid,p_lease uuid,p_error text)
returns void language plpgsql security definer set search_path=public,pg_temp as $$
declare r public.observer_runs;l private.observer_run_leases;
begin
  select * into r from public.observer_runs where id=p_run for update;
  select * into l from private.observer_run_leases where run_id=p_run;
  if r.status<>'queued' or l.lease is distinct from p_lease then return; end if;
  update private.observer_run_leases set error=left(coalesce(p_error,'schedule_unavailable'),100) where run_id=p_run;
  if l.attempts>=5 then
    update public.observer_runs set status='failed',error='evaluation_schedule_failed',finished_at=now() where id=p_run;
    perform private.observer_finalize_batch(r.batch_id);
  end if;
end $$;

revoke all on function public.observer_pending_runs(integer),public.observer_schedule_run(uuid,uuid,text,text,text,text,jsonb),
  public.observer_run_schedule_error(uuid,uuid,text) from public,anon,authenticated;
grant execute on function public.observer_pending_runs(integer),public.observer_schedule_run(uuid,uuid,text,text,text,text,jsonb),
  public.observer_run_schedule_error(uuid,uuid,text) to service_role;

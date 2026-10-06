-- A persisted score alone does not prove a working interface. Public preview
-- requires a valid termination and at least one officially committed action.
create or replace function public.observer_reconcile_preparations()
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
      if run.status in ('failed','cancelled') or (run.status='scored' and not coalesce(
          run.score_summary->>'termination_reason' in ('survey_complete','global_wallclock_expired')
          and (run.score_summary->>'committed_action_count') ~ '^[1-9][0-9]*$',false)) or exists(select 1 from private.observer_jobs
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

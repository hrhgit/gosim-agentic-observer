-- Keep every run's data unless a durable copy exists. Compaction now clears
-- step observations, answers and the initial catalog only for scored runs whose
-- private result (workflow_result.json) is archived in a GitHub result
-- repository. Failed and cancelled runs have no such copy and stay intact.
create or replace function private.observer_compact_finished_runs(p_limit integer default 200)
returns integer language plpgsql security definer set search_path=public,pg_temp as $$
declare v_runs uuid[];
begin
  select array_agg(id) into v_runs from (select r.id from public.observer_runs r
      where r.status='scored' and r.result_path like 'github:%'
        and coalesce(r.finished_at,r.created_at)<now()-interval '1 hour'
        and (exists(select 1 from private.observer_messages m where m.run_id=r.id
               and (m.observation<>'{}'::jsonb or m.response is not null))
          or exists(select 1 from private.observer_sessions s where s.run_id=r.id and s.publication is not null))
      limit greatest(1,least(coalesce(p_limit,200),1000))) x;
  if v_runs is null then return 0; end if;
  update private.observer_messages set observation='{}'::jsonb,response=null
    where run_id=any(v_runs) and (observation<>'{}'::jsonb or response is not null);
  update private.observer_sessions set publication=null where run_id=any(v_runs) and publication is not null;
  return coalesce(array_length(v_runs,1),0);
end $$;
revoke all on function private.observer_compact_finished_runs(integer) from public,anon,authenticated;

do $cron$
begin
  if exists(select 1 from pg_available_extensions where name='pg_cron') then
    create extension if not exists pg_cron;
    perform cron.unschedule(jobid) from cron.job where jobname='observer-compact-finished-runs';
    perform cron.schedule('observer-compact-finished-runs','*/10 * * * *','select private.observer_compact_finished_runs()');
  end if;
end $cron$;

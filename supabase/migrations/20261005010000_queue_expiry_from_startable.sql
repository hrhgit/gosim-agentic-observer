-- Queue expiry counts from when a run can start (fix 2026-10-04): the third evaluation of an
-- "evaluate 3 times and average" set waited behind the second (they run one after another,
-- each up to the wall-clock cap) and public.observer_reconcile_sessions expired it 30 minutes
-- after it was created, leaving the set incomplete.
--
-- 1. private.observer_run_startable_since: when a queued run became able to start: its
--    creation, or the finish of the earlier evaluations of its self-check set (local
--    sessions: of the earlier scenarios of its batch); null while one of those still runs.
-- 2. public.observer_reconcile_sessions (based on the live definition) expires a queued run
--    only 30 minutes after it could start and only if the dispatcher never leased it; a run
--    with a lease follows the scheduling retry budget of observer_pending_runs (parked with
--    an incident, never failed). Running sessions past their deadline expire as before.
-- 3. A self-check evaluation that fails for a platform reason (refunded) is replaced
--    automatically by a new evaluation at the end of its set (at most 2 replacements per
--    set; not after the phase closed). The failed one is kept and stays refunded, so the
--    set still uses 3 of the day's evaluations.
-- Function replacements and one new trigger only; nothing running changes.

create or replace function private.observer_run_startable_since(p_run uuid)
returns timestamptz language sql stable security definer set search_path=public,pg_temp as $$
  select case
    when b.repeat_group is not null and exists(select 1 from public.observer_batches e
      where e.repeat_group=b.repeat_group and e.id<>b.id and e.status in ('queued','running')
        and (e.created_at,e.id)<(b.created_at,b.id)) then null
    when b.mode='local' and exists(select 1 from public.observer_runs o where o.batch_id=r.batch_id and o.id<>r.id
        and o.status in ('queued','starting','ready','running') and (o.created_at,o.id)<(r.created_at,r.id)) then null
    else greatest(r.created_at,
      coalesce((select max(e.finished_at) from public.observer_batches e where b.repeat_group is not null
        and e.repeat_group=b.repeat_group and e.id<>b.id and (e.created_at,e.id)<(b.created_at,b.id)),r.created_at),
      coalesce((select max(o.finished_at) from public.observer_runs o where b.mode='local' and o.batch_id=r.batch_id
        and o.id<>r.id and (o.created_at,o.id)<(r.created_at,r.id)),r.created_at))
  end
  from public.observer_runs r join public.observer_batches b on b.id=r.batch_id where r.id=p_run
$$;
revoke all on function private.observer_run_startable_since(uuid) from public,anon,authenticated;

create or replace function public.observer_reconcile_sessions()
 RETURNS integer
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public', 'pg_temp'
AS $function$
declare v record; n integer:=0;
begin
  for v in select id from private.observer_model_calls where status='reserved'
    and created_at<now()-interval '5 minutes' order by created_at limit 100
  loop
    perform public.observer_settle_model(v.id,null);
    n:=n+1;
  end loop;
  -- Queued/never-dispatched runs and missing job callbacks must not lock a team
  -- out forever. Expiration is an infrastructure failure, never a scored zero.
  -- A queued run expires only when it has been able to start for 30 minutes and the
  -- dispatcher never took it: waiting for an earlier evaluation of its self-check set
  -- (or an earlier scenario of a local session) is not stuck, and a run the dispatcher
  -- is retrying (it has a lease) follows the lease budget in observer_pending_runs
  -- (parked with an incident, never failed).
  for v in select r.id,r.batch_id from public.observer_runs r
    left join private.observer_sessions s on s.run_id=r.id
    where (r.status='queued' and not exists(select 1 from private.observer_run_leases l where l.run_id=r.id)
        and private.observer_run_startable_since(r.id)<now()-interval '30 minutes')
      or (r.status in ('starting','ready','running') and s.expires_at<=now())
    order by r.created_at limit 100
  loop
    update public.observer_runs set status='failed',error='evaluation_expired',finished_at=now()
      where id=v.id and status in ('queued','starting','ready','running');
    perform private.observer_finalize_batch(v.batch_id);
    n:=n+1;
  end loop;
  return n;
end $function$;

create or replace function private.observer_replace_failed_repeat()
returns trigger language plpgsql security definer set search_path=public,pg_temp as $$
declare v_id uuid;
begin
  if new.status='failed' and old.status is distinct from 'failed' and new.repeat_group is not null
    and new.quota_refunded and new.purpose='formal' and new.superseded_at is null
    and exists(select 1 from public.phases p join public.observer_phase_settings c on c.phase_id=p.id
      where p.id=new.phase_id and p.is_active and not c.sealed and (p.ends_at is null or now()<p.ends_at))
    and (select count(*) from public.observer_batches where repeat_group=new.repeat_group)<coalesce(new.repeat_runs,3)+2
    and exists(select 1 from public.profiles u where u.id=new.user_id and u.team_id=new.team_id and not u.is_banned)
  then
    insert into public.observer_batches(team_id,user_id,phase_id,revision_id,mode,repeat_group,repeat_runs,created_at)
      values(new.team_id,new.user_id,new.phase_id,new.revision_id,new.mode,new.repeat_group,new.repeat_runs,clock_timestamp())
      returning id into v_id;
    insert into public.observer_runs(batch_id,scenario_id,created_at)
      select v_id,scenario_id,clock_timestamp() from public.phase_scenarios where phase_id=new.phase_id order by scenario_id;
    perform private.audit('observer.repeat_replaced',jsonb_build_object('repeat_group',new.repeat_group,
      'failed_batch',new.id,'batch',v_id));
  end if;
  return null;
end $$;
revoke all on function private.observer_replace_failed_repeat() from public,anon,authenticated;
drop trigger if exists observer_replace_failed_repeat on public.observer_batches;
create trigger observer_replace_failed_repeat after update of status on public.observer_batches
  for each row execute function private.observer_replace_failed_repeat();
notify pgrst,'reload schema';

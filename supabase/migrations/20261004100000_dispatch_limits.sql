-- Dispatcher throughput (daily quota 40 from 2026-10-04): one observer-dispatch call
-- repeats its schedule-and-dispatch pass until a pass finds no work, max_passes is
-- reached or pass_seconds have elapsed (below observer_tick's 50-second spacing, so
-- invocations never overlap). Per pass: preparations (db cap 10), runs (db cap 10),
-- score jobs (db cap 20), job dispatches (db cap 20). Leases, skip-locked rows and the
-- two-minute re-dispatch guard keep repeated passes from dispatching a job twice.
--
--   select public.observer_set_dispatch_limits(p_max_passes=>8);           -- tune
--   select public.observer_set_dispatch_limits(3,5,5,10,1,40);              -- rollback: the old single pass
create table if not exists private.observer_dispatch_limits(
  id boolean primary key default true check (id),
  preparations integer not null default 6 check (preparations between 1 and 10),
  runs integer not null default 10 check (runs between 1 and 10),
  scores integer not null default 20 check (scores between 1 and 20),
  jobs integer not null default 20 check (jobs between 1 and 20),
  max_passes integer not null default 8 check (max_passes between 1 and 20),
  pass_seconds integer not null default 40 check (pass_seconds between 5 and 45),
  updated_at timestamptz not null default now()
);
insert into private.observer_dispatch_limits(id) values(true) on conflict do nothing;
revoke all on private.observer_dispatch_limits from public,anon,authenticated;

create or replace function public.observer_dispatch_limits()
returns jsonb language sql stable security definer set search_path=public,pg_temp as $$
  select to_jsonb(l)-'id' from private.observer_dispatch_limits l where l.id
$$;

create or replace function public.observer_set_dispatch_limits(p_preparations integer default null,p_runs integer default null,
  p_scores integer default null,p_jobs integer default null,p_max_passes integer default null,p_pass_seconds integer default null)
returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$
begin
  update private.observer_dispatch_limits set preparations=coalesce(p_preparations,preparations),runs=coalesce(p_runs,runs),
    scores=coalesce(p_scores,scores),jobs=coalesce(p_jobs,jobs),max_passes=coalesce(p_max_passes,max_passes),
    pass_seconds=coalesce(p_pass_seconds,pass_seconds),updated_at=now() where id;
  perform private.audit('observer.dispatch_limits',public.observer_dispatch_limits());
  return public.observer_dispatch_limits();
end $$;

revoke all on function public.observer_dispatch_limits() from public,anon,authenticated;
revoke all on function public.observer_set_dispatch_limits(integer,integer,integer,integer,integer,integer) from public,anon,authenticated;
grant execute on function public.observer_dispatch_limits() to service_role;
grant execute on function public.observer_set_dispatch_limits(integer,integer,integer,integer,integer,integer) to service_role;
notify pgrst,'reload schema';

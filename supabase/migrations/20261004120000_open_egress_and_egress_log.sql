-- Open egress and the per-run egress log.
--
-- Open egress (owner decision 2026-10-04): during a run the project may reach any
-- public destination on port 443 or 80; private, loopback, link-local, CGNAT,
-- multicast and metadata addresses stay unreachable, also through DNS rebinding
-- (the runner's sidecar resolves every name itself, project_platform/team_egress.py).
-- The team's allowed-domain list is no longer used while open egress is on; the
-- data is kept so that the rollback (switch off) restores the allow-list as it was.
--
--   observer_hardening.open_egress        global switch (default off)
--   observer_hardening.open_egress_teams  pilot teams before the global switch
--   rollback: update private.observer_hardening set open_egress=false, open_egress_teams='{}' where id;
--
-- Egress log: the runner reports, per destination host and port, the number of
-- connections and refusals, bytes each way and first/last time (never content) in
-- its job receipt (result.egress). A trigger copies it into
-- private.observer_run_egress, readable by organizers; the team sees the same
-- record in its own run log (agent.log) and result bundle (egress.json), and
-- through observer_run_egress_log for its own runs. Idempotent.

alter table private.observer_hardening add column if not exists open_egress boolean not null default false;
alter table private.observer_hardening add column if not exists open_egress_teams uuid[] not null default '{}';

create or replace function private.observer_open_egress_on(p_team uuid)
returns boolean language sql stable security definer set search_path=public,pg_temp as $$
  select coalesce((select h.open_egress or p_team=any(h.open_egress_teams) from private.observer_hardening h where h.id),
    false)
$$;

create or replace function public.observer_run_team_egress(p_run uuid)
returns jsonb language sql stable security definer set search_path=public,pg_temp as $$
  select jsonb_build_object(
    'enabled',private.observer_team_egress_on(b.team_id),
    'open',private.observer_open_egress_on(b.team_id),
    'variables',coalesce((select jsonb_agg(jsonb_build_object('id',v.id,'name',v.name,'secret',v.secret,
        'encrypted_value',v.encrypted_value,'plain_value',v.plain_value) order by v.name)
      from private.observer_team_variables v where v.team_id=b.team_id),'[]'::jsonb),
    'domains',coalesce((select jsonb_agg(d.host order by d.host) from private.observer_team_domains d
      where d.team_id=b.team_id),'[]'::jsonb))
  from public.observer_runs r join public.observer_batches b on b.id=r.batch_id where r.id=p_run
$$;

create or replace function public.observer_preparation_team_egress(p_revision uuid)
returns jsonb language sql stable security definer set search_path=public,pg_temp as $$
  select jsonb_build_object(
    'enabled',coalesce((select h.prepare_direct_model from private.observer_hardening h where h.id),false)
      and private.observer_team_egress_on(p.team_id),
    'open',private.observer_open_egress_on(p.team_id),
    'variables',coalesce((select jsonb_agg(jsonb_build_object('id',v.id,'name',v.name,'secret',v.secret,
        'encrypted_value',v.encrypted_value,'plain_value',v.plain_value) order by v.name)
      from private.observer_team_variables v where v.team_id=p.team_id),'[]'::jsonb),
    'domains',coalesce((select jsonb_agg(d.host order by d.host) from private.observer_team_domains d
      where d.team_id=p.team_id),'[]'::jsonb))
  from public.observer_revisions r join public.observer_projects p on p.id=r.project_id where r.id=p_revision
$$;

create or replace function public.observer_team_environment()
returns jsonb language sql stable security definer set search_path=public,pg_temp as $$
  select jsonb_build_object(
    'enabled',private.observer_team_egress_on(t.team_id),
    'open',private.observer_open_egress_on(t.team_id),
    'variables',coalesce((select jsonb_agg(jsonb_build_object('name',v.name,'secret',v.secret,'hint',v.hint,
        'value',v.plain_value,'updated_at',v.updated_at) order by v.name)
      from private.observer_team_variables v where v.team_id=t.team_id),'[]'::jsonb),
    'domains',coalesce((select jsonb_agg(d.host order by d.host) from private.observer_team_domains d
      where d.team_id=t.team_id),'[]'::jsonb),
    'relay_key_missing',private.observer_team_model_mode(t.team_id)='relay'
      and not exists(select 1 from private.observer_team_variables v where v.team_id=t.team_id and v.secret),
    'limits',jsonb_build_object('variables',20,'domains',10,'value_bytes',8192))
  from (select private.observer_team_of(auth.uid()) team_id) t where t.team_id is not null
$$;

-- Keep the switches visible next to the existing ones.
create or replace function public.observer_hardening()
returns jsonb language sql stable security definer set search_path=public,pg_temp as $$
  select coalesce((select jsonb_build_object('restricted_egress',h.restricted_egress,'rescore',h.rescore,
      'team_egress',h.team_egress,'prepare_direct_model',h.prepare_direct_model,'model_proxy_retired',h.model_proxy_retired,
      'open_egress',h.open_egress)
    from private.observer_hardening h where h.id),
    jsonb_build_object('restricted_egress',false,'rescore',false,'team_egress',false,
      'prepare_direct_model',false,'model_proxy_retired',false,'open_egress',false))
$$;

create table if not exists private.observer_run_egress(
  run_id uuid not null references public.observer_runs(id) on delete cascade,
  job_id uuid not null,
  host text not null check (length(host) between 1 and 253),
  port integer not null check (port between 0 and 65535),
  connections bigint not null default 0 check (connections>=0),
  refused bigint not null default 0 check (refused>=0),
  bytes_up bigint not null default 0 check (bytes_up>=0),
  bytes_down bigint not null default 0 check (bytes_down>=0),
  first_at timestamptz,
  last_at timestamptz,
  recorded_at timestamptz not null default now(),
  primary key(job_id,host,port)
);
create index if not exists observer_run_egress_run on private.observer_run_egress(run_id);
revoke all on private.observer_run_egress from public,anon,authenticated;

-- Copies a finished job's egress record (defensive: malformed entries are skipped).
create or replace function private.observer_record_run_egress()
returns trigger language plpgsql security definer set search_path=public,pg_temp as $$
declare e jsonb;
begin
  if new.run_id is null or new.result is null or jsonb_typeof(new.result->'egress')<>'array' then return new; end if;
  if old.result is not distinct from new.result then return new; end if;
  for e in select value from jsonb_array_elements(new.result->'egress') with ordinality x(value,n) where n<=600 loop
    begin
      if jsonb_typeof(e->'host')='string' and (e->>'host') ~ '^[a-z0-9.:_\[\]()-]{1,253}$'
        and jsonb_typeof(e->'port')='number' then
        insert into private.observer_run_egress(run_id,job_id,host,port,connections,refused,bytes_up,bytes_down,
            first_at,last_at)
          values(new.run_id,new.id,e->>'host',(e->>'port')::integer,greatest(coalesce((e->>'connections')::bigint,0),0),
            greatest(coalesce((e->>'refused')::bigint,0),0),greatest(coalesce((e->>'bytes_up')::bigint,0),0),
            greatest(coalesce((e->>'bytes_down')::bigint,0),0),(e->>'first')::timestamptz,(e->>'last')::timestamptz)
          on conflict do nothing;
      end if;
    exception when others then
      null; -- one malformed entry never blocks the job receipt
    end;
  end loop;
  return new;
end $$;
drop trigger if exists observer_jobs_record_egress on private.observer_jobs;
create trigger observer_jobs_record_egress after update of result on private.observer_jobs
  for each row execute function private.observer_record_run_egress();

-- A team member: the record of one of the team's own runs.
create or replace function public.observer_run_egress_log(p_run uuid)
returns jsonb language sql stable security definer set search_path=public,pg_temp as $$
  select coalesce(jsonb_agg(jsonb_build_object('host',e.host,'port',e.port,'connections',e.connections,
      'refused',e.refused,'bytes_up',e.bytes_up,'bytes_down',e.bytes_down,'first_at',e.first_at,'last_at',e.last_at)
      order by e.bytes_up+e.bytes_down desc,e.host),'[]'::jsonb)
  from private.observer_run_egress e join public.observer_runs r on r.id=e.run_id
    join public.observer_batches b on b.id=r.batch_id
  where e.run_id=p_run and b.team_id=private.observer_team_of(auth.uid())
$$;

revoke all on function private.observer_open_egress_on(uuid),private.observer_record_run_egress() from public,anon,authenticated;
revoke all on function public.observer_run_egress_log(uuid) from public,anon;
grant execute on function public.observer_run_egress_log(uuid) to authenticated;
notify pgrst,'reload schema';

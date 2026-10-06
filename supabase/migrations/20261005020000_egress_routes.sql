-- Egress route (出网线路): a team may send its evaluation traffic through a platform route
-- instead of the runner's own address (some model relays refuse GitHub-hosted runner
-- addresses). Choices: direct (default), cn (回国代理), overseas (海外代理); with
-- auto_fallback (default on) a connection goes direct when no route node works.
--
-- Only labels live here. The node settings are the Supabase secret OBSERVER_EGRESS_ROUTES,
-- which the job API adds to the claim payload (sealed for the public pool); they never
-- reach the database, a job input, a page, a log or the participant container
-- (project_platform/team_egress.py, ops/egress-routes.md).
--
--   observer_hardening.egress_routes          global switch (default off)
--   observer_hardening.egress_routes_teams    pilot/test teams before the global switch
--   observer_hardening.egress_route_cap_bytes proxied bytes (both ways) per run, default 500 MB
--   rollback: update private.observer_hardening set egress_routes=false, egress_routes_teams='{}' where id;
--
-- The runner records the path of every destination (direct, cn, overseas:nodeN) in the
-- egress log and a per-run route summary (labels and counters only) in
-- private.observer_run_egress_route. Additive and idempotent.

alter table private.observer_hardening add column if not exists egress_routes boolean not null default false;
alter table private.observer_hardening add column if not exists egress_routes_teams uuid[] not null default '{}';
alter table private.observer_hardening add column if not exists egress_route_cap_bytes bigint not null default 524288000;

create table if not exists private.observer_team_egress_route(
  team_id uuid primary key references public.teams(id) on delete cascade,
  route text not null default 'direct' check (route in ('direct','cn','overseas')),
  auto_fallback boolean not null default true,
  updated_by uuid,
  updated_at timestamptz not null default now()
);
revoke all on private.observer_team_egress_route from public,anon,authenticated;

create or replace function private.observer_egress_routes_on(p_team uuid)
returns boolean language sql stable security definer set search_path=public,pg_temp as $$
  select coalesce((select h.egress_routes or p_team=any(h.egress_routes_teams) from private.observer_hardening h
    where h.id),false)
$$;

-- The job-input form (labels only), or null: direct, switched off, or no open egress.
create or replace function private.observer_team_route(p_team uuid)
returns jsonb language sql stable security definer set search_path=public,pg_temp as $$
  select case when private.observer_egress_routes_on(p_team) and private.observer_open_egress_on(p_team)
      and r.route<>'direct' then
    jsonb_build_object('name',r.route,'fallback',r.auto_fallback,'cap_bytes',
      greatest(coalesce((select h.egress_route_cap_bytes from private.observer_hardening h where h.id),524288000),1))
    end
  from private.observer_team_egress_route r where r.team_id=p_team
$$;

-- A team member chooses the route (any member, like the team's variables).
create or replace function public.observer_set_team_egress_route(p_route text,p_auto_fallback boolean default null)
returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$
declare v_team uuid;
begin
  perform private.assert_not_banned();
  v_team:=private.observer_team_of(auth.uid());
  if v_team is null then raise exception 'team_required'; end if;
  if p_route is null or p_route not in ('direct','cn','overseas') then raise exception 'invalid_egress_route'; end if;
  if p_route<>'direct' and not (private.observer_egress_routes_on(v_team) and private.observer_open_egress_on(v_team)) then
    raise exception 'egress_route_unavailable'; end if;
  insert into private.observer_team_egress_route(team_id,route,auto_fallback,updated_by)
    values(v_team,p_route,coalesce(p_auto_fallback,true),auth.uid())
    on conflict(team_id) do update set route=excluded.route,
      auto_fallback=coalesce(p_auto_fallback,private.observer_team_egress_route.auto_fallback),
      updated_by=excluded.updated_by,updated_at=now();
  perform private.audit('observer.team_egress_route_saved',jsonb_build_object('team_id',v_team,'user_id',auth.uid(),
    'route',p_route,'auto_fallback',p_auto_fallback));
  return public.observer_team_environment();
end $$;

-- The workspace: the same as before plus the team's route (labels only).
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
    'limits',jsonb_build_object('variables',20,'domains',10,'value_bytes',8192),
    'egress_route',jsonb_build_object(
      'available',private.observer_egress_routes_on(t.team_id) and private.observer_open_egress_on(t.team_id),
      'route',coalesce((select r.route from private.observer_team_egress_route r where r.team_id=t.team_id),'direct'),
      'auto_fallback',coalesce((select r.auto_fallback from private.observer_team_egress_route r
        where r.team_id=t.team_id),true)))
  from (select private.observer_team_of(auth.uid()) team_id) t where t.team_id is not null
$$;

-- Scheduler only (service role): as before plus "route" (labels; null for direct).
create or replace function public.observer_run_team_egress(p_run uuid)
returns jsonb language sql stable security definer set search_path=public,pg_temp as $$
  select jsonb_build_object(
    'enabled',private.observer_team_egress_on(b.team_id),
    'open',private.observer_open_egress_on(b.team_id),
    'variables',coalesce((select jsonb_agg(jsonb_build_object('id',v.id,'name',v.name,'secret',v.secret,
        'encrypted_value',v.encrypted_value,'plain_value',v.plain_value) order by v.name)
      from private.observer_team_variables v where v.team_id=b.team_id),'[]'::jsonb),
    'domains',coalesce((select jsonb_agg(d.host order by d.host) from private.observer_team_domains d
      where d.team_id=b.team_id),'[]'::jsonb),
    'route',private.observer_team_route(b.team_id))
  from public.observer_runs r join public.observer_batches b on b.id=r.batch_id where r.id=p_run
$$;

create or replace function public.observer_hardening()
returns jsonb language sql stable security definer set search_path=public,pg_temp as $$
  select coalesce((select jsonb_build_object('restricted_egress',h.restricted_egress,'rescore',h.rescore,
      'team_egress',h.team_egress,'prepare_direct_model',h.prepare_direct_model,'model_proxy_retired',h.model_proxy_retired,
      'open_egress',h.open_egress,'egress_routes',h.egress_routes)
    from private.observer_hardening h where h.id),
    jsonb_build_object('restricted_egress',false,'rescore',false,'team_egress',false,
      'prepare_direct_model',false,'model_proxy_retired',false,'open_egress',false,'egress_routes',false))
$$;

-- Egress log: the path each destination took. One row per destination as before; a
-- destination that used two paths in one job is recorded as 'mixed'.
alter table private.observer_run_egress add column if not exists path text
  check (path is null or path ~ '^(direct|none|mixed|cn|overseas:node[1-4])$');

create table if not exists private.observer_run_egress_route(
  job_id uuid primary key,
  run_id uuid not null references public.observer_runs(id) on delete cascade,
  route text not null check (route in ('cn','overseas')),
  auto_fallback boolean not null,
  cap_bytes bigint not null,
  nodes integer not null,
  proxied_bytes bigint not null default 0,
  capped boolean not null default false,
  failovers integer not null default 0,
  fallbacks integer not null default 0,
  paths jsonb not null default '{}'::jsonb,   -- {label: {connections, bytes_up, bytes_down}}
  recorded_at timestamptz not null default now()
);
create index if not exists observer_run_egress_route_run on private.observer_run_egress_route(run_id);
revoke all on private.observer_run_egress_route from public,anon,authenticated;

create or replace function private.observer_record_run_egress()
returns trigger language plpgsql security definer set search_path=public,pg_temp as $$
declare e jsonb; v_path text; s jsonb;
begin
  if new.run_id is null or new.result is null then return new; end if;
  if old.result is not distinct from new.result then return new; end if;
  if jsonb_typeof(new.result->'egress')='array' then
    for e in select value from jsonb_array_elements(new.result->'egress') with ordinality x(value,n) where n<=600 loop
      begin
        if jsonb_typeof(e->'host')='string' and (e->>'host') ~ '^[a-z0-9.:_\[\]()-]{1,253}$'
          and jsonb_typeof(e->'port')='number' then
          v_path:=case when (e->>'path') ~ '^(direct|none|cn|overseas:node[1-4])$' then e->>'path' end;
          insert into private.observer_run_egress as x(run_id,job_id,host,port,path,connections,refused,bytes_up,
              bytes_down,first_at,last_at)
            values(new.run_id,new.id,e->>'host',(e->>'port')::integer,v_path,
              greatest(coalesce((e->>'connections')::bigint,0),0),greatest(coalesce((e->>'refused')::bigint,0),0),
              greatest(coalesce((e->>'bytes_up')::bigint,0),0),greatest(coalesce((e->>'bytes_down')::bigint,0),0),
              (e->>'first')::timestamptz,(e->>'last')::timestamptz)
            on conflict(job_id,host,port) do update set
              connections=x.connections+excluded.connections,refused=x.refused+excluded.refused,
              bytes_up=x.bytes_up+excluded.bytes_up,bytes_down=x.bytes_down+excluded.bytes_down,
              first_at=least(x.first_at,excluded.first_at),last_at=greatest(x.last_at,excluded.last_at),
              path=case when x.path is not distinct from excluded.path then x.path
                when x.path='none' then excluded.path when excluded.path='none' then x.path else 'mixed' end;
        end if;
      exception when others then
        null; -- one malformed entry never blocks the job receipt
      end;
    end loop;
  end if;
  s:=new.result->'egress_route';
  if jsonb_typeof(s)='object' then
    begin
      insert into private.observer_run_egress_route(job_id,run_id,route,auto_fallback,cap_bytes,nodes,proxied_bytes,
          capped,failovers,fallbacks,paths)
        select new.id,new.run_id,s->>'route',(s->>'fallback')::boolean,(s->>'cap_bytes')::bigint,(s->>'nodes')::integer,
          (s->>'proxied_bytes')::bigint,(s->>'capped')::boolean,(s->>'failovers')::integer,(s->>'fallbacks')::integer,
          coalesce((select jsonb_object_agg(k,v) from jsonb_each(case when jsonb_typeof(s->'paths')='object'
              then s->'paths' else '{}'::jsonb end) p(k,v)
            where k ~ '^(direct|cn|overseas:node[1-4])$' and jsonb_typeof(v)='object'),'{}'::jsonb)
        on conflict(job_id) do nothing;
    exception when others then
      null;
    end;
  end if;
  return new;
end $$;

-- A team member: the record of one of the team's own runs (now with the path).
create or replace function public.observer_run_egress_log(p_run uuid)
returns jsonb language sql stable security definer set search_path=public,pg_temp as $$
  select coalesce(jsonb_agg(jsonb_build_object('host',e.host,'port',e.port,'path',coalesce(e.path,'direct'),
      'connections',e.connections,'refused',e.refused,'bytes_up',e.bytes_up,'bytes_down',e.bytes_down,
      'first_at',e.first_at,'last_at',e.last_at)
      order by e.bytes_up+e.bytes_down desc,e.host),'[]'::jsonb)
  from private.observer_run_egress e join public.observer_runs r on r.id=e.run_id
    join public.observer_batches b on b.id=r.batch_id
  where e.run_id=p_run and b.team_id=private.observer_team_of(auth.uid())
$$;

revoke all on function private.observer_egress_routes_on(uuid),private.observer_team_route(uuid)
  from public,anon,authenticated;
revoke all on function public.observer_set_team_egress_route(text,boolean) from public,anon;
grant execute on function public.observer_set_team_egress_route(text,boolean) to authenticated;
notify pgrst,'reload schema';

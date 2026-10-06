-- Staged rollout of team egress: teams listed in observer_hardening.team_egress_teams
-- use it while the global switch is still off (pilot/test teams first).
alter table private.observer_hardening add column if not exists team_egress_teams uuid[] not null default '{}';

create or replace function private.observer_team_egress_on(p_team uuid)
returns boolean language sql stable security definer set search_path=public,pg_temp as $$
  select coalesce((select h.team_egress or p_team=any(h.team_egress_teams) from private.observer_hardening h where h.id),false)
$$;

-- Same result as before plus "enabled" for this run's team.
create or replace function public.observer_run_team_egress(p_run uuid)
returns jsonb language sql stable security definer set search_path=public,pg_temp as $$
  select jsonb_build_object(
    'enabled',private.observer_team_egress_on(b.team_id),
    'variables',coalesce((select jsonb_agg(jsonb_build_object('id',v.id,'name',v.name,'secret',v.secret,
        'encrypted_value',v.encrypted_value,'plain_value',v.plain_value) order by v.name)
      from private.observer_team_variables v where v.team_id=b.team_id),'[]'::jsonb),
    'domains',coalesce((select jsonb_agg(d.host order by d.host) from private.observer_team_domains d
      where d.team_id=b.team_id),'[]'::jsonb))
  from public.observer_runs r join public.observer_batches b on b.id=r.batch_id where r.id=p_run
$$;

-- What the workspace shows: the same "enabled" for the caller's team.
create or replace function public.observer_team_environment()
returns jsonb language sql stable security definer set search_path=public,pg_temp as $$
  select jsonb_build_object(
    'enabled',private.observer_team_egress_on(t.team_id),
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

revoke all on function private.observer_team_egress_on(uuid) from public,anon,authenticated;
notify pgrst,'reload schema';

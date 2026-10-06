-- Retiring the platform model proxy (observer-model), staged and reversible.
--
-- Since team egress is on (20261004040000, observer_hardening.team_egress) a team's
-- program reaches its own provider with its own variables; evaluations no longer use
-- the proxy (last proxied evaluation call 2026-10-04 05:14 UTC). Two users were left:
--
-- * Preparation's automatic adaptation (projects without observer.project.json).
--   prepare_direct_model=true gives the prepare job the team's variables
--   (team_egress) instead of a proxy credential; project_platform/model_client.py
--   then calls OPENAI_* or ANTHROPIC_* directly (https, team's allowed domains only).
-- * The local runner (project_platform/local.py): without --model-base-url it passes
--   the participant's own variables through (no platform change needed).
--
-- model_proxy_retired=true makes the proxy refuse runs of every team that has team
-- egress on (error model_proxy_retired). The proxy code stays deployed. Switching
-- team egress off (the egress rollback) reopens the proxy for those teams at once,
-- without touching this switch, so the proxy remains the egress rollback path.
--
-- Rollback: select public.observer_set_model_proxy(p_prepare_direct=>false, p_retired=>false);

alter table private.observer_hardening add column if not exists prepare_direct_model boolean not null default false;
alter table private.observer_hardening add column if not exists model_proxy_retired boolean not null default false;

create or replace function private.observer_model_proxy_retired(p_team uuid)
returns boolean language sql stable security definer set search_path=public,pg_temp as $$
  select coalesce((select h.model_proxy_retired from private.observer_hardening h where h.id),false)
    and private.observer_team_egress_on(p_team)
$$;

-- The prepare job's direct model access: the team's variables while the switch is on.
create or replace function public.observer_preparation_team_egress(p_revision uuid)
returns jsonb language sql stable security definer set search_path=public,pg_temp as $$
  select jsonb_build_object(
    'enabled',coalesce((select h.prepare_direct_model from private.observer_hardening h where h.id),false)
      and private.observer_team_egress_on(p.team_id),
    'variables',coalesce((select jsonb_agg(jsonb_build_object('id',v.id,'name',v.name,'secret',v.secret,
        'encrypted_value',v.encrypted_value,'plain_value',v.plain_value) order by v.name)
      from private.observer_team_variables v where v.team_id=p.team_id),'[]'::jsonb),
    'domains',coalesce((select jsonb_agg(d.host order by d.host) from private.observer_team_domains d
      where d.team_id=p.team_id),'[]'::jsonb))
  from public.observer_revisions r join public.observer_projects p on p.id=r.project_id where r.id=p_revision
$$;

create or replace function public.observer_model_route(p_run uuid, p_token text)
returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$
declare v_team uuid;
begin
  perform private.observer_capability(p_run,p_token,'participant');
  select b.team_id into v_team from public.observer_runs r join public.observer_batches b on b.id=r.batch_id where r.id=p_run;
  -- Retired for teams on team egress (they call their provider directly).
  if private.observer_model_proxy_retired(v_team) then raise exception 'model_proxy_retired'; end if;
  if not private.observer_personal_models_only(p_run) then return jsonb_build_object('personal',false); end if;
  if private.observer_run_model_mode(p_run)='stored' then
    return jsonb_build_object('personal',true,'mode','stored','protocol',private.observer_team_model_protocol(v_team));
  end if;
  insert into private.observer_personal_model_channels(run_id) values(p_run) on conflict do nothing;
  return (select jsonb_build_object('personal',true,'mode','relay','topic',topic,
      'protocol',private.observer_team_model_protocol(v_team))
    from private.observer_personal_model_channels where run_id=p_run);
end $$;

create or replace function public.observer_hardening()
returns jsonb language sql stable security definer set search_path=public,pg_temp as $$
  select coalesce((select jsonb_build_object('restricted_egress',h.restricted_egress,'rescore',h.rescore,
      'team_egress',h.team_egress,'prepare_direct_model',h.prepare_direct_model,'model_proxy_retired',h.model_proxy_retired)
    from private.observer_hardening h where h.id),
    jsonb_build_object('restricted_egress',false,'rescore',false,'team_egress',false,
      'prepare_direct_model',false,'model_proxy_retired',false))
$$;

create or replace function public.observer_set_model_proxy(p_prepare_direct boolean default null,p_retired boolean default null)
returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$
begin
  insert into private.observer_hardening(id) values(true) on conflict do nothing;
  update private.observer_hardening set prepare_direct_model=coalesce(p_prepare_direct,prepare_direct_model),
    model_proxy_retired=coalesce(p_retired,model_proxy_retired),updated_at=now() where id;
  perform private.audit('observer.model_proxy',public.observer_hardening());
  return public.observer_hardening();
end $$;

revoke all on function private.observer_model_proxy_retired(uuid),public.observer_preparation_team_egress(uuid),
  public.observer_model_route(uuid,text),public.observer_hardening(),public.observer_set_model_proxy(boolean,boolean)
  from public,anon,authenticated;
grant execute on function public.observer_preparation_team_egress(uuid),public.observer_model_route(uuid,text),
  public.observer_hardening(),public.observer_set_model_proxy(boolean,boolean) to service_role;

-- The organizer-paid route (practice on house credits, "Organizer test API", qwen):
-- last call 2026-09-24 09:51 UTC, 8 calls in total. Off; a run that would use it gets
-- model_not_available. Rollback: update private.observer_providers set enabled=true where team_id is null;
update private.observer_providers set enabled=false where team_id is null and enabled;

notify pgrst,'reload schema';

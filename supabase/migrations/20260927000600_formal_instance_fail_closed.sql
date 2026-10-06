-- Final formal evaluations must never fall back to the shared scenario template.
-- A formal run in a final phase (counts_for_final, or the 'online' phase) now
-- requires a private instance: allocation fails when no calibration matches,
-- scheduling refuses a run without an instance, and a result without one is
-- rejected. Practice, preview and non-final phases are unchanged.

create or replace function private.observer_requires_instance(p_batch uuid)
returns boolean language sql stable security definer set search_path=public,pg_temp as $$
  select exists(select 1 from public.observer_batches b join public.phases p on p.id=b.phase_id
    where b.id=p_batch and b.purpose='formal' and (p.counts_for_final or p.slug='online'));
$$;
revoke all on function private.observer_requires_instance(uuid) from public,anon,authenticated;

create or replace function private.observer_allocate_instance()
returns trigger language plpgsql security definer set search_path=public,pg_temp as $$
declare v record; v_seed text;
begin
  select b.team_id,b.phase_id,c.profile_id,c.seed_scope into v from public.observer_batches b
    join private.observer_scenario_calibration c on c.phase_id=b.phase_id and c.scenario_id=new.scenario_id
    where b.id=new.batch_id and b.purpose='formal';
  if not found then
    if private.observer_requires_instance(new.batch_id) then raise exception 'formal_instance_not_configured'; end if;
    return new;
  end if;
  if v.seed_scope='run' then
    insert into private.observer_scenario_instances(run_id,profile_id) values(new.id,v.profile_id);
    return new;
  end if;
  insert into private.observer_team_instance_seeds(team_id,phase_id,scenario_id)
    values(v.team_id,v.phase_id,new.scenario_id) on conflict do nothing;
  select seed into v_seed from private.observer_team_instance_seeds
    where team_id=v.team_id and phase_id=v.phase_id and scenario_id=new.scenario_id;
  insert into private.observer_scenario_instances(run_id,profile_id,seed) values(new.id,v.profile_id,v_seed);
  return new;
end $$;
revoke all on function private.observer_allocate_instance() from public,anon,authenticated;

-- The scheduler builds the engine job from this; a NULL for a final formal run
-- would silently evaluate the template, so refuse instead.
create or replace function public.observer_instance_input(p_run uuid)
returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$
declare v jsonb;
begin
  select jsonb_build_object('seed',i.seed,'profile_id',i.profile_id,'profile',p.profile,
      'bundle_digest',p.bundle_digest,'max_candidates',32) into v
    from private.observer_scenario_instances i join private.observer_calibration_profiles p on p.id=i.profile_id
    where i.run_id=p_run;
  if v is null and private.observer_requires_instance((select batch_id from public.observer_runs where id=p_run)) then
    raise exception 'formal_instance_missing'; end if;
  return v;
end $$;
revoke all on function public.observer_instance_input(uuid) from public,anon,authenticated;
grant execute on function public.observer_instance_input(uuid) to service_role;

create or replace function private.observer_verify_calibrated_result()
returns trigger language plpgsql security definer set search_path=public,pg_temp as $$
declare v private.observer_scenario_instances; d jsonb; c jsonb; v_raw double precision; v_score double precision;
begin
  if new.score_summary is null then return new; end if;
  select * into v from private.observer_scenario_instances where run_id=new.id;
  if not found then
    if private.observer_requires_instance(new.batch_id) then raise exception 'formal_instance_missing'; end if;
    return new;
  end if;
  if v.record is null then raise exception 'instance_not_recorded'; end if;
  d:=v.record->'difficulty'; c:=new.score_summary->'calibration';
  v_raw:=(new.score_summary->'raw_score'->>'total')::double precision;
  if v_raw is null or v_raw in ('NaN'::double precision,'Infinity'::double precision,'-Infinity'::double precision) or
    (c->>'version') is distinct from 'observer-reference-panel-v1' or
    (c->>'instance_commitment') is distinct from (v.record->>'instance_digest') or
    (c->>'wait_score')::double precision is distinct from (d->>'wait_score')::double precision or
    (c->>'reference_score')::double precision is distinct from (d->>'reference_score')::double precision or
    (c->>'span')::double precision is distinct from (d->>'span')::double precision then
    raise exception 'invalid_calibrated_score'; end if;
  v_score:=round((10000*(v_raw-(d->>'wait_score')::double precision)/(d->>'span')::double precision)::numeric,6)::double precision;
  if new.score is null or new.score in ('NaN'::double precision,'Infinity'::double precision,'-Infinity'::double precision) or
    abs(new.score-v_score)>0.000001 or (c->>'adjusted_score')::double precision is null or
    (c->>'adjusted_score')::double precision in ('NaN'::double precision,'Infinity'::double precision,'-Infinity'::double precision) or
    abs((c->>'adjusted_score')::double precision-v_score)>0.000001 then raise exception 'invalid_calibrated_score'; end if;
  return new;
end $$;
revoke all on function private.observer_verify_calibrated_result() from public,anon,authenticated;

-- Hardening: clients only read these tables (RLS). Admins edit scenarios
-- through the admin page (authenticated UPDATE under the is_admin policy), so
-- that one privilege stays. Nothing writes submissions as anon.
revoke insert,delete,truncate,trigger,references on public.scenarios from anon,authenticated;
revoke update on public.scenarios from anon;
revoke insert,update,delete,truncate,trigger,references on public.submissions from anon;

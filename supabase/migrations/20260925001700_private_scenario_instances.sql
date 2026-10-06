-- Opt-in only: no existing phase, submission, run or score is changed.
-- Profiles and seeds live entirely outside the participant-readable schema.
create table private.observer_calibration_profiles (
  id uuid primary key default gen_random_uuid(),
  scenario_id uuid not null references public.scenarios(id),
  bundle_digest text not null check (bundle_digest ~ '^[0-9a-f]{64}$'),
  profile jsonb not null check (coalesce(
    profile->>'schema_version' = 'observer-calibration-profile-v1' and
    profile->>'panel_version' = 'observer-reference-panel-v1' and
    profile->>'template_digest' ~ '^[0-9a-f]{64}$' and jsonb_typeof(profile->'bounds')='object',false)),
  created_at timestamptz not null default now()
);
create table private.observer_scenario_calibration (
  phase_id uuid not null references public.phases(id),
  scenario_id uuid not null references public.scenarios(id),
  profile_id uuid not null references private.observer_calibration_profiles(id),
  primary key(phase_id,scenario_id)
);
create table private.observer_scenario_instances (
  run_id uuid primary key references public.observer_runs(id),
  profile_id uuid not null references private.observer_calibration_profiles(id),
  -- Three independently random UUIDs provide more than 256 bits of entropy;
  -- hash them to a fixed 256-bit key without adding an extension dependency.
  seed text not null unique default encode(sha256(convert_to(
    gen_random_uuid()::text||gen_random_uuid()::text||gen_random_uuid()::text,'UTF8')),'hex')
    check(seed ~ '^[0-9a-f]{64}$'),
  record jsonb,
  created_at timestamptz not null default now(),
  recorded_at timestamptz
);
revoke all on private.observer_calibration_profiles,private.observer_scenario_calibration,
  private.observer_scenario_instances from public,anon,authenticated;

create function private.observer_immutable_calibration_profile()
returns trigger language plpgsql set search_path=public,pg_temp as $$
begin raise exception 'calibration_profile_immutable'; end $$;
create trigger observer_immutable_calibration_profile before update or delete
  on private.observer_calibration_profiles for each row execute function private.observer_immutable_calibration_profile();

create function private.observer_configure_calibration()
returns trigger language plpgsql security definer set search_path=public,pg_temp as $$
declare v_phase uuid; v_profile private.observer_calibration_profiles;
begin
  v_phase:=case when TG_OP='DELETE' then old.phase_id else new.phase_id end;
  if TG_OP='UPDATE' and (old.phase_id,old.scenario_id) is distinct from (new.phase_id,new.scenario_id) then
    raise exception 'calibration_configuration_immutable'; end if;
  perform pg_advisory_xact_lock(hashtextextended('observer-calibration/'||v_phase::text,0));
  if exists(select 1 from public.observer_batches where phase_id=v_phase and purpose='formal') then
    raise exception 'calibration_configuration_immutable'; end if;
  if TG_OP='DELETE' then return old; end if;
  select * into v_profile from private.observer_calibration_profiles where id=new.profile_id;
  if v_profile.scenario_id is distinct from new.scenario_id or
    not exists(select 1 from public.phase_scenarios where phase_id=new.phase_id and scenario_id=new.scenario_id) or
    not exists(select 1 from private.observer_scenario_bundles where scenario_id=new.scenario_id and digest=v_profile.bundle_digest)
    then raise exception 'calibration_template_mismatch'; end if;
  return new;
end $$;
create trigger observer_configure_calibration before insert or update or delete
  on private.observer_scenario_calibration for each row execute function private.observer_configure_calibration();

-- Freeze the actual scenario roster as well as its calibration bindings. This
-- shares the admission lock so an organizer edit cannot race a first batch.
create function private.observer_freeze_calibrated_roster()
returns trigger language plpgsql security definer set search_path=public,pg_temp as $$
declare v_phase uuid;
begin
  for v_phase in select distinct phase_id from unnest(array[
    case when TG_OP<>'INSERT' then old.phase_id end,
    case when TG_OP<>'DELETE' then new.phase_id end]) as phases(phase_id)
    where phase_id is not null order by phase_id
  loop
    perform pg_advisory_xact_lock(hashtextextended('observer-calibration/'||v_phase::text,0));
    if exists(select 1 from private.observer_scenario_calibration where phase_id=v_phase) and
      exists(select 1 from public.observer_batches where phase_id=v_phase and purpose='formal') then
      raise exception 'calibration_configuration_immutable'; end if;
  end loop;
  if TG_OP='DELETE' then return old; end if;
  return new;
end $$;
create trigger observer_freeze_calibrated_roster before insert or update or delete
  on public.phase_scenarios for each row execute function private.observer_freeze_calibrated_roster();
revoke all on function private.observer_freeze_calibrated_roster() from public,anon,authenticated;

create function private.observer_check_calibrated_batch()
returns trigger language plpgsql security definer set search_path=public,pg_temp as $$
begin
  if new.purpose<>'formal' then return new; end if;
  perform pg_advisory_xact_lock(hashtextextended('observer-calibration/'||new.phase_id::text,0));
  if exists(select 1 from private.observer_scenario_calibration where phase_id=new.phase_id) and
    exists(select 1 from public.phase_scenarios ps where ps.phase_id=new.phase_id and not exists(
      select 1 from private.observer_scenario_calibration c where c.phase_id=ps.phase_id and c.scenario_id=ps.scenario_id))
    then raise exception 'calibration_configuration_incomplete'; end if;
  return new;
end $$;
create trigger observer_check_calibrated_batch before insert on public.observer_batches
  for each row execute function private.observer_check_calibrated_batch();

create function private.observer_allocate_instance()
returns trigger language plpgsql security definer set search_path=public,pg_temp as $$
begin
  insert into private.observer_scenario_instances(run_id,profile_id)
    select new.id,c.profile_id from public.observer_batches b
      join private.observer_scenario_calibration c on c.phase_id=b.phase_id and c.scenario_id=new.scenario_id
      where b.id=new.batch_id and b.purpose='formal';
  return new;
end $$;
create trigger observer_allocate_instance after insert on public.observer_runs
  for each row execute function private.observer_allocate_instance();

create function private.observer_freeze_instance()
returns trigger language plpgsql set search_path=public,pg_temp as $$
begin
  if TG_OP='DELETE' or (new.run_id,new.profile_id,new.seed,new.created_at) is distinct from
    (old.run_id,old.profile_id,old.seed,old.created_at) or
    (old.record is not null and (new.record,new.recorded_at) is distinct from (old.record,old.recorded_at)) then
    raise exception 'instance_immutable'; end if;
  return new;
end $$;
create trigger observer_freeze_instance before update or delete on private.observer_scenario_instances
  for each row execute function private.observer_freeze_instance();

create function public.observer_instance_input(p_run uuid)
returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$
declare v jsonb;
begin
  select jsonb_build_object('seed',i.seed,'profile_id',i.profile_id,'profile',p.profile,
      'bundle_digest',p.bundle_digest,'max_candidates',32) into v
    from private.observer_scenario_instances i join private.observer_calibration_profiles p on p.id=i.profile_id
    where i.run_id=p_run;
  return v;
end $$;

create function public.observer_record_instance(p_run uuid,p_token text,p_record jsonb)
returns void language plpgsql security definer set search_path=public,pg_temp as $$
declare v_session private.observer_sessions; v private.observer_scenario_instances;
  p private.observer_calibration_profiles; d jsonb; bound record;
  v_floor double precision; v_span double precision; v_reference double precision;
  v_value double precision; v_low double precision; v_high double precision;
begin
  v_session:=private.observer_capability(p_run,p_token,'engine');
  select * into v from private.observer_scenario_instances where run_id=p_run for update;
  if not found then raise exception 'instance_not_configured'; end if;
  select * into p from private.observer_calibration_profiles where id=v.profile_id;
  if p_record is null or jsonb_typeof(p_record)<>'object' or octet_length(p_record::text)>65536 or
    (p_record->>'seed') is distinct from v.seed or
    (p_record->>'generator_version') is distinct from 'observer-weather-instance-v1' or
    (p_record->>'template_digest') is distinct from (p.profile->>'template_digest') or
    not coalesce(p_record->>'instance_digest' ~ '^[0-9a-f]{64}$',false) or
    not coalesce(p_record->>'profile_digest' ~ '^[0-9a-f]{64}$',false) or
    not coalesce(p_record->>'candidate' ~ '^([0-9]|[12][0-9]|3[01])$',false) or
    (p_record->'difficulty'->>'panel_version') is distinct from 'observer-reference-panel-v1'
    then raise exception 'invalid_instance_record'; end if;
  d:=p_record->'difficulty';
  v_floor:=(d->>'wait_score')::double precision;
  v_span:=(d->>'span')::double precision;
  v_reference:=(d->>'reference_score')::double precision;
  if v_floor is null or v_span is null or v_reference is null or v_span<=0 or
    v_floor in ('NaN'::double precision,'Infinity'::double precision,'-Infinity'::double precision) or
    v_span in ('NaN'::double precision,'Infinity'::double precision,'-Infinity'::double precision) or
    v_reference in ('NaN'::double precision,'Infinity'::double precision,'-Infinity'::double precision) or
    abs(v_reference-v_floor-v_span)>0.000001 or
    (select array_agg(key order by key) from jsonb_each(p.profile->'bounds')) is distinct from
      array['gain_rate','open_fraction','requests_first','required_first','span']::text[] then
    raise exception 'invalid_instance_record'; end if;
  for bound in select key,value from jsonb_each(p.profile->'bounds') loop
    v_low:=(bound.value->>0)::double precision; v_high:=(bound.value->>1)::double precision;
    v_value:=case when bound.key in ('span','open_fraction') then (d->>bound.key)::double precision
      else round((10000*((d->'policies'->bound.key->>'score')::double precision-v_floor)/v_span)::numeric,6)::double precision end;
    if v_value is null or v_low is null or v_high is null or v_low>v_high or
      v_value in ('NaN'::double precision,'Infinity'::double precision,'-Infinity'::double precision) or
      v_low in ('NaN'::double precision,'Infinity'::double precision,'-Infinity'::double precision) or
      v_high in ('NaN'::double precision,'Infinity'::double precision,'-Infinity'::double precision) or
      v_value<v_low-0.000001 or v_value>v_high+0.000001 then raise exception 'scenario_not_comparable'; end if;
  end loop;
  if abs(((d->'policies'->'gain_rate'->>'score')::double precision+
      (d->'policies'->'required_first'->>'score')::double precision+
      (d->'policies'->'requests_first'->>'score')::double precision)/3-v_reference)>0.000001 or
    (d->'policies'->'wait'->>'score')::double precision is distinct from v_floor then
    raise exception 'invalid_instance_record'; end if;
  if v.record is not null then
    if v.record is distinct from p_record then raise exception 'instance_record_conflict'; end if;
    return;
  end if;
  if v_session.publication is not null then raise exception 'instance_already_published'; end if;
  update private.observer_scenario_instances set record=p_record,recorded_at=clock_timestamp() where run_id=p_run;
end $$;

-- Even a trusted engine must freeze its selected scenario before publishing
-- the first observation. Old fixed scenarios keep their existing behavior.
create function private.observer_require_instance_record()
returns trigger language plpgsql security definer set search_path=public,pg_temp as $$
begin
  if new.publication is not null and exists(select 1 from private.observer_scenario_instances
    where run_id=new.run_id and record is null) then raise exception 'instance_not_recorded'; end if;
  return new;
end $$;
create trigger observer_require_instance_record before update of publication on private.observer_sessions
  for each row execute function private.observer_require_instance_record();

create function private.observer_verify_calibrated_result()
returns trigger language plpgsql security definer set search_path=public,pg_temp as $$
declare v private.observer_scenario_instances; d jsonb; c jsonb; v_raw double precision; v_score double precision;
begin
  if new.score_summary is null then return new; end if;
  select * into v from private.observer_scenario_instances where run_id=new.id;
  if not found then return new; end if;
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
create trigger observer_verify_calibrated_result before update of score_summary,score on public.observer_runs
  for each row execute function private.observer_verify_calibrated_result();

revoke all on function private.observer_immutable_calibration_profile(),private.observer_configure_calibration(),
  private.observer_check_calibrated_batch(),private.observer_allocate_instance(),private.observer_require_instance_record(),
  public.observer_instance_input(uuid),public.observer_record_instance(uuid,text,jsonb) from public,anon,authenticated;
revoke all on function private.observer_verify_calibrated_result() from public,anon,authenticated;
revoke all on function private.observer_freeze_instance() from public,anon,authenticated;
grant execute on function public.observer_instance_input(uuid),public.observer_record_instance(uuid,text,jsonb) to service_role;

-- Publish the original component scores separately from the calibrated ranking.
create or replace function public.observer_board(p_phase uuid,p_limit integer default 100)
returns jsonb language sql stable security definer set search_path=public,pg_temp as $$
 select coalesce(jsonb_agg(to_jsonb(rows) order by rows.rank,rows.scored_at),'[]') from (
  select board.rank,board.team_id,board.team_name,board.score as total_score,board.finished_at as scored_at,
    board.batch_id as observer_batch_id,'observer'::text as kind,
    bool_and(r.score_summary ? 'calibration') as calibrated,
    avg(coalesce((parts.raw->>'total')::double precision,r.score)) as raw_total_score,
    avg(coalesce((parts.raw->>'base_science')::double precision,0)) as base_science,
    avg(coalesce((parts.raw->>'program_bonus')::double precision,0)) as program_bonus,
    avg(coalesce((parts.raw->>'request_reward')::double precision,0)) as request_reward,
    avg(coalesce((parts.raw->>'report_reward')::double precision,0)) as report_reward,
    avg(coalesce((parts.raw->>'coverage_bonus')::double precision,0)) as coverage_bonus,
    avg(coalesce((parts.raw->>'coverage_evenness')::double precision,0)) as coverage_evenness,
    avg(coalesce((select sum(value::double precision) from jsonb_each_text(parts.raw->'penalties')),0)) as penalty_total,
    avg((r.score_summary->>'completed_tiles')::double precision) as completed_tiles,
    avg((r.score_summary->>'required_missing')::double precision) as required_missing,
    (select count(*) from public.observer_batches b where b.phase_id=p_phase and b.team_id=board.team_id
      and b.purpose='formal' and b.status='scored') as submission_count
  from public.observer_leaderboard(p_phase,p_limit) board
  join public.observer_runs r on r.batch_id=board.batch_id
  cross join lateral (select coalesce(r.score_summary->'raw_score',r.score_summary->'score') as raw) parts
  where public.observer_phase_visible(p_phase)
  group by board.rank,board.team_id,board.team_name,board.score,board.finished_at,board.batch_id
 ) rows
$$;

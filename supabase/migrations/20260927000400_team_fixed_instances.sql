-- Formal scenarios: each team gets one private instance per phase and scenario,
-- reused by every evaluation of that team. Different teams still get different
-- weather and hidden tags. A fresh instance per evaluation ('run') is kept as a
-- switch but is off by default.
alter table private.observer_scenario_calibration
  add column if not exists seed_scope text not null default 'team' check (seed_scope in ('team','run'));

create table if not exists private.observer_team_instance_seeds (
  team_id uuid not null references public.teams(id),
  phase_id uuid not null references public.phases(id),
  scenario_id uuid not null references public.scenarios(id),
  seed text not null unique default encode(sha256(convert_to(
    gen_random_uuid()::text||gen_random_uuid()::text||gen_random_uuid()::text,'UTF8')),'hex')
    check(seed ~ '^[0-9a-f]{64}$'),
  created_at timestamptz not null default now(),
  primary key(team_id,phase_id,scenario_id)
);
revoke all on private.observer_team_instance_seeds from public,anon,authenticated;

-- Several runs of one team now share a seed.
alter table private.observer_scenario_instances drop constraint if exists observer_scenario_instances_seed_key;

create or replace function private.observer_allocate_instance()
returns trigger language plpgsql security definer set search_path=public,pg_temp as $$
declare v record; v_seed text;
begin
  select b.team_id,b.phase_id,c.profile_id,c.seed_scope into v from public.observer_batches b
    join private.observer_scenario_calibration c on c.phase_id=b.phase_id and c.scenario_id=new.scenario_id
    where b.id=new.batch_id and b.purpose='formal';
  if not found then return new; end if;
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

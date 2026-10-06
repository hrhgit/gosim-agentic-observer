-- Organizer decision 2026-09-26: fixed formal scenarios, a team-chosen final
-- version and one hidden final evaluation.
--
-- 1. The 'online' phase evaluates on three fixed scenarios (eval-a, eval-b, eval-c):
--    every team plays the same template. Their files and weather stay private
--    (observations arrive step by step). Its calibration rows are removed
--    and the fail-closed rule of 20260927000600 now applies only to phases that
--    are configured for calibration (at least one calibration row). Phases that
--    keep calibration (acceptance) still refuse a run without a private instance.
--    Source files stay private for formal scenarios as before; only a formal
--    scenario whose weather, forecasts and events are all public (none today)
--    would open once its phase starts.
-- 2. Sealed phases (observer_phase_settings.sealed): the hidden final phase. Until
--    organizers set its leaderboard_mode to 'published', participants cannot see
--    the phase, its scenario names, its batches, runs, logs or result downloads,
--    and nobody signed in can start an evaluation there; organizers create its
--    batches through private.observer_run_hidden_final (scripts/run-hidden-final.py).
--    Its scenario files stay private, and its scenario cannot be shared with any
--    other phase.
-- 3. Final version: during an open formal phase a team member may mark one of
--    the team's approved, not withdrawn versions as its final version, until the
--    phase ends_at. Without a choice the version of the team's best scored batch
--    of that phase is used.
-- Functions are based on the live definitions.

-- 1. Fixed formal scenarios ---------------------------------------------------
create or replace function private.observer_requires_instance(p_batch uuid)
returns boolean language sql stable security definer set search_path=public,pg_temp as $$
  select exists(select 1 from public.observer_batches b where b.id=p_batch and b.purpose='formal'
    and exists(select 1 from private.observer_scenario_calibration c where c.phase_id=b.phase_id));
$$;
revoke all on function private.observer_requires_instance(uuid) from public,anon,authenticated;

-- The trigger refuses this once a formal batch exists in the phase; 'online'
-- has none (it opens 2026-10-04 16:00Z), so a failure here is intentional.
delete from private.observer_scenario_calibration
  where phase_id in (select id from public.phases where slug='online');

-- 2. Sealed (hidden) phases ---------------------------------------------------
alter table public.observer_phase_settings add column if not exists sealed boolean not null default false;

-- True while a sealed phase's results are not published.
create or replace function public.observer_phase_sealed(p_phase uuid)
returns boolean language sql stable security definer set search_path=public,pg_temp as $$
  select exists(select 1 from public.observer_phase_settings s join public.phases p on p.id=s.phase_id
    where s.phase_id=p_phase and s.sealed and p.leaderboard_mode<>'published')
$$;
revoke all on function public.observer_phase_sealed(uuid) from public;
grant execute on function public.observer_phase_sealed(uuid) to anon,authenticated,service_role;

-- Used by the restrictive policies on phases, phase settings and phase scenarios,
-- by the observer boards and by the evaluation quota.
create or replace function public.observer_phase_visible(p_phase uuid)
returns boolean language sql stable security definer set search_path=public,pg_temp as $$
  select not exists(select 1 from public.observer_phase_settings c where c.phase_id=p_phase
    and c.access_team_id is not null and not exists(select 1 from public.profiles p
      where p.id=auth.uid() and not p.is_banned and (p.team_id=c.access_team_id or p.is_admin)))
    and (not public.observer_phase_sealed(p_phase) or public.is_admin())
$$;

-- A scenario of a sealed phase stays unnamed until its results are published.
-- Team-restricted internal phases (observer_phase_settings.access_team_id, e.g.
-- the randomized acceptance phase without dates) no longer keep a formal
-- scenario unnamed after the public phase has opened.
create or replace function public.observer_scenario_listed(p_scenario uuid)
returns boolean language sql stable security definer set search_path=public,pg_temp as $$
  select not exists(select 1 from public.phase_scenarios ps join public.phases p on p.id=ps.phase_id
      left join public.observer_phase_settings s on s.phase_id=p.id
      where ps.scenario_id=p_scenario and (((p.counts_for_final or p.slug='online') and s.access_team_id is null
        and (p.starts_at is null or now()<p.starts_at)) or public.observer_phase_sealed(p.id)))
    or exists(select 1 from public.phase_scenarios ps join public.phases p on p.id=ps.phase_id
      left join public.observer_phase_settings s on s.phase_id=p.id
      where ps.scenario_id=p_scenario and not p.counts_for_final and p.slug<>'online' and p.is_active
        and s.access_team_id is null and (p.starts_at is null or now()>=p.starts_at))
$$;

-- Scenario source files (storage bucket 'scenarios', restrictive policy "formal
-- source files remain private" from 20260927000100). eval-a/b/c keep their three
-- public flags false, so their files stay private; the rules below only matter
-- for a formal scenario deliberately made fully public:
--   * a scenario of a sealed phase stays private, also after its results are
--     published (organizers release it deliberately by unsealing the phase);
--   * a scenario of a formal phase ('online' or counts_for_final) stays private
--     unless its weather, forecasts and events are all public, and, for a phase
--     open to everyone, until that phase starts;
--   * otherwise the per-file flags of the permissive "scenario files read"
--     policy apply as before (tile_anomalies.csv is never listed there).
create or replace function public.observer_formal_source(p_slug text)
returns boolean language sql stable security definer set search_path=public,pg_temp as $$
  select exists (select 1 from public.scenarios s
    join public.phase_scenarios ps on ps.scenario_id=s.id
    join public.phases p on p.id=ps.phase_id
    left join public.observer_phase_settings c on c.phase_id=p.id
    where s.slug=p_slug and (coalesce(c.sealed,false)
      or ((p.counts_for_final or p.slug='online') and (
        not (s.weather_public and s.forecasts_public and s.events_public)
        or (c.access_team_id is null and (p.starts_at is null or now()<p.starts_at))))))
$$;

-- A hidden final scenario belongs to its sealed phase only: it can never be
-- linked to 'online' or any other phase, and a phase whose scenarios are shared
-- cannot become sealed.
create or replace function private.observer_sealed_scenario_exclusive()
returns trigger language plpgsql security definer set search_path=public,pg_temp as $$
begin
  if tg_table_name='phase_scenarios' then
    if exists(select 1 from public.phase_scenarios ps join public.observer_phase_settings c on c.phase_id=ps.phase_id
        where ps.scenario_id=new.scenario_id and ps.phase_id<>new.phase_id and c.sealed)
      or (exists(select 1 from public.observer_phase_settings where phase_id=new.phase_id and sealed)
        and exists(select 1 from public.phase_scenarios where scenario_id=new.scenario_id and phase_id<>new.phase_id))
      then raise exception 'sealed_scenario_shared'; end if;
  elsif new.sealed and exists(select 1 from public.phase_scenarios ps join public.phase_scenarios other
      on other.scenario_id=ps.scenario_id and other.phase_id<>ps.phase_id where ps.phase_id=new.phase_id) then
    raise exception 'sealed_scenario_shared';
  end if;
  return new;
end $$;
revoke all on function private.observer_sealed_scenario_exclusive() from public,anon,authenticated;
drop trigger if exists observer_sealed_scenario_exclusive on public.phase_scenarios;
create trigger observer_sealed_scenario_exclusive before insert or update on public.phase_scenarios
  for each row execute function private.observer_sealed_scenario_exclusive();
drop trigger if exists observer_sealed_scenario_exclusive on public.observer_phase_settings;
create trigger observer_sealed_scenario_exclusive before insert or update of sealed on public.observer_phase_settings
  for each row execute function private.observer_sealed_scenario_exclusive();

-- Runs are readable through their batch (observer_run_read), so this also hides
-- runs, scores, result paths and therefore result downloads (observer-portal
-- download_result reads the run with the participant's own token).
drop policy if exists observer_batch_sealed on public.observer_batches;
create policy observer_batch_sealed on public.observer_batches as restrictive for select to anon,authenticated
  using (not public.observer_phase_sealed(phase_id) or public.is_admin());

-- Program logs of a sealed run may echo hidden observations.
create or replace function public.observer_diagnostics(p_revision uuid default null, p_run uuid default null)
returns jsonb language plpgsql stable security definer set search_path=public,pg_temp as $$
declare team uuid; result jsonb;
begin
  select team_id into team from public.profiles where id=auth.uid() and not is_banned;
  if team is null or (p_revision is null)=(p_run is null) then raise exception 'diagnostics_not_found';end if;
  if p_revision is not null and not exists(select 1 from public.observer_revisions r
    join public.observer_projects p on p.id=r.project_id where r.id=p_revision and p.team_id=team)
    then raise exception 'diagnostics_not_found';end if;
  if p_run is not null and not exists(select 1 from public.observer_runs r
    join public.observer_batches b on b.id=r.batch_id where r.id=p_run and b.team_id=team
      and (not public.observer_phase_sealed(b.phase_id) or public.is_admin()))
    then raise exception 'diagnostics_not_found';end if;
  select coalesce(jsonb_agg(jsonb_build_object('kind',j.kind,'status',j.status,
    'code',left(coalesce(j.result->'diagnostics'->>'code',nullif(j.error,''),j.status),100),
    'log',left(coalesce(j.result->'diagnostics'->>'log',''),32768),'finished_at',j.finished_at)
    order by j.created_at),'[]'::jsonb) into result
  from private.observer_jobs j
  left join public.observer_runs r on r.id=j.run_id
  left join public.observer_batches b on b.id=r.batch_id
  where (p_run is not null and j.run_id=p_run)
    or (p_revision is not null and (j.revision_id=p_revision or (b.revision_id=p_revision and b.purpose='preview')));
  return result;
end $$;

-- Nobody signed in starts an evaluation in a sealed phase (organizers use
-- private.observer_run_hidden_final without a user token), and a sealed phase
-- does not keep project uploads open after the public phase has ended.
create or replace function private.observer_restrict_phase_writes()
returns trigger language plpgsql security definer set search_path=public,pg_temp as $$
declare team uuid;
begin
  if tg_table_name='observer_batches' then
    if exists(select 1 from public.observer_phase_settings where phase_id=new.phase_id
      and access_team_id is not null and access_team_id<>new.team_id) then raise exception 'phase_closed'; end if;
    if auth.uid() is not null and exists(select 1 from public.observer_phase_settings where phase_id=new.phase_id and sealed)
      then raise exception 'phase_closed'; end if;
  else
    select team_id into team from public.observer_projects where id=new.project_id;
    if not exists(select 1 from public.observer_phase_settings c join public.phases p on p.id=c.phase_id
      where c.projects_enabled and not c.sealed and p.is_active and (p.ends_at is null or now()<p.ends_at)
        and (c.access_team_id is null or c.access_team_id=team)) then raise exception 'projects_not_enabled'; end if;
  end if;
  return new;
end $$;

-- 3. Final version ------------------------------------------------------------
create table if not exists private.observer_final_versions (
  team_id uuid not null references public.teams(id) on delete cascade,
  phase_id uuid not null references public.phases(id) on delete cascade,
  revision_id uuid not null references public.observer_revisions(id),
  chosen_by uuid not null,
  chosen_at timestamptz not null default now(),
  primary key(team_id,phase_id)
);
revoke all on private.observer_final_versions from public,anon,authenticated;

-- An open (not sealed) formal phase in which teams choose a final version.
create or replace function private.observer_final_phase(p_phase uuid)
returns boolean language sql stable security definer set search_path=public,pg_temp as $$
  select exists(select 1 from public.phases p join public.observer_phase_settings s on s.phase_id=p.id
    where p.id=p_phase and p.is_active and (p.counts_for_final or p.slug='online')
      and s.projects_enabled and not s.sealed and (p.starts_at is null or now()>=p.starts_at))
$$;

-- The team's final version for a phase: its valid choice, else the version of
-- its best scored batch (ties: the earlier batch, as on the board).
create or replace function private.observer_final_version_state(p_team uuid, p_phase uuid)
returns jsonb language sql stable security definer set search_path=public,pg_temp as $$
  with ph as (select p.id,p.ends_at from public.phases p where p.id=p_phase),
  chosen as (select f.revision_id,f.chosen_by,f.chosen_at from private.observer_final_versions f
    join public.observer_revisions r on r.id=f.revision_id join public.observer_projects j on j.id=r.project_id
    where f.team_id=p_team and f.phase_id=p_phase and j.team_id=p_team and r.status='approved' and r.archived_at is null),
  best as (select b.id,b.revision_id,b.score from public.observer_batches b
    where b.team_id=p_team and b.phase_id=p_phase and b.purpose='formal' and b.status='scored'
      and b.revision_id is not null and b.score is not null
    order by b.score desc,b.created_at,b.id limit 1)
  select jsonb_build_object('phase_id',ph.id,'deadline',ph.ends_at,
    'locked',ph.ends_at is not null and now()>=ph.ends_at,
    'revision_id',coalesce(c.revision_id,b.revision_id),
    'source',case when c.revision_id is not null then 'chosen' when b.revision_id is not null then 'best' end,
    'chosen_revision_id',c.revision_id,'chosen_by',c.chosen_by,'chosen_at',c.chosen_at,
    'best_batch_id',b.id,'best_revision_id',b.revision_id,'best_score',b.score)
  from ph left join chosen c on true left join best b on true
$$;
revoke all on function private.observer_final_phase(uuid), private.observer_final_version_state(uuid,uuid)
  from public,anon,authenticated;

-- The signed-in user's team, for every phase in which it chooses a final version.
create or replace function public.observer_final_versions()
returns jsonb language sql stable security definer set search_path=public,pg_temp as $$
  select coalesce(jsonb_agg(private.observer_final_version_state(u.team_id,p.id) order by p.sort_order,p.id),'[]'::jsonb)
  from public.profiles u cross join public.phases p
  where u.id=auth.uid() and not u.is_banned and u.team_id is not null
    and private.observer_final_phase(p.id) and public.observer_phase_visible(p.id)
$$;

-- p_revision null clears the choice (the default applies again).
create or replace function public.observer_set_final_version(p_phase uuid, p_revision uuid default null)
returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$
declare v_team uuid; v_phase public.phases; v_revision public.observer_revisions;
begin
  perform private.assert_not_banned();
  select team_id into v_team from public.profiles where id=auth.uid();
  if v_team is null then raise exception 'team_required'; end if;
  perform 1 from public.teams where id=v_team for update;
  select * into v_phase from public.phases where id=p_phase;
  if not found or not private.observer_final_phase(p_phase) or not public.observer_phase_visible(p_phase) then
    raise exception 'final_phase_invalid'; end if;
  if v_phase.ends_at is not null and now()>=v_phase.ends_at then raise exception 'final_version_locked'; end if;
  if p_revision is null then
    delete from private.observer_final_versions where team_id=v_team and phase_id=p_phase;
  else
    select r.* into v_revision from public.observer_revisions r join public.observer_projects j on j.id=r.project_id
      where r.id=p_revision and j.team_id=v_team;
    if not found then raise exception 'revision_not_found'; end if;
    if v_revision.archived_at is not null then raise exception 'revision_withdrawn'; end if;
    if v_revision.status<>'approved' then raise exception 'revision_not_approved'; end if;
    insert into private.observer_final_versions(team_id,phase_id,revision_id,chosen_by)
      values(v_team,p_phase,p_revision,auth.uid())
      on conflict(team_id,phase_id) do update set revision_id=excluded.revision_id,chosen_by=excluded.chosen_by,chosen_at=now();
  end if;
  perform private.audit('observer.final_version',jsonb_build_object('phase_id',p_phase,'revision_id',p_revision));
  return private.observer_final_version_state(v_team,p_phase);
end $$;

-- A chosen final version cannot be withdrawn; clear the choice first (possible
-- only before the phase ends, so the final version cannot change afterwards).
create or replace function public.observer_withdraw_revision(p_revision uuid)
returns void language plpgsql security definer set search_path=public,pg_temp as $$
declare v public.observer_revisions;
begin
  perform private.assert_not_banned();
  select r.* into v from public.observer_revisions r join public.observer_projects p on p.id=r.project_id
    where r.id=p_revision and public.observer_team(p.team_id) for update of r;
  if not found then raise exception 'revision_not_found'; end if;
  if v.archived_at is not null then return; end if;
  if v.status not in ('queued','reviewable','failed','approved')
    or exists(select 1 from public.observer_batches where revision_id=p_revision and purpose='formal')
    or exists(select 1 from private.observer_final_versions where revision_id=p_revision) then
    raise exception 'revision_not_withdrawable'; end if;
  update public.observer_revisions set archived_at=now(),
    status=case when v.status='queued' then 'failed' else v.status end,
    error=case when v.status='queued' then 'Withdrawn by the team.' else v.error end
    where id=p_revision;
  perform private.audit('observer.withdraw', jsonb_build_object('revision_id',p_revision,'status',v.status));
end $$;

-- Organizer view: every team with a confirmed version or a choice in the phase.
create or replace function public.observer_admin_final_versions(p_phase uuid)
returns jsonb language plpgsql stable security definer set search_path=public,pg_temp as $$
begin
  if not public.is_admin() then raise exception 'forbidden'; end if;
  return (select coalesce(jsonb_agg(x.row order by x.name),'[]'::jsonb) from (
    select t.name, private.observer_final_version_state(t.id,p_phase)||jsonb_build_object(
      'team_id',t.id,'team_name',t.name,'team_hidden',t.is_hidden,
      'project_title',(select j.title from public.observer_revisions r join public.observer_projects j on j.id=r.project_id
        where r.id=(private.observer_final_version_state(t.id,p_phase)->>'revision_id')::uuid),
      'chosen_by_github',(select u.github from public.profiles u
        where u.id=(private.observer_final_version_state(t.id,p_phase)->>'chosen_by')::uuid),
      'model_mode',private.observer_team_model_mode(t.id),
      'model_key_saved',exists(select 1 from private.observer_team_models m join private.observer_providers v on v.id=m.provider_id
        where m.team_id=t.id and v.encrypted_key<>'')) as row
    from public.teams t
    where exists(select 1 from public.observer_projects j join public.observer_revisions r on r.project_id=j.id
        where j.team_id=t.id and r.status='approved' and r.archived_at is null)
      or exists(select 1 from private.observer_final_versions f where f.team_id=t.id and f.phase_id=p_phase)) x);
end $$;

-- 4. Hidden final evaluation (organizers only; no participant route) -----------
-- One formal batch per team in the sealed target phase for its final version in
-- the source phase, outside the daily limit. Dry run unless p_apply. Refuses
-- before the source phase has ended unless p_before_freeze (testing, one team).
create or replace function private.observer_run_hidden_final(p_source uuid, p_target uuid, p_team uuid default null,
  p_apply boolean default false, p_before_freeze boolean default false, p_retry_failed boolean default false)
returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$
declare v_source public.phases; v_target public.phases; t record; v_state jsonb; v_revision uuid; v_user uuid;
  v_batch uuid; v_items jsonb:='[]'; v_item jsonb; v_reason text; v_created integer:=0;
begin
  perform pg_advisory_xact_lock(hashtextextended('observer-hidden-final/'||p_target::text,0));
  select * into v_source from public.phases where id=p_source;
  select * into v_target from public.phases where id=p_target;
  if v_source.id is null or v_target.id is null then raise exception 'phase_not_found'; end if;
  if not exists(select 1 from public.observer_phase_settings where phase_id=p_target and sealed and projects_enabled)
    or not v_target.counts_for_final or not v_target.is_active then raise exception 'target_not_sealed_final_phase'; end if;
  if not exists(select 1 from public.phase_scenarios where phase_id=p_target)
    or exists(select 1 from public.phase_scenarios ps where ps.phase_id=p_target
      and not exists(select 1 from private.observer_scenario_bundles b where b.scenario_id=ps.scenario_id))
    then raise exception 'target_scenarios_not_ready'; end if;
  if v_source.ends_at is null or now()<v_source.ends_at then
    if not p_before_freeze or p_team is null then raise exception 'source_phase_not_finished'; end if;
  end if;
  for t in select x.id,x.name,x.is_hidden,x.leader_id from public.teams x
    where (p_team is null or x.id=p_team) order by x.name,x.id
  loop
    v_state:=private.observer_final_version_state(t.id,p_source);
    v_revision:=(v_state->>'revision_id')::uuid;
    if v_revision is null and p_team is null then continue; end if;
    v_reason:=null; v_user:=null; v_batch:=null;
    if v_revision is null then v_reason:='no_final_version';
    elsif exists(select 1 from public.observer_batches where team_id=t.id and phase_id=p_target and purpose='formal'
        and (status<>'failed' or not p_retry_failed)) then v_reason:='already_evaluated';
    elsif not exists(select 1 from private.observer_materializations where revision_id=v_revision) then
      v_reason:='version_not_materialized';
    end if;
    if v_reason is null then
      -- The batch runs as a current, unbanned member: the chooser, the confirmer, the leader, else the earliest member.
      select u.id into v_user from public.profiles u
        where u.team_id=t.id and not u.is_banned
        order by (u.id=(v_state->>'chosen_by')::uuid) desc nulls last,
          (u.id=(select approved_by from public.observer_revisions where id=v_revision)) desc nulls last,
          (u.id=t.leader_id) desc nulls last, u.created_at, u.id limit 1;
      if v_user is null then v_reason:='no_active_member'; end if;
    end if;
    if v_reason is null and p_apply then
      insert into public.observer_batches(team_id,user_id,phase_id,revision_id,mode,purpose)
        values(t.id,v_user,p_target,v_revision,'project','formal') returning id into v_batch;
      insert into public.observer_runs(batch_id,scenario_id) select v_batch,scenario_id from public.phase_scenarios where phase_id=p_target;
      v_created:=v_created+1;
    end if;
    v_item:=jsonb_build_object('team_id',t.id,'team_name',t.name,'team_hidden',t.is_hidden,
      'revision_id',v_revision,'source',v_state->>'source','best_score',v_state->'best_score',
      'user_id',v_user,'model_mode',private.observer_team_model_mode(t.id),
      'model_key_saved',exists(select 1 from private.observer_team_models m join private.observer_providers v on v.id=m.provider_id
        where m.team_id=t.id and v.encrypted_key<>''),
      'action',case when v_reason is not null then 'skip' when p_apply then 'created' else 'would_create' end,
      'reason',v_reason,'batch_id',v_batch);
    v_items:=v_items||jsonb_build_array(v_item);
  end loop;
  if p_apply and v_created>0 then
    perform private.audit('observer.hidden_final_started',jsonb_build_object('source',p_source,'target',p_target,
      'team',p_team,'batches',v_created));
  end if;
  return jsonb_build_object('apply',p_apply,'source_phase',v_source.slug,'target_phase',v_target.slug,
    'source_ends_at',v_source.ends_at,'frozen',v_source.ends_at is not null and now()>=v_source.ends_at,
    'created',v_created,'teams',v_items);
end $$;
revoke all on function private.observer_run_hidden_final(uuid,uuid,uuid,boolean,boolean,boolean) from public,anon,authenticated;

revoke all on function public.observer_final_versions(), public.observer_set_final_version(uuid,uuid),
  public.observer_admin_final_versions(uuid) from public,anon;
grant execute on function public.observer_final_versions(), public.observer_set_final_version(uuid,uuid),
  public.observer_admin_final_versions(uuid) to authenticated,service_role;
notify pgrst,'reload schema';

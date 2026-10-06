-- Saving a model key on the server becomes an explicit opt-in, and saved keys
-- are deleted automatically once no phase can use them any more.
--
-- 1. Default: a team that has never chosen is in relay mode ("do not save").
--    Teams that chose stored mode, or saved a key (which selects stored mode),
--    keep their row and are not changed.
-- 2. Auto-purge: every participant-owned key of a team is wiped (with the same
--    private.observer_forget_provider_key used by the manual purge) when
--      a) no phase that can use the team's key is open or upcoming: every such
--         active phase has an end time, and the latest one ended at least the
--         retention period ago (default 7 days, leaving time to verify results
--         and re-run top teams);
--      b) the key itself was saved at least the retention period ago;
--      c) nothing of the team is in progress: no queued/starting/ready/running
--         run, no queued/running batch, no queued/preparing project revision and
--         no unsettled model-call reservation on its keys.
--    The phases that can use a key are those whose runs are formal
--    (private.observer_personal_models_only): counts_for_final, slug 'online',
--    'observer-acceptance-*' and 'practice-projects'; while the site is in
--    competition mode also every other active phase that can host runs. A phase
--    restricted to another team (observer_phase_settings.access_team_id) does not
--    hold this team's key. There is no "results verified" flag in the schema, so
--    the phase end plus the retention period stands in for it.
--    pg_cron runs the purge hourly where available. public.observer_purge_provider_keys()
--    is unchanged and can still be run at any time.
-- The migration is idempotent and alters no existing table.

-- 1. Opt-in default ------------------------------------------------------------
-- Any team that already has a saved key keeps stored mode explicitly.
insert into private.observer_team_model_modes(team_id,mode,updated_by)
  select m.team_id,'stored',m.saved_by from private.observer_team_models m
  on conflict(team_id) do nothing;

create or replace function private.observer_team_model_mode(p_team uuid)
returns text language sql stable security definer set search_path=public,pg_temp as $$
  select coalesce((select mode from private.observer_team_model_modes where team_id=p_team),'relay')
$$;
revoke all on function private.observer_team_model_mode(uuid) from public,anon,authenticated;

-- 2. Automatic purge -----------------------------------------------------------
create table if not exists private.observer_key_retention (
  id boolean primary key default true check(id),
  enabled boolean not null default true,
  retention interval not null default interval '7 days' check (retention>=interval '0'),
  last_run_at timestamptz
);
insert into private.observer_key_retention(id) values(true) on conflict(id) do nothing;
revoke all on private.observer_key_retention from public,anon,authenticated;

-- When this team's keys may be purged: the later of the end of the last phase
-- that can use them and the time the key was saved, plus the retention period.
-- NULL while a phase that can use the key is open-ended or none has been set up.
create or replace function private.observer_key_purge_after(p_team uuid,p_saved timestamptz)
returns timestamptz language sql stable security definer set search_path=public,pg_temp as $$
  with competition as (select exists(select 1 from private.observer_site_mode where id and mode='competition') on_),
  phases as (
    select p.ends_at from public.phases p
      left join public.observer_phase_settings s on s.phase_id=p.id, competition c
     where p.is_active and (s.access_team_id is null or s.access_team_id=p_team)
       and (p.counts_for_final or p.slug='online' or p.slug like 'observer-acceptance-%'
            or p.slug='practice-projects' or (c.on_ and s.phase_id is not null)))
  select case when not exists(select 1 from phases) or exists(select 1 from phases where ends_at is null) then null
    else greatest((select max(ends_at) from phases),p_saved)
      +(select retention from private.observer_key_retention where id) end
$$;

-- True while anything of the team could still call its model.
create or replace function private.observer_team_model_busy(p_team uuid)
returns boolean language sql stable security definer set search_path=public,pg_temp as $$
  select exists(select 1 from public.observer_batches b where b.team_id=p_team and b.status in ('queued','running'))
    or exists(select 1 from public.observer_runs r join public.observer_batches b on b.id=r.batch_id
      where b.team_id=p_team and r.status in ('queued','starting','ready','running'))
    or exists(select 1 from public.observer_revisions v join public.observer_projects j on j.id=v.project_id
      where j.team_id=p_team and v.status in ('queued','preparing'))
    or exists(select 1 from private.observer_model_calls c join private.observer_providers p on p.id=c.provider_id
      where p.team_id=p_team and c.status='reserved')
$$;

-- Scheduled step. Returns the number of keys deleted.
create or replace function private.observer_auto_purge_provider_keys()
returns integer language plpgsql security definer set search_path=public,pg_temp as $$
declare cfg private.observer_key_retention; t record; v record; n integer:=0; teams jsonb:='[]';
begin
  select * into cfg from private.observer_key_retention where id for update skip locked;
  if not found or not cfg.enabled then return 0; end if;
  update private.observer_key_retention set last_run_at=clock_timestamp() where id;
  for t in select distinct p.team_id from private.observer_providers p
    where p.team_id is not null and p.encrypted_key<>'' order by p.team_id loop
    -- Serialises with saving, replacing and deleting a key for this team.
    perform 1 from public.teams where id=t.team_id for update;
    if private.observer_team_model_busy(t.team_id) then continue; end if;
    for v in select p.id,coalesce(m.saved_at,p.created_at) saved_at from private.observer_providers p
      left join private.observer_team_models m on m.provider_id=p.id
      where p.team_id=t.team_id and p.encrypted_key<>'' order by p.id loop
      if private.observer_key_purge_after(t.team_id,v.saved_at)<=clock_timestamp() then
        if private.observer_forget_provider_key(v.id) then
          n:=n+1;
          if not teams ? t.team_id::text then teams:=teams||to_jsonb(t.team_id::text); end if;
        end if;
      end if;
    end loop;
  end loop;
  if n>0 then
    perform private.audit('observer.provider_keys_auto_purged',jsonb_build_object('count',n,'teams',teams));
  end if;
  return n;
end $$;

revoke all on function private.observer_key_purge_after(uuid,timestamptz),private.observer_team_model_busy(uuid),
  private.observer_auto_purge_provider_keys() from public,anon,authenticated;

-- Hosted databases have pg_cron; plain Postgres (the migration tests) does not,
-- and there the function is installed and simply never called on a timer.
do $cron$
begin
  if exists(select 1 from pg_available_extensions where name='pg_cron') then
    create extension if not exists pg_cron;
    perform cron.unschedule(jobid) from cron.job where jobname='observer-purge-provider-keys';
    perform cron.schedule('observer-purge-provider-keys','17 * * * *','select private.observer_auto_purge_provider_keys()');
  end if;
end $cron$;

notify pgrst,'reload schema';

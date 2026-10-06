-- Organizer reset of today's evaluation quota (owner decision 2026-10-04). Nothing is deleted: a reset
-- is a recorded event, and a team's daily count only includes formal evaluations created after the
-- later of the start of the current UTC day and the phase's latest reset. Evaluation records, scores
-- and leaderboards are untouched. Additive: one new table, the single "evaluations used" definition
-- (private.observer_batches_used, used by admission, the website and the CLI quota) and two new RPCs.
--
-- Run (admin session, service role, or a direct SQL session):
--   select public.observer_reset_daily_quota(null, '2026-10-04 organizer reset');                -- all active contestant phases
--   select public.observer_reset_daily_quota(array['practice-projects','online'], 'note');        -- named phases

create table if not exists private.observer_quota_resets(
  id uuid primary key default gen_random_uuid(),
  phase_ids uuid[] not null check (cardinality(phase_ids) > 0),
  reset_at timestamptz not null default now(),
  note text not null default '',
  created_by uuid,          -- the admin's user id; null for the service role or a direct SQL session
  actor text not null       -- 'admin', 'service_role' or the database session user
);
create index if not exists observer_quota_resets_reset_at on private.observer_quota_resets(reset_at desc);
revoke all on private.observer_quota_resets from public, anon, authenticated;

-- Where today's count starts for a phase: the start of the UTC day, or a later organizer reset.
create or replace function private.observer_quota_window_start(p_phase uuid)
returns timestamptz language sql stable security definer set search_path=public,pg_temp as $$
  select greatest(date_trunc('day', now() at time zone 'UTC') at time zone 'UTC',
    (select max(r.reset_at) from private.observer_quota_resets r where p_phase = any(r.phase_ids) and r.reset_at <= now()))
$$;
revoke all on function private.observer_quota_window_start(uuid) from public, anon, authenticated;

-- The single definition of "evaluations used today" for admission and display.
create or replace function private.observer_batches_used(p_team uuid, p_phase uuid)
returns integer language sql stable security definer set search_path=public,pg_temp as $$
  select count(*)::integer from public.observer_batches where team_id=p_team and phase_id=p_phase and purpose='formal'
    and not quota_refunded and created_at >= private.observer_quota_window_start(p_phase)
$$;
revoke all on function private.observer_batches_used(uuid,uuid) from public, anon, authenticated;

-- Default phases: active, not sealed, open to every team, not ended (a phase that has not started yet is included).
create or replace function public.observer_reset_daily_quota(p_phases text[] default null, p_note text default '')
returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$
declare v_ids uuid[]; v_slugs text[]; v_missing text[]; v_id uuid; v_at timestamptz; v_actor text;
begin
  -- Admins (website session), the service role, or a direct SQL session (no request claims; not reachable through the API).
  v_actor := case when public.is_admin() then 'admin'
    when coalesce(auth.role(),'') = 'service_role' then 'service_role'
    when nullif(current_setting('request.jwt.claims', true), '') is null and auth.uid() is null then session_user::text end;
  if v_actor is null then raise exception 'admin_only'; end if;
  if p_phases is null or cardinality(p_phases) = 0 then
    select array_agg(p.id order by p.sort_order), array_agg(p.slug order by p.sort_order) into v_ids, v_slugs
      from public.phases p join public.observer_phase_settings s on s.phase_id=p.id
      where p.is_active and not s.sealed and s.access_team_id is null and (p.ends_at is null or p.ends_at > now());
  else
    select array_agg(p.id order by p.sort_order), array_agg(p.slug order by p.sort_order) into v_ids, v_slugs
      from public.phases p join public.observer_phase_settings s on s.phase_id=p.id where p.slug = any(p_phases);
    select array_agg(x) into v_missing from unnest(p_phases) x where x <> all(coalesce(v_slugs, '{}'));
    if v_missing is not null then raise exception 'unknown_phase: %', array_to_string(v_missing, ', '); end if;
  end if;
  if v_ids is null then raise exception 'no_phases'; end if;
  insert into private.observer_quota_resets(phase_ids, note, created_by, actor)
    values(v_ids, coalesce(p_note, ''), auth.uid(), v_actor) returning id, reset_at into v_id, v_at;
  perform private.audit('observer.reset_daily_quota', jsonb_build_object('reset_id', v_id, 'phases', v_slugs, 'note', coalesce(p_note, '')));
  return jsonb_build_object('reset_id', v_id, 'reset_at', v_at, 'phases', to_jsonb(v_slugs), 'note', coalesce(p_note, ''));
end $$;
revoke all on function public.observer_reset_daily_quota(text[], text) from public, anon;
grant execute on function public.observer_reset_daily_quota(text[], text) to authenticated, service_role;

-- The contestant notice: the latest reset in the last 24 hours that covers a phase the caller's team can see.
-- Null for visitors without a team, banned users, or when there is nothing to tell.
create or replace function public.observer_quota_reset_notice()
returns jsonb language sql stable security definer set search_path=public,pg_temp as $$
  select jsonb_build_object('id', r.id, 'reset_at', r.reset_at, 'phases', (
      select coalesce(jsonb_agg(jsonb_build_object('phase_id', p.id, 'slug', p.slug, 'name_en', p.name_en, 'name_zh', p.name_zh,
        'daily_batches', s.daily_batches) order by p.sort_order), '[]'::jsonb)
      from public.phases p join public.observer_phase_settings s on s.phase_id=p.id
      where p.id = any(r.phase_ids) and public.observer_phase_visible(p.id)))
  from private.observer_quota_resets r
  where r.reset_at > now() - interval '24 hours' and r.reset_at <= now()
    and exists(select 1 from public.profiles u where u.id=auth.uid() and u.team_id is not null and not u.is_banned)
    and exists(select 1 from unnest(r.phase_ids) x where public.observer_phase_visible(x))
  order by r.reset_at desc limit 1
$$;
revoke all on function public.observer_quota_reset_notice() from public, anon;
grant execute on function public.observer_quota_reset_notice() to authenticated, service_role;

notify pgrst,'reload schema';

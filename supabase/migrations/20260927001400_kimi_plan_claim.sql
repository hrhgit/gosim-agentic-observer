-- Kimi Coding Plan: one code per team that has run through the Playground (organizer decision 2026-09-27).
-- Codes live in public.redeem_codes with provider 'kimi' and are imported with admin_import_redeem_codes.
-- A team qualifies with at least one successful score: a scored CSV submission in the 'practice' phase or a
-- scored formal batch in 'practice-projects'. Only the team captain (teams.leader_id) claims; every member sees it.

create or replace function private.kimi_plan_qualified(p_team uuid)
returns boolean language sql stable security definer set search_path=public,pg_temp as $$
  select exists(select 1 from public.submissions s join public.phases p on p.id=s.phase_id
      where s.team_id=p_team and p.slug='practice' and s.status='scored' and not s.is_excluded)
    or exists(select 1 from public.observer_batches b join public.phases p on p.id=b.phase_id
      where b.team_id=p_team and p.slug='practice-projects' and b.status='scored' and b.purpose='formal')
$$;
revoke all on function private.kimi_plan_qualified(uuid) from public,anon,authenticated;

-- the caller's team: eligibility, captain flag, pool state and the team's code (visible to every member)
create or replace function public.kimi_plan_status()
returns jsonb language plpgsql stable security definer set search_path=public,pg_temp as $$
declare v_uid uuid:=auth.uid(); v_team public.teams; v_qualified boolean; v_row public.redeem_codes; v_by text;
  v_pool bigint:=(select count(*) from public.redeem_codes where provider='kimi' and status='available');
  v_imported boolean:=exists(select 1 from public.redeem_codes where provider='kimi');
begin
  select t.* into v_team from public.teams t join public.profiles p on p.team_id=t.id where p.id=v_uid;
  if not found then
    return jsonb_build_object('has_team',false,'imported',v_imported,'available',v_pool);
  end if;
  v_qualified:=private.kimi_plan_qualified(v_team.id);
  select * into v_row from public.redeem_codes where provider='kimi' and status='assigned' and team_id=v_team.id
    order by assigned_at, id limit 1;
  if found then select coalesce(nullif(nickname,''),name) into v_by from public.profiles where id=v_row.assigned_by; end if;
  return jsonb_build_object('has_team',true,'is_captain',v_team.leader_id=v_uid,
    'hidden',v_team.is_hidden,'qualified',v_qualified,
    'eligible',v_qualified and (not v_team.is_hidden or public.is_admin()),
    'imported',v_imported,'available',v_pool,
    'code',v_row.code,'note',v_row.note,'claimed_at',v_row.assigned_at,'claimed_by',v_by);
end $$;

-- captain-only, atomic, one per team, idempotent (a repeated claim returns the same code)
create or replace function public.claim_kimi_plan_code()
returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$
declare v_uid uuid:=auth.uid(); v_team public.teams; v_row public.redeem_codes;
begin
  perform private.assert_not_banned();
  if v_uid is null then raise exception 'not_authenticated'; end if;
  select t.* into v_team from public.teams t join public.profiles p on p.team_id=t.id where p.id=v_uid;
  if not found then raise exception 'need_team'; end if;
  if v_team.leader_id is distinct from v_uid then raise exception 'captain_only'; end if;
  -- serialize concurrent claims of the same team so a double click never takes two codes
  perform pg_advisory_xact_lock(hashtextextended('kimi_plan:'||v_team.id::text,0));
  select * into v_row from public.redeem_codes where provider='kimi' and status='assigned' and team_id=v_team.id
    order by assigned_at, id limit 1;
  if found then
    return jsonb_build_object('code',v_row.code,'note',v_row.note,'claimed_at',v_row.assigned_at,'already',true);
  end if;
  if (v_team.is_hidden and not public.is_admin()) or not private.kimi_plan_qualified(v_team.id) then
    raise exception 'not_eligible';
  end if;
  select * into v_row from public.redeem_codes where provider='kimi' and status='available'
    order by id limit 1 for update skip locked;
  if not found then raise exception 'no_codes_left'; end if;
  update public.redeem_codes set status='assigned',team_id=v_team.id,assigned_by=v_uid,assigned_at=now()
    where id=v_row.id returning * into v_row;
  perform private.audit('redeem.kimi_plan.claim',jsonb_build_object('team_id',v_team.id,'code_id',v_row.id));
  return jsonb_build_object('code',v_row.code,'note',v_row.note,'claimed_at',v_row.assigned_at,'already',false);
end $$;

-- admin: every qualified team (hidden ones flagged) plus any team already holding a Kimi code
create or replace function public.admin_kimi_plan_teams()
returns table (team_id uuid, team_name text, is_hidden boolean, captain_name text, captain_email text,
  qualified boolean, first_scored_at timestamptz, code text, claimed_at timestamptz, claimed_by_email text)
language plpgsql stable security definer set search_path=public,pg_temp as $$
#variable_conflict use_column
begin
  if not public.is_admin() then raise exception 'admin_only'; end if;
  return query
  with scores as (
    select s.team_id, min(coalesce(s.finished_at,s.created_at)) first_at from public.submissions s
      join public.phases p on p.id=s.phase_id
      where p.slug='practice' and s.status='scored' and not s.is_excluded group by s.team_id
    union all
    select b.team_id, min(b.finished_at) from public.observer_batches b join public.phases p on p.id=b.phase_id
      where p.slug='practice-projects' and b.status='scored' and b.purpose='formal' group by b.team_id
  ), firsts as (select x.team_id, min(x.first_at) first_at from scores x group by x.team_id),
  codes as (
    select distinct on (r.team_id) r.team_id, r.code, r.assigned_at, r.assigned_by from public.redeem_codes r
    where r.provider='kimi' and r.status='assigned' order by r.team_id, r.assigned_at, r.id
  )
  select t.id, t.name, t.is_hidden, coalesce(nullif(l.nickname,''),l.name), l.email,
    f.team_id is not null, f.first_at, c.code, c.assigned_at, a.email
  from public.teams t
  left join firsts f on f.team_id=t.id
  left join codes c on c.team_id=t.id
  left join public.profiles l on l.id=t.leader_id
  left join public.profiles a on a.id=c.assigned_by
  where f.team_id is not null or c.team_id is not null
  order by t.is_hidden, (c.team_id is not null), f.first_at nulls last, t.name;
end $$;

-- 'kimi' codes go through the captain claim above, never through the generic per-member claim
create or replace function public.claim_redeem_code(p_provider text)
returns jsonb language plpgsql security definer set search_path = public as $$
declare v_uid uuid := auth.uid(); v_team uuid; v_row public.redeem_codes;
begin
  perform private.assert_not_banned();
  if p_provider = 'kimi' then raise exception 'kimi_plan_claim_required'; end if;
  if exists(select 1 from private.observer_site_mode where mode='competition') then raise exception 'personal_api_required'; end if;
  select team_id into v_team from public.profiles where id = v_uid;
  if v_team is null then raise exception 'need_team'; end if;
  select * into v_row from public.redeem_codes where provider = p_provider and status = 'assigned' and team_id = v_team limit 1;
  if found then return jsonb_build_object('provider', v_row.provider, 'code', v_row.code, 'note', v_row.note, 'already', true); end if;
  select * into v_row from public.redeem_codes where provider = p_provider and status = 'available'
    order by id limit 1 for update skip locked;
  if not found then raise exception 'no_codes_left'; end if;
  update public.redeem_codes set status = 'assigned', team_id = v_team, assigned_by = v_uid, assigned_at = now() where id = v_row.id;
  perform private.audit('redeem.claim', jsonb_build_object('provider', p_provider, 'team_id', v_team));
  return jsonb_build_object('provider', v_row.provider, 'code', v_row.code, 'note', v_row.note, 'already', false);
end $$;

create or replace function public.redeem_providers()
returns table (provider text, available bigint, claimed_by_my_team boolean)
language sql stable security definer set search_path = public as $$
  select r.provider, count(*) filter (where r.status = 'available'),
         bool_or(r.status = 'assigned' and r.team_id = public.my_team_id())
  from public.redeem_codes r where r.provider <> 'kimi' group by r.provider order by r.provider;
$$;

revoke all on function public.kimi_plan_status(), public.claim_kimi_plan_code(), public.admin_kimi_plan_teams()
  from public, anon;
grant execute on function public.kimi_plan_status(), public.claim_kimi_plan_code(), public.admin_kimi_plan_teams()
  to authenticated;

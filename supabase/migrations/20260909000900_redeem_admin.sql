-- Admin tooling for sponsor credit codes (complements 20260909000800_redeem_codes.sql).
create or replace function public.admin_redeem_stats()
returns table (provider text, available bigint, assigned bigint, revoked bigint, total bigint, note text)
language sql stable security definer set search_path = public as $$
  select provider, count(*) filter (where status = 'available'), count(*) filter (where status = 'assigned'),
         count(*) filter (where status = 'revoked'), count(*),
         coalesce((select r2.note from public.redeem_codes r2 where r2.provider = r.provider and r2.note <> '' order by r2.created_at desc limit 1), '')
  from public.redeem_codes r where public.is_admin() group by provider order by provider;
$$;

create or replace function public.admin_redeem_codes(p_provider text default null, p_status text default null)
returns table (id bigint, provider text, code text, note text, status text, team_id uuid, team_name text, assigned_by_email text, assigned_at timestamptz, created_at timestamptz)
language sql stable security definer set search_path = public as $$
  select r.id, r.provider, r.code, r.note, r.status, r.team_id, t.name, p.email, r.assigned_at, r.created_at
  from public.redeem_codes r left join public.teams t on t.id = r.team_id left join public.profiles p on p.id = r.assigned_by
  where public.is_admin() and (p_provider is null or r.provider = p_provider) and (p_status is null or r.status = p_status)
  order by r.provider, r.status, r.id limit 2000;
$$;

-- assign a code of a provider to a team (p_replace revokes the team's current code first)
create or replace function public.admin_assign_redeem_code(p_team_id uuid, p_provider text, p_replace boolean default false)
returns text language plpgsql security definer set search_path = public as $$
declare v_code text;
begin
  if not public.is_admin() then raise exception 'admin_only'; end if;
  if p_replace then
    update public.redeem_codes set status = 'revoked' where team_id = p_team_id and provider = p_provider and status = 'assigned';
  elsif exists (select 1 from public.redeem_codes where team_id = p_team_id and provider = p_provider and status = 'assigned') then
    raise exception 'already_assigned';
  end if;
  update public.redeem_codes set status = 'assigned', team_id = p_team_id, assigned_by = auth.uid(), assigned_at = now()
  where id = (select id from public.redeem_codes where provider = p_provider and status = 'available' order by id limit 1 for update skip locked)
  returning code into v_code;
  if v_code is null then raise exception 'no_codes_left'; end if;
  perform private.audit('admin.redeem.assign', jsonb_build_object('team_id', p_team_id, 'provider', p_provider, 'replace', p_replace));
  return v_code;
end $$;

-- revoke an assigned code (it is not handed out again) or delete an unused one
create or replace function public.admin_revoke_redeem_code(p_id bigint, p_delete boolean default false)
returns void language plpgsql security definer set search_path = public as $$
begin
  if not public.is_admin() then raise exception 'admin_only'; end if;
  if p_delete then
    delete from public.redeem_codes where id = p_id and status = 'available';
  else
    update public.redeem_codes set status = 'revoked' where id = p_id;
  end if;
  perform private.audit('admin.redeem.revoke', jsonb_build_object('id', p_id, 'delete', p_delete));
end $$;

-- the team's own codes with provider notes (what the dashboard shows)
create or replace function public.my_redeem_codes()
returns table (provider text, code text, note text, assigned_at timestamptz)
language sql stable security definer set search_path = public as $$
  select r.provider, r.code, r.note, r.assigned_at from public.redeem_codes r
  where r.status = 'assigned' and r.team_id = public.my_team_id() order by r.provider;
$$;

-- optional explanatory text for the credits panel, editable by admins
insert into public.site_settings (key, value) values ('credits_note', '{"en": "", "zh": ""}'::jsonb) on conflict (key) do nothing;
drop policy if exists "settings public keys" on public.site_settings;
create policy "settings public keys" on public.site_settings for select to anon, authenticated
  using (key in ('registration_open', 'event', 'credits_note') or public.is_admin());

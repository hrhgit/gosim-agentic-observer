-- Sponsor API credit codes for local development: admins import codes, each team claims one per provider.
create table if not exists public.redeem_codes (
  id bigint generated always as identity primary key,
  provider text not null,                      -- e.g. 'openai', 'deepseek', 'kimi'
  code text not null,
  note text not null default '',               -- shown to the team next to the code (model names, quota)
  status text not null default 'available' check (status in ('available', 'assigned', 'revoked')),
  team_id uuid references public.teams(id) on delete set null,
  assigned_by uuid references public.profiles(id) on delete set null,
  assigned_at timestamptz,
  created_at timestamptz not null default now(),
  unique (provider, code)
);
create index if not exists redeem_codes_team_idx on public.redeem_codes(team_id);
alter table public.redeem_codes enable row level security;

-- teams see only the codes assigned to them; admins see all and manage rows
drop policy if exists "codes own team" on public.redeem_codes;
create policy "codes own team" on public.redeem_codes for select to authenticated
  using ((team_id is not null and team_id = public.my_team_id()) or public.is_admin());
drop policy if exists "codes admin write" on public.redeem_codes;
create policy "codes admin write" on public.redeem_codes for all to authenticated using (public.is_admin()) with check (public.is_admin());

-- providers with codes still available (counts only), visible to every participant
create or replace function public.redeem_providers()
returns table (provider text, available bigint, claimed_by_my_team boolean)
language sql stable security definer set search_path = public as $$
  select r.provider, count(*) filter (where r.status = 'available'),
         bool_or(r.status = 'assigned' and r.team_id = public.my_team_id())
  from public.redeem_codes r group by r.provider order by r.provider;
$$;

-- one code per team per provider, assigned atomically
create or replace function public.claim_redeem_code(p_provider text)
returns jsonb language plpgsql security definer set search_path = public as $$
declare v_uid uuid := auth.uid(); v_team uuid; v_row public.redeem_codes;
begin
  perform private.assert_not_banned();
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

-- admin bulk import: one code per line; returns the number inserted (duplicates ignored)
create or replace function public.admin_import_redeem_codes(p_provider text, p_codes text, p_note text default '')
returns integer language plpgsql security definer set search_path = public as $$
declare n integer;
begin
  if not public.is_admin() then raise exception 'admin_only'; end if;
  insert into public.redeem_codes (provider, code, note)
  select trim(p_provider), trim(c), coalesce(p_note, '') from unnest(string_to_array(p_codes, E'\n')) as c where trim(c) <> ''
  on conflict (provider, code) do nothing;
  get diagnostics n = row_count;
  perform private.audit('admin.redeem.import', jsonb_build_object('provider', p_provider, 'n', n));
  return n;
end $$;

do $$ begin
  if exists (select 1 from pg_publication where pubname = 'supabase_realtime') then
    alter publication supabase_realtime add table public.redeem_codes;
  end if;
exception when duplicate_object then null; end $$;

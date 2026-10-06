-- Popups are shown at most once per person (web/src/lib/popupRules.ts). Which popups a signed-in user has seen is
-- kept here as well as in the browser, so a popup seen on one device or browser stays seen on the others.
--
-- Additive only: one private table and two RPCs for the caller's own rows. Keys are short opaque strings
-- ("ann:<id>:<hash>", "kimi:<team>:<role>", "quota:<id>", "inbox:<id>"); at most 50 per call and the newest 500
-- per user are kept.

create table if not exists private.popup_seen(
  user_id uuid not null references auth.users(id) on delete cascade,
  key text not null check (char_length(key) between 1 and 200),
  seen_at timestamptz not null default now(),
  primary key (user_id, key)
);
revoke all on private.popup_seen from public, anon, authenticated;

create or replace function public.my_seen_popups()
returns text[] language sql stable security definer set search_path = public, pg_temp as $$
  select coalesce(array_agg(s.key order by s.seen_at desc), '{}'::text[])
  from (select key, seen_at from private.popup_seen where user_id = auth.uid() order by seen_at desc limit 500) s
$$;

create or replace function public.mark_popups_seen(p_keys text[])
returns void language plpgsql security definer set search_path = public, pg_temp as $$
begin
  if auth.uid() is null then raise exception 'not_authenticated'; end if;
  if coalesce(cardinality(p_keys), 0) > 50 then raise exception 'too_many_keys'; end if;
  insert into private.popup_seen(user_id, key)
  select auth.uid(), k from unnest(coalesce(p_keys, '{}'::text[])) k
  where char_length(k) between 1 and 200
  on conflict (user_id, key) do nothing;
  delete from private.popup_seen p where p.user_id = auth.uid() and p.key not in (
    select key from private.popup_seen where user_id = auth.uid() order by seen_at desc, key limit 500);
end $$;

revoke all on function public.my_seen_popups() from public, anon;
revoke all on function public.mark_popups_seen(text[]) from public, anon;
grant execute on function public.my_seen_popups() to authenticated;
grant execute on function public.mark_popups_seen(text[]) to authenticated;

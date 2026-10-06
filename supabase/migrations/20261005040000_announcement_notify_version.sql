-- Pinned announcements pop up again only when an organizer explicitly asks for it ("重新提醒所有人"), not on
-- every edit of the title or body. The popup's seen key becomes id + notify_version (web/src/lib/popupRules.ts).
--
-- Additive only: one column (default 1, existing rows keep 1), one admin RPC that bumps it, and a one-off copy
-- of the existing seen records: whoever saw any version of an announcement (keys "ann:<id>:<hash>") counts as
-- having seen version 1 ("ann:<id>:v1"), so nobody is re-popped by this change.

alter table public.announcements add column if not exists notify_version integer not null default 1
  check (notify_version >= 1);

create or replace function public.renotify_announcement(p_id bigint)
returns integer language plpgsql security definer set search_path = public, pg_temp as $$
declare v integer;
begin
  if not public.is_admin() then raise exception 'forbidden'; end if;
  update public.announcements set notify_version = notify_version + 1 where id = p_id returning notify_version into v;
  if v is null then raise exception 'not_found'; end if;
  return v;
end $$;
revoke all on function public.renotify_announcement(bigint) from public, anon;
grant execute on function public.renotify_announcement(bigint) to authenticated;

insert into private.popup_seen(user_id, key, seen_at)
select distinct on (s.user_id, k.key) s.user_id, k.key, s.seen_at
from private.popup_seen s
cross join lateral (select 'ann:' || (regexp_match(s.key, '^ann:([0-9]+):[0-9a-f]{8}$'))[1] || ':v1' as key) k
where s.key ~ '^ann:[0-9]+:[0-9a-f]{8}$'
order by s.user_id, k.key, s.seen_at
on conflict (user_id, key) do nothing;

-- Direct messages between friends (owner request 2026-10-05). Additive only: two new private tables and new RPCs;
-- nothing existing changes.
--
-- * Text only, 1..1000 characters, plain text (the site renders it as text and links only http(s) URLs).
-- * Only friends can send (private.friendships); a block on either side stops delivery both ways (blocking already
--   ends the friendship, and the block itself is checked too). The sender is never told which one it was.
-- * Each sender may send 10 messages per rolling minute and 200 per rolling 24 hours.
-- * Privacy: the tables are private (no direct access for anon/authenticated); every read goes through an RPC that
--   returns only messages the caller sent or received.
-- * The recipient can report a message; organizers see open reports and can delete the message (its text is cleared,
--   both sides then see "removed by the organizers") or dismiss the report.
-- * No Realtime: the site polls (every 5 s while the friends panel is open, otherwise the existing 60 s badge poll),
--   so the database sees one light RPC per open panel.

create table if not exists private.direct_messages(
  id bigint generated always as identity primary key,
  sender_id uuid not null references public.profiles(id) on delete cascade,
  recipient_id uuid not null references public.profiles(id) on delete cascade,
  body text not null,
  created_at timestamptz not null default clock_timestamp(),
  read_at timestamptz,
  deleted_at timestamptz,                       -- removed by an organizer after a report: body is cleared
  deleted_by uuid references public.profiles(id) on delete set null,
  check (sender_id <> recipient_id),
  check (char_length(body) <= 1000 and (deleted_at is not null or char_length(btrim(body)) > 0))
);
create index if not exists direct_messages_pair on private.direct_messages(least(sender_id, recipient_id), greatest(sender_id, recipient_id), id desc);
create index if not exists direct_messages_sender on private.direct_messages(sender_id, created_at desc);
create index if not exists direct_messages_recipient on private.direct_messages(recipient_id, id desc);
create index if not exists direct_messages_unread on private.direct_messages(recipient_id, sender_id) where read_at is null and deleted_at is null;

create table if not exists private.direct_message_reports(
  id bigint generated always as identity primary key,
  message_id bigint not null references private.direct_messages(id) on delete cascade,
  reporter_id uuid not null references public.profiles(id) on delete cascade,
  reason text not null default '',
  status text not null default 'open' check (status in ('open', 'dismissed', 'removed')),
  created_at timestamptz not null default clock_timestamp(),
  resolved_at timestamptz,
  resolved_by uuid references public.profiles(id) on delete set null,
  unique (message_id, reporter_id)
);
create index if not exists direct_message_reports_open on private.direct_message_reports(created_at) where status = 'open';

revoke all on private.direct_messages, private.direct_message_reports from public, anon, authenticated;
-- Defence in depth (the tables are not exposed and nobody but the owner has grants): participants only.
alter table private.direct_messages enable row level security;
drop policy if exists direct_messages_participants on private.direct_messages;
create policy direct_messages_participants on private.direct_messages for select to authenticated
  using (auth.uid() in (sender_id, recipient_id));
alter table private.direct_message_reports enable row level security;

create or replace function private.dm_limits() returns jsonb language sql immutable as $$
  select jsonb_build_object('max_length', 1000, 'per_minute', 10, 'per_day', 200)
$$;

-- True when the two can message each other right now: friends, and neither has blocked the other.
create or replace function private.dm_allowed(p_one uuid, p_two uuid) returns boolean
language sql stable security definer set search_path = public, pg_temp as $$
  select exists (select 1 from private.friendships where user_a = least(p_one, p_two) and user_b = greatest(p_one, p_two))
     and not exists (select 1 from private.user_blocks where (blocker_id = p_one and blocked_id = p_two) or (blocker_id = p_two and blocked_id = p_one))
     and not exists (select 1 from public.profiles where id in (p_one, p_two) and is_banned)
$$;

revoke all on function private.dm_limits(), private.dm_allowed(uuid, uuid) from public, anon, authenticated;

-- Send a message. Returns {"id", "created_at"} or {"error": code}: not_friends, empty, too_long, rate_minute, rate_day.
create or replace function public.dm_send(p_to uuid, p_body text) returns jsonb
language plpgsql security definer set search_path = public, pg_temp as $$
declare v_me uuid := auth.uid(); v_body text := btrim(coalesce(p_body, ''), E' \t\r\n'); v_lim jsonb := private.dm_limits(); v_row private.direct_messages;
begin
  perform private.assert_not_banned();
  if v_body = '' then return jsonb_build_object('error', 'empty'); end if;
  if char_length(v_body) > (v_lim->>'max_length')::int then return jsonb_build_object('error', 'too_long'); end if;
  if p_to is null or p_to = v_me or not private.dm_allowed(v_me, p_to) then return jsonb_build_object('error', 'not_friends'); end if;
  perform pg_advisory_xact_lock(hashtext('dm_send:' || v_me::text));
  if (select count(*) from private.direct_messages where sender_id = v_me and created_at > clock_timestamp() - interval '1 minute')
       >= (v_lim->>'per_minute')::int then
    return jsonb_build_object('error', 'rate_minute');
  end if;
  if (select count(*) from private.direct_messages where sender_id = v_me and created_at > clock_timestamp() - interval '24 hours')
       >= (v_lim->>'per_day')::int then
    return jsonb_build_object('error', 'rate_day');
  end if;
  insert into private.direct_messages(sender_id, recipient_id, body) values (v_me, p_to, v_body) returning * into v_row;
  return jsonb_build_object('id', v_row.id, 'created_at', v_row.created_at);
end $$;

-- Unread messages for the caller: not deleted, and not from someone they blocked or who is banned.
create or replace function private.dm_unread_for(p_me uuid) returns table(sender_id uuid, n integer)
language sql stable security definer set search_path = public, pg_temp as $$
  select m.sender_id, count(*)::integer from private.direct_messages m join public.profiles s on s.id = m.sender_id
  where m.recipient_id = p_me and m.read_at is null and m.deleted_at is null and not s.is_banned
    and not exists (select 1 from private.user_blocks b where b.blocker_id = p_me and b.blocked_id = m.sender_id)
  group by m.sender_id
$$;
revoke all on function private.dm_unread_for(uuid) from public, anon, authenticated;

-- The floating button's badge (one call per poll): friend requests waiting for an answer + unread messages.
create or replace function public.social_counts() returns jsonb
language plpgsql stable security definer set search_path = public, pg_temp as $$
begin
  if auth.uid() is null then return jsonb_build_object('requests', 0, 'unread', 0); end if;
  return jsonb_build_object('requests', public.friend_request_count(),
    'unread', coalesce((select sum(n) from private.dm_unread_for(auth.uid())), 0));
end $$;

-- The panel's home view: incoming friend requests, then everyone the caller can talk to or has talked to
-- (friends, and past partners who are not blocked or banned) with their last message and unread count.
create or replace function public.dm_overview() returns jsonb
language plpgsql stable security definer set search_path = public, pg_temp as $$
declare v_me uuid := auth.uid();
begin
  perform private.assert_not_banned();
  return jsonb_build_object(
    'limits', private.dm_limits(),
    'requests', public.friend_request_count(),
    'incoming', coalesce((select jsonb_agg(jsonb_build_object(
        'id', r.id, 'user_id', p.id, 'name', coalesce(nullif(p.nickname, ''), p.name), 'avatar_url', p.avatar_url, 'created_at', r.created_at)
        order by r.created_at desc)
      from private.friend_requests r join public.profiles p on p.id = r.sender_id
      where r.recipient_id = v_me and r.status = 'pending' and not r.muted and not p.is_banned), '[]'::jsonb),
    'people', coalesce((select jsonb_agg(jsonb_build_object(
        'user_id', p.id, 'uid', u.uid, 'name', coalesce(nullif(p.nickname, ''), p.name), 'avatar_url', p.avatar_url,
        'team_name', case when t.is_hidden then null else t.name end, 'in_team', p.team_id is not null,
        'is_friend', x.is_friend, 'can_send', private.dm_allowed(v_me, p.id),
        'last_at', lm.created_at, 'last_from_me', lm.sender_id = v_me,
        'last_body', case when lm.deleted_at is not null then null else left(lm.body, 80) end, 'last_deleted', lm.deleted_at is not null,
        'unread', coalesce(un.n, 0))
        order by coalesce(un.n, 0) > 0 desc, lm.created_at desc nulls last, lower(coalesce(nullif(p.nickname, ''), p.name)), p.id)
      from (select other, bool_or(is_friend) is_friend from (
              select case when f.user_a = v_me then f.user_b else f.user_a end other, true is_friend
                from private.friendships f where v_me in (f.user_a, f.user_b)
              union all
              select distinct case when m.sender_id = v_me then m.recipient_id else m.sender_id end, false
                from private.direct_messages m where m.sender_id = v_me or m.recipient_id = v_me) s group by other) x
      join public.profiles p on p.id = x.other
      left join private.profile_uids u on u.profile_id = p.id
      left join public.teams t on t.id = p.team_id
      left join lateral (select m.* from private.direct_messages m
          where least(m.sender_id, m.recipient_id) = least(v_me, p.id) and greatest(m.sender_id, m.recipient_id) = greatest(v_me, p.id)
          order by m.id desc limit 1) lm on true
      left join private.dm_unread_for(v_me) un on un.sender_id = p.id
      where not p.is_banned
        and not exists (select 1 from private.user_blocks b where b.blocker_id = v_me and b.blocked_id = p.id)), '[]'::jsonb));
end $$;

-- One conversation: the latest 200 messages (or only those after p_after, for polling), oldest first; the
-- caller's unread messages in it become read. Hidden when the caller has blocked the other person.
create or replace function public.dm_thread(p_with uuid, p_after bigint default 0) returns jsonb
language plpgsql security definer set search_path = public, pg_temp as $$
declare v_me uuid := auth.uid();
begin
  perform private.assert_not_banned();
  if p_with is null or p_with = v_me or exists (select 1 from private.user_blocks where blocker_id = v_me and blocked_id = p_with) then
    raise exception 'user_not_found';
  end if;
  update private.direct_messages set read_at = clock_timestamp()
    where recipient_id = v_me and sender_id = p_with and read_at is null;
  return jsonb_build_object(
    'can_send', private.dm_allowed(v_me, p_with),
    'messages', coalesce((select jsonb_agg(jsonb_build_object('id', m.id, 'from_me', m.sender_id = v_me,
        'body', case when m.deleted_at is null then m.body end, 'deleted', m.deleted_at is not null, 'created_at', m.created_at,
        'read', m.read_at is not null) order by m.id)
      from (select * from private.direct_messages m
            where least(m.sender_id, m.recipient_id) = least(v_me, p_with) and greatest(m.sender_id, m.recipient_id) = greatest(v_me, p_with)
              and m.id > coalesce(p_after, 0)
            order by m.id desc limit 200) m), '[]'::jsonb));
end $$;

-- The recipient reports a message to the organizers (once per message; the sender is not told).
create or replace function public.dm_report(p_message bigint, p_reason text default '') returns void
language plpgsql security definer set search_path = public, pg_temp as $$
begin
  perform private.assert_not_banned();
  if not exists (select 1 from private.direct_messages where id = p_message and recipient_id = auth.uid() and deleted_at is null) then
    raise exception 'message_not_found';
  end if;
  insert into private.direct_message_reports(message_id, reporter_id, reason)
    values (p_message, auth.uid(), left(coalesce(p_reason, ''), 300)) on conflict do nothing;
end $$;

-- Organizers: open reports, one row per message.
create or replace function public.admin_dm_reports() returns jsonb
language plpgsql stable security definer set search_path = public, pg_temp as $$
begin
  if not public.is_admin() then raise exception 'admin_only'; end if;
  return coalesce((select jsonb_agg(jsonb_build_object('message_id', m.id, 'body', m.body, 'sent_at', m.created_at,
      'sender_id', s.id, 'sender_name', coalesce(nullif(s.nickname, ''), s.name), 'sender_email', s.email,
      'recipient_id', r.id, 'recipient_name', coalesce(nullif(r.nickname, ''), r.name), 'recipient_email', r.email,
      'reports', x.n, 'reasons', x.reasons, 'first_at', x.first_at) order by x.first_at)
    from (select message_id, count(*) n, array_agg(reason order by created_at) filter (where reason <> '') reasons, min(created_at) first_at
          from private.direct_message_reports where status = 'open' group by message_id) x
    join private.direct_messages m on m.id = x.message_id
    join public.profiles s on s.id = m.sender_id join public.profiles r on r.id = m.recipient_id), '[]'::jsonb);
end $$;

create or replace function public.admin_delete_dm(p_message bigint) returns void
language plpgsql security definer set search_path = public, pg_temp as $$
begin
  if not public.is_admin() then raise exception 'admin_only'; end if;
  update private.direct_messages set body = '', deleted_at = clock_timestamp(), deleted_by = auth.uid()
    where id = p_message and deleted_at is null;
  update private.direct_message_reports set status = 'removed', resolved_at = clock_timestamp(), resolved_by = auth.uid()
    where message_id = p_message and status = 'open';
  insert into public.audit_log(user_id, action, detail) values (auth.uid(), 'admin.dm_delete', jsonb_build_object('message_id', p_message));
end $$;

create or replace function public.admin_dismiss_dm_reports(p_message bigint) returns void
language plpgsql security definer set search_path = public, pg_temp as $$
begin
  if not public.is_admin() then raise exception 'admin_only'; end if;
  update private.direct_message_reports set status = 'dismissed', resolved_at = clock_timestamp(), resolved_by = auth.uid()
    where message_id = p_message and status = 'open';
end $$;

revoke all on function public.dm_send(uuid, text), public.social_counts(), public.dm_overview(), public.dm_thread(uuid, bigint),
  public.dm_report(bigint, text), public.admin_dm_reports(), public.admin_delete_dm(bigint), public.admin_dismiss_dm_reports(bigint)
  from public, anon, authenticated;
grant execute on function public.dm_send(uuid, text), public.social_counts(), public.dm_overview(), public.dm_thread(uuid, bigint),
  public.dm_report(bigint, text), public.admin_dm_reports(), public.admin_delete_dm(bigint), public.admin_dismiss_dm_reports(bigint)
  to authenticated;
notify pgrst, 'reload schema';

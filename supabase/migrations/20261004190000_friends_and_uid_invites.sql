-- Friends and team invitations by UID (owner decision 2026-10-04). Additive only: four new private tables
-- and new RPCs; nothing existing changes. A team invitation by UID is an ordinary row in
-- private.team_invitations, so the recipient accepts, declines and the sender withdraws it exactly as before.
--
-- Privacy: all tables are private (no direct access for anon/authenticated); every read goes through an RPC
-- that returns only the caller's own requests and friends. A UID is only ever resolved inside a send action,
-- every such action counts towards a daily limit (successful or not), and the sender of a pending friend
-- request sees only the UID they typed, never the recipient's name, until the request is accepted.

create table if not exists private.friend_requests(
  id uuid primary key default gen_random_uuid(),
  sender_id uuid not null references public.profiles(id) on delete cascade,
  recipient_id uuid not null references public.profiles(id) on delete cascade,
  recipient_uid bigint not null,                -- what the sender typed: all they see until it is accepted
  status text not null default 'pending' check (status in ('pending','accepted','declined','cancelled')),
  muted boolean not null default false,         -- the recipient has blocked the sender: never shown to them
  created_at timestamptz not null default clock_timestamp(),
  updated_at timestamptz not null default clock_timestamp(),
  check (sender_id <> recipient_id)
);
create unique index if not exists friend_request_pending on private.friend_requests(sender_id, recipient_id) where status = 'pending';
create index if not exists friend_request_recipient on private.friend_requests(recipient_id) where status = 'pending';

create table if not exists private.friendships(
  user_a uuid not null references public.profiles(id) on delete cascade,
  user_b uuid not null references public.profiles(id) on delete cascade,
  created_at timestamptz not null default clock_timestamp(),
  primary key (user_a, user_b),
  check (user_a < user_b)
);
create index if not exists friendships_user_b on private.friendships(user_b);

create table if not exists private.user_blocks(
  blocker_id uuid not null references public.profiles(id) on delete cascade,
  blocked_id uuid not null references public.profiles(id) on delete cascade,
  created_at timestamptz not null default clock_timestamp(),
  primary key (blocker_id, blocked_id),
  check (blocker_id <> blocked_id)
);

-- Every by-UID action (friend request or team invitation), counted whether or not the UID existed.
create table if not exists private.uid_actions(
  id bigint generated always as identity primary key,
  actor_id uuid not null references public.profiles(id) on delete cascade,
  kind text not null check (kind in ('friend','invite')),
  created_at timestamptz not null default clock_timestamp()
);
create index if not exists uid_actions_actor on private.uid_actions(actor_id, kind, created_at desc);

revoke all on private.friend_requests, private.friendships, private.user_blocks, private.uid_actions from public, anon, authenticated;

-- At most this many by-UID actions of one kind per rolling 24 hours.
create or replace function private.uid_daily_limit() returns integer language sql immutable as $$ select 20 $$;

-- Records one by-UID action for the caller; false when the daily limit is already used up.
create or replace function private.take_uid_action(p_kind text) returns boolean
language plpgsql security definer set search_path = public, pg_temp as $$
begin
  perform pg_advisory_xact_lock(hashtext('uid_action:' || auth.uid()::text));
  if (select count(*) from private.uid_actions where actor_id = auth.uid() and kind = p_kind
        and created_at > clock_timestamp() - interval '24 hours') >= private.uid_daily_limit() then
    return false;
  end if;
  insert into private.uid_actions(actor_id, kind) values (auth.uid(), p_kind);
  return true;
end $$;

-- The profile behind a UID, unless banned.
create or replace function private.profile_by_uid(p_uid bigint) returns uuid
language sql stable security definer set search_path = public, pg_temp as $$
  select u.profile_id from private.profile_uids u join public.profiles p on p.id = u.profile_id
  where u.uid = p_uid and not p.is_banned
$$;

create or replace function private.make_friends(p_one uuid, p_two uuid) returns void
language plpgsql security definer set search_path = public, pg_temp as $$
begin
  insert into private.friendships(user_a, user_b) values (least(p_one, p_two), greatest(p_one, p_two)) on conflict do nothing;
  -- Any other pending request between the two is settled by the friendship.
  update private.friend_requests set status = 'accepted', updated_at = clock_timestamp()
    where status = 'pending' and ((sender_id = p_one and recipient_id = p_two) or (sender_id = p_two and recipient_id = p_one));
end $$;

revoke all on function private.uid_daily_limit(), private.take_uid_action(text), private.profile_by_uid(bigint),
  private.make_friends(uuid, uuid) from public, anon, authenticated;

-- Send a friend request to a UID. Returns {"status": "sent"|"accepted"|"already_friends"} or {"error": code}.
-- An unknown, banned or otherwise unavailable UID all give the same 'uid_unavailable'.
create or replace function public.send_friend_request(p_uid bigint) returns jsonb
language plpgsql security definer set search_path = public, pg_temp as $$
declare v_me uuid := auth.uid(); v_to uuid; v_id uuid;
begin
  perform private.assert_not_banned();
  if p_uid is null or p_uid = (select uid from private.profile_uids where profile_id = v_me) then
    return jsonb_build_object('error', 'self');
  end if;
  if not private.take_uid_action('friend') then return jsonb_build_object('error', 'daily_limit'); end if;
  v_to := private.profile_by_uid(p_uid);
  if v_to is null or v_to = v_me then return jsonb_build_object('error', 'uid_unavailable'); end if;
  if exists (select 1 from private.user_blocks where blocker_id = v_me and blocked_id = v_to) then
    return jsonb_build_object('error', 'blocked_by_you');
  end if;
  if exists (select 1 from private.friendships where user_a = least(v_me, v_to) and user_b = greatest(v_me, v_to)) then
    return jsonb_build_object('status', 'already_friends');
  end if;
  -- They already asked: this is a yes.
  if exists (select 1 from private.friend_requests where sender_id = v_to and recipient_id = v_me and status = 'pending' and not muted) then
    perform private.make_friends(v_me, v_to);
    perform private.audit('friend.accept', jsonb_build_object('user_id', v_to));
    return jsonb_build_object('status', 'accepted');
  end if;
  insert into private.friend_requests(sender_id, recipient_id, recipient_uid, muted)
    values (v_me, v_to, p_uid, exists (select 1 from private.user_blocks where blocker_id = v_to and blocked_id = v_me))
    on conflict (sender_id, recipient_id) where status = 'pending' do nothing returning id into v_id;
  if v_id is null then
    select id into v_id from private.friend_requests where sender_id = v_me and recipient_id = v_to and status = 'pending';
  end if;
  perform private.audit('friend.request', jsonb_build_object('request_id', v_id));
  return jsonb_build_object('status', 'sent', 'request_id', v_id);
end $$;

create or replace function public.respond_friend_request(p_request uuid, p_accept boolean) returns text
language plpgsql security definer set search_path = public, pg_temp as $$
declare v private.friend_requests; v_status text := case when p_accept then 'accepted' else 'declined' end;
begin
  perform private.assert_not_banned();
  select * into v from private.friend_requests where id = p_request and recipient_id = auth.uid() and not muted for update;
  if not found then raise exception 'request_not_found'; end if;
  if v.status = v_status then return v_status; end if;
  if v.status <> 'pending' then raise exception 'request_finished'; end if;
  if p_accept then
    if exists (select 1 from public.profiles where id = v.sender_id and is_banned) then raise exception 'request_not_found'; end if;
    perform private.make_friends(v.sender_id, v.recipient_id);
  else
    update private.friend_requests set status = 'declined', updated_at = clock_timestamp() where id = p_request;
  end if;
  perform private.audit('friend.respond', jsonb_build_object('request_id', p_request, 'accept', p_accept));
  return v_status;
end $$;

create or replace function public.cancel_friend_request(p_request uuid) returns void
language plpgsql security definer set search_path = public, pg_temp as $$
begin
  perform private.assert_not_banned();
  update private.friend_requests set status = 'cancelled', updated_at = clock_timestamp()
    where id = p_request and sender_id = auth.uid() and status = 'pending';
  if not found then raise exception 'request_finished'; end if;
end $$;

create or replace function public.remove_friend(p_user uuid) returns void
language plpgsql security definer set search_path = public, pg_temp as $$
begin
  perform private.assert_not_banned();
  delete from private.friendships where user_a = least(auth.uid(), p_user) and user_b = greatest(auth.uid(), p_user);
  if not found then raise exception 'not_friends'; end if;
  perform private.audit('friend.remove', jsonb_build_object('user_id', p_user));
end $$;

-- Block someone (from a request or the friend list): ends the friendship, their pending and future requests
-- are never shown (they are not told), and the caller's own pending requests to them are withdrawn.
create or replace function public.block_user(p_user uuid) returns void
language plpgsql security definer set search_path = public, pg_temp as $$
declare v_me uuid := auth.uid();
begin
  perform private.assert_not_banned();
  if p_user is null or p_user = v_me or not exists (select 1 from public.profiles where id = p_user) then raise exception 'user_not_found'; end if;
  insert into private.user_blocks(blocker_id, blocked_id) values (v_me, p_user) on conflict do nothing;
  delete from private.friendships where user_a = least(v_me, p_user) and user_b = greatest(v_me, p_user);
  update private.friend_requests set muted = true, updated_at = clock_timestamp()
    where sender_id = p_user and recipient_id = v_me and status = 'pending';
  update private.friend_requests set status = 'cancelled', updated_at = clock_timestamp()
    where sender_id = v_me and recipient_id = p_user and status = 'pending';
  perform private.audit('friend.block', jsonb_build_object('user_id', p_user));
end $$;

create or replace function public.unblock_user(p_user uuid) returns void
language plpgsql security definer set search_path = public, pg_temp as $$
begin
  perform private.assert_not_banned();
  delete from private.user_blocks where blocker_id = auth.uid() and blocked_id = p_user;
end $$;

-- The caller's own UID, friends, pending requests (received and sent) and blocked people.
create or replace function public.my_friends() returns jsonb
language plpgsql stable security definer set search_path = public, pg_temp as $$
declare v_me uuid := auth.uid();
begin
  perform private.assert_not_banned();
  return jsonb_build_object(
    'uid', (select uid from private.profile_uids where profile_id = v_me),
    'daily_limit', private.uid_daily_limit(),
    'friends', coalesce((select jsonb_agg(jsonb_build_object(
        'user_id', p.id, 'uid', u.uid, 'name', coalesce(nullif(p.nickname, ''), p.name), 'avatar_url', p.avatar_url,
        'team_id', case when t.is_hidden then null else t.id end, 'team_name', case when t.is_hidden then null else t.name end,
        'in_team', p.team_id is not null, 'since', f.created_at)
        order by lower(coalesce(nullif(p.nickname, ''), p.name)), p.id)
      from private.friendships f
      join public.profiles p on p.id = case when f.user_a = v_me then f.user_b else f.user_a end
      left join private.profile_uids u on u.profile_id = p.id
      left join public.teams t on t.id = p.team_id
      where v_me in (f.user_a, f.user_b) and not p.is_banned), '[]'::jsonb),
    'incoming', coalesce((select jsonb_agg(jsonb_build_object(
        'id', r.id, 'user_id', p.id, 'name', coalesce(nullif(p.nickname, ''), p.name), 'avatar_url', p.avatar_url, 'created_at', r.created_at)
        order by r.created_at desc)
      from private.friend_requests r join public.profiles p on p.id = r.sender_id
      where r.recipient_id = v_me and r.status = 'pending' and not r.muted and not p.is_banned), '[]'::jsonb),
    'outgoing', coalesce((select jsonb_agg(jsonb_build_object('id', r.id, 'uid', r.recipient_uid, 'created_at', r.created_at)
        order by r.created_at desc)
      from private.friend_requests r where r.sender_id = v_me and r.status = 'pending'), '[]'::jsonb),
    'blocked', coalesce((select jsonb_agg(jsonb_build_object('user_id', p.id, 'name', coalesce(nullif(p.nickname, ''), p.name))
        order by b.created_at desc)
      from private.user_blocks b join public.profiles p on p.id = b.blocked_id where b.blocker_id = v_me), '[]'::jsonb));
end $$;

-- A captain invites the person behind a UID to the team: an ordinary team invitation (same table, same accept,
-- decline and withdraw, same size, lock and one-team rules). Returns {"status": "sent", "invitation_id"} or
-- {"error": code}: not_in_team, leader_only, locked, full, self, daily_limit, uid_not_found, recipient_in_team.
create or replace function public.send_team_invite_by_uid(p_uid bigint) returns jsonb
language plpgsql security definer set search_path = public, pg_temp as $$
declare v_me uuid := auth.uid(); v_team public.teams; v_to uuid; v_to_team uuid; v_id uuid;
begin
  perform private.assert_not_banned();
  select t.* into v_team from public.teams t where t.id = public.my_team_id() for update;
  if not found then return jsonb_build_object('error', 'not_in_team'); end if;
  if v_team.leader_id <> v_me then return jsonb_build_object('error', 'leader_only'); end if;
  if p_uid is null or p_uid = (select uid from private.profile_uids where profile_id = v_me) then
    return jsonb_build_object('error', 'self');
  end if;
  if v_team.is_locked then return jsonb_build_object('error', 'locked'); end if;
  if (select count(*) from public.profiles where team_id = v_team.id) >= v_team.max_size then return jsonb_build_object('error', 'full'); end if;
  if not private.take_uid_action('invite') then return jsonb_build_object('error', 'daily_limit'); end if;
  v_to := private.profile_by_uid(p_uid);
  if v_to is null then return jsonb_build_object('error', 'uid_not_found'); end if;
  select team_id into v_to_team from public.profiles where id = v_to;
  if v_to_team is not null then return jsonb_build_object('error', 'recipient_in_team'); end if;
  -- Someone who blocked the captain is not bothered; the captain is not told.
  if exists (select 1 from private.user_blocks where blocker_id = v_to and blocked_id = v_me) then
    return jsonb_build_object('status', 'sent');
  end if;
  insert into private.team_invitations(team_id, team_name, sender_id, recipient_id)
    values (v_team.id, v_team.name, v_me, v_to)
    on conflict (team_id, recipient_id) where status = 'pending' and kind = 'invite' do nothing returning id into v_id;
  if v_id is null then
    select id into v_id from private.team_invitations
      where team_id = v_team.id and recipient_id = v_to and status = 'pending' and kind = 'invite';
  end if;
  perform private.audit('team.invite_by_uid', jsonb_build_object('invitation_id', v_id));
  return jsonb_build_object('status', 'sent', 'invitation_id', v_id);
end $$;

revoke all on function public.send_friend_request(bigint), public.respond_friend_request(uuid, boolean),
  public.cancel_friend_request(uuid), public.remove_friend(uuid), public.block_user(uuid), public.unblock_user(uuid),
  public.my_friends(), public.send_team_invite_by_uid(bigint) from public, anon, authenticated;
grant execute on function public.send_friend_request(bigint), public.respond_friend_request(uuid, boolean),
  public.cancel_friend_request(uuid), public.remove_friend(uuid), public.block_user(uuid), public.unblock_user(uuid),
  public.my_friends(), public.send_team_invite_by_uid(bigint) to authenticated;

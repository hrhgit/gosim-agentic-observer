-- Incoming friend requests join the header bell / Dashboard badges (owner follow-up 2026-10-04). Additive: one
-- read-only RPC. Counts requests still waiting for the caller's answer (not "unread": the badge stays until the
-- request is accepted, declined or the sender is blocked); blocked senders' and banned senders' requests never count.
create or replace function public.friend_request_count() returns integer
language sql stable security definer set search_path = public, pg_temp as $$
  select count(*)::integer from private.friend_requests r join public.profiles s on s.id = r.sender_id
  where r.recipient_id = auth.uid() and r.status = 'pending' and not r.muted and not s.is_banned
$$;
revoke all on function public.friend_request_count() from public, anon, authenticated;
grant execute on function public.friend_request_count() to authenticated;
notify pgrst, 'reload schema';

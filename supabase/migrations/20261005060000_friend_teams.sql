-- Friends' teams for 「邀请入队」 / 「申请加入」 in the friend lists (owner follow-up 2026-10-05). Additive: one read-only RPC.
--
-- One call returns, for each of the caller's friends who is in a visible team: the team, whether it can take a join
-- request right now (not locked, not full; the same rules request_team_join enforces) and whether the caller already
-- has a pending request to it. Hidden (test) teams are left out, like everywhere else. Board ranks come from the
-- existing board RPCs on the client (one cached call for all friends).
create or replace function public.friend_teams() returns jsonb
language plpgsql stable security definer set search_path = public, pg_temp as $$
declare v_me uuid := auth.uid();
begin
  perform private.assert_not_banned();
  return coalesce((select jsonb_agg(jsonb_build_object(
      'user_id', p.id, 'team_id', t.id, 'team_name', t.name, 'is_locked', t.is_locked, 'max_size', t.max_size,
      'member_count', (select count(*) from public.profiles m where m.team_id = t.id),
      'requested', exists (select 1 from private.team_invitations i where i.team_id = t.id and i.sender_id = v_me
                           and i.kind = 'request' and i.status = 'pending')))
    from private.friendships f
    join public.profiles p on p.id = case when f.user_a = v_me then f.user_b else f.user_a end
    join public.teams t on t.id = p.team_id
    where v_me in (f.user_a, f.user_b) and not p.is_banned and not t.is_hidden), '[]'::jsonb);
end $$;

revoke all on function public.friend_teams() from public, anon, authenticated;
grant execute on function public.friend_teams() to authenticated;
notify pgrst, 'reload schema';

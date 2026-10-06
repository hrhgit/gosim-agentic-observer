-- 「按 UID 找人」 (owner request 2026-10-05). Additive: one RPC, and 'lookup' joins the by-UID action kinds.
--
-- A signed-in participant enters a UID and sees that person's card: the same wall-type fields as person_card
-- (role, affiliation, city and contact only when the person is on the Teammates wall; never email or real name),
-- and the WeChat QR only when its owner's visibility allows this viewer. Anti-enumeration: every lookup counts
-- towards the daily limit (20 per kind per rolling 24 h, as for friend requests and invitations) whether or not
-- the UID exists, and an unknown, banned or otherwise unavailable UID all answer the same {"error":"not_found"}
-- after the same work.

alter table private.uid_actions drop constraint if exists uid_actions_kind_check;
alter table private.uid_actions add constraint uid_actions_kind_check check (kind in ('friend', 'invite', 'lookup'));

create or replace function public.find_by_uid(p_uid bigint) returns jsonb
language plpgsql security definer set search_path = public, pg_temp as $$
declare v_me uuid := auth.uid(); v_id uuid; v record; v_friend boolean;
begin
  perform private.assert_not_banned();
  if p_uid is null or p_uid < 100000001 or p_uid > 999999999 then return jsonb_build_object('error', 'not_found'); end if;
  if not private.take_uid_action('lookup') then return jsonb_build_object('error', 'daily_limit'); end if;
  v_id := private.profile_by_uid(p_uid);                       -- null for unknown or banned
  select p.id, coalesce(nullif(p.nickname, ''), p.name) as name, p.avatar_url, p.github, p.blurb, p.astro_level, p.ai_level,
         p.seeking, p.seeking_count, (p.looking_for_team and p.team_id is null) as looking_for_team, p.show_on_wall,
         p.role, p.affiliation, p.city, p.contact, p.team_id, t.name as team_name, t.is_hidden as team_hidden
    into v from public.profiles p left join public.teams t on t.id = p.team_id
    where p.id = coalesce(v_id, '00000000-0000-0000-0000-000000000000'::uuid) and not p.is_banned;
  if v_id is null or v.id is null then return jsonb_build_object('error', 'not_found'); end if;
  v_friend := exists (select 1 from private.friendships f where f.user_a = least(v_me, v.id) and f.user_b = greatest(v_me, v.id));
  return jsonb_build_object(
    'id', v.id, 'uid', p_uid, 'self', v.id = v_me, 'is_friend', v_friend, 'in_team', v.team_id is not null,
    'name', v.name, 'avatar_url', nullif(v.avatar_url, ''), 'github', nullif(v.github, ''), 'blurb', nullif(v.blurb, ''),
    'astro_level', v.astro_level, 'ai_level', v.ai_level, 'seeking', coalesce(v.seeking, ''), 'seeking_count', v.seeking_count,
    'looking_for_team', v.looking_for_team, 'team_name', case when v.team_hidden then null else v.team_name end, 'on_wall', v.show_on_wall,
    'role', case when v.show_on_wall then v.role end,
    'affiliation', case when v.show_on_wall then v.affiliation end,
    'city', case when v.show_on_wall then v.city end,
    'contact', case when v.show_on_wall then nullif(v.contact, '') end,
    'wechat_qr', (select q.object_path from private.wechat_qr q where q.user_id = v.id and private.can_view_wechat_qr(v.id)));
end $$;

revoke all on function public.find_by_uid(bigint) from public, anon, authenticated;
grant execute on function public.find_by_uid(bigint) to authenticated;
notify pgrst, 'reload schema';

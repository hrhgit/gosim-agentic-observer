-- Profile cards for team requests and invitations (owner request 2026-10-04). Additive, read-only.
--
-- A captain opening a pending join request sees the applicant's card; a person invited to (or asking to join) a
-- team sees that team's card and its members' cards. Only wall-type fields are returned: never email, real name,
-- contact unless the person shows it on the Teammates wall, heard_from, locale or any admin field.
-- Who may open a person's card: someone with a pending request/invitation involving them (either side, or a member
-- of the team concerned), a teammate, or anyone signed in when the person is on the Teammates wall (whose fields
-- are already public there). The WeChat QR path comes back only when private.can_view_wechat_qr allows it, and is
-- then signed through the existing storage policy.

create or replace function private.card_related(p_user uuid) returns boolean
language sql stable security definer set search_path = public, pg_temp as $$
  select auth.uid() is not null and (
    p_user = auth.uid()
    or exists (select 1 from public.profiles a join public.profiles b on b.team_id = a.team_id
               where a.id = auth.uid() and b.id = p_user and a.team_id is not null)
    or exists (select 1 from private.team_invitations i
               where i.status = 'pending' and i.team_id is not null and auth.uid() in (i.sender_id, i.recipient_id)
                 and (p_user in (i.sender_id, i.recipient_id)
                      or exists (select 1 from public.profiles m where m.id = p_user and m.team_id = i.team_id))))
$$;

create or replace function public.person_card(p_user uuid) returns jsonb
language plpgsql stable security definer set search_path = public, pg_temp as $$
declare v record; v_related boolean;
begin
  perform private.assert_not_banned();
  select p.id, coalesce(nullif(p.nickname, ''), p.name) as name, p.avatar_url, p.github, p.blurb, p.astro_level, p.ai_level,
         p.seeking, p.seeking_count, (p.looking_for_team and p.team_id is null) as looking_for_team, p.show_on_wall,
         p.role, p.affiliation, p.city, p.contact, t.name as team_name
    into v from public.profiles p left join public.teams t on t.id = p.team_id
    where p.id = p_user and not p.is_banned;
  if not found then raise exception 'card_not_available'; end if;
  v_related := private.card_related(p_user);
  if not (v_related or v.show_on_wall) then raise exception 'card_not_available'; end if;
  return jsonb_build_object(
    'id', v.id, 'name', v.name, 'avatar_url', nullif(v.avatar_url, ''), 'github', nullif(v.github, ''), 'blurb', nullif(v.blurb, ''),
    'astro_level', v.astro_level, 'ai_level', v.ai_level, 'seeking', coalesce(v.seeking, ''), 'seeking_count', v.seeking_count,
    'looking_for_team', v.looking_for_team, 'team_name', v.team_name, 'on_wall', v.show_on_wall,
    -- Shown on the public wall only when the person chose to be there.
    'role', case when v.show_on_wall then v.role end,
    'affiliation', case when v.show_on_wall then v.affiliation end,
    'city', case when v.show_on_wall then v.city end,
    'contact', case when v.show_on_wall then nullif(v.contact, '') end,
    'wechat_qr', (select q.object_path from private.wechat_qr q where q.user_id = v.id and private.can_view_wechat_qr(v.id)));
end $$;

-- A team's card: own team, or a team with a pending request/invitation involving the caller.
create or replace function public.team_card(p_team uuid) returns jsonb
language plpgsql stable security definer set search_path = public, pg_temp as $$
declare v record;
begin
  perform private.assert_not_banned();
  if not (coalesce(p_team = public.my_team_id(), false) or exists (select 1 from private.team_invitations i
      where i.team_id = p_team and i.status = 'pending' and auth.uid() in (i.sender_id, i.recipient_id))) then
    raise exception 'card_not_available';
  end if;
  select t.id, t.name, t.project_idea, t.max_size, t.is_locked into v from public.teams t where t.id = p_team;
  if not found then raise exception 'card_not_available'; end if;
  return jsonb_build_object('id', v.id, 'name', v.name, 'project_idea', nullif(v.project_idea, ''), 'max_size', v.max_size,
    'is_locked', v.is_locked,
    'members', coalesce((select jsonb_agg(jsonb_build_object('id', p.id, 'name', coalesce(nullif(p.nickname, ''), p.name),
        'avatar_url', nullif(p.avatar_url, ''), 'github', nullif(p.github, ''), 'is_leader', t.leader_id = p.id,
        'astro_level', p.astro_level, 'ai_level', p.ai_level) order by (t.leader_id = p.id) desc, p.created_at)
      from public.profiles p join public.teams t on t.id = p.team_id where p.team_id = p_team and not p.is_banned), '[]'::jsonb));
end $$;

revoke all on function private.card_related(uuid), public.person_card(uuid), public.team_card(uuid) from public, anon, authenticated;
grant execute on function public.person_card(uuid), public.team_card(uuid) to authenticated;

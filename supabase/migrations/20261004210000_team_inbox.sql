-- Pending team requests that cannot be missed (captain feedback 2026-10-04: "an application came in and I
-- cannot find where to accept it"). The existing notification list only highlights *unread* rows, so a
-- request that was seen once stopped standing out while still waiting for an answer. Additive: two new
-- read-only RPCs; no table, row or existing function changes. Existing pending rows are shown as they are.
--
--   public.my_team_inbox()             everything still waiting for the caller (with why it may be blocked)
--                                       plus the caller's own requests/invitations of the last 14 days
--   public.team_notification_counts()  {pending, unread} for the header badges
--
-- "pending" counts only rows the caller can act on now: a join request to the caller's team (the caller is
-- its captain; requests follow the captain on transfer) whose sender has no team yet, or an invitation to a
-- caller without a team. A request whose sender joined another team in the meantime is listed as blocked
-- ('joined_other_team') so the captain can clear it, but never counted.

create or replace function private.team_inbox_rows(p_user uuid)
returns table(id uuid,kind text,direction text,team_id uuid,team_name text,sender_id uuid,sender_name text,
  recipient_id uuid,recipient_name text,status text,created_at timestamptz,updated_at timestamptz,blocked text,unread boolean)
language sql stable security definer set search_path=public,pg_temp as $$
  select i.id,i.kind,case when i.recipient_id=p_user then 'received' else 'sent' end,i.team_id,coalesce(t.name,i.team_name),
    i.sender_id,s.name,i.recipient_id,r.name,
    case when i.team_id is null and i.status='pending' then 'cancelled' else i.status end,i.created_at,i.updated_at,
    case when i.recipient_id<>p_user or i.status<>'pending' or t.id is null then null
      when i.kind='request' and t.leader_id is distinct from p_user then 'leader_only'
      when i.kind='request' and s.team_id is not null then 'joined_other_team'
      when i.kind='invite' and r.team_id is not null then 'already_in_team'
      when (case when i.kind='request' then s.is_banned else false end) then 'unavailable'
      when t.is_locked then 'locked'
      when (select count(*) from public.profiles m where m.team_id=t.id)>=t.max_size then 'full'
    end,
    case when i.recipient_id=p_user then i.recipient_read_at is null or i.recipient_read_at<i.updated_at
      else i.sender_read_at is null or i.sender_read_at<i.updated_at end
  from private.team_invitations i join public.profiles s on s.id=i.sender_id join public.profiles r on r.id=i.recipient_id
  left join public.teams t on t.id=i.team_id
  where (i.recipient_id=p_user and i.status='pending' and i.team_id is not null)
     or (i.sender_id=p_user and (i.status='pending' or i.updated_at>now()-interval '14 days'))
$$;
revoke all on function private.team_inbox_rows(uuid) from public,anon,authenticated;

create or replace function public.my_team_inbox()
returns jsonb language plpgsql stable security definer set search_path=public,pg_temp as $$
begin
  perform private.assert_not_banned();
  return jsonb_build_object(
    'received',coalesce((select jsonb_agg(to_jsonb(x) order by x.created_at,x.id) from (
      select * from private.team_inbox_rows(auth.uid()) where direction='received' limit 200) x),'[]'::jsonb),
    'sent',coalesce((select jsonb_agg(to_jsonb(x) order by x.status<>'pending',x.updated_at desc,x.id) from (
      select * from private.team_inbox_rows(auth.uid()) where direction='sent'
      order by status<>'pending',updated_at desc limit 50) x),'[]'::jsonb));
end $$;

create or replace function public.team_notification_counts()
returns jsonb language sql stable security definer set search_path=public,pg_temp as $$
  select jsonb_build_object(
    'pending',(select count(*) from private.team_inbox_rows(auth.uid()) where direction='received' and status='pending' and blocked is null),
    'unread',public.team_invitation_unread())
$$;

revoke all on function public.my_team_inbox(),public.team_notification_counts() from public,anon;
grant execute on function public.my_team_inbox(),public.team_notification_counts() to authenticated;
notify pgrst,'reload schema';

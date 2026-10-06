-- Membership changes only after the receiving person accepts.
create table private.team_invitations (
  id uuid primary key default gen_random_uuid(),
  team_id uuid references public.teams(id) on delete set null,
  team_name text not null,
  sender_id uuid not null references public.profiles(id) on delete cascade,
  recipient_id uuid not null references public.profiles(id) on delete cascade,
  kind text not null default 'invite' check(kind in ('invite','request')),
  status text not null default 'pending' check(status in ('pending','accepted','declined','cancelled')),
  created_at timestamptz not null default clock_timestamp(),
  updated_at timestamptz not null default clock_timestamp(),
  sender_read_at timestamptz,
  recipient_read_at timestamptz,
  check(sender_id<>recipient_id)
);
create unique index team_invitation_pending on private.team_invitations(team_id,recipient_id) where status='pending' and kind='invite';
create unique index team_request_pending on private.team_invitations(team_id,sender_id) where status='pending' and kind='request';
create index team_invitation_sender on private.team_invitations(sender_id,updated_at desc);
create index team_invitation_recipient on private.team_invitations(recipient_id,updated_at desc);
revoke all on private.team_invitations from public,anon,authenticated;

-- Pending applications belong to the current captain after a transfer.
create function private.move_team_requests_to_leader()
returns trigger language plpgsql security definer set search_path=public,pg_temp as $$
begin
  if new.leader_id is distinct from old.leader_id then
    update private.team_invitations set recipient_id=new.leader_id,recipient_read_at=null,updated_at=clock_timestamp()
      where team_id=new.id and kind='request' and status='pending';
  end if;
  return new;
end $$;
create trigger move_team_requests_to_leader after update of leader_id on public.teams
  for each row execute function private.move_team_requests_to_leader();
revoke all on function private.move_team_requests_to_leader() from public,anon,authenticated;

create function public.team_directory()
returns table(id uuid,name text,member_count bigint,max_size integer,is_locked boolean)
language sql stable security definer set search_path=public,pg_temp as $$
  select t.id,t.name,count(p.id),t.max_size,t.is_locked
  from public.teams t left join public.profiles p on p.team_id=t.id
  where not t.is_hidden
  group by t.id
  order by (not t.is_locked and count(p.id)<t.max_size) desc,t.created_at desc
  limit 200
$$;

create function public.request_team_join(p_team_id uuid)
returns uuid language plpgsql security definer set search_path=public,pg_temp as $$
declare v_team public.teams; v_current uuid; v_id uuid;
begin
  perform private.assert_not_banned();
  select team_id into v_current from public.profiles where id=auth.uid() for update;
  if not found then raise exception 'not_authenticated'; end if;
  if v_current is not null then raise exception 'already_in_team'; end if;
  select * into v_team from public.teams where id=p_team_id for update;
  if not found or v_team.is_hidden then raise exception 'team_unavailable'; end if;
  if v_team.is_locked then raise exception 'locked'; end if;
  if (select count(*) from public.profiles where team_id=p_team_id)>=v_team.max_size then raise exception 'full'; end if;
  insert into private.team_invitations(team_id,team_name,sender_id,recipient_id,kind)
    values(v_team.id,v_team.name,auth.uid(),v_team.leader_id,'request')
    on conflict(team_id,sender_id) where status='pending' and kind='request' do nothing returning id into v_id;
  if v_id is null then select id into v_id from private.team_invitations
    where team_id=v_team.id and sender_id=auth.uid() and status='pending' and kind='request'; end if;
  perform private.audit('team.request_join',jsonb_build_object('invitation_id',v_id));
  return v_id;
end $$;

create function public.send_team_invite(p_recipient uuid)
returns uuid language plpgsql security definer set search_path=public,pg_temp as $$
declare v_team public.teams; v_id uuid;
begin
  perform private.assert_not_banned();
  select t.* into v_team from public.teams t where t.id=public.my_team_id() for update;
  if not found then raise exception 'not_in_team'; end if;
  if v_team.is_locked then raise exception 'locked'; end if;
  if (select count(*) from public.profiles where team_id=v_team.id)>=v_team.max_size then raise exception 'full'; end if;
  if not exists(select 1 from public.profiles where id=p_recipient and id<>auth.uid() and not is_banned
    and team_id is null and show_on_wall) then raise exception 'recipient_unavailable'; end if;
  insert into private.team_invitations(team_id,team_name,sender_id,recipient_id)
    values(v_team.id,v_team.name,auth.uid(),p_recipient)
    on conflict(team_id,recipient_id) where status='pending' and kind='invite' do nothing returning id into v_id;
  if v_id is null then select id into v_id from private.team_invitations
    where team_id=v_team.id and recipient_id=p_recipient and status='pending' and kind='invite'; end if;
  perform private.audit('team.invite',jsonb_build_object('invitation_id',v_id));
  return v_id;
end $$;

create function public.my_team_invitations(p_before timestamptz default null,p_before_id uuid default null)
returns jsonb language plpgsql stable security definer set search_path=public,pg_temp as $$
begin
  perform private.assert_not_banned();
  if (p_before is null) <> (p_before_id is null) then raise exception 'invalid_pagination'; end if;
  return coalesce((select jsonb_agg(to_jsonb(r) order by r.updated_at desc,r.id desc) from (
    select i.id,i.kind,i.team_id,i.sender_id,i.recipient_id,coalesce(t.name,i.team_name) as team_name,s.name as sender_name,p.name as recipient_name,
      case when i.recipient_id=auth.uid() then 'received' else 'sent' end as direction,
      case when i.team_id is null and i.status='pending' then 'cancelled' else i.status end as status,
      i.created_at,i.updated_at,case when i.recipient_id=auth.uid() then
        i.recipient_read_at is null or i.recipient_read_at<i.updated_at else
        i.sender_read_at is null or i.sender_read_at<i.updated_at end as unread
    from private.team_invitations i join public.profiles s on s.id=i.sender_id join public.profiles p on p.id=i.recipient_id
    left join public.teams t on t.id=i.team_id
    where auth.uid() in (i.sender_id,i.recipient_id)
      and (p_before is null or (i.updated_at,i.id)<(p_before,p_before_id))
    order by i.updated_at desc,i.id desc limit 100
  ) r),'[]'::jsonb);
end $$;

create function public.team_invitation_unread()
returns bigint language sql stable security definer set search_path=public,pg_temp as $$
  select count(*) from private.team_invitations where
    (sender_id=auth.uid() and (sender_read_at is null or sender_read_at<updated_at)) or
    (recipient_id=auth.uid() and (recipient_read_at is null or recipient_read_at<updated_at))
$$;

create function public.mark_team_invitations_read(p_ids uuid[],p_through timestamptz)
returns void language plpgsql security definer set search_path=public,pg_temp as $$
begin
  perform private.assert_not_banned();
  if cardinality(p_ids)>100 then raise exception 'too_many_invitations'; end if;
  update private.team_invitations set sender_read_at=clock_timestamp()
    where id=any(p_ids) and sender_id=auth.uid() and updated_at<=p_through;
  update private.team_invitations set recipient_read_at=clock_timestamp()
    where id=any(p_ids) and recipient_id=auth.uid() and updated_at<=p_through;
end $$;

create function public.respond_team_invite(p_invitation uuid,p_accept boolean)
returns text language plpgsql security definer set search_path=public,pg_temp as $$
declare v private.team_invitations; v_team public.teams; v_current uuid; v_status text; v_joiner uuid;
begin
  perform private.assert_not_banned();
  select * into v from private.team_invitations where id=p_invitation and recipient_id=auth.uid();
  if not found then raise exception 'invitation_not_found'; end if;
  -- Same membership lock order as join_team.
  v_joiner:=case when v.kind='request' then v.sender_id else v.recipient_id end;
  select team_id into v_current from public.profiles where id=v_joiner for update;
  select * into v_team from public.teams where id=v.team_id for update;
  select * into v from private.team_invitations where id=p_invitation for update;
  v_status:=case when p_accept then 'accepted' else 'declined' end;
  if v.status=v_status then return v_status; end if;
  if v.status<>'pending' then raise exception 'invitation_finished'; end if;
  if v.kind='request' and v_team.leader_id is distinct from auth.uid() then raise exception 'leader_only'; end if;
  if p_accept then
    if exists(select 1 from public.profiles where id=v_joiner and is_banned) then raise exception 'banned'; end if;
    if v_current is not null then raise exception 'already_in_team'; end if;
    if v_team.id is null then raise exception 'invitation_finished'; end if;
    if v_team.is_locked then raise exception 'locked'; end if;
    if (select count(*) from public.profiles where team_id=v_team.id)>=v_team.max_size then raise exception 'full'; end if;
    update public.profiles set team_id=v_team.id,looking_for_team=false where id=v_joiner;
  end if;
  update private.team_invitations set status=v_status,updated_at=clock_timestamp(),sender_read_at=null,
    recipient_read_at=clock_timestamp() where id=p_invitation;
  perform private.audit('team.invite_response',jsonb_build_object('invitation_id',p_invitation,'status',v_status));
  return v_status;
end $$;

create function public.cancel_team_invite(p_invitation uuid)
returns void language plpgsql security definer set search_path=public,pg_temp as $$
begin
  perform private.assert_not_banned();
  update private.team_invitations set status='cancelled',updated_at=clock_timestamp(),sender_read_at=clock_timestamp(),recipient_read_at=null
    where id=p_invitation and sender_id=auth.uid() and status='pending';
  if not found then raise exception 'invitation_finished'; end if;
end $$;

revoke all on function public.team_directory(),public.request_team_join(uuid),public.send_team_invite(uuid),public.my_team_invitations(timestamptz,uuid),public.team_invitation_unread(),
  public.mark_team_invitations_read(uuid[],timestamptz),public.respond_team_invite(uuid,boolean),public.cancel_team_invite(uuid) from public,anon,authenticated;
grant execute on function public.team_directory() to anon,authenticated;
grant execute on function public.request_team_join(uuid),
  public.send_team_invite(uuid),public.my_team_invitations(timestamptz,uuid),public.team_invitation_unread(),
  public.mark_team_invitations_read(uuid[],timestamptz),public.respond_team_invite(uuid,boolean),public.cancel_team_invite(uuid) to authenticated;

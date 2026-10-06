-- Optional WeChat QR code on the profile (owner decision 2026-10-04). Additive only.
--
-- The image lives in a PRIVATE bucket and is only ever served as a short-lived signed URL to a logged-in,
-- non-banned participant the owner allows: everyone signed in ('all') or friends only ('friends', the
-- default). Uploads and deletions go through the wechat-qr edge function (service role), which first checks
-- the image really decodes as a QR code; participants have no direct write access to the bucket. Anyone who
-- can see a code can report it; admins see reports and remove a code (the function deletes the object too).

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('wechat-qr', 'wechat-qr', false, 1048576, array['image/png','image/jpeg','image/webp'])
on conflict (id) do update set public = false, file_size_limit = excluded.file_size_limit,
  allowed_mime_types = excluded.allowed_mime_types;

create table if not exists private.wechat_qr(
  user_id uuid primary key references public.profiles(id) on delete cascade,
  object_path text not null unique check (object_path ~ '^[0-9a-f-]{36}/[0-9a-f-]{36}\.(png|jpg|webp)$'),
  visibility text not null default 'friends' check (visibility in ('all','friends')),
  updated_at timestamptz not null default clock_timestamp()
);

create table if not exists private.wechat_qr_reports(
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null references public.profiles(id) on delete cascade,
  object_path text not null,
  reporter_id uuid not null references public.profiles(id) on delete cascade,
  reason text not null default '' check (char_length(reason) <= 300),
  status text not null default 'open' check (status in ('open','removed','dismissed')),
  created_at timestamptz not null default clock_timestamp(),
  resolved_at timestamptz,
  resolved_by uuid
);
create unique index if not exists wechat_qr_report_once on private.wechat_qr_reports(owner_id, reporter_id, object_path);
create index if not exists wechat_qr_report_open on private.wechat_qr_reports(created_at desc) where status = 'open';
revoke all on private.wechat_qr, private.wechat_qr_reports from public, anon, authenticated;

-- May the caller see this user's code? Signed in, neither side banned, and 'all' or friends (or their own).
create or replace function private.can_view_wechat_qr(p_owner uuid) returns boolean
language sql stable security definer set search_path = public, pg_temp as $$
  select auth.uid() is not null and exists (
    select 1 from private.wechat_qr q join public.profiles o on o.id = q.user_id
    join public.profiles v on v.id = auth.uid()
    where q.user_id = p_owner and not o.is_banned and not v.is_banned
      and (q.user_id = auth.uid() or q.visibility = 'all'
        or exists (select 1 from private.friendships f where f.user_a = least(auth.uid(), p_owner) and f.user_b = greatest(auth.uid(), p_owner))))
$$;

-- Signed URLs are only issued for the current object of a code the caller may see.
create or replace function public.can_view_wechat_qr_object(p_name text) returns boolean
language sql stable security definer set search_path = public, pg_temp as $$
  select exists (select 1 from private.wechat_qr q where q.object_path = p_name and private.can_view_wechat_qr(q.user_id))
    or public.is_admin()
$$;
-- (In public, not private: the storage policy below runs as the signed-in user, who has no access to private.)
revoke all on function private.can_view_wechat_qr(uuid), public.can_view_wechat_qr_object(text) from public, anon, authenticated;
grant execute on function public.can_view_wechat_qr_object(text) to authenticated;

drop policy if exists "wechat qr read allowed" on storage.objects;
create policy "wechat qr read allowed" on storage.objects for select to authenticated
using (bucket_id = 'wechat-qr' and public.can_view_wechat_qr_object(name));

-- The caller's own code (path to sign, visibility) or null.
create or replace function public.my_wechat_qr() returns jsonb
language sql stable security definer set search_path = public, pg_temp as $$
  select jsonb_build_object('path', object_path, 'visibility', visibility, 'updated_at', updated_at)
  from private.wechat_qr where user_id = auth.uid()
$$;

create or replace function public.set_wechat_qr_visibility(p_visibility text) returns void
language plpgsql security definer set search_path = public, pg_temp as $$
begin
  perform private.assert_not_banned();
  if p_visibility not in ('all','friends') then raise exception 'invalid_visibility'; end if;
  update private.wechat_qr set visibility = p_visibility, updated_at = clock_timestamp() where user_id = auth.uid();
  if not found then raise exception 'no_wechat_qr'; end if;
end $$;

-- Which of these users have a code the caller may see, with the object path to sign (at most 200 at once).
create or replace function public.wechat_qr_visible(p_users uuid[]) returns jsonb
language plpgsql stable security definer set search_path = public, pg_temp as $$
begin
  perform private.assert_not_banned();
  if cardinality(p_users) > 200 then raise exception 'too_many_users'; end if;
  return coalesce((select jsonb_object_agg(q.user_id, q.object_path) from private.wechat_qr q
    where q.user_id = any(p_users) and private.can_view_wechat_qr(q.user_id)), '{}'::jsonb);
end $$;

create or replace function public.report_wechat_qr(p_owner uuid, p_reason text default '') returns void
language plpgsql security definer set search_path = public, pg_temp as $$
declare v_path text;
begin
  perform private.assert_not_banned();
  if p_owner = auth.uid() or not private.can_view_wechat_qr(p_owner) then raise exception 'no_wechat_qr'; end if;
  select object_path into v_path from private.wechat_qr where user_id = p_owner;
  insert into private.wechat_qr_reports(owner_id, object_path, reporter_id, reason)
    values (p_owner, v_path, auth.uid(), left(coalesce(p_reason, ''), 300)) on conflict do nothing;
end $$;

-- Organizers: open reports with the reported object's path (to sign) and whether it is still the current one.
create or replace function public.admin_wechat_qr_reports() returns jsonb
language plpgsql stable security definer set search_path = public, pg_temp as $$
begin
  if not public.is_admin() then raise exception 'admin_only'; end if;
  return coalesce((select jsonb_agg(jsonb_build_object('owner_id', r.owner_id, 'owner_name', coalesce(nullif(o.nickname, ''), o.name),
      'owner_email', o.email, 'path', r.object_path, 'current', q.object_path = r.object_path,
      'reports', r.n, 'reasons', r.reasons, 'first_at', r.first_at) order by r.first_at)
    from (select owner_id, object_path, count(*) n, array_agg(reason order by created_at) filter (where reason <> '') reasons, min(created_at) first_at
          from private.wechat_qr_reports where status = 'open' group by owner_id, object_path) r
    join public.profiles o on o.id = r.owner_id left join private.wechat_qr q on q.user_id = r.owner_id), '[]'::jsonb);
end $$;

create or replace function public.admin_dismiss_wechat_qr_reports(p_owner uuid) returns void
language plpgsql security definer set search_path = public, pg_temp as $$
begin
  if not public.is_admin() then raise exception 'admin_only'; end if;
  update private.wechat_qr_reports set status = 'dismissed', resolved_at = clock_timestamp(), resolved_by = auth.uid()
    where owner_id = p_owner and status = 'open';
end $$;

-- Service role only (the wechat-qr edge function): store a verified upload, returning the replaced object
-- path for deletion; or remove a code (own, or any for an admin), returning the object path to delete.
create or replace function public.wechat_qr_store(p_user uuid, p_path text) returns text
language plpgsql security definer set search_path = public, pg_temp as $$
declare v_old text;
begin
  if coalesce(auth.role(), '') <> 'service_role' then raise exception 'service_only'; end if;
  select object_path into v_old from private.wechat_qr where user_id = p_user for update;
  insert into private.wechat_qr(user_id, object_path) values (p_user, p_path)
    on conflict (user_id) do update set object_path = excluded.object_path, updated_at = clock_timestamp();
  return v_old;
end $$;

create or replace function public.wechat_qr_remove(p_user uuid, p_actor uuid) returns text
language plpgsql security definer set search_path = public, pg_temp as $$
declare v_old text; v_admin boolean;
begin
  if coalesce(auth.role(), '') <> 'service_role' then raise exception 'service_only'; end if;
  v_admin := coalesce((select is_admin from public.profiles where id = p_actor), false);
  if p_user <> p_actor and not v_admin then raise exception 'admin_only'; end if;
  delete from private.wechat_qr where user_id = p_user returning object_path into v_old;
  if v_admin and p_user <> p_actor then
    update private.wechat_qr_reports set status = 'removed', resolved_at = clock_timestamp(), resolved_by = p_actor
      where owner_id = p_user and status = 'open';
    insert into public.audit_log(user_id, action, detail) values (p_actor, 'admin.wechat_qr_remove', jsonb_build_object('owner_id', p_user));
  end if;
  return v_old;
end $$;

revoke all on function public.my_wechat_qr(), public.set_wechat_qr_visibility(text), public.wechat_qr_visible(uuid[]),
  public.report_wechat_qr(uuid, text), public.admin_wechat_qr_reports(), public.admin_dismiss_wechat_qr_reports(uuid),
  public.wechat_qr_store(uuid, text), public.wechat_qr_remove(uuid, uuid) from public, anon, authenticated;
grant execute on function public.my_wechat_qr(), public.set_wechat_qr_visibility(text), public.wechat_qr_visible(uuid[]),
  public.report_wechat_qr(uuid, text), public.admin_wechat_qr_reports(), public.admin_dismiss_wechat_qr_reports(uuid) to authenticated;
grant execute on function public.wechat_qr_store(uuid, text), public.wechat_qr_remove(uuid, uuid) to service_role;

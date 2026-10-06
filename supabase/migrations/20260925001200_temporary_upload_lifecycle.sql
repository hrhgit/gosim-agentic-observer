alter table private.observer_uploads add column run_id uuid references public.observer_runs(id);
alter table private.observer_uploads add column cleaned_at timestamptz;
create index observer_uploads_expired on private.observer_uploads(expires_at) where cleaned_at is null;

create function public.observer_accept_uploaded_csv(p_run uuid,p_user uuid,p_upload uuid,p_digest text)
returns void language plpgsql security definer set search_path=public,pg_temp as $$
declare u private.observer_uploads;
begin
  select * into u from private.observer_uploads where id=p_upload for update;
  if not found or u.purpose<>'csv' or u.expires_at<=clock_timestamp() or u.cleaned_at is not null
    or (u.run_id is not null and u.run_id<>p_run)
    or not exists(select 1 from public.profiles where id=p_user and team_id=u.team_id and not is_banned)
    then raise exception 'upload_not_found';end if;
  perform public.observer_accept_csv(p_run,p_user,p_digest);
  update private.observer_uploads set run_id=p_run,consumed_at=coalesce(consumed_at,clock_timestamp()) where id=p_upload;
end $$;

create function public.observer_expired_uploads(p_limit integer default 50)
returns jsonb language sql stable security definer set search_path=public,pg_temp as $$
 select coalesce(jsonb_agg(jsonb_build_object('id',u.id,'path',u.path)),'[]') from (
   select u.id,u.path from private.observer_uploads u
   where u.cleaned_at is null and u.expires_at<now()
     and (u.consumed_at is null
       or (u.purpose='source' and exists(select 1 from private.observer_materializations m where m.revision_id=u.revision_id))
       or (u.purpose='csv' and exists(select 1 from public.observer_runs r where r.id=u.run_id
         and r.status='scored' and r.result_path like 'github:%')))
   order by u.expires_at limit greatest(1,least(coalesce(p_limit,50),100))
 ) u
$$;

create function public.observer_upload_cleaned(p_upload uuid)
returns void language sql security definer set search_path=public,pg_temp as $$
 update private.observer_uploads set cleaned_at=coalesce(cleaned_at,now()) where id=p_upload and expires_at<now()
$$;
revoke all on function public.observer_accept_uploaded_csv(uuid,uuid,uuid,text),public.observer_expired_uploads(integer),
  public.observer_upload_cleaned(uuid) from public,anon,authenticated;
grant execute on function public.observer_accept_uploaded_csv(uuid,uuid,uuid,text),public.observer_expired_uploads(integer),
  public.observer_upload_cleaned(uuid) to service_role;

-- Retain the same timer, with expired eligible staging objects also counting as
-- pending work. Submitted originals without a durable snapshot are preserved.
create or replace function private.observer_tick() returns bigint
language plpgsql security definer set search_path=pg_catalog,pg_temp as $$
declare c private.observer_dispatch_config; capability text; request_id bigint;
begin
  select * into c from private.observer_dispatch_config where id and enabled for update skip locked;
  if not found or c.last_enqueued_at>clock_timestamp()-interval '50 seconds' then return null; end if;
  if not exists(select 1 from public.observer_revisions where status in ('queued','preparing'))
     and not exists(select 1 from public.observer_runs where status in ('queued','starting','ready','running'))
     and not exists(select 1 from private.observer_jobs where status in ('queued','dispatched','claimed'))
     and public.observer_expired_uploads(1)='[]'::jsonb then return null; end if;
  if not exists(select 1 from pg_extension where extname='pg_net')
     or to_regclass('vault.decrypted_secrets') is null then return null; end if;
  execute 'select decrypted_secret from vault.decrypted_secrets where id=$1' into capability using c.secret_id;
  if capability is null or length(capability)<40 then return null; end if;
  select net.http_post(url:=c.endpoint,
    headers:=jsonb_build_object('Content-Type','application/json','Authorization','Bearer '||capability),
    body:='{}'::jsonb,timeout_milliseconds:=120000) into request_id;
  update private.observer_dispatch_config set last_enqueued_at=clock_timestamp(),last_request_id=request_id where id;
  return request_id;
end $$;

-- A private, idle-aware timer advances the durable queue without a resident server.
-- Its capability is installed separately in Vault, never in migration text.
create table private.observer_dispatch_config (
  id boolean primary key default true check(id),
  enabled boolean not null default false,
  endpoint text not null check(endpoint ~ '^https://[a-z0-9]+\.supabase\.co/functions/v1/observer-dispatch$'),
  secret_id uuid not null,
  last_enqueued_at timestamptz,
  last_request_id bigint
);
revoke all on private.observer_dispatch_config from public,anon,authenticated;

create function private.observer_tick() returns bigint
language plpgsql security definer set search_path=pg_catalog,pg_temp as $$
declare c private.observer_dispatch_config; capability text; request_id bigint;
begin
  select * into c from private.observer_dispatch_config where id and enabled for update skip locked;
  if not found or c.last_enqueued_at>clock_timestamp()-interval '50 seconds' then return null; end if;
  if not exists(select 1 from public.observer_revisions where status in ('queued','preparing'))
     and not exists(select 1 from public.observer_runs where status in ('queued','starting','ready','running'))
     and not exists(select 1 from private.observer_jobs where status in ('queued','dispatched','claimed')) then return null; end if;
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
revoke all on function private.observer_tick() from public,anon,authenticated;

do $cron$
begin
  if exists(select 1 from pg_available_extensions where name='pg_cron') then
    create extension if not exists pg_cron;
    perform cron.unschedule(jobid) from cron.job where jobname='observer-platform-dispatch';
    perform cron.schedule('observer-platform-dispatch','* * * * *','select private.observer_tick()');
  end if;
end $cron$;

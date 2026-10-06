-- Worker liveness for participants: the evaluator publishes site_settings.worker_heartbeat; everyone may read it.
-- queue_depth() / queue_position(id) let the submission page say how many runs are ahead.
drop policy if exists "settings public keys" on public.site_settings;
create policy "settings public keys" on public.site_settings for select to anon, authenticated
  using (key in ('registration_open', 'event', 'credits_note', 'worker_heartbeat') or public.is_admin());

create or replace function public.queue_depth() returns integer
language sql stable security definer set search_path = public as $$
  select count(*)::int from public.submissions where status in ('queued', 'running');
$$;
grant execute on function public.queue_depth() to anon, authenticated, service_role;

create or replace function public.queue_position(p_id bigint) returns integer
language sql stable security definer set search_path = public as $$
  select count(*)::int from public.submissions s, public.submissions me
  where me.id = p_id and s.status in ('queued', 'running') and s.id <> me.id and (s.status = 'running' or s.created_at < me.created_at)
    and (me.team_id = public.my_team_id() or public.is_admin());
$$;
grant execute on function public.queue_position(bigint) to authenticated, service_role;

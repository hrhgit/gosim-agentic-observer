-- Existing submissions and their scoring updates remain unchanged. New online
-- phases must use the session-backed project/CSV flow, including direct RPC use.
create function private.observer_submission_admission()
returns trigger language plpgsql security definer set search_path=public,pg_temp as $$
begin
  if tg_op='UPDATE' and new.phase_id is not distinct from old.phase_id then return new; end if;
  if exists(select 1 from public.observer_phase_settings s where s.phase_id=new.phase_id
    and (s.projects_enabled or s.local_sessions_enabled)) then
    raise exception 'online_session_required';
  end if;
  return new;
end $$;
revoke all on function private.observer_submission_admission() from public,anon,authenticated;
create trigger observer_submission_admission before insert or update of phase_id on public.submissions
for each row execute function private.observer_submission_admission();

notify pgrst,'reload schema';

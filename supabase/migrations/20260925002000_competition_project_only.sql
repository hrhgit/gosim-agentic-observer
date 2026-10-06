-- Formal evaluations require confirmed complete projects. Existing rows remain intact.
create function private.require_competition_project()
returns trigger language plpgsql security definer set search_path=public,pg_temp as $$
begin
  if exists(select 1 from public.phases where id=new.phase_id and (counts_for_final or slug='online')) then
    if tg_table_name='submissions' then raise exception 'competition_project_required'; end if;
    if new.purpose='formal' and (new.revision_id is null or new.mode<>'project') then
      raise exception 'competition_project_required'; end if;
  end if;
  return new;
end $$;
create trigger a_require_competition_project before insert on public.submissions
  for each row execute function private.require_competition_project();
create trigger a_require_competition_project before insert on public.observer_batches
  for each row execute function private.require_competition_project();
revoke all on function private.require_competition_project() from public,anon,authenticated;

-- Settings also tell current clients that local CSV sessions are unavailable.
-- Scores, submissions, dates and the legacy practice configuration are untouched.
update public.observer_phase_settings set local_sessions_enabled=false
  where phase_id in (select id from public.phases where counts_for_final or slug='online');

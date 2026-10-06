-- Fair clock (challenge/fair_clock.py, docs/fair-clock.md). A colocated (v4) card's budget
-- (runtime_seconds, 900) is now charged in normalized seconds of the agent's own work; the
-- engine enforces it and a hard real-time cap of 3 x the budget. The session deadline is only
-- the outer safety net (and bounds model access), so for colocated phases it now covers that
-- cap plus two minutes, and the session lives at least 30 minutes beyond it. Other phases are
-- unchanged. Runtimes from before the fair clock still stop at their own 900 s, so this is
-- safe to apply before the runner rollout.
create or replace function public.observer_begin(p_run uuid,p_token text)
returns timestamptz language plpgsql security definer set search_path = public, pg_temp as $$
declare v private.observer_sessions; v_seconds integer; v_colocated boolean; v_deadline timestamptz;
  v_expires timestamptz;
begin
  v:=private.observer_capability(p_run,p_token,'engine');
  if v.ready_at is null then raise exception 'participant_not_ready'; end if;
  if v.deadline_at is not null then return v.deadline_at; end if;
  select case when b.purpose='preview' then least(c.runtime_seconds,300) else c.runtime_seconds end,
         coalesce(c.colocated,false)
    into v_seconds, v_colocated from public.observer_runs r join public.observer_batches b on b.id=r.batch_id
    join public.observer_phase_settings c on c.phase_id=b.phase_id where r.id=p_run;
  v_expires:=v.expires_at;
  if v_colocated then
    v_seconds:=v_seconds*3+120;
    v_expires:=greatest(v.expires_at,clock_timestamp()+make_interval(secs=>v_seconds+1800));
  end if;
  v_deadline:=least(clock_timestamp()+make_interval(secs=>v_seconds),v_expires);
  update private.observer_sessions set deadline_at=v_deadline,expires_at=v_expires where run_id=p_run;
  update public.observer_runs set status='running',started_at=clock_timestamp() where id=p_run;
  return v_deadline;
end $$;

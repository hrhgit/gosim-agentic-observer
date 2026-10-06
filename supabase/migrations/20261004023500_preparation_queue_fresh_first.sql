-- Fresh revisions first: a revision that has never been leased must not wait behind
-- old revisions that keep failing and coming back (head-of-line blocking after the
-- 2026-10-04 requeue of stuck revisions). Only the ORDER BY changed.
CREATE OR REPLACE FUNCTION public.observer_pending_preparations(p_limit integer DEFAULT 3)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public', 'pg_temp'
AS $function$
declare r record;c private.observer_preparation_config;l private.observer_preparations;result jsonb:='[]';v_gameplay text;v_minutes integer;
begin
  select x.* into c from private.observer_preparation_config x
    join public.scenarios s on s.id=x.scenario_id
    join public.observer_phase_settings f on f.phase_id=x.phase_id
    where x.enabled and f.projects_enabled and s.is_active and s.weather_public and s.events_public and s.forecasts_public;
  if not found then return result; end if;
  v_gameplay:=case when exists(select 1 from public.scenarios where id=c.scenario_id and contract='v4-score-v1') then 'v4' else 'v3' end;
  for r in select rev.id,p.owner_id,p.team_id,rev.source_kind,rev.source_location
    from public.observer_revisions rev join public.observer_projects p on p.id=rev.project_id
    join public.profiles u on u.id=p.owner_id and u.team_id=p.team_id and not u.is_banned
    left join private.observer_preparations prep on prep.revision_id=rev.id
    -- A parked row stays out of this scan entirely: no new lease until an
    -- organizer clears it via observer_admin_requeue.
    where rev.status='queued' and (prep.revision_id is null or (prep.expires_at<=now() and prep.paused_at is null))
    order by coalesce(prep.attempts,0),rev.created_at,rev.id for update of rev skip locked limit greatest(1,least(coalesce(p_limit,3),10))
  loop
    select * into l from private.observer_preparations where revision_id=r.id;
    if found and l.expires_at>now() then continue; end if;
    if found and now()-l.first_leased_at>interval '2 hours' then
      update private.observer_preparations set paused_at=now() where revision_id=r.id;
      perform private.observer_raise_incident('revision',r.id,'preparation_retry_budget_exhausted',
        jsonb_build_object('attempts',l.attempts,'team_id',r.team_id,'owner_id',r.owner_id));
      continue;
    end if;
    v_minutes:=case when found then (least(20,2^greatest(l.attempts,1)))::int else 2 end;
    insert into private.observer_preparations(revision_id,lease,expires_at,phase_id,scenario_id,model)
      values(r.id,gen_random_uuid(),now()+(v_minutes||' minutes')::interval,c.phase_id,c.scenario_id,c.model)
      on conflict(revision_id) do update set lease=excluded.lease,expires_at=excluded.expires_at,
        attempts=private.observer_preparations.attempts+1 returning * into l;
    result:=result || jsonb_build_array(to_jsonb(r)||jsonb_build_object('lease',l.lease,'model_run_id',l.model_run_id,'model',l.model,
      'gameplay',v_gameplay));
  end loop;
  return result;
end $function$
;

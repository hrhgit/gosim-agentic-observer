"""New board aggregates one complete evaluation without exposing private evidence."""
import uuid
from psycopg.types.json import Jsonb
from test_project_database import database,setup,query,rpc,identity  # noqa: F401


def test_board_uses_one_complete_batch_and_respects_private_phase(setup):
    s=setup;uri=s['uri'];scenario=uuid.uuid4()
    query(uri,"insert into public.scenarios(id,slug,name) values(%s,%s,'Second')",(scenario,str(scenario)))
    query(uri,'insert into public.phase_scenarios values(%s,%s)',(s['phase'],scenario))
    batches=[]
    for scores in ((100,0),(60,60),(0,100)):
        batch=rpc(uri,'observer_create_batch',s['phase'],None,role='authenticated',user=s['user']);batches.append(batch)
        runs=query(uri,'select id from public.observer_runs where batch_id=%s order by scenario_id',(batch,))
        for (run,),value in zip(runs,scores):
            summary={'score':{'total':value,'base_science':value+5,'program_bonus':2,'report_reward':3,'penalties':{'bad_action':10}},
              'completed_tiles':4,'required_missing':2}
            query(uri,"update public.observer_runs set status='scored',score=%s,score_summary=%s,result_path='github:private-evidence',finished_at=now() where id=%s",(value,Jsonb(summary),run))
        query(uri,'select private.observer_finalize_batch(%s)',(batch,))
    rows=rpc(uri,'observer_board',s['phase'],100,role='anon')
    assert len(rows)==1
    row=rows[0]
    assert row['observer_batch_id']==str(batches[1]) and row['total_score']==60
    assert row['submission_count']==3 and row['base_science']==65 and row['penalty_total']==10
    assert row['report_reward']==3 and row['completed_tiles']==4
    assert 'private-evidence' not in str(rows) and 'run_id' not in str(rows)
    query(uri,'update public.observer_phase_settings set access_team_id=%s where phase_id=%s',(s['team'],s['phase']))
    assert rpc(uri,'observer_board',s['phase'],100,role='anon')==[]
    assert query(uri,'select * from public.observer_leaderboard(%s)',(s['phase'],),role='anon')==[]
    assert rpc(uri,'observer_board',s['phase'],100,role='authenticated',user=s['user'])[0]['total_score']==60


def test_champion_trace_is_private_in_every_phase_state(setup):
    s=setup;uri=s['uri'];practice=uuid.uuid4();sky=uuid.uuid4();slug=str(sky)
    query(uri,"insert into public.phases(id,slug,name_en,name_zh,sort_order) values(%s,%s,'Practice','练习赛',49)",(practice,str(practice)))
    query(uri,"insert into public.scenarios(id,slug,name,weather_public,tiles_public) values(%s,%s,'Public sky',true,true)",(sky,slug))
    query(uri,'insert into public.phase_scenarios values(%s,%s)',(practice,sky))
    submission=query(uri,"""insert into public.submissions(team_id,user_id,phase_id,scenario_id,kind,storage_path,status,score)
      values(%s,%s,%s,%s,'results','old.csv','scored',21085.3) returning id""",(s['team'],s['user'],practice,sky))[0][0]
    query(uri,"insert into public.evaluations(submission_id,scenario_id,status,score) values(%s,%s,'scored',21085.3)",(submission,sky))
    public_path=f"{s['team']}/sub-{submission}/{slug}/report.json"
    private_path=f"private/sub-999999/{s['scenario']}/report.json"
    query(uri,"insert into storage.objects(bucket_id,name) values('results',%s),('results',%s)",(public_path,private_path))
    outsider, _ = identity(uri)
    teammate, _ = identity(uri, team=s['team'])
    admin, _ = identity(uri)
    query(uri,'update public.profiles set is_admin=true where id=%s',(admin,))
    artifact_paths = [public_path, public_path.replace('report.json','decisions.csv'),
                      public_path.replace('report.json','decision_replay.html')]
    for path in artifact_paths[1:]:
        query(uri,"insert into storage.objects(bucket_id,name) values('results',%s)",(path,))
    # Even a public-scenario champion remains private before/during/after finals.
    query(uri,"update public.phases set counts_for_final=true,sort_order=1 where id=%s",(s['phase'],))
    for schedule in ("starts_at=now()+interval '1 day'", "starts_at=now()-interval '1 day'",
                     "starts_at=now()-interval '2 days',ends_at=now()-interval '1 day'"):
        query(uri,'update public.phases set '+schedule+' where id=%s',(s['phase'],))
        for role, user in [('anon',None),('authenticated',outsider)]:
            assert rpc(uri,'champion_run',role=role,user=user) is None
            assert rpc(uri,'champion_report_path',role=role,user=user) is None
            assert query(uri,"select name from storage.objects where bucket_id='results' and name=any(%s)",
                         (artifact_paths+[private_path],),role=role,user=user)==[]
        for user in (s['user'],teammate,admin):
            visible=query(uri,"select name from storage.objects where bucket_id='results' and name=any(%s)",
                          (artifact_paths,),role='authenticated',user=user)
            assert {v[0] for v in visible} == set(artifact_paths)
    assert query(uri,'select score from public.submissions where id=%s',(submission,))==[(21085.3,)]
    # Public ranking still works; only the private trace is withheld.
    assert query(uri,'select total_score from public.leaderboard(%s,1,%s)',
                 (str(practice),slug),role='anon')==[(21085.3,)]

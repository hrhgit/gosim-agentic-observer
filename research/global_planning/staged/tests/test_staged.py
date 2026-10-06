import copy,gzip,json,sqlite3
from pathlib import Path
import numpy as np
import pytest,torch
from research.global_planning.data import micro_entries,build_micro
from research.global_planning.common import ROOT,digest
from research.global_planning.staged.data import entries
from research.global_planning.staged.rollout import episode,rule,expanded_rule
from research.global_planning.staged.features import encode,Normalizer
from research.global_planning.staged.training import Policy,checkpoint,load,optimize,CONFIG
from research.global_planning.model import PlanningNet
from research.global_planning.staged.search import FutureSampler,SearchPolicy


def frame_from(trace):
    with gzip.open(trace.folder/'trace.jsonl.gz','rt') as f:
        for line in f:
            r=json.loads(line)
            if r['type']=='proposal':return r['frame']

def test_complete_scene_data_splits():
    data=entries();assert len({e['seed'] for e in data})==48
    assert {s:sum(e['split']==s for e in data) for s in ['train','validation','test','stress']}==dict(train=16,validation=8,test=16,stress=8)
    assert all(sum(e['card']==c and e['split']==s for e in data)==n for s,n in [('train',4),('validation',2),('test',4),('stress',2)] for c in 'abcd')

@pytest.mark.parametrize('stage',[2,3,4])
def test_teacher_actions_sql_and_full_rewards(tmp_path,stage):
    b=build_micro(micro_entries()[0],tmp_path/'bundle')
    t=episode(b,tmp_path/'run',stage=stage,models=False)
    rows=list(t.rows());assert rows and t.summary['termination']['reason']=='survey_complete'
    assert abs(t.summary['reward_sum']-t.summary['total'])<1e-6
    assert all(np.isfinite(r['c']).all() for r in rows)
    assert rows[0]['x'].shape==(55,) and rows[0]['c'].shape[1]==84
    assert [r['return_'] for r in rows]==pytest.approx([sum(t.summary['rewards'][r['step']:]) for r in rows],abs=1e-6)
    if stage==4:assert {'program','fiber'}.issubset({r['kind'] for r in rows})
    with pytest.raises(ValueError):episode(b,tmp_path/'run',stage=stage,key='changed',models=False)


def test_wait_and_future_labels_do_not_enter_features(tmp_path):
    b=build_micro(micro_entries()[1],tmp_path/'bundle')
    def wait(frame):return len(frame['candidates'])-1 if frame['kind']=='region' else frame['teacher'],{}
    t=episode(b,tmp_path/'wait',wait,stage=3,models=False)
    frame=frame_from(t);altered=copy.deepcopy(frame);altered['seed']=998;altered['teacher']=999;altered['truth']={'weather':0};altered['final_total']=1000
    left=encode(frame);right=encode(altered)
    for a,b in zip(left,right):np.testing.assert_array_equal(a,b)
    with gzip.open(t.folder/'trace.jsonl.gz','rt') as f:
        actions=[json.loads(line)['action'] for line in f if json.loads(line)['type']=='action']
    assert any(a['action']=='wait' for a in actions)


def test_streaming_training_resume_and_negative_value(tmp_path):
    torch.set_num_threads(1);torch.manual_seed(101)
    b=build_micro(micro_entries()[0],tmp_path/'bundle');t=episode(b,tmp_path/'run',stage=2,models=False)
    norm=Normalizer.fit([t]);model=PlanningNet(55,84);opt=torch.optim.Adam(model.parameters(),lr=3e-4)
    optimize(model,opt,norm,t,True);checkpoint(tmp_path/'state.pt',model,opt,norm,phase='imitation',seed=101)
    restored,optimizer,norm2,state=load(tmp_path/'state.pt')
    optimize(model,opt,norm,t,True);optimize(restored,optimizer,norm2,t,True)
    for a,b in zip(model.parameters(),restored.parameters()):torch.testing.assert_close(a,b,rtol=0,atol=0)


def test_search_public_only_repeatable_and_expansion_budget(tmp_path):
    torch.set_num_threads(1);torch.manual_seed(101)
    b=build_micro(micro_entries()[0],tmp_path/'bundle');t=episode(b,tmp_path/'run',stage=2,models=False)
    frame=frame_from(t);norm=Normalizer.fit([t]);model=PlanningNet(55,84)
    sampler=FutureSampler(dict(ratio_quantiles=[1.]*101))
    for budget,depth in [(16,2),(64,3)]:
        left=SearchPolicy(Policy(model,norm),sampler,budget,depth)(frame)
        modified=copy.deepcopy(frame);modified['seed']=5;modified['truth']={'final_weather':2};modified['teacher']=999
        right=SearchPolicy(Policy(model,norm),sampler,budget,depth)(modified)
        assert left==right and left[1]['search_nodes']==budget
        assert frame['now_utc']==modified['now_utc']


def test_failure_during_public_proposal_exits_children(tmp_path):
    # A large public candidate frame used to block cleanup on a full stdout pipe.
    entry={**micro_entries()[0],'steps':5}
    b=build_micro(entry,tmp_path/'bundle')
    import csv
    catalogue=b/'public/targets.csv'
    with catalogue.open() as f:rows=list(csv.DictReader(f));fields=list(rows[0])
    enlarged=rows+[{**row,'target_id':f'{row["target_id"]}-{i}','ra_deg':float(row['ra_deg'])+i*.001} for i in range(60) for row in rows]
    with catalogue.open('w') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(enlarged)
    def fail(frame):raise RuntimeError('intentional caller failure')
    with pytest.raises(RuntimeError,match='intentional caller failure'):
        episode(b,tmp_path/'failed',fail,stage=3,models=False)
    assert not (tmp_path/'failed/episode.json').exists()
    assert json.loads((tmp_path/'failed/error.json').read_text())['status']=='failed'


def test_two_simulators_preserve_independent_policy_randomness(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    torch.set_num_threads(1);torch.manual_seed(101)
    b=build_micro(micro_entries()[0],tmp_path/'bundle')
    teacher=episode(b,tmp_path/'teacher',models=False)
    model=PlanningNet(55,84);norm=Normalizer.fit([teacher])
    def run(name):
        return episode(b,tmp_path/name,Policy(model,norm,sample=True,seed=202),stage=2,key='seed202',models=False)
    serial=run('serial')
    with ThreadPoolExecutor(max_workers=2) as pool:parallel=list(pool.map(run,['parallel0','parallel1']))
    def actions(t):
        with gzip.open(t.folder/'trace.jsonl.gz','rt') as f:
            return [r['action'] for line in f if (r:=json.loads(line))['type']=='action']
    assert all(actions(t)==actions(serial) and t.summary['total']==serial.summary['total'] for t in parallel)


def test_fit_can_resume_finished_imitation_without_replaying_scenes(tmp_path):
    from research.global_planning.staged.training import fit
    torch.set_num_threads(1)
    entry=micro_entries()[0];scene=build_micro(entry,tmp_path/'scenes'/entry['id'])
    teacher=episode(scene,tmp_path/'teacher',models=False)
    validation=[entry];output=tmp_path/'fit'
    first=fit([teacher],[entry],validation,tmp_path/'scenes',output,2,rounds=0)
    sha=digest(first);episode_path=output/'imitation-validation'/entry['id']/'episode.json';mtime=episode_path.stat().st_mtime_ns
    second=fit([teacher],[entry],validation,tmp_path/'scenes',output,2,rounds=0)
    assert digest(second)==sha and episode_path.stat().st_mtime_ns==mtime


def test_more_control_requires_registered_gain_and_no_card_regression():
    from research.global_planning.staged.experiment import select_control
    previous=dict(stage=2,rows=[dict(id=c,card=c,total=100.,completion='survey_complete') for c in 'abcd'])
    marginal=dict(stage=3,rows=[dict(r,total=100.5) for r in previous['rows']])
    assert select_control(previous,marginal) is previous
    improvement=dict(stage=3,rows=[dict(r,total=101.5) for r in previous['rows']])
    assert select_control(previous,improvement) is improvement
    regression=dict(stage=4,rows=[dict(r,total=98 if r['card']=='a' else 110) for r in improvement['rows']])
    assert select_control(improvement,regression) is improvement


def test_same_inference_is_reused_when_checkpoint_progress_changes(tmp_path,monkeypatch):
    from research.global_planning.staged.training import evaluate
    import research.global_planning.staged.rollout as rollout
    torch.set_num_threads(1);torch.manual_seed(101)
    entry=micro_entries()[0];scenes=tmp_path/'scenes';b=build_micro(entry,scenes/entry['id'])
    teacher=episode(b,tmp_path/'teacher',models=False);norm=Normalizer.fit([teacher]);model=PlanningNet(55,84)
    opt=torch.optim.Adam(model.parameters(),lr=3e-4)
    checkpoint(tmp_path/'first.pt',model,opt,norm,seed=101,round=1)
    first=evaluate(tmp_path/'first.pt',[entry],scenes,tmp_path/'eval/first',2)
    checkpoint(tmp_path/'second.pt',model,opt,norm,seed=101,round=2)
    assert digest(tmp_path/'first.pt')!=digest(tmp_path/'second.pt')
    def forbidden(*args,**kwargs):raise AssertionError('Completed inference must not start another engine')
    monkeypatch.setattr(rollout,'Environment',forbidden)
    second=evaluate(tmp_path/'second.pt',[entry],scenes,tmp_path/'eval/second',2)
    assert first[0]['total']==second[0]['total'] and second[0]['reused_from'] is not None


def test_same_output_cannot_have_two_active_experiments(tmp_path):
    from research.global_planning.staged.experiment import experiment_lock
    with experiment_lock(tmp_path):
        with pytest.raises(RuntimeError,match='already running'):
            with experiment_lock(tmp_path):pass
    with experiment_lock(tmp_path):pass  # A stale file is not a stale owner.

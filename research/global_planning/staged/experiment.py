from __future__ import annotations
import argparse,copy,json,os,platform,shutil,subprocess,sys,time,fcntl
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import numpy as np
import torch
from ..common import ROOT,save,digest,verify_engine
from ..gates import compare
from ..data import build_micro,micro_entries
from .data import entries,build
from .rollout import AGENT,episode,rule,expanded_rule,Trajectory
from .training import CONFIG,SIMULATORS,fit,evaluate,Policy,Routed,DetailTeacher,load,policy_factory
from .search import FutureSampler,SearchPolicy
from .entry import export
from .acceptance import native_check,package
from .export_runtime import materialize

@contextmanager
def experiment_lock(output):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    with (output/'execution.lock').open('a+') as handle:
        try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError(f'Experiment is already running: {output}')
        handle.seek(0);handle.truncate();handle.write(json.dumps(dict(pid=os.getpid())));handle.flush()
        try:yield
        finally:fcntl.flock(handle,fcntl.LOCK_UN)

def source_hashes():
    paths=list((ROOT/'research/global_planning').glob('*.py'))+list(Path(__file__).parent.glob('*.py'))+list((AGENT/'src').rglob('*.ts'))
    return {str(p.relative_to(ROOT)):digest(p) for p in sorted(paths)}

def select_control(accepted,candidate):
    candidate['comparison_accepted']=compare(candidate['rows'],accepted['rows'])
    return candidate if candidate['comparison_accepted']['promotion_passed'] else accepted

def references(items,scenarios,out,stage,policy_factory=lambda:rule,key='rule',node=None):
    def run_one(item):
        t=episode(scenarios/item['id'],out/item['id'],policy_factory(),stage=stage,key=key,node=node)
        t.summary['entry']=item
        row=dict(id=item['id'],card=item['card'],total=t.summary['total'],completion=t.summary['termination']['reason'],components=t.summary['components'],
                 wall_seconds=t.summary['wall_seconds'],node_cpu=t.summary['node_cpu'],node_peak_rss=t.summary['node_peak_rss'])
        print(json.dumps(dict(phase=str(out),scene=item['id'],total=t.summary['total'],seconds=t.summary['wall_seconds'])),flush=True)
        return t,row
    trajectories=[];rows=[]
    with ThreadPoolExecutor(max_workers=SIMULATORS) as pool:
        for t,row in pool.map(run_one,items):
            trajectories.append(t);rows.append(row);save(out/'summary.json',dict(rows=rows,complete=len(rows)==len(items)))
    return trajectories,rows

def run(output,node=None):
    output=Path(output);output.mkdir(parents=True,exist_ok=True);torch.set_num_threads(1)
    small_path=output/'small-stages/state.json'
    if not small_path.exists():
        subprocess.run([sys.executable,'-m','research.global_planning.staged.small_experiment','--out',str(output/'small-stages')],cwd=ROOT,check=True)
    small_record=json.loads(small_path.read_text())
    if small_record.get('status')!='small_scene_evaluations_complete':raise ValueError('Complete the small-scene pilot before formal expansion')
    specification=dict(schema='global-planning-staged-v2',authorization='User explicitly requested continuing stages 2-6 despite stage1 failure',
        stage1_result=digest(ROOT/'artifacts/global-planning-v1/stage1-result.json'),sources=source_hashes(),engine=verify_engine(),config=CONFIG,
        data=entries(),small_result_sha256=digest(small_path),simulators=SIMULATORS,pilot=dict(training='One complete scene per card',validation='One complete scene per card',imitation_epochs=2,ppo_rounds=1),
        scale_up='Only >=1% validation improvement and no card worse than -1%; otherwise retain smaller experiment',
        final='Freeze before independent formal test/stress generation; native execution only per user request',search=[dict(expansions=16,depth=2),dict(expansions=64,depth=3)],
        environment=dict(python=sys.version,torch=torch.__version__,numpy=np.__version__,platform=platform.platform()))
    protocol=output/'protocol.json'
    if protocol.exists() and json.loads(protocol.read_text())!=specification:raise ValueError('Source/config changed: use a new experiment output')
    save(protocol,specification)
    manifest=entries();scenarios=output/'scenarios'
    training=[e for e in manifest if e['split']=='train' and e['id'].endswith('-0')]
    validation=[e for e in manifest if e['split']=='validation' and e['id'].endswith('-0')]
    for item in training+validation:build(item,scenarios/item['id'])
    state=dict(status='running',stage=2,results={});save(output/'state.json',state)
    teachers,baseline=references(training,scenarios,output/'stage2/teacher/train',2,node=node)
    _,baseline_val=references(validation,scenarios,output/'stage2/teacher/validation',2,node=node)
    candidates=[]
    for architecture in ('gru','mlp'):
        path=fit(teachers,training,validation,scenarios,output/'stage2'/architecture,2,node=node,architecture=architecture)
        rows=evaluate(path,validation,scenarios,output/'stage2'/architecture/'selected-validation',2,node=node)
        candidates.append(dict(stage=2,architecture=architecture,path=str(path),comparison=compare(rows,baseline_val),rows=rows))
    best=max(candidates,key=lambda c:np.mean([r['total'] for r in c['rows']]))
    teacher_by_stage={2:teachers};state['results']['2']=candidates;save(output/'state.json',state)
    if best['comparison']['promotion_passed']:
        expanded_train=[e for e in manifest if e['split']=='train'];expanded_val=[e for e in manifest if e['split']=='validation']
        for item in expanded_train+expanded_val:build(item,scenarios/item['id'])
        teachers,_=references(expanded_train,scenarios,output/'stage2/teacher/train',2,node=node)
        _,baseline_val=references(expanded_val,scenarios,output/'stage2/teacher/validation',2,node=node)
        training,validation=expanded_train,expanded_val;teacher_by_stage[2]=teachers
        path=fit(teachers,training,validation,scenarios,Path(best['path']).parent,2,rounds=10,node=node,architecture=best['architecture'])
        rows=evaluate(path,validation,scenarios,output/'stage2/expanded-selected-validation',2,node=node)
        best={**best,'path':str(path),'rows':rows,'comparison':compare(rows,baseline_val)}
    all_candidates=[best];eligible=[best];previous=best
    small_path=output/'small-stages/state.json'
    small=json.loads(small_path.read_text()) if small_path.exists() else None
    for stage in (3,4):
        if small and small.get('status')=='small_scene_evaluations_complete':
            small_result=small['results'][str(stage)]
            small_reference=max(small['results']['2'],key=lambda r:np.mean([row['total'] for row in r['rows']]))
            gate=compare(small_result['rows'],small_reference['rows'])
            if not gate['promotion_passed']:
                state['results'][str(stage)]=dict(status='not_expanded_small_scene_gate_failed',small_scene_comparison=gate,
                    reason='Added control did not improve the accepted finite-choice model on the independent small-scene pilot')
                save(output/'state.json',state);continue
        state['stage']=stage;save(output/'state.json',state)
        # Higher-control pilots continue under the user's new instruction, even
        # if the previous pilot did not pass. Adoption gates remain unchanged.
        if stage==4 and previous['stage']!=3:
            state['results']['4']=dict(status='not_expanded_upstream_not_validated',reason='Keep the accepted finite-choice model');save(output/'state.json',state);continue
        upstream=previous['path'] if stage==4 else None
        if stage==4:
            teacher_factory=lambda upstream=upstream:DetailTeacher(policy_factory(upstream))
        else:teacher_factory=lambda:rule
        teacher_rows,_=references(training,scenarios,output/f'stage{stage}/teacher/train',stage,teacher_factory,key=f'teacher-stage{stage}:{digest(Path(upstream)) if upstream else "rule"}',node=node)
        _,range_rule=references(validation,scenarios,output/f'stage{stage}/same-range-rule',stage,
            policy_factory=(lambda:expanded_rule) if stage==3 else teacher_factory,key=f'same-range-stage{stage}',node=node)
        teacher_by_stage[stage]=teacher_rows
        path=fit(teacher_rows,training,validation,scenarios,output/f'stage{stage}/gru',stage,upstream=upstream,node=node)
        rows=evaluate(path,validation,scenarios,output/f'stage{stage}/selected-validation',stage,upstream=upstream,node=node)
        result=dict(stage=stage,path=str(path),upstream=upstream,rows=rows,comparison_previous=compare(rows,previous['rows']),
                    comparison_baseline=compare(rows,baseline_val),comparison_same_range=compare(rows,range_rule))
        state['results'][str(stage)]=result;save(output/'state.json',state)
        all_candidates.append(result)
        if select_control(eligible[-1],result) is result:eligible.append(result)
        previous=result
        # Keep the chosen upper policy frozen for details, even when stage3
        # does not improve. Interface stage is recorded explicitly below.
    sampler=FutureSampler.fit(teacher_by_stage[2]);save(output/'stage5/sampler.json',sampler.state)
    val_traces=[Trajectory(output/'stage2/teacher/validation'/e['id']) for e in validation]
    audit=sampler.audit(val_traces);save(output/'stage5/sampler-audit.json',audit)
    search_rows=[]
    # Compare search on the best region/time policy. Fiber-level search is not
    # introduced; a details model remains fixed when testing it separately.
    direct=max((c for c in eligible if c['stage'] in (2,3)),key=lambda c:np.mean([r['total'] for r in c['rows']]))
    for expansions,depth in ([(16,2),(64,3)] if audit['adoption_ready'] else []):
        def factory():
            model,_,normalizer,_=load(direct['path']);return SearchPolicy(Policy(model,normalizer),sampler,expansions,depth)
        rows=evaluate(direct['path'],validation,scenarios,output/'stage5'/f'search-{expansions}',direct['stage'],
            key=digest(dict(weights=digest(Path(direct['path'])),sampler=sampler.state,expansions=expansions,depth=depth)),
            policy_factory_override=factory,node=node)
        result=dict(stage=5,path=direct['path'],interface_stage=direct['stage'],search=dict(expansions=expansions,depth=depth,sampler=sampler.state),
                    rows=rows,comparison_direct=compare(rows,direct['rows']),sampler_adoption_ready=audit['adoption_ready'])
        search_rows.append(result)
    state['results']['5']=search_rows or dict(status='not_expanded_future_sampler_unvalidated',reason=audit['reason'],small_scene_results=small['results']['5'] if small else None);save(output/'state.json',state)
    # No unvalidated sampled process may become the selected deployed policy.
    learned=eligible[-1]
    selected=dict(**learned,sha256=digest(Path(learned['path'])),selection='validation only; each added control must improve >=1% versus the accepted simpler model',
                  adoption_candidate=compare(learned['rows'],baseline_val)['promotion_passed'])
    freeze=output/'frozen.json'
    if freeze.exists() and json.loads(freeze.read_text())!=selected:raise ValueError('Frozen selection changed')
    save(freeze,selected)
    test=[e for e in manifest if e['split']=='test'];stress=[e for e in manifest if e['split']=='stress']
    for item in test+stress:build(item,scenarios/item['id'])
    _,test_rule=references(test,scenarios,output/'stage6/baseline/test',2,node=node)
    test_rows=evaluate(selected['path'],test,scenarios,output/'stage6/model/test',selected['stage'],upstream=selected.get('upstream'),node=node)
    _,stress_rule=references(stress,scenarios,output/'stage6/baseline/stress',2,node=node)
    stress_rows=evaluate(selected['path'],stress,scenarios,output/'stage6/model/stress',selected['stage'],upstream=selected.get('upstream'),node=node)
    test_comparison=compare(test_rows,test_rule);stress_comparison=compare(stress_rows,stress_rule)
    seeds=[]
    if selected['adoption_candidate']:
        for seed in (202,303):
            path=fit(teacher_by_stage[selected['stage']],training,validation,scenarios,output/'stage6'/f'seed-{seed}',selected['stage'],seed=seed,
                     upstream=selected.get('upstream'),node=node,architecture=selected.get('architecture','gru'))
            rows=evaluate(path,test,scenarios,output/'stage6'/f'seed-{seed}'/'test',selected['stage'],upstream=selected.get('upstream'),node=node)
            seeds.append(dict(seed=seed,comparison=compare(rows,test_rule)))
    materialize();config=export(AGENT/'models/global-planning',selected['path'],selected['stage'],selected.get('upstream'))
    smoke=build_micro(micro_entries()[7],output/'stage6/native-smoke')
    native=native_check(smoke,config,output/'stage6/native',node=node)
    if native['passed']:
        full_native=native_check(scenarios/test[0]['id'],config,output/'stage6/native-full',node=node,full_schedule=True)
        native['formal_complete_schedule_passed']=full_native['passed'];save(output/'stage6/native-full-result.json',full_native)
    archive=package(config,output/'stage6/package')
    confirmed=(int(test_comparison['mean_difference']>0)+sum(s['comparison']['mean_difference']>0 for s in seeds)>=2
        and len(seeds)==2 and test_comparison['interval_95'][0]>0 and stress_comparison['relative_improvement']>=-.02
        and test_comparison['complete'] and stress_comparison['complete'] and native['passed'] and archive['passed']
        and native.get('formal_complete_schedule_passed',False))
    if confirmed:
        # Activate only the separately verified copy; original agents stay unchanged.
        project=AGENT/'observer.project.json';original_project=json.loads(project.read_text())
        activated={**original_project,'run':['node','dist/global-entry.js']}
        settings=json.loads(config.read_text());settings['selected_for_adoption']=True
        save(project,activated);save(config,settings);archive=package(config,output/'stage6/package')
        if not archive['passed']:
            confirmed=False;settings['selected_for_adoption']=False;save(project,original_project);save(config,settings)
            archive=package(config,output/'stage6/package')
    final=dict(native_full_schedule_pending=not native.get('formal_complete_schedule_passed',False),test=test_comparison,stress=stress_comparison,seeds=seeds,native=native,package=archive,adopted=confirmed,
               interpretation='Local self-generated comparisons only; not an online score prediction')
    save(output/'stage6/result.json',final);state.update(stage=6,status='evaluations_complete_native_entry_pending' if final['native_full_schedule_pending'] else 'complete',final=final);save(output/'state.json',state)
    render(output,state);return state

def render(output,state):
    lines=['# 全局规划第 2–6 层实测','',f"状态：{state['status']}。",'所有完整赛程采用正式公开目标/日历及自生成隐藏条件，原公开数据保持原样。','']
    lines+=['| 层次 | 比较 | 平均变化 | 每卡变化 |','|---|---|---:|---|']
    for layer,results in state['results'].items():
        for r in results if isinstance(results,list) else [results]:
            comparison=r.get('comparison',r.get('comparison_previous',r.get('comparison_direct')))
            if comparison is None:
                lines.append(f"| {layer} | 未扩大 | — | {r.get('reason',r.get('status'))} |");continue
            lines.append(f"| {layer} | {r.get('architecture',r.get('search',{}).get('expansions','上一层'))} | {comparison['relative_improvement']:.2%} | {comparison['by_card']} |")
    lines+=['','完整分项、时间、内存、动作、公开反馈、配套模型版本、权重和数据来源在同目录。',
            '试点每卡一场训练/验证，2 轮模仿、1 轮 PPO；达到门槛才扩到原定 16/8 及 10 轮，规模变更在 protocol.json 预先固定。',
            '搜索未来抽样仅为公开分数残差近似，未验证完整天气与事件过程；搜索效果即使提高也不会因此自动采用。',
            '按用户要求仅在本机运行，容器验收不属于本次范围；本机小场景入口检查不能代替完整正式赛程入口检查。']
    if 'final' in state:lines.append(f"最终采用：{state['final']['adopted']}。未满足全部条件时保留原基线。")
    (output/'REPORT.md').write_text('\n'.join(lines)+'\n')

def main():
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,default=ROOT/'artifacts/global-planning-v2');p.add_argument('--node');a=p.parse_args()
    try:
        with experiment_lock(a.out):run(a.out,a.node)
    except BaseException as error:
        if 'Experiment is already running:' in str(error):raise
        state_path=a.out/'state.json'
        state=json.loads(state_path.read_text()) if state_path.exists() else dict(stage='initialization',results={})
        state.update(status='interrupted' if isinstance(error,KeyboardInterrupt) else 'failed',error=repr(error))
        save(a.out/'failure.json',dict(status=state['status'],stage=state['stage'],error=repr(error)))
        save(state_path,state);render(a.out,state);raise

if __name__=='__main__':main()

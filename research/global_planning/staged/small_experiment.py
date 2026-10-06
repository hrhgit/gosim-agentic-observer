"""Independent SELF-GENERATED small-scene pilot; never a formal-score claim."""
import sys,json,copy,csv,shutil,argparse
from pathlib import Path
import numpy as np,torch
from research.global_planning.data import build_micro,micro_entries
from research.global_planning.common import ROOT,save,digest,verify_engine
from research.global_planning.staged import training
from research.global_planning.staged.training import fit,evaluate,policy_factory,DetailTeacher,Policy,load
from research.global_planning.staged.rollout import episode,rule,expanded_rule,Trajectory
from research.global_planning.staged.experiment import source_hashes,experiment_lock
from research.global_planning.staged.search import FutureSampler,SearchPolicy
from research.global_planning.gates import compare
def run(output):
 training.SIMULATORS=1;torch.set_num_threads(1)
 out=Path(output);scenes=out/'scenarios';out.mkdir(parents=True,exist_ok=True)
 entries=[{**e,'id':e['id'].replace('micro','staged-small'),'seed':e['seed']+400000,'steps':12,'source':'SELF-GENERATED small staged pilot'} for e in micro_entries()]
 protocol=dict(engine=verify_engine(),sources=source_hashes(),entries=entries,config=training.CONFIG,simulators=1,
   geometry='Four fibers; six sky groups, two targets per group; legal exposure 60-180 seconds; 12 minute nights',
   ppo_grouping='Four artificial curriculum groups cycle across small scenes, not formal-card labels',
   selection='Validation only; test generated after weights frozen; small scenes are not formal-card evaluations',
   host_cost_note='Formal pilot runs concurrently; elapsed timings are not isolated hardware benchmarks')
 if (out/'protocol.json').exists():assert json.loads((out/'protocol.json').read_text())==protocol
 save(out/'protocol.json',protocol)
 def build(e):
  b=scenes/e['id']
  if (b/'source.json').exists():
   record=json.loads((b/'source.json').read_text());assert record['entry']==e
   if record.get('generator')=='staged-micro-v2':
    for p,h in record['hashes'].items():assert digest(b/p)==h
    return b
   shutil.rmtree(b)
  build_micro(e,b)
  parent=out/'provenance'/e['id']/'parent-source.json';parent.parent.mkdir(parents=True,exist_ok=True)
  shutil.copyfile(b/'source.json',parent)
  cfg=b/'config/v4_fiber_config.json';c=json.loads(cfg.read_text());c['field']['n_fibers']=4;c['exposure']['max_duration_seconds']=180;save(cfg,c)
  p=b/'public/targets.csv'
  with p.open() as f:rows=list(csv.DictReader(f));fields=list(rows[0])
  alternatives=[{**r,'target_id':r['target_id']+'-alternative','ra_deg':float(r['ra_deg'])+.06,'required':'false','science_weight':float(r['science_weight'])*1.6,'feature_flux':float(r['feature_flux'])*.5} for r in rows]
  with p.open('w') as f:w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows+alternatives)
  save(b/'source.json',dict(entry=e,official_hidden_data=False,generator='staged-micro-v2',
    parent_source_sha=digest(parent),description=protocol['geometry'],
    hashes={str(p.relative_to(b)):digest(p) for p in b.rglob('*') if p.is_file() and p.name!='source.json'}))
  return b
 def reference(items,folder,stage,factory=lambda:rule,key='rule'):
  traces=[];rows=[]
  for e in items:
   t=episode(build(e),folder/e['id'],factory(),stage=stage,key=key)
   traces.append(t);rows.append(dict(id=e['id'],card=e['card'],total=t.summary['total'],completion=t.summary['termination']['reason'],wall_seconds=t.summary['wall_seconds']))
  save(folder/'summary.json',dict(rows=rows,complete=True));return traces,rows
 train=[e for e in entries if e['split']=='train'];val=[e for e in entries if e['split']=='validation'];test=[e for e in entries if e['split']=='test']
 for e in train+val:build(e)
 ppo_entries=[{**e,'card':'abcd'[i%4]} for i,e in enumerate(train)]
 state=dict(status='running',scale='SELF-GENERATED SMALL SCENES ONLY',results={});save(out/'state.json',state)
 teachers,_=reference(train,out/'stage2/teacher/train',2)
 _,baseline=reference(val,out/'stage2/teacher/validation',2)
 options=[]
 for architecture in ['gru','mlp']:
  p=fit(teachers,ppo_entries,val,scenes,out/f'stage2/{architecture}',2,architecture=architecture)
  rows=evaluate(p,val,scenes,out/f'stage2/{architecture}/selected-validation',2)
  options.append(dict(stage=2,architecture=architecture,path=str(p),rows=rows,comparison=compare(rows,baseline)))
 state['results']['2']=options;save(out/'state.json',state)
 previous=max(options,key=lambda c:np.mean([r['total'] for r in c['rows']]));all_options=[previous]
 for stage in (3,4):
  upstream=previous['path'] if stage==4 else None
  factory=(lambda:DetailTeacher(policy_factory(upstream))) if upstream else (lambda:rule)
  teachers,_=reference(train,out/f'stage{stage}/teacher/train',stage,factory,key=f'teacher:{digest(Path(upstream)) if upstream else "rule"}')
  _,same_range=reference(val,out/f'stage{stage}/same-range-rule',stage,(lambda:expanded_rule) if stage==3 else factory,key=f'range-{stage}')
  p=fit(teachers,ppo_entries,val,scenes,out/f'stage{stage}/gru',stage,upstream=upstream)
  rows=evaluate(p,val,scenes,out/f'stage{stage}/selected-validation',stage,upstream=upstream)
  result=dict(stage=stage,path=str(p),upstream=upstream,rows=rows,comparison=compare(rows,previous['rows']),comparison_baseline=compare(rows,baseline),comparison_same_range=compare(rows,same_range))
  state['results'][str(stage)]=result;save(out/'state.json',state);previous=result;all_options.append(result)
 small_teacher=[Trajectory(out/'stage2/teacher/train'/e['id']) for e in train]
 sampler=FutureSampler.fit(small_teacher);audit=sampler.audit([Trajectory(out/'stage2/teacher/validation'/e['id']) for e in val]);save(out/'stage5/sampler-audit.json',audit)
 eligible=[all_options[0]]
 for c in all_options[1:]:
  if compare(c['rows'],eligible[-1]['rows'])['promotion_passed']:eligible.append(c)
 direct=max([c for c in eligible if c['stage'] in (2,3)],key=lambda c:np.mean([r['total'] for r in c['rows']]))
 search=[]
 for budget,depth in [(16,2),(64,3)]:
  def factory():
   model,_,norm,_=load(direct['path']);return SearchPolicy(Policy(model,norm),sampler,budget,depth)
  rows=evaluate(direct['path'],val,scenes,out/f'stage5/search-{budget}',direct['stage'],key=f'search-{budget}:{digest(Path(direct["path"]))}',policy_factory_override=factory)
  search.append(dict(stage=5,expansions=budget,comparison=compare(rows,direct['rows']),sampler_adoption_ready=False))
 state['results']['5']=search
 selected=eligible[-1];save(out/'frozen.json',selected)
 _,baseline_test=reference(test,out/'stage6/baseline/test',2)
 rows=evaluate(selected['path'],test,scenes,out/'stage6/model/test',selected['stage'],upstream=selected.get('upstream'))
 state.update(status='small_scene_evaluations_complete',final=dict(selected_stage=selected['stage'],comparison=compare(rows,baseline_test),formal_adoption_proven=False))
 save(out/'state.json',state)
 lines=['# 第2–6层小场景结果','','数据完全自行生成；不能代替完整正式赛程比较。','','|层次|设置|验证平均变化|比较对象|','|---|---|---:|---|']
 for stage,result in state['results'].items():
  for r in result if isinstance(result,list) else [result]:lines.append(f"|{stage}|{r.get('architecture',r.get('expansions','GRU'))}|{r['comparison']['relative_improvement']:.2%}|{'原规则' if stage=='2' else '上一层/不搜索版本'}|")
 lines+=['',f"冻结第{selected['stage']}层；独立16场小场景平均变化：{state['final']['comparison']['relative_improvement']:.2%}。",'搜索未验证完整未来天气与事件过程，不能据此采用。','正式完整赛程在另一目录继续运行。']
 (out/'REPORT.md').write_text('\n'.join(lines)+'\n');print(json.dumps(state['final']),flush=True)

def main():
 p=argparse.ArgumentParser();p.add_argument('--out',type=Path,default=ROOT/'artifacts/global-planning-v2/small-stages');a=p.parse_args()
 with experiment_lock(a.out):run(a.out)
if __name__=='__main__':main()

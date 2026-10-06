from __future__ import annotations
import json, random, time, hashlib, shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import numpy as np
import torch
from torch.distributions import Categorical
from torch.nn import functional as F
from ..common import save,digest
from ..model import PlanningNet
from .features import Normalizer
from .rollout import episode,Trajectory,rule,expanded_rule
SIMULATORS = 2  # Prior one/two-worker test: identical actions, lower total wall time.
CONFIG=dict(learning_rate=3e-4,clip=.2,updates_per_batch=4,gamma=1.,value_weight=.5,entropy_weight=.01,
            gradient_clip=.5,history_gradient_steps=64,return_scale=10000.,pilot_imitation_epochs=2,
            pilot_ppo_rounds=1,expanded_ppo_rounds=10,device='cpu',threads=1)

class Policy:
    def __init__(self,model,normalizer,*,sample=False,seed=101):
        self.model,self.normalizer,self.sample=model,normalizer,sample
        self.memory=model.initial();self.generator=torch.Generator().manual_seed(seed)
        raw=b''.join(t.detach().cpu().numpy().tobytes() for t in model.state_dict().values())
        self.version='global-planning-v2-'+hashlib.sha256(raw).hexdigest()[:16]
    def predict(self,frame,memory=None):
        x,c=self.normalizer.apply(frame)
        with torch.no_grad():return self.model(torch.from_numpy(x),torch.from_numpy(c),self.memory if memory is None else memory)
    def __call__(self,frame):
        logits,value,self.memory=self.predict(frame)
        selected=int(torch.multinomial(logits.softmax(-1),1,generator=self.generator)) if self.sample else int(logits.argmax())
        return selected,dict(log_probability=float(logits.log_softmax(-1)[selected]),action_probabilities=logits.softmax(-1).tolist(),value=float(value)*CONFIG['return_scale'],model_version=self.version,trainable=True)

class Routed:
    """Stage 4 freezes upstream choices; only program/fiber decisions are learned."""
    def __init__(self,upstream,details):self.upstream,self.details=upstream,details
    def __call__(self,frame):
        if frame['kind'] in ('program','fiber'):return self.details(frame)
        index,info=self.upstream(frame);return index,{**info,'trainable':False}

class DetailTeacher:
    def __init__(self,upstream):self.upstream=upstream
    def __call__(self,frame):
        if frame['kind'] in ('program','fiber'):return frame['teacher'],dict(trainable=True)
        index,info=self.upstream(frame);return index,{**info,'trainable':False}

def checkpoint(path,model,optimizer,normalizer,**state):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);temporary=path.with_suffix('.tmp')
    torch.save(dict(model_config=model.config,model=model.state_dict(),optimizer=optimizer.state_dict(),normalizer=normalizer.state,
        config=CONFIG,torch_rng=torch.get_rng_state(),python_rng=random.getstate(),**state),temporary);temporary.replace(path)
def load(path):
    data=torch.load(path,map_location='cpu',weights_only=True)
    if data['config']!=CONFIG:raise ValueError('Training config mismatch')
    model=PlanningNet(**data['model_config']);model.load_state_dict(data['model'])
    optimizer=torch.optim.Adam(model.parameters(),lr=CONFIG['learning_rate']);optimizer.load_state_dict(data['optimizer'])
    torch.set_rng_state(data['torch_rng']);random.setstate(data['python_rng'])
    reserved={'model_config','model','optimizer','normalizer','config','torch_rng','python_rng'}
    return model,optimizer,Normalizer(data['normalizer']),{k:v for k,v in data.items() if k not in reserved}

def optimize(model,optimizer,normalizer,trajectory,imitation,stats=None):
    memory=model.initial();optimizer.zero_grad();losses=[];count=trajectory.summary['choices'];totals=dict(policy=0.,value=0.,count=count)
    if count==0:raise ValueError('No trainable choices')
    for i,row in enumerate(trajectory.rows()):
        x,c=normalizer.apply_arrays(row['x'],row['c']);logits,value,memory=model(torch.from_numpy(x),torch.from_numpy(c),memory)
        value_loss=F.mse_loss(value,torch.tensor(row['return_']/CONFIG['return_scale'],dtype=torch.float32))
        if imitation:loss=F.cross_entropy(logits[None,:],torch.tensor([row['teacher']]))
        else:
            advantage=(row['return_']-row['value'])/CONFIG['return_scale'];advantage=(advantage-stats[0])/max(stats[1],1e-6)
            distribution=Categorical(logits=logits);ratio=(distribution.log_prob(torch.tensor(row['selected']))-row['log_probability']).exp()
            loss=-torch.minimum(ratio*advantage,ratio.clamp(.8,1.2)*advantage)-CONFIG['entropy_weight']*distribution.entropy()
        losses.append(loss+CONFIG['value_weight']*value_loss)
        totals['policy']+=float(loss.detach());totals['value']+=float(value_loss.detach())
        if len(losses)==CONFIG['history_gradient_steps'] or i+1==count:
            torch.stack(losses).mean().backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(),CONFIG["gradient_clip"]);optimizer.step();optimizer.zero_grad()
            memory=memory.detach();losses=[]
    return totals

def policy_factory(path,*,upstream=None,sample=False,seed=101):
    model,_,normalizer,_=load(path);policy=Policy(model,normalizer,sample=sample,seed=seed)
    if upstream:
        upper_model,_,upper_norm,_=load(upstream)
        policy=Routed(Policy(upper_model,upper_norm),policy)
    return policy

def policy_identity(policy):
    if isinstance(policy,Routed):return dict(upper=policy_identity(policy.upstream),details=policy_identity(policy.details))
    return dict(weights=policy.version,normalizer=policy.normalizer.state,model=policy.model.config)

def evaluate(path,entries,scenarios,output,stage,*,upstream=None,node=None,key=None,policy_factory_override=None):
    # Completed deterministic results can be reused across checkpoint metadata
    # changes. Exact public bundle, weights, normalization and bridge must match.
    output=Path(output);cached=list(output.parent.rglob('episode.json')) if output.parent.exists() else []
    def run_one(entry):
        policy=policy_factory_override() if policy_factory_override else policy_factory(path,upstream=upstream)
        identity=key or digest(policy_identity(policy));target=output/entry['id'];reused_from=None
        expected=dict(bundle_source=digest(scenarios/entry['id']/'source.json'),policy=identity,stage=stage,models=True,rule_only=False)
        from .rollout import AGENT
        expected.update(compiled_bridge=digest(AGENT/'dist/tools/planning-bridge.js'),compiled_agent=digest(AGENT/'dist/agent.js'),
                        compiled_planner=digest(AGENT/'dist/core/planner.js'),compiled_planning=digest(AGENT/'dist/planning.js'),features=digest(Path(__file__).parent/'features.py'))
        if not target.exists():
            for existing in cached:
                stored=json.loads(existing.read_text())
                if stored['identity']==expected:
                    shutil.copytree(existing.parent,target,copy_function=shutil.copy2);reused_from=str(existing.parent);break
        t=episode(scenarios/entry['id'],output/entry['id'],policy,stage=stage,key=identity,node=node)
        row=dict(id=entry['id'],card=entry['card'],total=t.summary['total'],completion=t.summary['termination']['reason'],
                 components=t.summary['components'],wall_seconds=t.summary['wall_seconds'],node_cpu=t.summary['node_cpu'],
                 node_peak_rss=t.summary['node_peak_rss'],trainer_peak_rss=t.summary['trainer_peak_rss'],reused_from=reused_from)
        print(json.dumps(dict(phase=str(output),scene=entry['id'],total=row['total'],wall_seconds=row['wall_seconds'])),flush=True)
        return row
    rows=[]
    with ThreadPoolExecutor(max_workers=SIMULATORS) as pool:
        for row in pool.map(run_one,entries):
            rows.append(row);save(output/'summary.json',dict(rows=rows,complete=len(rows)==len(entries)))
    return rows

def fit(teacher,training_entries,validation,scenarios,output,stage,*,seed=101,rounds=1,upstream=None,node=None,architecture='gru'):
    torch.set_num_threads(1);torch.manual_seed(seed);random.seed(seed);output.mkdir(parents=True,exist_ok=True)
    resume=output/'resume.pt';history_path=output/'history.json'
    if resume.exists():model,optimizer,normalizer,state=load(resume)
    else:
        normalizer=Normalizer.fit(teacher);model=PlanningNet(len(normalizer.state['global']['mean']),len(normalizer.state['candidate']['mean']),architecture)
        optimizer=torch.optim.Adam(model.parameters(),lr=CONFIG['learning_rate'])
        state=dict(phase='imitation',epoch=0,round=0,seed=seed,stage=stage,best_mean=-float('inf'),best_phase='none')
    if state['seed']!=seed or state['stage']!=stage:raise ValueError('Resume identity mismatch')
    history=json.loads(history_path.read_text()) if history_path.exists() else []
    validation_ids=[e['id'] for e in validation]
    old_summary=output/'imitation-validation/summary.json'
    if state.get('validation_ids') is None and old_summary.exists():
        state['validation_ids']=[r['id'] for r in json.loads(old_summary.read_text())['rows']]
    if state.get('validation_ids') != validation_ids:
        # Score populations change when the pilot expands: refresh the old best
        # on exactly the new validation scenes before comparing new weights.
        if state['phase']!='imitation' and (output/'best.pt').exists():
            torch_rng,python_rng=torch.get_rng_state(),random.getstate()
            rows=evaluate(output/'best.pt',validation,scenarios,output/'population-refresh'/digest(validation_ids),stage,upstream=upstream,node=node)
            state['best_mean']=float(np.mean([r['total'] for r in rows]))
            best_model,best_opt,best_norm,best_state=load(output/'best.pt')
            best_state.update(best_mean=state['best_mean'],validation_ids=validation_ids)
            checkpoint(output/'best.pt',best_model,best_opt,best_norm,**best_state)
            torch.set_rng_state(torch_rng);random.setstate(python_rng)
            history.append(dict(phase='validation_population_refresh',validation_ids=validation_ids,best_mean=state['best_mean']))
        state['validation_ids']=validation_ids
        checkpoint(resume,model,optimizer,normalizer,**state);save(history_path,history)
    if state['phase']=='imitation':
        for epoch in range(state['epoch'],CONFIG['pilot_imitation_epochs']):
            order=list(range(len(teacher)));random.shuffle(order)
            before=time.perf_counter();losses=[optimize(model,optimizer,normalizer,teacher[i],True) for i in order]
            state['epoch']=epoch+1;history.append(dict(phase='imitation',epoch=epoch+1,losses=losses,seconds=time.perf_counter()-before))
            checkpoint(resume,model,optimizer,normalizer,**state);save(history_path,history)
        state['phase']='ppo';checkpoint(output/'imitation.pt',model,optimizer,normalizer,**state)
        rows=evaluate(output/'imitation.pt',validation,scenarios,output/'imitation-validation',stage,upstream=upstream,node=node)
        state['best_mean']=float(np.mean([r['total'] for r in rows]));state['best_phase']='imitation'
        checkpoint(output/'best.pt',model,optimizer,normalizer,**state);checkpoint(resume,model,optimizer,normalizer,**state)
    for iteration in range(state['round'],rounds):
        batch=[];weight=digest(resume)
        chosen=[[e for e in training_entries if e['card']==card][iteration%len([e for e in training_entries if e['card']==card])] for card in sorted({e['card'] for e in training_entries})]
        def collect(pair):
            i,entry=pair
            policy=Policy(model,normalizer,sample=True,seed=seed+iteration*4+i)
            if upstream:
                upper,_,norm,_=load(upstream);policy=Routed(Policy(upper,norm),policy)
            t=episode(scenarios/entry['id'],output/'rollouts'/f'round-{iteration+1:03}'/entry['id'],policy,stage=stage,
                      key=digest(dict(weight=weight,entry=entry,iteration=iteration,seed=seed)),node=node)
            print(json.dumps(dict(stage=stage,round=iteration+1,scene=entry['id'],total=t.summary['total'])),flush=True)
            return t
        with ThreadPoolExecutor(max_workers=SIMULATORS) as pool:batch=list(pool.map(collect,enumerate(chosen)))
        adv=[(r['return_']-r['value'])/CONFIG['return_scale'] for t in batch for r in t.rows()];stats=float(np.mean(adv)),float(np.std(adv))
        losses=[]
        for _ in range(CONFIG['updates_per_batch']):
            order=list(range(len(batch)));random.shuffle(order)
            losses.extend(optimize(model,optimizer,normalizer,batch[i],False,stats) for i in order)
        state['round']=iteration+1;history.append(dict(phase='ppo',round=iteration+1,scores=[t.summary['total'] for t in batch],losses=losses))
        # The one-round pilot validates once; expanded runs validate at 5 and 10.
        if rounds==1 or (iteration+1)%5==0:
            path=output/f'round-{iteration+1:03}.pt';checkpoint(path,model,optimizer,normalizer,**state)
            rows=evaluate(path,validation,scenarios,output/f'validation-{iteration+1:03}',stage,upstream=upstream,node=node)
            mean=float(np.mean([r['total'] for r in rows]));history[-1]['validation_mean']=mean
            if mean>state['best_mean']:state['best_mean']=mean;state['best_phase']='ppo';checkpoint(output/'best.pt',model,optimizer,normalizer,**state)
        checkpoint(resume,model,optimizer,normalizer,**state);save(history_path,history)
    save(output/'selection.json',dict(seed=seed,stage=stage,architecture=architecture,rounds=rounds,best_phase=state['best_phase'],validation_mean=state['best_mean'],checkpoint_sha256=digest(output/'best.pt')))
    return output/'best.pt'

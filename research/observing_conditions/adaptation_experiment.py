"""One training run per architecture, independent calibration and sealed test."""
from __future__ import annotations
import argparse
import copy
import hashlib
import importlib.metadata
import json
from pathlib import Path
import time
import joblib
import numpy as np
import torch
from torch.nn.utils import clip_grad_norm_
from .adaptation_data import DEFAULT, entries, protocol, digest
from .adaptation_model import Normalizer, CorrectionNet, TreeCorrection, ResidualLookup, OnlineUpdate, infer_gru
from .information_model import training_loss, numeric, measure, calibration_fit, calibration_apply, selection_score, reliability_gate, NAMES
from .data import ROOT, save_json
from .features import utc

METHODS=('hold','foundation','lookup','gru','tree','gru_bias','gru_head')


def load_split(output,split):
    fields={k:[] for k in ('x','history','y','base','hold','coordinates')};scene=[];meta=[]
    for e in entries():
        if e['split']!=split:continue
        directory=Path(output)/'scenarios'/e['id']
        with np.load(directory/'samples.npz') as data:
            for k in fields:fields[k].append(data[k].copy())
            scene.extend([e['id']]*len(data['x']))
        meta.extend(json.loads((directory/'sample_metadata.json').read_text()))
    return {k:np.concatenate(v) for k,v in fields.items()}|dict(scene=np.array(scene),meta=meta)


def train(output):
    directory=Path(output)/'models';directory.mkdir(parents=True,exist_ok=True)
    if (directory/'training_complete.json').exists():return load_models(output)
    torch.set_num_threads(1);torch.manual_seed(101);np.random.seed(101)
    train=load_split(output,'train');valid=load_split(output,'validation')
    normalizer=Normalizer.fit(train);normalizer.save(directory/'normalizer.npz')
    scale=float(normalizer.values['quality_scale']);model=CorrectionNet(train['x'].shape[-1],train['history'].shape[-1],scale)
    optimizer=torch.optim.AdamW(model.parameters(),lr=.001,weight_decay=.0001)
    n=normalizer.apply(train);tensors={k:torch.from_numpy(v) for k,v in n.items()};y=torch.from_numpy(train['y'])
    history=[];best=float('inf');start_epoch=0;cpu_before=time.process_time();wall_before=time.perf_counter()
    checkpoint=directory/'checkpoint.pt'
    if checkpoint.exists():
        saved=torch.load(checkpoint,map_location='cpu',weights_only=False)
        model.load_state_dict(saved['model']);optimizer.load_state_dict(saved['optimizer'])
        history=saved['history'];best=saved['best'];start_epoch=saved['epoch'];torch.set_rng_state(saved['rng'])
    reference=measure(valid['y'],valid['hold'])
    for epoch in range(start_epoch,10):
        model.train();order=torch.randperm(len(y));losses=[];began=time.perf_counter()
        for first in range(0,len(y),512):
            ix=order[first:first+512]
            out=model(*[tensors[k][ix] for k in ('x','history','held')])
            loss=training_loss(out,y[ix],scale)
            optimizer.zero_grad();loss.backward();clip_grad_norm_(model.parameters(),1.);optimizer.step()
            losses.append(float(loss.detach()))
        p=infer_gru(model,normalizer,valid);metrics=measure(valid['y'],p);score=selection_score(metrics,reference)
        if score<best:best=score;torch.save(model.state_dict(),directory/'gru.pt')
        history.append(dict(epoch=epoch+1,loss=float(np.mean(losses)),validation=metrics,
                            selection_score=score,elapsed_seconds=time.perf_counter()-began))
        torch.save(dict(model=model.state_dict(),optimizer=optimizer.state_dict(),epoch=epoch+1,
                        rng=torch.get_rng_state(),history=history,best=best),checkpoint)
        save_json(directory/'training_history.json',history)
        print('GRU',epoch+1,round(score,4),round(history[-1]['elapsed_seconds'],2),flush=True)
    timing=dict(gru_cpu_seconds=time.process_time()-cpu_before,gru_wall_seconds=time.perf_counter()-wall_before,
                epochs_completed=len(history),initializations=1,parameters=sum(p.numel() for p in model.parameters()),
                training_rows=len(y),device='CPU single thread float32; small model avoids MPS startup overhead')
    model.load_state_dict(torch.load(directory/'gru.pt',map_location='cpu',weights_only=True));model.eval()
    if not (directory/'tree.joblib').exists():
        began=time.process_time();wall=time.perf_counter();tree=TreeCorrection().fit(train)
        joblib.dump(tree,directory/'tree.joblib');timing.update(tree_cpu_seconds=time.process_time()-began,
            tree_wall_seconds=time.perf_counter()-wall,tree_output_fits=10)
    if not (directory/'lookup.joblib').exists():joblib.dump(ResidualLookup().fit(train),directory/'lookup.joblib')
    timing['dependencies']={name:importlib.metadata.version(name) for name in ('torch','numpy','scipy','lightgbm','scikit-learn','joblib')}
    save_json(directory/'training_complete.json',timing);return load_models(output)


def load_models(output):
    d=Path(output)/'models';n=Normalizer.load(d/'normalizer.npz')
    model=CorrectionNet(len(n.values['x_mean']),len(n.values['history_mean']),float(n.values['quality_scale']))
    model.load_state_dict(torch.load(d/'gru.pt',map_location='cpu',weights_only=True));model.eval()
    return model,n,joblib.load(d/'tree.joblib'),joblib.load(d/'lookup.joblib')


def replay_online(data,output,model,normalizer,mode):
    predictions=np.zeros((len(data['x']),12),np.float32);cost=[]
    for scene in np.unique(data['scene']):
        update=OnlineUpdate(model,normalizer,mode)
        with np.load(Path(output)/'scenarios'/scene/'online_public.npz') as f:online={k:f[k].copy() for k in f.files}
        # Complete exposure groups, ordered by feedback availability, never by candidate target labels.
        groups=[np.flatnonzero(online['exposure_index']==index) for index in np.unique(online['exposure_index'])]
        cursor=0;indices=np.flatnonzero(data['scene']==scene)
        times=np.array([utc(data['meta'][int(i)]['now_utc']).timestamp() for i in indices])
        for now in np.unique(times):
            while cursor<len(groups) and online['available_at'][groups[cursor][0]]<=now:
                ix=groups[cursor]
                update.observe({k:online[k][ix] for k in ('x','history','lower','upper')});cursor+=1
            ix=indices[times==now]
            predictions[ix]=update.predict({k:data[k][ix] for k in ('x','history')})
        # Include the unsampled remainder in the whole-card update-cost measurement.
        while cursor<len(groups):
            ix=groups[cursor];update.observe({k:online[k][ix] for k in ('x','history','lower','upper')});cursor+=1
        cost.append(dict(scene=scene,mode=mode,updates=update.updates,public_clean_exposures=len(groups),
                         update_cpu_seconds=update.cpu_seconds,final_bias=update.bias))
    return predictions,cost


def raw_predictions(output,split,models):
    data=load_split(output,split);model,normalizer,tree,lookup=models
    p=dict(hold=data['hold'],foundation=data['base'],lookup=lookup.predict(data),
           gru=infer_gru(model,normalizer,data),tree=tree.predict(data));cost=[]
    for name,mode in [('gru_bias','bias'),('gru_head','head')]:
        p[name],c=replay_online(data,output,model,normalizer,mode);cost.extend(c)
    return data,p,cost


def freeze(output):
    output=Path(output)
    if (output/'frozen.json').exists():return json.loads((output/'frozen.json').read_text())
    models=train(output);cal,cp,cc=raw_predictions(output,'calibration',models)
    valid,vp,vc=raw_predictions(output,'validation',models)
    settings={k:calibration_fit(cal['y'],p) for k,p in cp.items()}
    calibrated={k:calibration_apply(vp[k],settings[k]) for k in METHODS}
    metrics={k:measure(valid['y'],p) for k,p in calibrated.items()}
    scores={k:selection_score(v,metrics['hold']) for k,v in metrics.items()}
    allowed=[k for k in ('gru','tree','gru_bias','gru_head') if reliability_gate(metrics[k])]
    # Reliability is an acceptance gate. If all fail, retain a research candidate and mark it unapproved.
    candidates=allowed or ['gru','tree','gru_bias','gru_head']
    chosen=min(candidates,key=scores.get)
    mask_history=copy.copy(valid);mask_history['history']=np.zeros_like(valid['history'])
    diagnostic=measure(valid['y'],calibration_apply(infer_gru(models[0],models[1],mask_history),settings['gru']))
    frozen=dict(selected_method=chosen,validation_reliability_passed=chosen in allowed,
        selection_scores=scores,validation=metrics,calibration=settings,
        online_rules=json.loads((output/'protocol.json').read_text())['online'],
        training=json.loads((output/'models/training_complete.json').read_text()),
        model_hashes={p.name:digest(p) for p in (output/'models').glob('*') if p.suffix in ('.pt','.npz','.joblib')},
        code_hashes={str(p.relative_to(ROOT)):digest(p) for p in (ROOT/'research/observing_conditions').glob('adaptation*.py')},
        calibration_scenes=sorted(set(cal['scene'])),validation_scenes=sorted(set(valid['scene'])),
        history_mask_diagnostic=diagnostic,
        history_mask_limitation='Input deletion at inference, not a separately trained ablation',
        freeze_utc=__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),
        source='SELF-GENERATED; no official hidden cards; no test labels used')
    save_json(output/'online_cost_development.json',cc+vc);save_json(output/'frozen.json',frozen)
    print('FROZEN',chosen,'reliability',chosen in allowed,flush=True);return frozen


def loss_rows(y,p):
    levels=np.array([.05,.5,.95]);q=y[:,0];u=y[:,1]
    dq=q[:,None]-p[:,1:4];du=u[:,None]-p[:,5:8]
    return dict(quality_pinball=np.maximum(levels*dq,(levels-1)*dq).mean(1),
        usable_pinball=np.maximum(levels*du,(levels-1)*du).mean(1),
        quality_mae=np.abs(q-p[:,0]),usable_mae=np.abs(u-p[:,4]),
        blocked_brier=(p[:,8]-y[:,2])**2,
        band_brier=((p[:,9:12]-np.eye(3)[y[:,3].astype(int)])**2).sum(1),
        quality_coverage=((p[:,1]<=q)&(q<=p[:,3])).astype(float),
        quality_interval_score=p[:,3]-p[:,1]+20*np.maximum(p[:,1]-q,0)+20*np.maximum(q-p[:,3],0))


def bootstrap(data,p,ref,repetitions=500):
    scenes=np.unique(data['scene']);counts=np.array([(data['scene']==s).sum() for s in scenes])
    a=loss_rows(data['y'],p);b=loss_rows(data['y'],ref)
    rng=np.random.default_rng(10103);indices=rng.integers(len(scenes),size=(repetitions,len(scenes)))
    result={}
    for k in a:
        diff=np.array([(a[k][data['scene']==s]-b[k][data['scene']==s]).sum() for s in scenes])
        sampled=diff[indices].sum(1)/counts[indices].sum(1)
        result[k]=dict(difference=float(diff.sum()/counts.sum()),ci95=np.quantile(sampled,[.025,.975]).tolist(),
                       scenes=len(scenes),resamples=repetitions,unit='complete scene, pooled mean difference')
    return result


def evaluate(output):
    output=Path(output);frozen=json.loads((output/'frozen.json').read_text());models=load_models(output)
    for name,h in frozen['model_hashes'].items():
        if digest(output/'models'/name)!=h:raise ValueError('frozen weights changed')
    data,raw,cost=raw_predictions(output,'test',models)
    p={k:calibration_apply(raw[k],frozen['calibration'][k]) for k in METHODS}
    metrics={k:measure(data['y'],v) for k,v in p.items()};chosen=frozen['selected_method'];m=metrics[chosen]
    subgroups={}
    axes=dict(horizon=np.array([v['horizon'] for v in data['meta']]),card=np.array([s.split('-')[1] for s in data['scene']]),
              conditions=np.array(['changed_weather_and_policy' if s.endswith('-7') else 'normal' for s in data['scene']]),
              history=np.array(['none' if v['history_count']==0 else 'partial' if v['history_count']<8 else 'eight' for v in data['meta']]),
              fault=np.array([str(v['suspected_fault']) for v in data['meta']]),
              duration=np.array([str(v['duration_seconds']) for v in data['meta']]))
    for axis,groups in axes.items():
        subgroups[axis]={str(g):{k:measure(data['y'][groups==g],v[groups==g]) for k,v in p.items()} for g in np.unique(groups)}
    acceptance=dict(validation_reliability=frozen['validation_reliability_passed'],test_reliability=reliability_gate(m),
                    quality_score_gain=selection_score(m,metrics['hold'])<=.95,
                    update_cost_cpu=max(v['update_cpu_seconds'] for v in cost)<=90.)
    online_difference={k:bootstrap(data,p[k],p['gru']) for k in ('gru_bias','gru_head')}
    report=dict(selected_method=chosen,metrics=metrics,subgroups=subgroups,acceptance=acceptance,
        passed=all(acceptance.values()),bootstrap_vs_hold={k:bootstrap(data,v,p['hold']) for k,v in p.items()},
        online_difference_vs_fixed_gru=online_difference,online_cost=cost,
        source='Independent SELF-GENERATED scenes only. This is not an agent competition score.',
        test_scenes=sorted(set(data['scene'])),records=len(data['y']),
        interval_scope='Per-position exposure outcomes; no per-record confidence guarantee; next-night transfer exploratory',
        measured_online_cpu='CPU process time for all updates through the complete card; preprocessing reported separately')
    save_json(output/'report.json',report);save_json(output/'online_cost_test.json',cost)
    np.savez_compressed(output/'test_predictions.npz',y=data['y'],scene=data['scene'],**p)
    lines=['# 连续观测反馈修正实验','', '数据：正式 A–D 公开输入与自行生成天气；不代表正式比赛隐藏数据。', '',
           f"选定方法：`{chosen}`；整体验收：{'通过' if report['passed'] else '未通过'}。模型选择与校准已在生成最终测试数据前固定。",'',
           '| 方法 | 质量范围评分↓ | 质量覆盖率 | 质量平均误差↓ | 有效比例误差↓ | 受阻概率误差↓ | 档位概率误差↓ | 档位概率偏差↓ |',
           '|---|---:|---:|---:|---:|---:|---:|---:|']
    for k,v in metrics.items():
        lines.append(f"| {k} | {v['quality_interval_score']:.4f} | {v['quality_coverage']:.1%} | {v['quality_mae']:.4f} | {v['usable_mae']:.4f} | {v['blocked_brier']:.4f} | {v['band_brier']:.4f} | {v['band_ece']:.1%} |")
    lines+=['','区间评分同时惩罚范围过宽和漏掉实际结果；覆盖率须结合评分与宽度看。',
            '概率偏差为十组按样本数加权的预测概率与实际频率差；档位取三类中的最大偏差。',
            '',f"训练记录 {frozen['training']['training_rows']} 条，GRU 参数 {frozen['training']['parameters']}，10轮、1次初始化；树模型各输出拟合一次。",'',
            '## 边界与用途','',
            '- 冻结原基础模型。反馈修正与基础预测分别保留；没有排序、截断候选或输出观测动作。',
            '- 得分满分、档位歧义、零得分保留限制标记；参数更新只用明确且未满分的反馈，标签仍依赖估计仪器效率。',
            '- 下一夜不是原基础模型的训练范围。测试的变化场景同时改变天气和采样策略，不能分开归因。',
            '- 校准仅4个独立场景、测试8个；分项结果与场景重复抽样区间见 report.json。',
            '- 尚未接入正式智能体，也未证明模型会提高比赛得分。',
            '', '## 训练与完整成本','', '```json',json.dumps(frozen['training'],ensure_ascii=False,indent=2),'```']
    (output/'report.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps(dict(selected=chosen,passed=report['passed'],metrics=metrics),ensure_ascii=False),flush=True)
    return report


def main():
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=('train','freeze','evaluate'))
    parser.add_argument('--output',type=Path,default=DEFAULT);args=parser.parse_args()
    torch.set_num_threads(1);protocol(args.output)
    {'train':train,'freeze':freeze,'evaluate':evaluate}[args.stage](args.output)


if __name__=='__main__':main()

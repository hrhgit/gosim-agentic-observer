"""Controlled data-quantity/coverage study; old experiment and weights are immutable.

python -m research.observing_conditions.tree_runtime research.observing_conditions.adaptation_scaling STAGE
"""
from __future__ import annotations
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import gzip
import json
from pathlib import Path
import shutil
import time
import warnings

import joblib
import numpy as np
import torch
from torch.nn.utils import clip_grad_norm_

from .adaptation_data import DEFAULT as OLD, entries, build_samples, digest
from .adaptation_rollout import run
from .adaptation_model import Normalizer, CorrectionNet, TreeCorrection, infer_gru
from .adaptation_experiment import bootstrap
from .data import ROOT, save_json, source_hashes
from .information_model import training_loss, measure, calibration_fit, calibration_apply, selection_score, reliability_gate

DEFAULT=ROOT/'artifacts/observing_conditions-data-scaling'
VARIANTS=('gru32','gru64','gru128','gru_balanced64','gru_diverse64',
          'tree64','tree128','tree_balanced64','tree_diverse64')
FIELDS=('x','history','y','base','hold','coordinates')


def fresh_entries():
    return [dict(id=f'scale-{card}-{i}',card=card,
                 split='train' if i<2 else 'test',seed=1480000+ci*1000+i,
                 policy='baseline',weather_shift=bool(i%2))
            for ci,card in enumerate('abcd') for i in range(4)]


def protocol(output):
    output.mkdir(parents=True,exist_ok=True)
    spec=dict(version='conditions-data-scaling-v1',source='SELF-GENERATED ONLY; formal public A-D unchanged',
        old_directory=str(OLD),entries=fresh_entries(),variants=VARIANTS,
        design='32/64/128 nested decision groups on the same 16 train scenes; '
               'balanced64 replaces 8 groups with first 8 exposures; diverse64 adds 8 independent scenes (4 normal/4 shifted)',
        exposure_candidates='8 positions x 4 horizons; same public feature and label code; ineligible heights excluded',
        training=dict(architecture='unchanged GRU32 or LightGBM',epochs=10,batch=512,seed=101,
                      initializations=1,learning_rate=.001,old64_weights_reused=True,online_parameter_updates=False),
        evaluation='same old 4 validation and 4 calibration scenes, bulk and initial-history diagnostics separately; '
                   '8 new test scenes generated only after freeze; all variants fixed in advance',
        selection='bulk validation credibility gate then composite information score, same as old study; '
                  'initial-history metrics are diagnostics, not additional selection searches',
        material_gain=.05,bootstrap_unit='whole independent scene; 500 resamples',
        limitations='More rows means more SGD steps at fixed 10 epochs, not an equal-compute comparison. '
                    'diverse adds weather-shift training as well as independent scenes; cannot isolate these effects. '
                    'Calibration still has only 4 independent scenes. Single initialization does not measure seed variance.',
        sources=source_hashes(),old_frozen_hash=digest(OLD/'frozen.json'),
        old_weights={p.name:digest(p) for p in (OLD/'models').glob('*') if p.suffix in ('.pt','.npz','.joblib')},
        code_hash=digest(Path(__file__)))
    spec=json.loads(json.dumps(spec))
    path=output/'protocol.json'
    if path.exists() and json.loads(path.read_text())!=spec:
        raise ValueError('sealed study changed; use a new output directory')
    save_json(path,spec)
    return spec


def supplementary(entry,output,original):
    """Read only pre-exposure public snapshots; never post-exposure feedback."""
    target=output/'supplementary/scenarios'/entry['id']
    complete=target/'samples_complete.json'
    if complete.exists():return json.loads(complete.read_text())
    target.mkdir(parents=True,exist_ok=True)
    if not (target/'bundle').exists():
        (target/'bundle').symlink_to((original/'bundle').resolve(),target_is_directory=True)
    old=json.loads((original/'decisions.json').read_text())
    selected={r['observe_index'] for r in old}
    rng=np.random.default_rng(entry['seed']+50000000)
    initial=[];reservoir=[];seen=0
    with gzip.open(original/'exposures.jsonl.gz','rt') as file:
        for line in file:
            r=json.loads(line)
            d=dict(now_utc=r['start_utc'],baseline_state=r['baseline_state_before'],
                   feedback_history=r['feedback_history_before'],observe_index=r['observe_index'])
            if r['observe_index']<8:initial.append(d)
            if r['observe_index'] in selected or r['observe_index']<8:continue
            seen+=1
            if len(reservoir)<64:reservoir.append(d)
            else:
                ix=int(rng.integers(seen))
                if ix<64:reservoir[ix]=d
    if len(initial)!=8 or len(reservoir)!=64:raise ValueError('incomplete continuous trajectory')
    save_json(target/'decisions.json',initial+sorted(reservoir,key=lambda r:r['observe_index']))
    save_json(target/'sampling.json',dict(initial=[r['observe_index'] for r in initial],
        extra=[r['observe_index'] for r in reservoir],public_snapshot='before exposure',
        excludes_original_indices=True,available_exposures=seen+len(initial)))
    return build_samples(entry,output/'supplementary')


def prepare_worker(args):
    entry,output,fresh=args;output=Path(output)
    torch.set_num_threads(1)
    original=output/'scenarios'/entry['id'] if fresh else OLD/'scenarios'/entry['id']
    if fresh:
        run(entry,original)
        build_samples(entry,output)
    info=supplementary(entry,output,original)
    return dict(scene=entry['id'],supplementary=info,
                trajectory=json.loads((original/'trajectory_complete.json').read_text()))


def prepare(output,stage,workers):
    if stage=='test' and not (output/'frozen.json').exists():raise ValueError('freeze before test generation')
    todo=([(e,output,False) for e in entries() if e['split']!='test']+
          [(e,output,True) for e in fresh_entries() if e['split']=='train']) if stage=='development' else [
              (e,output,True) for e in fresh_entries() if e['split']=='test']
    began=time.perf_counter();result=[]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures=[pool.submit(prepare_worker,args) for args in todo]
        for future in as_completed(futures):
            value=future.result();result.append(value);print('prepared',value['scene'],flush=True)
    save_json(output/f'preparation_{stage}.json',dict(wall_seconds=time.perf_counter()-began,workers=workers,scenes=result))


def read(directory,scene):
    with np.load(directory/'samples.npz') as f:data={k:f[k].copy() for k in FIELDS}
    data['meta']=json.loads((directory/'sample_metadata.json').read_text())
    data['scene']=np.full(len(data['y']),scene)
    return data


def take(data,mask):
    ix=np.arange(len(data['y']))[mask]
    return {k:(v[mask] if isinstance(v,np.ndarray) else [v[int(i)] for i in ix]) for k,v in data.items()}


def concat(items):
    return {k:(np.concatenate([d[k] for d in items]) if isinstance(items[0][k],np.ndarray)
               else sum((d[k] for d in items),[])) for k in items[0]}


def data_split(output,split,variant='64',initial=False):
    items=[]
    source=fresh_entries() if split=='test' else entries()
    for e in source:
        if e['split']!=split:continue
        original=output/'scenarios'/e['id'] if split=='test' else OLD/'scenarios'/e['id']
        bulk=read(original,e['id'])
        if initial or (split=='train' and variant in ('128','balanced64')):
            extra=read(output/'supplementary/scenarios'/e['id'],e['id'])
            early=np.array([m['observe_index']<8 for m in extra['meta']])
        if initial:items.append(take(extra,early));continue
        if split=='train' and variant in ('32','balanced64'):
            indices=np.array([m['observe_index'] for m in bulk['meta']])
            groups=np.unique(indices);rng=np.random.default_rng(e['seed']+60000000)
            pool=groups if variant=='32' else groups[groups>=8]
            selected=rng.permutation(pool)[:32 if variant=='32' else 56]
            bulk=take(bulk,np.isin(indices,selected))
            if variant=='balanced64':bulk=concat([bulk,take(extra,early)])
        if split=='train' and variant=='128':bulk=concat([bulk,take(extra,~early)])
        items.append(bulk)
    if split=='train' and variant=='diverse64':
        items.extend(read(output/'scenarios'/e['id'],e['id']) for e in fresh_entries() if e['split']=='train')
    return concat(items)


def train_variant(output,name,valid):
    directory=output/'models'/name
    if (directory/'complete.json').exists():return
    directory.mkdir(parents=True,exist_ok=True)
    kind,size=name.split('_',1) if '_' in name else (name[:3] if name.startswith('gru') else 'tree',name[3:] if name.startswith('gru') else name[4:])
    began=time.perf_counter();cpu=time.process_time()
    train=data_split(output,'train',size)
    if name in ('gru64','tree64'):
        for filename in (('gru.pt','normalizer.npz') if kind=='gru' else ('tree.joblib',)):
            shutil.copyfile(OLD/'models'/filename,directory/filename)
        timing=dict(training_seconds=0.,cpu_seconds=0.,reused_existing=True)
    elif kind=='tree':
        tree=TreeCorrection().fit(train);joblib.dump(tree,directory/'tree.joblib')
        timing=dict(training_seconds=time.perf_counter()-began,cpu_seconds=time.process_time()-cpu,reused_existing=False)
    else:
        torch.manual_seed(101);np.random.seed(101)
        normalizer=Normalizer.fit(train);normalizer.save(directory/'normalizer.npz')
        scale=float(normalizer.values['quality_scale']);model=CorrectionNet(scale=scale)
        optimizer=torch.optim.AdamW(model.parameters(),lr=.001,weight_decay=.0001)
        tensors={k:torch.from_numpy(v) for k,v in normalizer.apply(train).items()};y=torch.from_numpy(train['y'])
        reference=measure(valid['y'],valid['hold']);best=float('inf');history=[]
        for epoch in range(10):
            model.train();order=torch.randperm(len(y));losses=[]
            for first in range(0,len(y),512):
                ix=order[first:first+512];out=model(*[tensors[k][ix] for k in ('x','history','held')])
                loss=training_loss(out,y[ix],scale);optimizer.zero_grad();loss.backward()
                clip_grad_norm_(model.parameters(),1.);optimizer.step();losses.append(float(loss.detach()))
            metrics=measure(valid['y'],infer_gru(model,normalizer,valid));score=selection_score(metrics,reference)
            if score<best:best=score;torch.save(model.state_dict(),directory/'gru.pt')
            history.append(dict(epoch=epoch+1,loss=float(np.mean(losses)),validation=metrics,selection_score=score))
        save_json(directory/'history.json',history)
        timing=dict(training_seconds=time.perf_counter()-began,cpu_seconds=time.process_time()-cpu,reused_existing=False)
    timing.update(rows=len(train['y']),scenes=len(np.unique(train['scene'])),decision_groups=len(set(
        (str(s),m['observe_index']) for s,m in zip(train['scene'],train['meta']))),
        initializations=0 if timing['reused_existing'] else 1,
        history_counts={str(h):sum(m['history_count']==h for m in train['meta']) for h in range(9)})
    save_json(directory/'complete.json',timing)
    print('trained',name,timing['rows'],round(timing['training_seconds'],2),flush=True)


def predict(output,name,data):
    directory=output/'models'/name
    if name.startswith('tree'):return joblib.load(directory/'tree.joblib').predict(data)
    normalizer=Normalizer.load(directory/'normalizer.npz');model=CorrectionNet(scale=float(normalizer.values['quality_scale']))
    model.load_state_dict(torch.load(directory/'gru.pt',map_location='cpu',weights_only=True))
    return infer_gru(model,normalizer,data)


def freeze(output):
    if (output/'frozen.json').exists():return
    valid=data_split(output,'validation');cal=data_split(output,'calibration');early=data_split(output,'validation',initial=True)
    settings={};metrics={};diagnostics={};training={}
    for name in VARIANTS:
        train_variant(output,name,valid)
        settings[name]=calibration_fit(cal['y'],predict(output,name,cal))
        metrics[name]=measure(valid['y'],calibration_apply(predict(output,name,valid),settings[name]))
        ep=calibration_apply(predict(output,name,early),settings[name]);diagnostics[name]=subgroups(early,ep)
        training[name]=json.loads((output/'models'/name/'complete.json').read_text())
    ref=measure(valid['y'],calibration_apply(valid['hold'],calibration_fit(cal['y'],cal['hold'])))
    scores={k:selection_score(v,ref) for k,v in metrics.items()}
    reliable=[k for k in VARIANTS if reliability_gate(metrics[k])]
    selected=min(reliable or list(VARIANTS),key=scores.get)
    save_json(output/'frozen.json',dict(selected=selected,selected_reliability=selected in reliable,
        validation=metrics,early_validation=diagnostics,scores=scores,calibration=settings,training=training,
        weights={str(p.relative_to(output)):digest(p) for p in (output/'models').rglob('*') if p.suffix in ('.pt','.npz','.joblib')},
        freeze_utc=__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat()))
    print('FROZEN',selected,'validation reliable',selected in reliable,flush=True)


def subgroups(data,p):
    axes=dict(horizon=np.array([m['horizon'] for m in data['meta']]),
        history=np.array(['none' if m['history_count']==0 else 'partial' if m['history_count']<8 else 'eight' for m in data['meta']]),
        card=np.array([s.split('-')[1] for s in data['scene']]),
        weather=np.array(['changed' if s.endswith('-3') else 'normal' for s in data['scene']]))
    return {a:{str(g):measure(data['y'][v==g],p[v==g])|dict(scenes=len(np.unique(data['scene'][v==g])))
               for g in np.unique(v)} for a,v in axes.items()}


def evaluate(output):
    frozen=json.loads((output/'frozen.json').read_text())
    for name,h in frozen['weights'].items():
        if digest(output/name)!=h:raise ValueError('frozen model changed')
    bulk=data_split(output,'test');early=data_split(output,'test',initial=True)
    predictions={name:calibration_apply(predict(output,name,bulk),frozen['calibration'][name]) for name in VARIANTS}
    ep={name:calibration_apply(predict(output,name,early),frozen['calibration'][name]) for name in VARIANTS}
    metrics={k:measure(bulk['y'],p) for k,p in predictions.items()}
    report=dict(source='SELF-GENERATED; not formal competition outcomes',records=len(bulk['y']),
        early_records=len(early['y']),selected=frozen['selected'],selected_reliability=reliability_gate(metrics[frozen['selected']]),
        training=frozen['training'],validation=frozen['validation'],metrics=metrics,
        bulk_subgroups={k:subgroups(bulk,p) for k,p in predictions.items()},
        early_subgroups={k:subgroups(early,p) for k,p in ep.items()},
        differences={k:bootstrap(bulk,p,predictions['gru64' if k.startswith('gru') else 'tree64']) for k,p in predictions.items()},
        early_differences={k:bootstrap(early,p,ep['gru64' if k.startswith('gru') else 'tree64']) for k,p in ep.items()},
        caveats=json.loads((output/'protocol.json').read_text())['limitations'])
    save_json(output/'report.json',report)
    np.savez_compressed(output/'test_predictions.npz',y=bulk['y'],scene=bulk['scene'],**predictions)
    np.savez_compressed(output/'early_predictions.npz',y=early['y'],scene=early['scene'],**ep)
    lines=['# 观测条件模型：是否需要增加数据量','',
        '正式公开 A–D 输入加自行生成天气；旧最终测试没有用于本轮选择。每个新方案只训练一次。',
        '32/64/128 指每场景采样的决策时刻，并非候选保留数量。所有位置照常输出。',
        '',f"验证集选定 {report['selected']}；新独立测试可靠性：{report['selected_reliability']}。",'',
        '| 方法 | 训练记录 | 独立训练场景 | 质量范围评分↓ | 质量覆盖率 | 有效比例覆盖率 | 档位概率偏差↓ | 质量平均误差↓ | 新训练秒数 |',
        '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for k,m in metrics.items():
        t=frozen['training'][k]
        lines.append(f"| {k} | {t['rows']} | {t['scenes']} | {m['quality_interval_score']:.4f} | {m['quality_coverage']:.1%} | {m['usable_coverage']:.1%} | {m['band_ece']:.1%} | {m['quality_mae']:.4f} | {t['training_seconds']:.2f} |")
    lines+=['','## 仅有 1–7 次观测历史的独立诊断','',
        '| 方法 | 样本数 | 场景数 | 质量范围评分↓ | 质量覆盖率 | 质量平均误差↓ |',
        '|---|---:|---:|---:|---:|---:|']
    for k,sg in report['early_subgroups'].items():
        m=sg['history']['partial']
        lines.append(f"| {k} | {m['samples']} | {m['scenes']} | {m['quality_interval_score']:.4f} | {m['quality_coverage']:.1%} | {m['quality_mae']:.4f} |")
    lines+=['','完整按卡、时间、历史与天气分项和整场景抽样差异范围见 report.json。',
        '初始历史诊断额外覆盖每场景前8次曝光，不与常规采样混合汇总。两者可能包含同一决策，不能当作新增独立场景。',
        '质量与有效比例范围目标为85%–95%，概率偏差目标≤5%；须同时看范围宽度和评分。',
        '', '## 限制','',report['caveats'],
        '下一夜不在冻结基础模型的原训练范围内；本轮不修改结构、特征或损失，不能用增加数据代替这些检查。']
    (output/'report.md').write_text('\n'.join(lines)+'\n')
    print('EVALUATED',len(bulk['y']),'bulk;',len(early['y']),'initial-history rows',flush=True)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=('development','freeze','test','evaluate'))
    parser.add_argument('--output',type=Path,default=DEFAULT);parser.add_argument('--workers',type=int,default=4)
    args=parser.parse_args();torch.set_num_threads(1)
    # Native LightGBM estimators were fit without pandas names; suppress that specific wrapper warning.
    warnings.filterwarnings('ignore',message='X does not have valid feature names')
    protocol(args.output)
    if args.stage in ('development','test'):prepare(args.output,args.stage,args.workers)
    elif args.stage=='freeze':freeze(args.output)
    else:evaluate(args.output)


if __name__=='__main__':main()

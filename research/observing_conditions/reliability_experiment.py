"""Frozen-network reliability study with explicit calendar/freshness diagnostics."""
from __future__ import annotations
import argparse
import copy
from concurrent.futures import ProcessPoolExecutor,as_completed
from datetime import datetime,timezone
import json
from pathlib import Path
import time
import shutil
import warnings
import numpy as np
import torch
from . import reliability_data as rd
from . import reliability_calibration as rc
from . import conditional_calibration as cc
from .adaptation_scaling import DEFAULT as SCALING,predict,prepare_worker
from .calibration_experiment import DEFAULT as CAL
from .adaptation_data import digest
from .adaptation_experiment import bootstrap
from .data import ROOT,save_json,source_hashes
from .information_model import measure,reliability_gate,probability_tables
from .adaptation_scaling import concat

MODELS=('gru64','gru128','tree64')


def protocol(output):
    output.mkdir(parents=True,exist_ok=True)
    spec=dict(version='conditions-reliability-v2',source='SELF-GENERATED ONLY; formal A-D public inputs unchanged',
        scenes={s:[e for e,_ in rd.sources(s,output)] for s in ('train','calibration','validation','test')},
        historical_tests='All scale and calcheck tests used here are development, not independent tests.',
        primary='gru64',frozen_model_references=list(MODELS),network_initializations=0,
        freshness='Full last-eight input retained; group distinguishes no prior observation, only prior-night records, '
                  '1-7 completed current-night exposures, and 8 completed current-night exposures.',
        sampling='Night opening on 16 calendar-spanning nights, early exposure on 16 nights, plus first eight survey exposures. '
                 'Causal pre-exposure states from full continuous trajectories. No new observation policy.',
        calibration='12 whole scenes, scene/decision/lead equal weights; same fixed network. Compare temperature and '
                    'temperature plus centered class bias; bias regularization .001 and .01 toward parent.',
        selection='Credibility first on 20 development scenes. Require aggregate Q/U coverage85-95%, blocked/bandECE<=5%; '
                  'Q coverage85-95% and both ECE<=5% for never/stale/partial/eight and next night with >=8 scenes. '
                  'Discrete U subgroup upper coverage is diagnostic. If none pass, exploratory only.',
        final_rule='Freeze before generating 16 new scenes; test failures do not trigger refitting on these scenes.',
        sources=source_hashes(),models={str(p.relative_to(ROOT)):digest(p) for model in MODELS
            for p in (SCALING/'models'/model).iterdir() if p.suffix in ('.pt','.npz','.joblib')})
    spec=json.loads(json.dumps(spec));path=output/'protocol.json'
    if path.exists() and json.loads(path.read_text())!=spec:raise ValueError('protocol changed')
    save_json(path,spec);return spec


def raw_cache(output,split,night,data):
    path=output/f'{split}_{"night" if night else "bulk"}_raw.npz'
    if path.exists():
        with np.load(path) as f:
            if f['rows'].item()!=len(data['y']):raise ValueError('cached sampling differs')
            return {k:f[k].copy() for k in MODELS}
    values={m:predict(SCALING,m,data) for m in MODELS};np.savez_compressed(path,rows=len(data['y']),**values)
    return values


def legacy_metadata(data):return [dict(m,history_count=m['all_history_count']) for m in data['meta']]


def grouped(data,p):
    lead,phase=rc.public_keys(data['meta'])
    axes=dict(horizon=lead,fresh_history=phase,weather=data['weather'],card=np.array([s.split('-')[1] for s in data['scene']]),
        survey_start=np.array(['first_eight' if m['observe_index']<8 else 'later' for m in data['meta']]),
        usable_endpoint=np.where(data['y'][:,1]==0,'zero',np.where(data['y'][:,1]==1,'one','interior')))
    return {name:{str(g):measure(data['y'][axis==g],p[axis==g])|dict(scenes=len(np.unique(data['scene'][axis==g])))
                  for g in np.unique(axis)} for name,axis in axes.items()}


def status(bulk,bsub,nsub):
    checks={'bulk':reliability_gate(bulk)};gaps=[]
    views=[('bulk',bulk),('next_night',bsub['horizon']['next_night'])]+list(nsub['fresh_history'].items())
    for key,m in views:
        if key!='bulk' and m['scenes']<8:continue
        cover=m['quality_coverage'];prob=max(m['band_ece'],m['blocked_ece'])
        gaps.append(max(.85-cover,cover-.95,0)/.05+max(prob-.05,0)/.05)
        if key!='bulk':checks[key]=.85<=cover<=.95 and prob<=.05
    return dict(passed=all(checks.values()),checks=checks,worst_gap=max(gaps))


def transform(key,raw,data,settings):
    return cc.apply(raw,legacy_metadata(data),settings) if key.endswith('/previous') else rc.apply(raw,data['meta'],settings)


def develop(output):
    began=time.perf_counter();cal=rd.load(output,'calibration');cn=rd.load(output,'calibration',True)
    valid=rd.load(output,'validation');vn=rd.load(output,'validation',True)
    cp=raw_cache(output,'calibration',False,cal);cnp=raw_cache(output,'calibration',True,cn)
    vp=raw_cache(output,'validation',False,valid);vnp=raw_cache(output,'validation',True,vn)
    old=json.loads((CAL/'frozen.json').read_text())['calibration'];settings={};metrics={};subs={};night={};checks={};cost={}
    for model in MODELS:
        for method in ('previous',)+rc.METHODS:
            key=model+'/'+method;start=time.perf_counter()
            settings[key]=old[model+'/history_horizon_sides'] if method=='previous' else rc.fit(cal,cp[model],cn,cnp[model],method)
            cost[key]=time.perf_counter()-start
            p=transform(key,vp[model],valid,settings[key]);npred=transform(key,vnp[model],vn,settings[key])
            metrics[key]=measure(valid['y'],p);subs[key]=grouped(valid,p);night[key]=grouped(vn,npred)
            checks[key]=status(metrics[key],subs[key],night[key])
            print(key,'bulk Q',round(metrics[key]['quality_coverage'],3),'bandECE',round(metrics[key]['band_ece'],3),
                  'night band ECE',{k:round(v['band_ece'],3) for k,v in night[key]['fresh_history'].items()},flush=True)
    diagnosis={}
    for name,data in [('calibration_night',cn),('validation_night',vn)]:
        _,phases=rc.public_keys(data['meta']);diagnosis[name]={}
        for phase in rc.PHASES:
            mask=phases==phase;y=data['y'][mask];scene=data['scene'][mask]
            diagnosis[name][phase]=dict(rows=len(y),band_counts=np.bincount(y[:,3].astype(int),minlength=3).tolist(),
                band_scenes=[len(np.unique(scene[y[:,3]==i])) for i in range(3)],
                endpoints=[int((y[:,1]==0).sum()),int(((y[:,1]>0)&(y[:,1]<1)).sum()),int((y[:,1]==1).sum())])
    save_json(output/'development.json',dict(settings=settings,metrics=metrics,subgroups=subs,night_subgroups=night,
        checks=checks,diagnosis=diagnosis,fitting_seconds=cost,wall_seconds=time.perf_counter()-began,
        rows=dict(calibration=len(cal['y']),calibration_night=len(cn['y']),validation=len(valid['y']),validation_night=len(vn['y']))))


def endpoint(output):
    from . import endpoint_model as em
    spec=dict(source='SELF-GENERATED',base='unchanged frozen gru64',architecture='63 inputs ->16 ReLU ->5 outputs',
        parameters=1109,mixture='P(U=0), P(0<U<1), P(U=1); beta shapes in [.2,20] for interior',
        training='16 original scenes, bulk plus calendar-spanning night samples; equal scene/decision weights; '
                 'AdamW .002, 12 epochs, batch512, seed101, ONE initialization; shared GRU frozen',
        calibration='12 independent scenes, bulk only; global regularized temperature and centered endpoint mass biases',
        selection='One head checkpoint minimizes U pinball + blocked Brier + endpoint Brier on 20 development bulk scenes',
        acceptance='Compare U pinball/interval score and blocked Brier; report endpoint support and discrete coverage; '
                   'keep existing U if candidate has no stable benefit. Final only after freeze.')
    path=output/'endpoint_protocol.json'
    if path.exists() and json.loads(path.read_text())!=spec:raise ValueError('endpoint protocol changed')
    save_json(path,spec)
    train=concat([rd.load(output,'train'),rd.load(output,'train',True)])
    rawpath=output/'endpoint_train_raw.npy'
    if rawpath.exists():raw=np.load(rawpath)
    else:raw=predict(SCALING,'gru64',train);np.save(rawpath,raw)
    valid=rd.load(output,'validation');vn=rd.load(output,'validation',True);cal=rd.load(output,'calibration')
    vr=raw_cache(output,'validation',False,valid)['gru64'];vnr=raw_cache(output,'validation',True,vn)['gru64']
    cr=raw_cache(output,'calibration',False,cal)['gru64']
    directory=output/'endpoint_head';em.train(directory,train,raw,valid,vr)
    cd=em.predict(directory,cal,cr);params=em.fit_mass(cal,cd)
    dv=em.predict(directory,valid,vr);dn=em.predict(directory,vn,vnr)
    results={};predictions={};masses={}
    dev=json.loads((output/'development.json').read_text());refsettings=dev['settings']['gru64/fresh_temperature']
    bv=rc.apply(vr,valid['meta'],refsettings);bn=rc.apply(vnr,vn['meta'],refsettings)
    for name,distribution,ndata,nraw,base in [('bulk',dv,valid,vr,bv),('night',dn,vn,vnr,bn)]:
        predictions[name+'_reference']=base
        for method in ('raw','calibrated'):
            dist=em.apply_mass(distribution,params) if method=='calibrated' else distribution
            p=em.numerical(dist,base);key=name+'/'+method;predictions[key]=p;masses[key]=dist
            results[key]=dict(metrics=measure(ndata['y'],p),subgroups=grouped(ndata,p),
                mass=em.mass_metrics(ndata['y'],dist),difference=bootstrap(ndata,p,base))
            print('endpoint',key,'U score',round(results[key]['metrics']['usable_interval_score'],4),
                'U coverage',round(results[key]['metrics']['usable_coverage'],3),
                'blockedECE',round(results[key]['metrics']['blocked_ece'],3),flush=True)
    save_json(output/'endpoint_development.json',dict(calibration=params,results=results,
        training=json.loads((directory/'complete.json').read_text()),reference='gru64/fresh_temperature',
        note='Beta interior is diagnostic and not recalibrated. Endpoint and severe probabilities arise from one coherent mixture.'))
    np.savez_compressed(output/'endpoint_development_predictions.npz',**predictions)
    np.savez_compressed(output/'endpoint_development_masses.npz',**masses)


def augment(output):
    import copy
    from . import endpoint_model as em
    spec=dict(calibration_extra=[e for e,_ in rd.sources('calibration_extra',output)],
        note='Old adapt final scenes now explicitly reused for calibration; never independent tests of this version.',
        training_unchanged=True,development_unchanged=True,calibration_scenes=20,
        candidate_methods=['20-scene fresh temperature','20-scene affine','20-scene affine shrink',
                           '20-scene temperature except never-observed affine'],
        reason='Rare first-survey bands only observed in two of the 12 calibration scenes; add existing independent trajectories.')
    path=output/'augmentation_protocol.json'
    if path.exists() and json.loads(path.read_text())!=spec:raise ValueError('augmentation changed')
    save_json(path,spec)
    cal=concat([rd.load(output,'calibration'),rd.load(output,'calibration_extra')])
    cn=concat([rd.load(output,'calibration',True),rd.load(output,'calibration_extra',True)])
    valid=rd.load(output,'validation');vn=rd.load(output,'validation',True)
    cpfile=output/'augmented_raw.npz'
    if cpfile.exists():
        with np.load(cpfile) as z:cp=z['bulk'].copy();cnp=z['night'].copy()
    else:
        cp=predict(SCALING,'gru64',cal);cnp=predict(SCALING,'gru64',cn);np.savez_compressed(cpfile,bulk=cp,night=cnp)
    vp=raw_cache(output,'validation',False,valid)['gru64'];vnp=raw_cache(output,'validation',True,vn)['gru64']
    params={};metrics={};subs={};night={};checks={};began=time.perf_counter()
    for method in rc.METHODS:params[method]=rc.fit(cal,cp,cn,cnp,method)
    hybrid=copy.deepcopy(params['fresh_temperature']);hybrid['method']='fresh_hybrid'
    for lead in cc.HORIZONS:hybrid['banks'][lead+'/never']=copy.deepcopy(params['fresh_affine']['banks'][lead+'/never'])
    params['fresh_hybrid']=hybrid
    for method,settings in params.items():
        p=rc.apply(vp,valid['meta'],settings);npred=rc.apply(vnp,vn['meta'],settings)
        metrics[method]=measure(valid['y'],p);subs[method]=grouped(valid,p);night[method]=grouped(vn,npred)
        checks[method]=status(metrics[method],subs[method],night[method])
        print('20cal',method,'gate',checks[method]['passed'],'gap',round(checks[method]['worst_gap'],3),
              'night bandECE',{k:round(v['band_ece'],3) for k,v in night[method]['fresh_history'].items()},flush=True)
    # Fitting the probabilities consumes no further network initialization.
    cd=em.predict(output/'endpoint_head',cal,cp);mass=em.fit_mass(cal,cd)
    dv=em.predict(output/'endpoint_head',valid,vp);dn=em.predict(output/'endpoint_head',vn,vnp)
    endpoints={}
    for name,d,data,raw in [('bulk',dv,valid,vp),('night',dn,vn,vnp)]:
        p=em.numerical(em.apply_mass(d,mass),raw)
        endpoints[name]=dict(metrics=measure(data['y'],p),subgroups=grouped(data,p),
            mass=em.mass_metrics(data['y'],em.apply_mass(d,mass)))
    save_json(output/'augmented_development.json',dict(settings=params,metrics=metrics,subgroups=subs,night_subgroups=night,
        checks=checks,fit_seconds=time.perf_counter()-began,calibration_scenes=sorted(set(cal['scene'])),
        endpoint_mass_calibration=mass,endpoint_results=endpoints))


def scene_ci(data,values,reference=None):
    delta=np.asarray(values,float)-(np.asarray(reference,float) if reference is not None else 0)
    scenes=np.unique(data['scene']);sums=np.array([delta[data['scene']==s].sum() for s in scenes]);counts=np.array([(data['scene']==s).sum() for s in scenes])
    rng=np.random.default_rng(101);draw=rng.integers(len(scenes),size=(500,len(scenes)))
    means=sums[draw].sum(1)/counts[draw].sum(1)
    return dict(estimate=float(delta.mean()),ci95=np.quantile(means,[.025,.975]).tolist(),scenes=len(scenes),unit='whole independent scene')


def interval_rows(y,p,column,lo,hi):
    return p[:,hi]-p[:,lo]+20*np.maximum(p[:,lo]-y[:,column],0)+20*np.maximum(y[:,column]-p[:,hi],0)


def freeze(output):
    from . import endpoint_model as em
    if (output/'frozen.json').exists():return
    dev=json.loads((output/'development.json').read_text());ed=json.loads((output/'endpoint_development.json').read_text())
    settings=copy.deepcopy(dev['settings']['gru64/fresh_temperature']);settings['method']='fresh_hybrid_12'
    for lead in cc.HORIZONS:settings['banks'][lead+'/never']=copy.deepcopy(dev['settings']['gru64/fresh_affine']['banks'][lead+'/never'])
    valid=rd.load(output,'validation');vn=rd.load(output,'validation',True)
    vr=raw_cache(output,'validation',False,valid)['gru64'];vnr=raw_cache(output,'validation',True,vn)['gru64']
    masses=np.load(output/'endpoint_development_masses.npz');p=em.numerical(masses['bulk/raw'],rc.apply(vr,valid['meta'],settings))
    npred=em.numerical(masses['night/raw'],rc.apply(vnr,vn['meta'],settings))
    metric=measure(valid['y'],p);sub=grouped(valid,p);nsub=grouped(vn,npred)
    checks=status(metric,sub,nsub)
    files=[Path(__file__),Path(__file__).with_name('reliability_data.py'),Path(__file__).with_name('reliability_calibration.py'),
           Path(__file__).with_name('reliability_predict.py'),Path(__file__).with_name('endpoint_model.py')]
    # Store the train-only constant endpoint reference, not final-test frequencies.
    train=concat([rd.load(output,'train'),rd.load(output,'train',True)]);tw=cc.weights(train)
    cls=em.target_class(train['y'][:,1]);constant=[float(tw[cls==i].sum()) for i in range(3)]
    save_json(output/'frozen.json',dict(selected='gru64/fresh_hybrid_12/endpoint_raw',settings=settings,
        endpoint_calibration=None,endpoint_selection='Raw mixture retained: lowest bulk+night mean interval score. '
            'Mass calibration on12 or20 scenes gives wider ranges and no stable overall improvement.',
        band_selection='Temperature outside never-observed; affine12 only for never-observed. '
            'Affine elsewhere and20-scene extension fail to improve enough; never-observed remains unsupported.',
        validation_metrics=metric,validation_subgroups=sub,validation_night_subgroups=nsub,validation_checks=checks,
        validation_passed=checks['passed'],training=ed['training'],constant_endpoint_mass=constant,
        train_scenes=sorted(set(train['scene'])),calibration_scenes=[e['id'] for e,_ in rd.sources('calibration')],
        extra_calibration_scenes_tried=[e['id'] for e,_ in rd.sources('calibration_extra')],
        validation_scenes=[e['id'] for e,_ in rd.sources('validation')],final_entries=rd.final_entries(),
        model_hashes=json.loads((output/'protocol.json').read_text())['models'],
        endpoint_hashes={str(p.relative_to(output)):digest(p) for p in (output/'endpoint_head').iterdir() if p.suffix in ('.pt','.npz')},
        scientific_code={str(p.relative_to(ROOT)):digest(p) for p in files},sources=source_hashes(),
        freeze_utc=datetime.now(timezone.utc).isoformat(),network_initializations=1,trunk_training_runs=0,
        automatic_actions=False,strict_gate_note='The original85-95% U coverage gate is retained; point masses give conservative coverage above95%. '
            'This candidate is exploratory, not declared fully accepted. First-ever band probabilities also fail5% development ECE.'))
    print('FROZEN exploratory candidate; validation passed',checks['passed'],flush=True)


def prepare_final(output,workers):
    if not (output/'frozen.json').exists():raise ValueError('freeze before final data')
    began=time.perf_counter();result=[]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for future in as_completed([pool.submit(prepare_worker,(e,output,True)) for e in rd.final_entries()]):
            value=future.result();result.append(value);print('fresh final trajectory',value['scene'],flush=True)
    save_json(output/'preparation_final.json',dict(wall_seconds=time.perf_counter()-began,workers=workers,scenes=result))
    rd.prepare(output,'test',workers)


def evaluate(output):
    from . import endpoint_model as em
    f=json.loads((output/'frozen.json').read_text())
    for group,root in [('model_hashes',ROOT),('endpoint_hashes',output),('scientific_code',ROOT)]:
        for name,h in f[group].items():
            if digest(root/name)!=h:raise ValueError('frozen '+group+' changed: '+name)
    if f['sources']!=source_hashes():raise ValueError('official source/public inputs changed')
    data=rd.load(output,'test');night=rd.load(output,'test',True)
    rp=raw_cache(output,'test',False,data);nrp=raw_cache(output,'test',True,night)
    dev=json.loads((output/'development.json').read_text());pred={};npred={};metrics={};sub={};nsub={};checks={};distributions={}
    for model in MODELS:
        key=model+'/previous';params=dev['settings'][key]
        pred[key]=transform(key,rp[model],data,params);npred[key]=transform(key,nrp[model],night,params)
    pred['gru64/fresh_hybrid']=rc.apply(rp['gru64'],data['meta'],f['settings'])
    npred['gru64/fresh_hybrid']=rc.apply(nrp['gru64'],night['meta'],f['settings'])
    distributions['bulk']=em.predict(output/'endpoint_head',data,rp['gru64'])
    distributions['night']=em.predict(output/'endpoint_head',night,nrp['gru64'])
    selected=f['selected'];pred[selected]=em.numerical(distributions['bulk'],pred['gru64/fresh_hybrid'])
    npred[selected]=em.numerical(distributions['night'],npred['gru64/fresh_hybrid'])
    for key,p in pred.items():
        metrics[key]=measure(data['y'],p);sub[key]=grouped(data,p);nsub[key]=grouped(night,npred[key]);checks[key]=status(metrics[key],sub[key],nsub[key])
    mass={};constant=np.array(f['constant_endpoint_mass'])
    for name,d,table in [('bulk',data,distributions['bulk']),('night',night,distributions['night'])]:
        mass[name]=dict(metrics=em.mass_metrics(d['y'],table),subgroups={})
        _,phase=rc.public_keys(d['meta'])
        for g in np.unique(phase):mass[name]['subgroups'][str(g)]=em.mass_metrics(d['y'][phase==g],table[phase==g])
        true=np.eye(3)[em.target_class(d['y'][:,1])]
        error=((table[:,:3]-true)**2).sum(1);ref=((constant-true)**2).sum(1)
        mass[name].update(constant=em.mass_metrics(d['y'],np.broadcast_to(np.r_[constant,1.,1.],table.shape)),
            difference_vs_constant=scene_ci(d,error,ref),beta_shape_warning='Sparse interior; empirical assessment only.')
    ref='gru64/previous'
    differences={k:bootstrap(data,p,pred[ref]) for k,p in pred.items()}
    for key,p in pred.items():
        for label,col,lo,hi in [('quality_interval_score',0,1,3),('usable_interval_score',1,5,7)]:
            differences[key][label]=scene_ci(data,interval_rows(data['y'],p,col,lo,hi),interval_rows(data['y'],pred[ref],col,lo,hi))
    report=dict(source='SELF-GENERATED ONLY; not formal competition performance',selected=selected,
        passed=f['validation_passed'] and checks[selected]['passed'],validation_passed=f['validation_passed'],
        strict_final_passed=checks[selected]['passed'],records=len(data['y']),night_records=len(night['y']),scenes=16,
        metrics=metrics,subgroups=sub,night_subgroups=nsub,checks=checks,endpoint=mass,
        difference_vs_previous= differences,night_difference=bootstrap(night,npred[selected],npred[ref]),
        stopping_reason='Extra calibration scenes and stronger class adjustment do not solve first-ever rare-band evidence. '
            'One small endpoint head adds objective masses and improves distribution scoring but remains conservative; '
            'no further seed/architecture/data enlargement justified by these development comparisons.',
        limitations=['First-ever band probabilities are not reliable enough for decisive interpretation.',
            'U ranges contain discrete atoms and may cover >95%; do not call exact90% calibrated.',
            'Rare complete closure and interior U are not accurately identified for every exposure.',
            'No per-candidate confidence guarantee; no automatic ranking, truncation, or exclusion.',
            'New sites, formal hidden data, Linux runtime and a complete agent score not evaluated this round.'])
    save_json(output/'report.json',report);np.savez_compressed(output/'test_predictions.npz',y=data['y'],scene=data['scene'],**pred)
    np.savez_compressed(output/'night_predictions.npz',y=night['y'],scene=night['scene'],**npred)
    np.savez_compressed(output/'endpoint_test_distributions.npz',**distributions)
    save_json(output/'probability_reliability.json',{key:probability_tables(data['y'],p,data['scene']) for key,p in pred.items()})
    lines=['# 观测条件可信度优化：最终核查','', '所有天气和事件均自行生成；正式公开A–D保持原样。',
        f"最终16个新场景；候选 `{selected}`；完整验收：{'通过' if report['passed'] else '未通过'}。",'',
        '| 方法 | 质量范围评分↓ | 质量覆盖率 | 有效比例范围评分↓ | 有效比例覆盖率 | 受阻概率偏差↓ | 档位概率偏差↓ |',
        '|---|---:|---:|---:|---:|---:|---:|']
    for key,m in metrics.items():lines.append(f"| {key} | {m['quality_interval_score']:.4f} | {m['quality_coverage']:.1%} | {m['usable_interval_score']:.4f} | {m['usable_coverage']:.1%} | {m['blocked_ece']:.1%} | {m['band_ece']:.1%} |")
    lines+=['','## 当晚信息多少','', '| 方法 / 条件 | 质量覆盖率 | 档位概率偏差↓ | 有效比例覆盖率 |','|---|---:|---:|---:|']
    for key in (ref,selected):
        for phase,m in nsub[key]['fresh_history'].items():lines.append(f"| {key} / {phase} | {m['quality_coverage']:.1%} | {m['band_ece']:.1%} | {m['usable_coverage']:.1%} |")
    lines+=['','never=整场尚无曝光；stale=已有记录但当晚暂无新曝光；partial=当晚1–7次；eight=当晚至少8次。',
        '当晚起始额外样本来自完整连续轨迹，单独汇总，不改变实际观测安排。',
        '', '## 新增信息与限制','',
        '小头输出完全不能观测、部分可观测、全程可观测三种概率，以及部分情况的分布。有效比例和受阻概率由同一个分布计算。',
        '这是客观概率，不能当作排除或执行观测的指令。',
        '主GRU冻结；仅1109参数小头、一次初始化、12轮，约2秒。重复运行不会重训已有头。',
        '12场景校准；另尝试加入8个旧场景，未见稳定收益，未保留；20开发场景；16新最终场景。',
        '旧正式公开文件、旧网络和旧实验不改。没有在这16场景上重调模型。',
        '首次观测的BRIGHT/BACKUP证据稀少，各架构都存在问题；附分组证据和未通过标记。',
        '0/1占比高使范围覆盖离散且偏保守，不能仅凭高覆盖宣称改善；一起看宽度、评分与概率可靠性。',
        '单点误差和按真实0/1分组的难例仍报告，不因总体均值较好而忽略。',
        '', '详细分项、整个场景为单位的差异范围、数据来源与重跑步骤见JSON及RELIABILITY.md。']
    (output/'report.md').write_text('\n'.join(lines)+'\n');print('FINAL evaluated; fully passed',report['passed'],flush=True)


def export(output):
    from .adaptation_scaling import OLD
    f=json.loads((output/'frozen.json').read_text());r=json.loads((output/'report.json').read_text());directory=output/'selected'
    (directory/'models').mkdir(parents=True,exist_ok=True)
    for filename in ('gru.pt','normalizer.npz'):shutil.copyfile(SCALING/'models/gru64'/filename,directory/'models'/filename)
    shutil.copytree(OLD/'foundation',directory/'foundation',dirs_exist_ok=True)
    shutil.copytree(output/'endpoint_head',directory/'endpoint_head',dirs_exist_ok=True)
    save_json(directory/'reliability.json',f['settings'])
    save_json(directory/'frozen.json',dict(selected_method='gru',calibration={'gru':dict(blocked_temperature=1.,band_temperature=1.,
        interval_correction=0.,usable_interval_correction=0.)},validation_reliability_passed=f['validation_passed']))
    save_json(directory/'report.json',dict(passed=r['passed']))
    support=dict(bulk=r['metrics'][f['selected']],night=r['night_subgroups'][f['selected']]['fresh_history'],
        endpoint=r['endpoint'],limits=r['limitations'],checks=r['checks'][f['selected']])
    save_json(directory/'support.json',support)
    save_json(directory/'manifest.json',dict(version='conditions-reliability-v2',selected=f['selected'],
        endpoint_mode='raw frozen mixture',network_initializations=1,trunk_training_runs=0,online_updates=0,
        final_test_passed=r['passed'],quality_definition='Q_env; efficiency1; no cap at1',
        hashes={str(p.relative_to(directory)):digest(p) for p in directory.rglob('*') if p.is_file() and p.name!='manifest.json'}))
    shutil.make_archive(str(output/'model_bundle'),'zip',directory);print('EXPORTED',directory,flush=True)


def main():
    p=argparse.ArgumentParser();p.add_argument('stage',choices=('develop','endpoint','augment','freeze','test','evaluate','export'));p.add_argument('--output',type=Path,default=rd.DEFAULT)
    p.add_argument('--workers',type=int,default=4)
    a=p.parse_args();torch.set_num_threads(1);warnings.filterwarnings('ignore',message='X does not have valid feature names')
    protocol(a.output)
    if a.stage=='develop':develop(a.output)
    elif a.stage=='endpoint':endpoint(a.output)
    elif a.stage=='augment':augment(a.output)
    elif a.stage=='freeze':freeze(a.output)
    elif a.stage=='test':prepare_final(a.output,a.workers)
    elif a.stage=='evaluate':evaluate(a.output)
    else:export(a.output)


if __name__=='__main__':main()

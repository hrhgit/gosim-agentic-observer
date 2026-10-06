"""Post-evaluation evidence and costs. Never changes predictions or fitted values."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import numpy as np
from . import reliability_data as rd,endpoint_model as em
from .reliability_experiment import export
from .metrics import reliability_bins
from .data import save_json


def complete(output):
    f=json.loads((output/'frozen.json').read_text());r=json.loads((output/'report.json').read_text());selected=f['selected']
    first=r['night_subgroups'][selected]['fresh_history']['never']
    extra=[]
    if first['quality_coverage']<.85:
        extra.append(f"First-ever Q range coverage is only {first['quality_coverage']:.1%}; do not treat that group as validated.")
    if first['blocked_ece']>.05:
        extra.append(f"First-ever severe-block probability error is {first['blocked_ece']:.1%}; aggregate reliability does not transfer to this group.")
    for text in extra:
        if text not in r['limitations']:r['limitations'].append(text)
    # Night diagnostics intentionally have no eight-fresh records. Check that
    # group from the bulk table too, rather than interpreting absence as a pass.
    def check(m):return dict(scenes=m['scenes'],quality_coverage=m['quality_coverage'],
        blocked_ece=m['blocked_ece'],band_ece=m['band_ece'],
        passed=.85<=m['quality_coverage']<=.95 and max(m['blocked_ece'],m['band_ece'])<=.05)
    eight=dict(validation=check(f['validation_subgroups']['fresh_history']['eight']),
               final=check(r['subgroups'][selected]['fresh_history']['eight']))
    r['additional_eight_history_gate']=eight
    r['passed']=r['passed'] and eight['validation']['passed'] and eight['final']['passed']
    save_json(output/'report.json',r)
    cost=dict(training=json.loads((output/'endpoint_head/complete.json').read_text()),
        frozen_base_training_runs=0,
        new16_scene_generation_seconds=json.loads((output/'preparation_final.json').read_text())['wall_seconds'],
        final_night_sampling_seconds=json.loads((output/'night_sampling_test.json').read_text())['wall_seconds'],
        development_night_sampling_seconds=json.loads((output/'night_sampling_development.json').read_text())['wall_seconds'],
        model_calibration_fit_seconds=sum(json.loads((output/'development.json').read_text())['fitting_seconds'].values()),
        extra_calibration_fit_seconds=json.loads((output/'augmented_development.json').read_text())['fit_seconds'],
        runtime=json.loads((output/'audit_runtime.json').read_text()))
    save_json(output/'cost_summary.json',cost)
    bins={}
    for name,night in [('bulk',False),('night',True)]:
        data=rd.load(output,'test',night);dist=np.load(output/'endpoint_test_distributions.npz')[name]
        cls=em.target_class(data['y'][:,1]);tables={}
        for i,label in enumerate(('fully_unusable','partly_usable','fully_usable')):
            probability=dist[:,i];truth=(cls==i).astype(float);table=reliability_bins(probability,truth)
            for j,row in enumerate(table):
                mask=(probability>=j/10)&((probability<(j+1)/10) if j<9 else (probability<=1))
                row.update(independent_scenes=len(np.unique(data['scene'][mask])),
                    positive_scenes=len(np.unique(data['scene'][mask&(truth==1)])),
                    absence_is_not_zero_risk=bool(mask.any() and not truth[mask].any()))
            tables[label]=table
        bins[name]=tables
    save_json(output/'endpoint_probability_reliability.json',bins)
    audits={k:json.loads((output/f'audit_{k}.json').read_text()) for k in ('contract','official','runtime')}
    assert all(a['passed'] for a in audits.values())
    value=dict(implementation_audits_passed=True,model_full_acceptance_passed=r['passed'],
        final_scene_count=16,complete_exposures=sum(v['trajectory']['exposures'] for v in json.loads((output/'preparation_final.json').read_text())['scenes']),
        fixed_predictions_unchanged=True,frozen_fit_parameters_unchanged=True,
        added_final_scope_notes=extra,additional_eight_gate=eight,
        audit_precision_note='The first audit used float32 SciPy beta CDF and differed by up to2.03e-6. '
            'Audit now computes float64 CDF, matching the unchanged predictor; no model/range tolerance was relaxed.',
        default_agent_modified=False,formal_hidden_competition_validated=False,
        status='Completed this research round; exploratory candidate with unresolved first-observation reliability and conservative U ranges.')
    save_json(output/'completion_audit.json',value)
    path=output/'report.md';text=path.read_text();marker='## 收尾核对'
    if marker in text:text=text[:text.index(marker)].rstrip()+'\n'
    text+='\n'+marker+'\n\n'
    text+=f"首次观测质量范围覆盖 {first['quality_coverage']:.1%}、档位偏差 {first['band_ece']:.1%}、受阻概率偏差 {first['blocked_ece']:.1%}；这一条件未通过，不建议把任何高概率当作确定结论。\n\n"
    text+=f"当晚已有至少8次曝光的分组已从一般采样表补核：开发通过={eight['validation']['passed']}，最终通过={eight['final']['passed']}。整体仍未通过。\n\n"
    text+=f"官方答案200条、未来公告24项、公开入口{audits['runtime']['public_calls']}次通过；主权重与固定统计参数不变。新增小头训练{cost['training']['training_seconds']:.2f}秒；16完整场景生成{cost['new16_scene_generation_seconds']:.1f}秒。\n"
    path.write_text(text)
    export(output)
    print('Completed; implementation audited, full model acceptance',r['passed'],flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,default=rd.DEFAULT);a=p.parse_args();complete(a.output)

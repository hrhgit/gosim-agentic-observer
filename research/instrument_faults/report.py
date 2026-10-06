"""Readable completion/coverage reports; experiment goals are not measured claims."""
from pathlib import Path
import json
import platform
import importlib.metadata

from research.observing_conditions.data import save_json
from .core import FEATURE_NAMES,FEATURE_VERSION


def environment(output):
    packages={k:importlib.metadata.version(k) for k in ('numpy','scipy','scikit-learn','torch','threadpoolctl')}
    save_json(Path(output)/'runtime.json',dict(python=platform.python_version(),platform=platform.platform(),packages=packages))
    save_json(Path(output)/'input_schema.json',dict(version=FEATURE_VERSION,numeric_feature_count=len(FEATURE_NAMES),feature_names=FEATURE_NAMES,
             excluded=['card_id','target_id','scenario_seed','true_weather','fault_multiplier','true_quality','true_band','event_boundaries','false_report_budget'],
             proxy='science_weight * min(feature_flux * actual_integer_duration * predicted_Q_env / public_normalization, 1) * band_probability_weighted_multiplier',
             proxy_warning='comparison proxy, NOT exact expected capped score; lower reference uses q05 and the minimum public program multiplier, NOT a calibrated score interval'))


def write(output):
    output=Path(output);state=json.loads((output/'_state.json').read_text())
    manifest_path=output/'manifest.json' if (output/'manifest.json').exists() else output/'planned_dataset.json'
    manifest=json.loads(manifest_path.read_text()) if manifest_path.exists() else {'entries':[]}
    complete=(output/'report.json').exists() and json.loads((output/'report.json').read_text()).get('completed',False)
    lines=['# 仪器故障实验','',f'当前状态：{state["phase"]}。','',
           '数据仅来自正式卡 A–D 的公开输入和自行生成的天气、事件、观测安排。限时请求为空；收益仅代表固定观测安排下的本地模拟。',
           '正式智能体未接入本模型，未验证比赛平台效果。','',
           f'计划场景：{len(manifest["entries"])}；已完成生成：{len(list((output/"scenarios").glob("*/prepared.json")))}；已完成真实天气预测和输入整理：{len(list((output/"scenarios").glob("*/features_complete.json")))}。','',
           '一个曝光汇总为一条样本；所有过程按完整场景分组。损失、训练权重和模拟收益使每个场景总影响相同。概率分组误差按样本数加权。',
           '发现条件和延迟统一使用不申报过程中的观测机会，避免修复后改变评测分母。正确修复后重新计算后续曝光反馈和得分。','']
    if not complete:
        lines+=['尚无完整模型比较、最终测试或外部测试成绩。目标 95% 申报正确率、90% 故障发现率和 5 毫秒推理都是验收目标。','',state.get('detail','')]
    else:
        report=json.loads((output/'report.json').read_text());kind=report['selection']['model']
        lines += [f'验证集选定：`{kind}`。最终测试通过全部目标：{report["passed"]}。','',
                  '| 模型 | 最终场景平均得分 | 相对不申报收益 | 申报正确比例 | 合格事件发现比例 | 误报/场景 | 需扣分误报/场景 | 概率分组误差 |',
                  '|---|---:|---:|---:|---:|---:|---:|---:|']
        def number(v):return '无数据' if v is None else f'{v:.4f}'
        for name in ('logistic','boosting','forest','neural'):
            m=report['tests']['test'][name];s=m['simulation']
            lines += ['| '+ ' | '.join([name,number(s['mean_score']),number(m['gain_vs_none']['mean']),number(s['report_precision']),number(s['eligible_event_recall']),number(s['false_per_scene']),number(s['paid_false_per_scene']),number(m['available_classification']['ece'])])+' |']
        m=report['tests']['test'][kind]
        lines+=['',f'与简单规则的配对得分差：{m["gain_vs_rule"]}。置信区间按完整场景重复抽样 2000 次。',
                f'发现延迟（曝光次数）：{m["simulation"]["delay_exposures"]}；实际时间：{m["simulation"]["delay_seconds"]}。',
                f'CPU 单线程推理：{report["selection"]["validation"][kind]["latency"]}。',
                '', '输入整理耗时见 simulations 下每条过程的 `input_timing_ms`；分类误差、概率平方误差、排序能力、分组和外部四类结果见 `report.json`。',
                '删除天气预测、历史和地震信息的分类对照见 `ablation_comparison.json`。这些对照没有参与最终模型选择。','',
                '未达目标项：'+str([key for key,value in report['acceptance'].items() if not value])]
    lines += ['', '开发评分检查见 `development_audit.json`（原始文件在独立开发目录），训练答案检查见 `saved_label_audit.json`；来源和种子见 `manifest.json`，天气依赖见 `weather_dependency.json`。',
              '比较得分参考值包含截断和档位概率近似；它不等于精确期望得分，q05 对应的参考下界也不是得分的校准区间。',
              '完整命令和接口说明见仓库的 `research/instrument_faults/README.md`。']
    (output/'REPORT.md').write_text('\n'.join(lines)+'\n')


def lite_report(output):
    """Keep the skill's standard evidence layout beside the scientific JSON report."""
    import shutil
    import subprocess
    from .evaluation import fingerprint,by_scene
    output=Path(output);result=json.loads((output/'report.json').read_text());selection=result['selection']
    root=output/'evaluation';root.mkdir(parents=True,exist_ok=True)
    entries=[e for e in json.loads((output/'manifest.json').read_text())['entries'] if e['split'] in ('test','external')]
    signature=fingerprint(output)
    save_json(root/'_state.json',dict(best_version='v1',metrics=[{'id':'score','kind':'float'},{'id':'false_reports','kind':'float'}],
              splits={'test':[e['id'] for e in entries]},source='self-generated fixed observation schedules; empty requests; not competition platform results'))
    for version,kind in [('baseline','rule'),('v1',selection['model'])]:
        directory=root/version;(directory/'traces').mkdir(parents=True,exist_ok=True);records=[]
        for entry in entries:
            files=list((output/'simulations'/signature).glob(f'*/{entry["id"]}/{kind}-*.json'))
            rows=[json.loads(p.read_text()) for p in files]
            if len(rows)!=2:raise ValueError('expected exactly two frozen-test traces for report export')
            score=sum(r['total'] for r in rows)/2;false=sum(r['false'] for r in rows)/2
            trace=[{'role':'user','content':json.dumps({'public_records':[str(output/'scenarios'/entry['id']/f'public-records-{t}.jsonl.gz') for t in (0,1)],
                      'input_schema':str(output/'input_schema.json'),'fixed_weather_version':json.loads((output/'weather_dependency.json').read_text())['identity']})},
                   {'role':'assistant','content':json.dumps({'model':kind,'prediction_decisions':[r['prediction_decisions'] for r in rows],
                      'reports':[r['reports'] for r in rows]},allow_nan=False)}]
            save_json(directory/'traces'/f'{entry["id"]}_rep0.json',trace)
            records.append(dict(prompt_id=entry['id'],rep=0,prompt={'public_input_reference':str(output/'scenarios'/entry['id'])},
                     grade={'score':score,'false_reports':false},model=kind,usage=None,status='ok',source=entry['split']))
        (directory/'results.jsonl').write_text(''.join(json.dumps(r,allow_nan=False)+'\n' for r in records))
        save_json(directory/'summary.json',dict(model=kind,cases=len(records),mean_score=sum(r['grade']['score'] for r in records)/len(records),
              usage=None,limitation='local numeric model, no API token usage; total includes official penalties'))
        (directory/'change.md').write_text('简单规则参照\n' if version=='baseline' else '验证集选定的故障概率模型及冻结申报规则\n')
    builder=Path('/Users/herh/.codex/skills/build-eval/scripts/build-report-lite.mjs')
    node=shutil.which('node')
    if node and builder.is_file():subprocess.run([node,str(builder),str(root.resolve())],check=True)
    else:save_json(root/'html_unavailable.json',dict(reason='Node or installed report builder unavailable; scientific Markdown/JSON report remains available'))

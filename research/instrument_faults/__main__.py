from __future__ import annotations
import argparse
import hashlib
import json
import sys
import time
import shutil
from pathlib import Path
from datetime import datetime,timezone

from research.observing_conditions.data import save_json
from .data import prepare,DEFAULT_OUTPUT,ROOT
from .audit import audit,audit_saved_labels
from .weather import freeze,FrozenWeather,audit_public_bridge
from .simulation import generate_features
from .models import train_all
from . import evaluation,ablations,report


def progress(output,phase,detail=''):
    output=Path(output)
    save_json(output/'_state.json',dict(phase=phase,detail=detail,updated_at=datetime.now(timezone.utc).isoformat()))
    report.write(output)
    print(phase+(': '+detail if detail else ''),flush=True)


def prepare_guarded(output,workers,pilot=False):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    paths=[Path(__file__).with_name('data.py'),ROOT/'research/observing_conditions/features.py']
    identity={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    marker=output/'generation_code.json'
    if marker.exists() and json.loads(marker.read_text())!=identity:raise ValueError('generation code changed: use a new dataset directory')
    save_json(marker,identity);prepare(output,workers,pilot)


def run(args):
    output=args.output;development=Path(str(output)+'-development')
    report.environment(output)
    progress(output,'development','8 个独立开发场景；不进入训练或评测集合')
    prepare_guarded(development,args.workers,True)
    if not (development/'audit.json').exists():audit(development,200)
    shutil.copy2(development/'audit.json',output/'development_audit.json')
    progress(output,'preparing','512 个主场景和 64 个外部场景')
    prepare_guarded(output,args.workers)
    progress(output,'feedback_only_training','完整独立无天气预测模型；训练、校准、冻结后独立测试')
    from .benchmark import run as run_feedback,compare as compare_feedback
    run_feedback(output,Path(str(output)+'-feedback'))
    progress(output,'awaiting_weather','正式训练需要已选定并通过可靠程度验证的真实天气模型')
    while not (args.weather_model/'metadata.json').is_file():
        if not args.await_weather:
            raise FileNotFoundError(f'weather dependency pending: {args.weather_model}')
        time.sleep(30)
    freeze(output,args.weather_model)
    progress(output,'features','冻结天气模型；生成曝光前预测和连续反馈')
    weather=FrozenWeather(output);audit_public_bridge(development,weather);generate_features(development,weather);generate_features(output,weather)
    audit_saved_labels(output,200)
    progress(output,'controlled_weather_comparison','相同反馈、公开输入与模型预算；仅增加真实天气预测做配对比较')
    compare_feedback(output,Path(str(output)+'-feedback'),Path(str(output)+'-weather-comparison'))
    progress(output,'training','四种模型、三个随机初始化复验和独立概率校准')
    train_all(output)
    progress(output,'validation_simulations','只用验证集选择申报阈值和最终模型')
    evaluation.select(output,args.workers)
    progress(output,'ablations','固定最终架构和参数，删除三组输入做对照')
    ablations.train(output)
    progress(output,'final_test','选择已冻结；最终和外部测试不用于调参')
    result=evaluation.test(output,args.workers);ablations.evaluate(output)
    report.lite_report(output)
    progress(output,'complete','全部实验完成；达到目标情况：'+str(result['passed']))
    return result


def main():
    parser=argparse.ArgumentParser(description='独立仪器故障研究；只用正式公开输入和自行生成数据')
    parser.add_argument('command',choices=['run','prepare','audit','features','train','select','ablations','test','predict','report'])
    parser.add_argument('--output',type=Path,default=DEFAULT_OUTPUT)
    parser.add_argument('--weather-model',type=Path,default=ROOT/'artifacts/observing_conditions/models/selected')
    parser.add_argument('--workers',type=int,default=2)
    parser.add_argument('--pilot',action='store_true')
    parser.add_argument('--await-weather',action='store_true',help='等待上游完成，不设超时或替代预测')
    parser.add_argument('--input',type=Path);parser.add_argument('--result',type=Path)
    args=parser.parse_args()
    if args.workers<1:parser.error('workers must be positive')
    try:
        if args.command=='run':run(args)
        elif args.command=='prepare':prepare_guarded(args.output,args.workers,args.pilot)
        elif args.command=='audit':audit(args.output,200)
        elif args.command=='features':freeze(args.output,args.weather_model);generate_features(args.output,FrozenWeather(args.output));audit_saved_labels(args.output,200)
        elif args.command=='train':train_all(args.output)
        elif args.command=='select':evaluation.select(args.output,args.workers)
        elif args.command=='ablations':ablations.train(args.output)
        elif args.command=='test':evaluation.test(args.output,args.workers);ablations.evaluate(args.output)
        elif args.command=='report':report.write(args.output)
        elif args.command=='predict':
            if args.input is None or args.result is None:parser.error('predict needs --input and --result')
            from .predict import Predictor
            save_json(args.result,Predictor(args.output).predict_request(json.loads(args.input.read_text())))
    except Exception as error:
        if args.command=='run':progress(args.output,'blocked' if isinstance(error,FileNotFoundError) or 'weather model has not passed' in str(error) else 'failed',f'{type(error).__name__}: {error}')
        raise


if __name__=='__main__':main()

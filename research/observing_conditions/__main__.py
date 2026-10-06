from __future__ import annotations
import argparse
from pathlib import Path
from .data import DEFAULT_OUTPUT, generate_dataset, audit_dataset


def main():
    parser=argparse.ArgumentParser(description='Independent public-input observation conditions experiment')
    parser.add_argument('command',choices=['generate','audit','run','train','predict'])
    parser.add_argument('--output',type=Path,default=DEFAULT_OUTPUT)
    parser.add_argument('--workers',type=int,default=4)
    parser.add_argument('--pilot',action='store_true')
    parser.add_argument('--input',type=Path)
    parser.add_argument('--result',type=Path)
    args=parser.parse_args()
    if args.pilot and args.output==DEFAULT_OUTPUT:
        parser.error('pilot requires a separate --output directory')
    if args.command=='generate':
        generate_dataset(args.output,args.workers,args.pilot)
    elif args.command=='audit':
        audit_dataset(args.output)
    elif args.command in ('run','train'):
        if args.command=='run':
            generate_dataset(args.output,args.workers)
            audit_dataset(args.output)
        from .experiment import run_experiment
        run_experiment(args.output)
    else:
        if args.input is None or args.result is None:
            parser.error('predict needs --input and --result')
        from .predict import Predictor
        import json
        from .data import save_json
        request=json.loads(args.input.read_text())
        save_json(args.result,Predictor(args.output/'models/selected').predict(request))


if __name__=='__main__':
    main()

"""Build a reproducible public request from a self-generated scenario, without labels."""
from __future__ import annotations
import argparse
import csv
import json
from pathlib import Path

import numpy as np

from .data import DEFAULT_OUTPUT,save_json
from .features import utc


def build_request(output=DEFAULT_OUTPUT,scene='a-000',group=0):
    directory=Path(output)/'scenarios'/scene
    metadata=json.loads((directory/'metadata.json').read_text())
    information=metadata['groups'][group];now=utc(information['now'])
    source=directory/'truth_and_public'
    def published(name):
        messages=[json.loads(line) for line in (source/name).read_text().splitlines()]
        return [message for message in messages if utc(message['issued_at_utc'])<=now]
    with np.load(directory/'samples.npz') as arrays:
        coordinates=arrays['coordinates'][group]
    return dict(initialize=dict(site=metadata['site'],scoring=metadata['score'],
        survey=dict(nights=list(csv.DictReader((source/'v4_night_calendar.csv').open()))),
        instrument=dict(exposure=dict(min_duration_seconds=60,max_duration_seconds=3600))),
        now_utc=information['now'],bulletins=published('v4_bulletins.jsonl'),forecasts=published('v4_forecasts.jsonl'),
        candidates=[dict(candidate_id=f'position-{index:03}',ra_deg=float(ra),dec_deg=float(dec),
            start_utc=information['start'],duration_seconds=information['requested'])
            for index,(ra,dec) in enumerate(coordinates)])


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,default=DEFAULT_OUTPUT)
    parser.add_argument('--scene',default='a-000')
    parser.add_argument('--group',type=int,default=0)
    args=parser.parse_args()
    destination=args.output/'example_request.json'
    save_json(destination,build_request(args.output,args.scene,args.group))
    print(destination)


if __name__=='__main__':
    main()

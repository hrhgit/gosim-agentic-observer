"""Same 1024-row public input, one CPU thread, no additional training."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import platform
import time
import numpy as np
import torch
from .adaptation_data import DEFAULT
from .adaptation_predict import AdaptationPredictor
from .adaptation_portable_tree import PortableTreeCorrection
from .adaptation_model import infer_gru
from .information_model import calibration_apply
from .data import save_json


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--directory',type=Path,default=DEFAULT)
    parser.add_argument('--output',type=Path,required=True);args=parser.parse_args();torch.set_num_threads(1)
    predictor=AdaptationPredictor(args.directory);model,normalizer,_,_=predictor.models
    tree=PortableTreeCorrection(args.directory/'models/portable-tree')
    with np.load(args.directory/'runtime_public_inputs.npz') as f:data={k:f[k][np.arange(1024)%len(f[k])] for k in f.files}
    settings=json.loads((args.directory/'frozen.json').read_text())['calibration']
    result={}
    for name in ('gru','tree'):
        times=[];output=None
        for i in range(35):
            start=time.perf_counter()
            output=infer_gru(model,normalizer,data) if name=='gru' else tree.predict(data)
            output=calibration_apply(output,settings[name])
            if i>=5:times.append((time.perf_counter()-start)*1000)
        assert np.isfinite(output).all()
        result[name]=dict(p50_ms=float(np.median(times)),p95_ms=float(np.quantile(times,.95)),
                          output_samples=output[:8].tolist())
        np.save(args.output.with_name(args.output.stem+'-'+name+'.npy'),output)
    save_json(args.output,dict(platform=platform.platform(),candidates=1024,threads=1,repeats=30,
        boundary='correction only incl. preprocessing normalization/calibration; shared frozen foundation and input feature building excluded',methods=result))
    print(json.dumps({name:{k:v for k,v in values.items() if k!='output_samples'} for name,values in result.items()}),flush=True)


if __name__=='__main__':main()

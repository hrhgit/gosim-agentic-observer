#!/usr/bin/env python3
"""Run the unchanged bundled local scorer with the cloud's 2 MiB stderr setting."""
import importlib.util
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
path=ROOT/'examples/_local/runner/run_local.py'
spec=importlib.util.spec_from_file_location('official_local_runner',path)
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
original=module.JsonlTransport


class CloudLogTransport(original):
    def __init__(self,*args,**kwargs):
        kwargs['log_limit']=2*1024*1024
        super().__init__(*args,**kwargs)


module.JsonlTransport=CloudLogTransport
if __name__=='__main__':
    raise SystemExit(module.main())

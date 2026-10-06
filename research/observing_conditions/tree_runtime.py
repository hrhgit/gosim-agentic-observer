"""Use one OpenMP runtime for PyTorch and LightGBM on macOS.

The loader reads DYLD_LIBRARY_PATH at process startup. Setting it after imports
does not repair already loaded libraries, so command entry points re-execute
once with the shared library path. No installed library is modified.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys


def shared_library_directory():
    if sys.platform!='darwin':return None
    spec=importlib.util.find_spec('torch')
    if spec is None or spec.origin is None:return None
    directory=Path(spec.origin).parent/'lib'
    return str(directory) if (directory/'libomp.dylib').exists() else None


def launch(module):
    directory=shared_library_directory()
    paths=os.environ.get('DYLD_LIBRARY_PATH','').split(':')
    if directory is not None and (not paths or paths[0]!=directory):
        env=dict(os.environ)
        env['DYLD_LIBRARY_PATH']=':'.join([directory,*[p for p in paths if p and p!=directory]])
        os.execve(sys.executable,[sys.executable,'-m',module,*sys.argv[1:]],env)


def require_shared_runtime():
    directory=shared_library_directory()
    if directory is not None and os.environ.get('DYLD_LIBRARY_PATH','').split(':')[0]!=directory:
        raise RuntimeError('On macOS, launch this process through '
            '`python -m research.observing_conditions.tree_runtime MODULE ...` '
            'or use the tree_predict CLI, so PyTorch and LightGBM share one OpenMP runtime.')


if __name__=='__main__':
    if len(sys.argv)<2:raise SystemExit('usage: tree_runtime MODULE [ARGS...]')
    module=sys.argv.pop(1)
    launch(module)
    import runpy
    sys.argv[0]=module
    runpy.run_module(module,run_name='__main__',alter_sys=True)

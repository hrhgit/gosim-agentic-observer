from pathlib import Path
import shutil
from ..common import ROOT
from .rollout import AGENT

def materialize():
    runtime=AGENT/'runtime/planning-package';package=runtime/'research/global_planning'
    package.mkdir(parents=True,exist_ok=True);(runtime/'research/__init__.py').write_text('')
    (package/'__init__.py').write_text('')
    for name in ['model.py','features.py','environment.py','bridge.py']:
        shutil.copyfile(ROOT/'research/global_planning'/name,package/name)
    (package/'common.py').write_text('''from pathlib import Path
import json,hashlib,os
ROOT=Path(__file__).resolve().parents[4]
AGENT=ROOT
ENGINE=ROOT/'unused-in-inference'
DEFAULT=ROOT/'unused-in-inference'
def save(path,value):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value))
def digest(value):
 data=value.read_bytes() if isinstance(value,Path) else json.dumps(value,sort_keys=True).encode();return hashlib.sha256(data).hexdigest()
''')
    (package/'staged').mkdir(exist_ok=True);(package/'staged/__init__.py').write_text('')
    for name in ['features.py','training.py','rollout.py','search.py','entry.py']:
        shutil.copyfile(ROOT/'research/global_planning/staged'/name,package/'staged'/name)
    # Root inside a packaged inference checkout is the agent directory. No truth paths are packaged.
    p=package/'staged/rollout.py';s=p.read_text().replace("AGENT=ROOT/'agents/global-planner-v2'","AGENT=ROOT")
    p.write_text(s)
    (AGENT/'runtime/planning_entry.py').write_text('''import sys,runpy
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent/'planning-package'))
runpy.run_module('research.global_planning.staged.entry',run_name='__main__')
''')
    return runtime

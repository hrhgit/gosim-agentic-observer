import sys,runpy
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent/'planning-package'))
runpy.run_module('research.global_planning.staged.entry',run_name='__main__')

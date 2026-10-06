import sys
from pathlib import Path
def baseline_module():
    directory=Path(__file__).resolve().parents[2]/"baseline_public"
    if str(directory) not in sys.path:sys.path.insert(0,str(directory))
    return None

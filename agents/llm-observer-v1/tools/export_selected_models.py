"""Snapshot public inference source and the selected, already trained models. No training."""
import ast
import hashlib
import json
from pathlib import Path
import shutil
import sys
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
OUT = Path(__file__).resolve().parents[1]
RUNTIME = OUT/'runtime'
SOURCES = {}

def emit(relative, names=None, header=None, methods=None):
    original=ROOT/relative
    source=original.read_text()
    if names is not None:
        tree=ast.parse(source); nodes=[]
        for node in tree.body:
            name=getattr(node,'name',None)
            assigned={n.id for n in ast.walk(node) if isinstance(n,ast.Name) and isinstance(n.ctx,ast.Store)} if not isinstance(node,(ast.FunctionDef,ast.ClassDef,ast.Import,ast.ImportFrom)) else set()
            if name in names or assigned & set(names):
                if methods and isinstance(node,ast.ClassDef) and name in methods:
                    node.body=[n for n in node.body if getattr(n,'name',None) in methods[name]]
                    # Only the constructor of the foundation is used here.
                    if name=='InformationPredictor':node.bases=[]
                nodes.append(node)
        source=header+'\n\n'+ast.unparse(ast.Module(body=nodes,type_ignores=[]))+'\n'
    dest=RUNTIME/'public_source'/relative
    dest.parent.mkdir(parents=True,exist_ok=True);dest.write_text(source)
    SOURCES[relative]={'source_sha256':hashlib.sha256(original.read_bytes()).hexdigest(),'snapshot_sha256':hashlib.sha256(dest.read_bytes()).hexdigest()}
    for p in dest.parents:
        if p==RUNTIME/'public_source':break
        (p/'__init__.py').touch()

base='from __future__ import annotations\nimport numpy as np\n'
emit('research/observing_conditions/features.py')
emit('challenge/tile_geometry_simulator.py', ['_julian_date','_local_sidereal_deg','_sun_equatorial_deg','_moon_equatorial_deg','_angular_separation_deg'], 'from __future__ import annotations\nimport math\nfrom datetime import datetime')
emit('challenge/v4_fiber_map.py',['radec_to_altaz'], 'from __future__ import annotations\nimport math\nfrom datetime import datetime\nfrom .tile_geometry_simulator import _local_sidereal_deg')
emit('challenge/v4_scorer.py',['_lunar_geometry'], 'from __future__ import annotations\nimport math\nfrom datetime import datetime\nfrom functools import lru_cache\nfrom .tile_geometry_simulator import _moon_equatorial_deg, _sun_equatorial_deg, _angular_separation_deg\nfrom .v4_fiber_map import radec_to_altaz')
emit('research/observing_conditions/models.py',['Normalizer','ConditionsNet','CausalBlock'],base+'import torch\nfrom torch import nn\nfrom torch.nn import functional as F')
emit('research/observing_conditions/information_model.py',['VERSION','NAMES','LEGACY','InformationNet','numeric','calibration_apply'],base+'import torch\nfrom torch import nn\nfrom torch.nn import functional as F\nfrom .models import ConditionsNet\nfrom .metrics import calibrate_apply')
emit('research/observing_conditions/metrics.py',['calibrate_apply'],base+'from scipy.special import expit,softmax')
emit('research/observing_conditions/information_predict.py',['InformationPredictor'],base+'import json,hashlib\nfrom pathlib import Path\nimport torch\nfrom .data import ROOT\nfrom .features import VERSION as FEATURE_VERSION\nfrom .models import Normalizer\nfrom .information_model import VERSION, InformationNet', {'InformationPredictor':['__init__']})
emit('research/observing_conditions/adaptation_model.py',['Normalizer','CorrectionNet','OnlineUpdate','infer_gru'],base+'from collections import deque\nimport copy,time\nimport torch\nfrom torch import nn\nfrom torch.nn import functional as F\nfrom .adaptation_features import QUALITY_INDEX\nfrom .information_model import numeric')
for name in ['adaptation_features','adaptation_feedback','adaptation_cache']:
    emit(f'research/observing_conditions/{name}.py')
emit('research/observing_conditions/adaptation_predict.py',['AdaptationPredictor'],base+'from datetime import timedelta\nimport json,hashlib,time\nfrom pathlib import Path\nimport torch\nfrom .adaptation_data import DEFAULT, VERSION\nfrom .adaptation_model import Normalizer, CorrectionNet, OnlineUpdate\nfrom .adaptation_feedback import public_online_bounds\nfrom .adaptation_features import public_features\nfrom .features import utc\nfrom .information_predict import InformationPredictor\nfrom .information_model import NAMES,calibration_apply\nfrom .adaptation_cache import PublicCache')
emit('research/observing_conditions/conditional_calibration.py',['HORIZONS','PHASES','public_keys','select_bank','apply'],base+'from scipy.special import expit,softmax')
emit('research/observing_conditions/reliability_calibration.py',['PHASES','public_keys','apply'],base+'from scipy.special import softmax\nfrom . import conditional_calibration as old')
emit('research/observing_conditions/endpoint_model.py',['public_extra','features','EndpointHead','distribution_numeric','infer','numerical'],base+'import torch\nfrom torch import nn\nfrom scipy.special import betainc,betaincinv,softmax')
emit('research/observing_conditions/reliability_data.py',['freshness'],base+'from pathlib import Path\nfrom .features import utc\nDEFAULT = Path(__file__).resolve().parents[3]/"models/conditions-reliability"')
emit('research/observing_conditions/reliability_predict.py',['ReliabilityPredictor'],base+'import hashlib,json,time\nfrom pathlib import Path\nimport torch\nfrom .adaptation_predict import AdaptationPredictor\nfrom . import reliability_data as rd,reliability_calibration as rc,endpoint_model as em\nfrom .information_model import NAMES\nfrom .features import utc')
emit('research/instrument_faults/core.py')
emit('research/instrument_faults/feedback_only.py',['VERSION','STATS','HISTORY','FEATURE_NAMES','FeedbackEvidence'],base+'import math\nfrom research.observing_conditions.features import utc,KINDS,DIRECTIONS,notice_matrix\nfrom .core import Evidence,PROGRAMS,age,finite_stats,matrix_stats')
emit('research/instrument_faults/report_model.py',['VERSION','BAND_NAMES','band_evidence'],base+'import math')
emit('research/instrument_faults/benchmark.py',['CONTEXT_INDICES','BASE_NAMES','public_exposure_inputs'],base+'from research.observing_conditions.features import CONTEXT_NAMES,CANDIDATE_NAMES\nfrom . import feedback_only as feedback')
# The snapshot includes only public geometry/state code, never scenario generation or truth loaders.
p=RUNTIME/'public_source/research/observing_conditions'
(p/'data.py').write_text('from pathlib import Path\nROOT = Path(__file__).resolve().parents[2]\n')
(p/'adaptation_data.py').write_text('from .data import ROOT\nDEFAULT=ROOT/"unused"\nVERSION="conditions-adaptation-v1"\n')
(p/'adaptation_rollout.py').write_text('import sys\nfrom pathlib import Path\ndef baseline_module():\n    directory=Path(__file__).resolve().parents[2]/"baseline_public"\n    if str(directory) not in sys.path:sys.path.insert(0,str(directory))\n    return None\n')
for name in ['conditions','astro']:
    rel=f'agents/claude-baseline-v1/agent/core/{name}.py'
    dest=RUNTIME/'public_source/baseline_public/core'/f'{name}.py';dest.parent.mkdir(parents=True,exist_ok=True)
    dest.write_bytes((ROOT/rel).read_bytes());(dest.parent/'__init__.py').touch()
    SOURCES[rel]={'source_sha256':hashlib.sha256((ROOT/rel).read_bytes()).hexdigest(),'snapshot_sha256':hashlib.sha256(dest.read_bytes()).hexdigest()}
selected=ROOT/'artifacts/observing_conditions-reliability/selected'
dest=OUT/'models/conditions-reliability'
if dest.exists():shutil.rmtree(dest)
shutil.copytree(selected,dest,ignore=shutil.ignore_patterns('__pycache__'))
manifest=json.loads((dest/'manifest.json').read_text())
for name,digest in manifest['hashes'].items():
    if hashlib.sha256((dest/name).read_bytes()).hexdigest()!=digest:raise ValueError(f'frozen file changed: {name}')
# Portable exact trees: no sklearn/joblib is needed during inference.
import joblib
model=joblib.load(ROOT/'artifacts/instrument_faults-report-model/model.joblib')
if model.kind!='boosting' or model.preprocessor or model.calibrator or model.features_mask is not None:raise ValueError('unsupported selected fault model')
base_model=model.base
trees=[]
for stage in base_model._predictors:
    if len(stage)!=1:raise ValueError('expected binary classifier')
    nodes=stage[0].nodes
    if nodes['is_categorical'].any():raise ValueError('categorical split requires separate export')
    trees.append([{k:float(n[k]) if k in ('value','num_threshold') else int(n[k]) for k in ('value','feature_idx','num_threshold','missing_go_to_left','left','right','is_leaf')} for n in nodes])
fault=OUT/'models/instrument-fault-report';fault.mkdir(exist_ok=True)
metadata=json.loads((ROOT/'artifacts/instrument_faults-report-model/model.json').read_text())
selection=json.loads((ROOT/'artifacts/instrument_faults-report-model/selection.json').read_text())
value=dict(model_id='instrument-fault-report',version='fault-report-model-v1',source='artifacts/instrument_faults-report-model',source_sha256=hashlib.sha256((ROOT/'artifacts/instrument_faults-report-model/model.joblib').read_bytes()).hexdigest(),baseline=float(base_model._baseline_prediction[0,0]),feature_count=base_model.n_features_in_,trees=trees,metadata=metadata,thresholds=selection['selected']['thresholds'],score_is_calibrated_fault_probability=False,independent_final_test_passed=None)
(fault/'manifest.json').write_text(json.dumps(value,separators=(',',':'))+'\n')
(RUNTIME/'snapshot.json').write_text(json.dumps(dict(source='Public inference snapshot; trained assets copied without retraining',conditions_manifest_sha256=hashlib.sha256((dest/'manifest.json').read_bytes()).hexdigest(),conditions_selected=manifest['selected'],fault_source_sha256=value['source_sha256'],fault_manifest_sha256=hashlib.sha256((fault/'manifest.json').read_bytes()).hexdigest(),files=SOURCES),indent=2)+'\n')
print(json.dumps({'conditions':manifest['selected'],'fault_trees':len(trees),'fault_features':base_model.n_features_in_,'snapshot_files':len(SOURCES)}))

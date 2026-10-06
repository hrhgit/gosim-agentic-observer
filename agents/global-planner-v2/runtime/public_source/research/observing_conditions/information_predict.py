from __future__ import annotations
import numpy as np
import json,hashlib
from pathlib import Path
import torch
from .data import ROOT
from .features import VERSION as FEATURE_VERSION
from .models import Normalizer
from .information_model import VERSION, InformationNet

class InformationPredictor:

    def __init__(self, directory=ROOT / 'artifacts/observing_conditions-information/models/selected'):
        self.directory = Path(directory)
        self.metadata = json.loads((self.directory / 'metadata.json').read_text())
        if self.metadata['version'] != VERSION or self.metadata['feature_version'] != FEATURE_VERSION:
            raise ValueError('information/feature version mismatch')
        self.normalizer = Normalizer.load(self.directory / 'normalizer.npz')
        self.model = InformationNet(self.metadata['architecture'], self.metadata['context_dim'], self.metadata['history_dim'], self.metadata['candidate_dim'])
        self.model.load_state_dict(torch.load(self.directory / 'weights.pt', map_location='cpu', weights_only=True))
        self.model.eval()
        digest = hashlib.sha256()
        for filename in ('metadata.json', 'normalizer.npz', 'weights.pt'):
            digest.update((self.directory / filename).read_bytes())
        self.metadata['model_version'] = VERSION + '-' + digest.hexdigest()[:16]
        self._extra = {}

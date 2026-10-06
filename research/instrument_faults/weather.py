"""Freeze and use the upstream public weather predictor, never hidden weather labels."""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import numpy as np
import torch

from research.observing_conditions.data import save_json
from research.observing_conditions.predict import Predictor
from .data import ROOT


def hashes(directory):
    return {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(Path(directory).glob('*')) if p.is_file()}


def freeze(output, source):
    output, source = Path(output), Path(source)
    metadata_path=source/'metadata.json'
    if not metadata_path.is_file():
        raise FileNotFoundError(f'weather model is not selected yet: {metadata_path}')
    metadata=json.loads(metadata_path.read_text())
    if not metadata.get('validation_reliable'):
        raise ValueError('weather model has not passed its validation reliability checks')
    if 'efficiency=1' not in metadata['quality_definition']:
        raise ValueError('weather labels must exclude instrument efficiency')
    source_manifest=source.parent.parent/'manifest.json'
    if not source_manifest.is_file() or hashlib.sha256(source_manifest.read_bytes()).hexdigest()!=metadata['dataset_manifest_sha256']:
        raise ValueError('weather training manifest identity cannot be verified')
    weather_manifest=json.loads(source_manifest.read_text())
    fault_manifest=json.loads((output/'manifest.json').read_text())
    generation_path=output/'generation_code.json'
    if generation_path.exists():
        sources=json.loads(generation_path.read_text());feature_path=ROOT/'research/observing_conditions/features.py'
        if sources[str(feature_path.relative_to(ROOT))]!=hashlib.sha256(feature_path.read_bytes()).hexdigest():
            raise ValueError('weather public input code changed since fault data generation')
    weather_seeds={e['seed'] for e in weather_manifest['entries']}
    fault_seeds={e['seed'] for e in fault_manifest['entries']}
    if weather_seeds & fault_seeds:
        raise ValueError('fault scenes overlap weather training/evaluation seeds')
    fingerprint=hashes(source)
    identity=hashlib.sha256(json.dumps(fingerprint,sort_keys=True).encode()).hexdigest()
    destination=output/'weather_model'
    marker=output/'weather_dependency.json'
    record=dict(identity=identity,artifact_sha256=fingerprint,metadata=metadata,
                source_directory=str(source.resolve()),source='actual trained public-input weather predictor',
                upstream_manifest_sha256=metadata['dataset_manifest_sha256'],seed_overlap_count=0,
                upstream_scene_count=len(weather_manifest['entries']))
    if marker.exists():
        if json.loads(marker.read_text())['identity']!=identity or hashes(destination)!=fingerprint:
            raise ValueError('weather model changed; create a new fault experiment directory')
        return record
    destination.mkdir(parents=True,exist_ok=True)
    for p in source.iterdir():
        if p.is_file():
            shutil.copy2(p,destination/p.name)
    shutil.copy2(source_manifest,output/'weather_training_manifest.json')
    save_json(marker,record)
    return record


class FrozenWeather:
    def __init__(self,output):
        self.output=Path(output)
        self.dependency=json.loads((self.output/'weather_dependency.json').read_text())
        if hashes(self.output/'weather_model')!=self.dependency['artifact_sha256']:
            raise ValueError('frozen weather artifact checksum mismatch')
        self.version=self.dependency['identity']
        self.predictor=Predictor(self.output/'weather_model')

    def predict_scene(self,directory,track):
        directory=Path(directory)
        marker=directory/f'weather-prediction-{track}.json'
        result_path=directory/f'weather-prediction-{track}.npy'
        if marker.exists():
            if json.loads(marker.read_text())['weather_identity']!=self.version:
                raise ValueError('cached weather predictions belong to another model')
            return np.load(result_path)
        with np.load(directory/f'weather-input-{track}.npz') as a:
            normalized=self.predictor.normalizer.apply(a)
        parts=[]
        previous=torch.get_num_threads();torch.set_num_threads(1)
        try:
            for first in range(0,len(normalized['context']),64):
                parts.append(self.predictor.numeric({k:v[first:first+64] for k,v in normalized.items()}))
        finally:
            torch.set_num_threads(previous)
        result=np.concatenate(parts).astype(np.float32)
        if not np.isfinite(result).all():
            raise ValueError('nonfinite weather output')
        np.save(result_path,result)
        save_json(marker,dict(weather_identity=self.version,shape=list(result.shape)))
        return result


def audit_public_bridge(development,weather):
    """Verify the optimized cache against the actual public request entry point."""
    from .data import read_public
    from .core import PROGRAMS
    output=Path(development);manifest=json.loads((output/'manifest.json').read_text());comparisons=[]
    for entry in manifest['entries']:
        directory=output/'scenarios'/entry['id'];site,fiber,score,nights,_=read_public(entry['card'])
        initialize={'site':site,'scoring':score,'survey':{'nights':nights},'instrument':{'exposure':fiber['exposure']}}
        rows=json.loads((directory/'schedule-0.json').read_text())
        cached=weather.predict_scene(directory,0)
        scenario=__import__('challenge.v4_runner',fromlist=['load_scenario']).load_scenario(directory/'scenario.json')
        for row in (rows[0],rows[len(rows)//2],rows[-1]):
            targets=[scenario.targets[i] for i in row['target_indices']]
            request=dict(initialize=initialize,now_utc=row['start'],bulletins=scenario.bulletins,forecasts=scenario.forecasts,
                candidates=[dict(candidate_id=t['target_id'],ra_deg=t['ra_deg'],dec_deg=t['dec_deg'],start_utc=row['start'],duration_seconds=row['requested']) for t in targets])
            response=weather.predictor.predict(request)
            from research.observing_conditions.predict import OUTPUT_NAMES
            actual=np.array([[p[name] for name in OUTPUT_NAMES] for p in response['predictions']])
            expected=cached[row['observe_index'],:len(targets)]
            error=float(np.abs(actual-expected).max(initial=0))
            if error>1e-5:raise AssertionError(f'public weather API/cache mismatch: {entry["id"]} {error}')
            comparisons.append(dict(scene=entry['id'],observe_index=row['observe_index'],maximum_error=error,targets=len(targets)))
    result=dict(weather_version=weather.version,comparisons=comparisons,maximum_error=max(r['maximum_error'] for r in comparisons))
    save_json(weather.output/'weather_bridge_audit.json',result)
    return result

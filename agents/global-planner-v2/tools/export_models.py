"""Export frozen information GRU/MLP for Node. No training or calibration fitting."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SOURCE = ROOT / 'artifacts/observing_conditions-information'
DEFAULT_DESTINATION = Path(__file__).resolve().parents[1] / 'models'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def export(source, destination):
    frozen = json.loads((source / 'frozen.json').read_text())
    report = json.loads((source / 'report.json').read_text())
    protocol = json.loads((source / 'protocol.json').read_text())
    for architecture in ('gru', 'mlp'):
        directory = source / 'models' / architecture
        if digest(directory / 'weights.pt') != frozen['weights'][architecture]:
            raise ValueError(f'{architecture}: frozen weight hash changed')
        if digest(directory / 'normalizer.npz') != frozen['normalizer_sha256']:
            raise ValueError(f'{architecture}: frozen normalizer changed')
        metadata = json.loads((directory / 'metadata.json').read_text())
        weights = torch.load(directory / 'weights.pt', map_location='cpu', weights_only=True)
        chunks, tensors, offset = [], {}, 0
        for name, tensor in weights.items():
            values = tensor.detach().numpy().astype('<f4')
            if not np.isfinite(values).all():
                raise ValueError(f'{architecture}/{name}: nonfinite weight')
            tensors[name] = dict(shape=list(values.shape), byte_offset=offset, elements=values.size)
            chunk = values.tobytes(order='C'); chunks.append(chunk); offset += len(chunk)
        payload = b''.join(chunks)
        if sum(t['elements'] for t in tensors.values()) != metadata['parameters']:
            raise ValueError('parameter count mismatch')
        with np.load(directory / 'normalizer.npz') as normalizer:
            normalization = {k: normalizer[k].tolist() for k in normalizer.files}
        # Each architecture uses its own already-frozen calibration and evidence.
        selected = architecture == frozen['selected']
        evidence = dict(
            selected=selected, validation_reliable=report['selection']['comparisons'][architecture]['reliable'],
            selected_model_final_test_passed=report['final_test_passed'] if selected else None,
            final_test_passed=report['final_test_passed'] if selected else None,
            status='research model; selected-model full acceptance failed' if selected else
                   'comparison model; no full subgroup acceptance was performed',
            point_metrics={split: section['models'][architecture]['metrics'] for split, section in report['tests'].items()},
            subgroup_review={split: section['subgroup_review'] for split, section in report['tests'].items()} if selected else None,
            limitation='Self-generated scenarios only; no per-position, joint or competition guarantee.')
        manifest = dict(
            schema='observer-neural-weights-v1', model_id=f'conditions-{architecture}', task='observing-conditions',
            backend='information-net-v3', weights_file='weights.f32', weights_sha256=hashlib.sha256(payload).hexdigest(),
            tensors=tensors, normalization=normalization,
            metadata=metadata | dict(feature_version=protocol['feature_version'], history_length=32,
                calibration=frozen['point_calibration'][architecture],
                region_calibration=frozen['region_calibration'][architecture],
                source=protocol['source'], quality_definition=protocol['quality_definition'],
                reliability_evidence=evidence),
            sources={str((directory / name).relative_to(ROOT)): digest(directory / name)
                     for name in ('weights.pt', 'normalizer.npz', 'metadata.json')} |
                    {str((source / name).relative_to(ROOT)): digest(source / name)
                     for name in ('frozen.json', 'report.json', 'protocol.json')})
        output = destination / manifest['model_id']; output.mkdir(parents=True, exist_ok=True)
        (output / 'weights.f32').write_bytes(payload)
        (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps(dict(model_id=manifest['model_id'], parameters=metadata['parameters'], bytes=len(payload))))
    example = json.loads((source / 'example_request.json').read_text())
    (destination / 'example-request.json').write_text(json.dumps(example, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=DEFAULT_SOURCE)
    parser.add_argument('--destination', type=Path, default=DEFAULT_DESTINATION)
    args = parser.parse_args()
    export(args.source.resolve(), args.destination.resolve())

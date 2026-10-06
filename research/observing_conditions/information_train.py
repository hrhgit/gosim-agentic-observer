"""One initialization each for GRU and MLP; information reliability first.

Training-only and finalization stages are resumable. Existing experiments are read-only.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
from pathlib import Path
import platform
import time

import numpy as np
import torch

from .data import ROOT, DEFAULT_OUTPUT, save_json, source_hashes
from .features import VERSION as FEATURE_VERSION
from .models import Normalizer
from .information_model import (VERSION, InformationNet, InformationBaseline, training_loss,
                                infer, measure, selection_score, calibration_fit, calibration_apply,
                                reliability_gate, NAMES)

DEFAULT_INFORMATION = ROOT / 'artifacts/observing_conditions-information'
ARCHITECTURES = ('gru', 'mlp')
SEED = 101
EPOCHS = 20
TRAIN_GROUPS = 32
EVALUATION_GROUPS = 64


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def subset(data, mask):
    return {k: v[mask] for k, v in data.items()}


def load_public_partitions(source, splits, groups, prefix=''):
    manifest = json.loads((source / 'manifest.json').read_text())
    fields = {k: [] for k in ('context', 'history', 'candidate', 'y')}
    scenes, cards = [], []
    for entry in manifest['entries']:
        if entry['split'] not in splits:
            continue
        directory = source / 'scenarios' / entry['id']
        if entry['split'] != 'site_test':
            if (directory / 'truth_and_public/v4_night_calendar.csv').read_bytes() != (
                    ROOT / 'cards' / entry['card'] / 'public/v4_night_calendar.csv').read_bytes():
                raise ValueError('formal public calendar was changed')
        with np.load(directory / 'samples.npz') as a:
            ix = np.linspace(0, len(a['context']) - 1, groups, dtype=int)
            if len(np.unique(ix)) != groups:
                raise ValueError('requested more independent groups than exist')
            for key in fields:
                fields[key].append(a[key][ix])
        scenes.extend([prefix + entry['id']] * groups)
        cards.extend([entry['card']] * groups)
    return {k: np.concatenate(v) for k, v in fields.items()} | dict(scene=np.array(scenes), card=np.array(cards))


def final_entries():
    entries = []
    for ci, card in enumerate('abcd'):
        for i in range(4):
            entries.append(dict(id=f'info-{card}-test-{i}', card=card, split='test', seed=960000 + 1000 * ci + i))
        for i in range(2):
            entries.append(dict(id=f'info-{card}-weather-{i}', card=card, split='weather_test', seed=970000 + 1000 * ci + i))
        for i in range(2):
            entries.append(dict(id=f'info-{card}-site-{i}', card=card, split='site_test', site_index=i,
                                seed=980000 + 1000 * ci + i))
    return entries


def protocol(base, output):
    source = json.loads((base / 'manifest.json').read_text())
    if source['sources'] != source_hashes():
        raise ValueError('formal public inputs or generator changed since the existing data')
    entries = final_entries()
    prior_seeds = set()
    for directory in ('observing_conditions', 'observing_conditions-v2', 'observing_conditions-trees'):
        path = ROOT / 'artifacts' / directory / 'manifest.json'
        if path.exists():
            prior_seeds.update(e['seed'] for e in json.loads(path.read_text())['entries'])
    if prior_seeds & {e['seed'] for e in entries}:
        raise ValueError('new final seeds overlap older experiments')
    inputs = {}
    for directory, splits in ((base, ('train', 'validation', 'calibration')),
                              (ROOT / 'artifacts/observing_conditions-v2', ('train',))):
        manifest = json.loads((directory / 'manifest.json').read_text())
        inputs[str(directory / 'manifest.json')] = digest(directory / 'manifest.json')
        for e in manifest['entries']:
            if e['split'] in splits:
                path = directory / 'scenarios' / e['id'] / 'samples.npz'
                inputs[str(path.relative_to(ROOT))] = digest(path)
    spec = dict(version=VERSION, feature_version=FEATURE_VERSION, architectures=list(ARCHITECTURES),
                seed=SEED, initializations_per_model=1, epochs=EPOCHS, learning_rate=.001,
                optimizer='AdamW', weight_decay=1e-4, dropout=.1, gradient_norm_limit=1,
                device='cpu', cpu_threads=1, precision='float32', batch_groups=16, batch_positions=512,
                device_reason='Existing same-encoder local measurements: CPU faster for GRU and MLP.',
                training='224 original train scenes plus 32 existing augmentation train scenes; 32 evenly spaced groups each',
                training_records=256 * TRAIN_GROUPS * 32,
                validation='32 existing scenes, 64 groups each; first half calibrates temporary selection probabilities/intervals',
                calibration='32 separate existing scenes, 64 groups each; never used to train neural weights',
                development_limitation='Existing training/validation/calibration scenes reused from earlier research; new final scenes are fresh.',
                selection='Validation only. Reliability gate then mean of four baseline-normalized proper scores; no mean-error or ranking term.',
                main_metrics=['quality_pinball', 'usable_pinball', 'blocked_brier', 'band_brier'],
                loss='Q pinball + U pinball + binary CE + band CE + .2*(normalized Q mean MSE + U mean MSE)',
                output_names=list(NAMES), source='Formal A-D public input + explicitly self-generated weather; NOT official hidden weather/results',
                quality_definition='Q_env, instrument efficiency=1; hidden instrument faults excluded',
                final_entries=entries, final_groups_per_scene=EVALUATION_GROUPS,
                regions=dict(directions=8, altitude_layers=3, representatives_per_region=9,
                             weights='equal solid angle within each region; conditional on whole-exposure height eligibility',
                             exposure_groups_per_scene=8, mean_interval='calibrated on region-mean errors, NOT averaged point intervals'),
                test_rule='Generate final scenarios only after model selection, all calibration, and parameters are frozen; never retune on final results',
                official_sources=source['sources'], input_hashes=inputs)
    path = output / 'protocol.json'
    if path.exists() and json.loads(path.read_text()) != spec:
        raise ValueError('protocol changed; use another output directory')
    save_json(path, spec)
    save_json(output / 'manifest.json', dict(version=FEATURE_VERSION, entries=entries,
              groups_per_scene=EVALUATION_GROUPS, sources=source['sources'], source=spec['source'], pilot=False))
    return spec


def make_model(architecture, data):
    return InformationNet(architecture, data['context'].shape[-1], data['history'].shape[-1],
                          data['candidate'].shape[-1])


def load_model(directory):
    meta = json.loads((directory / 'metadata.json').read_text())
    model = InformationNet(meta['architecture'], meta['context_dim'], meta['history_dim'], meta['candidate_dim'])
    model.load_state_dict(torch.load(directory / 'weights.pt', map_location='cpu', weights_only=True))
    return model.eval()


def train_one(output, architecture, train, validation, normalizer, reference, signature):
    directory = output / 'models' / architecture
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / '.training.lock').open('a') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if (directory / 'complete.json').exists():
            if json.loads((directory / 'complete.json').read_text())['signature'] != signature:
                raise ValueError('completed training signature changed')
            return load_model(directory)
        torch.manual_seed(SEED)
        model = make_model(architecture, train)
        optimizer = torch.optim.AdamW(model.parameters(), lr=.001, weight_decay=1e-4)
        normalized = normalizer.apply(train)
        tensors = {k: torch.from_numpy(v) for k, v in normalized.items()}
        tensors['y'] = torch.from_numpy(train['y'])
        val_normalized = normalizer.apply(validation)
        scale = float(normalizer.values['quality_scale'])
        first, best, elapsed = 0, float('inf'), 0.
        latest = directory / 'latest.pt'
        if latest.exists():
            state = torch.load(latest, map_location='cpu', weights_only=True)
            if state['signature'] != signature:
                raise ValueError('resume signature changed')
            model.load_state_dict(state['model']); optimizer.load_state_dict(state['optimizer'])
            torch.set_rng_state(state['rng'])
            first, best, elapsed = state['epoch'] + 1, state['best'], state['elapsed']
        save_json(directory / 'metadata.json', dict(version=VERSION, architecture=architecture, seed=SEED,
                  context_dim=train['context'].shape[-1], history_dim=train['history'].shape[-1],
                  candidate_dim=train['candidate'].shape[-1], quality_scale=scale,
                  parameters=sum(p.numel() for p in model.parameters()), epochs=EPOCHS,
                  training_records=train['y'].shape[0] * train['y'].shape[1], signature=signature,
                  torch=torch.__version__, python=platform.python_version()))
        normalizer.save(directory / 'normalizer.npz')
        for epoch in range(first, EPOCHS):
            begin = time.perf_counter(); model.train()
            for group in optimizer.param_groups:
                group['lr'] = .001 * (.1 + .9 * .5 * (1 + math.cos(math.pi * epoch / (EPOCHS - 1))))
            order = np.random.default_rng(SEED * 1000 + epoch).permutation(len(train['context']))
            total = 0.
            for offset in range(0, len(order), 16):
                ix = order[offset:offset + 16]
                optimizer.zero_grad(set_to_none=True)
                p = model(tensors['context'][ix], tensors['history'][ix], tensors['candidate'][ix])
                loss = training_loss(p, tensors['y'][ix], scale)
                loss.backward(); torch.nn.utils.clip_grad_norm_(model.parameters(), 1); optimizer.step()
                total += float(loss.detach())
            m = measure(validation['y'], infer(model, val_normalized, scale))
            score = selection_score(m, reference)
            if not np.isfinite(score):
                raise ValueError('nonfinite validation score')
            if score < best:
                best = score
                torch.save(model.state_dict(), directory / 'weights.pt')
                save_json(directory / 'best_validation.json', dict(epoch=epoch + 1, score=score, metrics=m))
            elapsed += time.perf_counter() - begin
            row = dict(epoch=epoch + 1, training_loss=total / math.ceil(len(order) / 16),
                       validation_score=score, validation=m, elapsed_seconds=elapsed)
            with (directory / 'history.jsonl').open('a') as f:
                f.write(json.dumps(row) + '\n')
            temporary = directory / 'latest.tmp'
            torch.save(dict(signature=signature, model=model.state_dict(), optimizer=optimizer.state_dict(),
                            epoch=epoch, best=best, elapsed=elapsed, rng=torch.get_rng_state()), temporary)
            temporary.replace(latest)
            save_json(output / 'state.json', dict(stage='training', architecture=architecture, epoch=epoch + 1,
                                                epochs=EPOCHS, elapsed_seconds=elapsed))
            print(f'{architecture} {epoch + 1}/{EPOCHS} score={score:.4f} Q-range={m["quality_coverage"]:.3f} '
                  f'U-range={m["usable_coverage"]:.3f} elapsed={elapsed:.1f}s', flush=True)
        save_json(directory / 'complete.json', dict(signature=signature, epochs=EPOCHS, seed=SEED,
                                                  elapsed_seconds=elapsed, initializations=1))
    return load_model(directory)


def train_run(base, output):
    output.mkdir(parents=True, exist_ok=True)
    p = protocol(base, output)
    torch.set_num_threads(1)
    begin = time.perf_counter()
    train = load_public_partitions(base, ('train',), TRAIN_GROUPS)
    augmented = load_public_partitions(ROOT / 'artifacts/observing_conditions-v2', ('train',), TRAIN_GROUPS, 'v2:')
    train = {k: np.concatenate([train[k], augmented[k]]) for k in train}
    validation = load_public_partitions(base, ('validation',), EVALUATION_GROUPS)
    if set(train['scene']) & set(validation['scene']):
        raise ValueError('scene leakage')
    normalizer = Normalizer.fit(train)
    normalizer.save(output / 'normalizer.npz')
    baselines = {name: InformationBaseline(constant=name == 'constant').fit(train)
                 for name in ('constant', 'lookup')}
    for name, baseline in baselines.items():
        save_json(output / f'{name}.json', baseline.as_dict())
    reference = measure(validation['y'], baselines['lookup'].predict(validation))
    save_json(output / 'validation_reference.json', reference)
    save_json(output / 'data_loading.json', dict(seconds=time.perf_counter() - begin, train_scenes=len(np.unique(train['scene'])),
              train_records=train['y'].shape[0] * 32, validation_scenes=len(np.unique(validation['scene'])),
              validation_records=validation['y'].shape[0] * 32, normalization='training only'))
    signature = digest(output / 'protocol.json')
    print(f'Train records={train["y"].shape[0] * 32}, validation={validation["y"].shape[0] * 32}; 2 single fits', flush=True)
    models = {a: train_one(output, a, train, validation, normalizer, reference, signature) for a in ARCHITECTURES}
    first = []
    for card in 'abcd':
        scenes = np.unique(validation['scene'][validation['card'] == card])
        first.extend(scenes[:len(scenes) // 2])
    fit_mask = np.isin(validation['scene'], first)
    reference_p = baselines['lookup'].predict(validation)
    ref_settings = calibration_fit(validation['y'][fit_mask], reference_p[fit_mask])
    ref_metrics = measure(validation['y'][~fit_mask], calibration_apply(reference_p[~fit_mask], ref_settings))
    comparisons = {}
    for architecture, model in models.items():
        predictions = infer(model, normalizer.apply(validation), float(normalizer.values['quality_scale']))
        settings = calibration_fit(validation['y'][fit_mask], predictions[fit_mask])
        m = measure(validation['y'][~fit_mask], calibration_apply(predictions[~fit_mask], settings))
        comparisons[architecture] = dict(metrics=m, score=selection_score(m, ref_metrics), reliable=reliability_gate(m),
                                        temporary_calibration=settings)
    eligible = [a for a in ARCHITECTURES if comparisons[a]['reliable']] or list(ARCHITECTURES)
    selected = min(eligible, key=lambda a: comparisons[a]['score'])
    save_json(output / 'selection.json', dict(selected=selected, comparisons=comparisons,
              reliability_gate_passed=any(v['reliable'] for v in comparisons.values()),
              rule=p['selection'], initialization_repetitions=1))
    save_json(output / 'state.json', dict(stage='trained', selected=selected, training_complete=True,
                                        calibration_complete=False, final_test_complete=False))
    print(f'TRAINING COMPLETE; selected={selected}; final calibration/test still pending', flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--output', type=Path, default=DEFAULT_INFORMATION)
    parser.add_argument('--stage', choices=('train', 'finalize', 'all'), default='all')
    args = parser.parse_args()
    if args.stage in ('train', 'all'):
        train_run(args.base, args.output)
    if args.stage in ('finalize', 'all'):
        from .information_eval import finalize
        finalize(args.base, args.output)


if __name__ == '__main__':
    main()

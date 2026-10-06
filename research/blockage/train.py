"""One fixed-parameter trial per structure and grouped fold; CPU only."""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import importlib.metadata
import json
import time
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
import torch
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits
from torch import nn

from .common import PARAMS, SEED, code_hashes, digest, freeze, write_json
from .evaluate import choose_threshold, report, weights


def sequences(frame, features):
    current = frame[features].to_numpy(dtype=np.float32)
    past = np.zeros((len(frame), 16, len(features) + 2), dtype=np.float32)
    for _, indices in frame.groupby("run", sort=False).indices.items():
        for j, row in enumerate(indices):
            previous = indices[max(0, j - 16):j]
            if len(previous):
                assert (frame.iloc[previous].end <= frame.iloc[row].start).all()
                past[row, -len(previous):, :-2] = current[previous]
                past[row, -len(previous):, -2] = frame.iloc[previous].zero
                past[row, -len(previous):, -1] = 1  # known-history mask
    return current, past


class Lookup:
    def __init__(self, mode):
        self.mode = mode

    def keys(self, frame):
        if self.mode == "R1":
            return list(zip(frame.f_previous_known.astype(int), frame.f_previous_zero.astype(int)))
        keys = []
        for row in frame.itertuples():
            active = [kind for kind in ("overcast", "haze", "rain", "cold_snap", "rocket_launch", "earthquake") if getattr(row, f"f_current_{kind}_active")]
            # Preserve simultaneous events, rather than arbitrarily selecting one type.
            event = "+".join(active) or "none"
            away = min((getattr(row, f"f_current_{kind}_angle") for kind in active), default=180)
            keys.append((event, 0 if away <= 30 else 1 if away <= 45 else 2, int(row.f_altitude // 10)))
        return keys

    def fit(self, frame):
        y, w = frame.zero.to_numpy(), weights(frame)
        self.prior = float(np.average(y, weights=w))
        total, zero = {}, {}
        for key, label, weight in zip(self.keys(frame), y, w):
            total[key] = total.get(key, 0) + weight
            zero[key] = zero.get(key, 0) + weight * label
        self.table = {key: zero[key] / value for key, value in total.items()}
        return self

    def predict(self, frame):
        return np.array([self.table.get(key, self.prior) for key in self.keys(frame)])


def hand_rule(frame):
    # Reproduce the conditional closed_probability function, including the zenith modifier.
    # The constants were informed by these same formal runs; descriptive comparator, not independent training evidence.
    away = frame.f_current_cold_snap_angle.to_numpy()
    stated = frame.f_note_closed_angle.to_numpy() <= 8
    p = np.where(away <= 30, 1, np.where(stated, 0.85, np.where(away <= 45, 0.35, 0)))
    p *= np.where(frame.f_altitude.to_numpy() >= 86, 0.5, 1)
    # This is only the wind-sector helper, not a claim to reproduce the whole agent's zero-score probability.
    return p * frame.f_current_cold_snap_active.to_numpy()


class GRU(nn.Module):
    def __init__(self, size):
        super().__init__()
        self.recurrent = nn.GRU(size + 2, 32, batch_first=True)
        self.head = nn.Sequential(nn.Linear(size + 32, 32), nn.ReLU(), nn.Linear(32, 1))

    def forward(self, current, past):
        _, h = self.recurrent(past)
        return self.head(torch.cat((current, h[-1]), dim=1)).squeeze(1)


def gru_fit_predict(current, past, frame, train_ix, valid_ix, test_ix, epochs=None):
    torch.manual_seed(SEED)
    scaler = StandardScaler().fit(current[train_ix])
    x = scaler.transform(current).astype(np.float32)
    h = past.copy()
    h[:, :, :-2] = (h[:, :, :-2] - scaler.mean_) / scaler.scale_
    h[:, :, :-2] *= h[:, :, -1:]
    tx, th = torch.from_numpy(x), torch.from_numpy(h)
    y = torch.from_numpy(frame.zero.to_numpy(dtype=np.float32))
    w = torch.from_numpy(weights(frame.iloc[train_ix].reset_index(drop=True)).astype(np.float32))
    model, best_loss, best_state, best_epoch = GRU(x.shape[1]), float("inf"), None, 1
    optimizer = torch.optim.AdamW(model.parameters(), lr=0.001)
    rng = np.random.default_rng(SEED)
    def predict(ix):
        model.eval()
        with torch.no_grad():
            return np.concatenate([torch.sigmoid(model(tx[ix[j:j + 256]], th[ix[j:j + 256]])).numpy() for j in range(0, len(ix), 256)])
    for epoch in range(epochs or 15):
        model.train()
        order = rng.permutation(len(train_ix))
        for j in range(0, len(order), 256):
            loc = order[j:j + 256]
            ix = train_ix[loc]
            optimizer.zero_grad()
            loss = (nn.functional.binary_cross_entropy_with_logits(model(tx[ix], th[ix]), y[ix], reduction="none") * w[loc]).mean()
            loss.backward()
            optimizer.step()
        if epochs:
            best_epoch = epoch + 1
        else:
            p = np.clip(predict(valid_ix), 1e-6, 1 - 1e-6)
            vy = frame.iloc[valid_ix].zero.to_numpy()
            value = float(np.average(-vy * np.log(p) - (1 - vy) * np.log1p(-p), weights=weights(frame.iloc[valid_ix].reset_index(drop=True))))
            if value < best_loss:
                best_loss, best_epoch = value, epoch + 1
                best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
    if best_state:
        model.load_state_dict(best_state)
    return predict(test_ix), best_epoch


def fit_model(name, current, past, frame, ti, vi, predict_ix, budget=None):
    if name == "M2":
        return gru_fit_predict(current, past, frame, ti, vi, predict_ix, epochs=budget)
    # Trees and logistic regression receive exactly the same ordered history as the GRU.
    x = np.concatenate([current, past.reshape(len(frame), -1)], axis=1)
    w = weights(frame.iloc[ti].reset_index(drop=True))
    if name == "M1":
        model = lgb.LGBMClassifier(**{**PARAMS, "n_estimators": budget or 400})
        options = {} if budget else dict(eval_set=[(x[vi], frame.iloc[vi].zero)], eval_sample_weight=[weights(frame.iloc[vi].reset_index(drop=True))], eval_metric="binary_logloss", callbacks=[lgb.early_stopping(30, verbose=False)])
        model.fit(x[ti], frame.iloc[ti].zero, sample_weight=w, **options)
        return model.predict_proba(x[predict_ix])[:, 1], int(model.best_iteration_ or model.n_estimators)
    scaler = StandardScaler().fit(x[ti])
    model = LogisticRegression(C=1, max_iter=1000, random_state=SEED)
    model.fit(scaler.transform(x[ti]), frame.iloc[ti].zero, sample_weight=w)
    return model.predict_proba(scaler.transform(x[predict_ix]))[:, 1], None


def experiment(frame, features, smoke=False, old_only=False, held_selection=None, inner_mode="card-oof"):
    current, past = sequences(frame, features)
    rows, metadata = [], []
    held_cards = sorted(c for c in frame.card.unique() if c.endswith("1"))
    if smoke:
        held_cards = held_cards[:1]
    if held_selection:
        held_cards = held_selection
    for held in held_cards:
        candidates = sorted(c for c in frame.card.unique() if c != held and (not old_only or not c.endswith("1")))
        # Fixed inner leave-card holdout. Both runs stay together; no row-level shuffle splitting.
        valid_card = next((c for c in candidates if c.endswith("1")), candidates[0])
        outer_train = np.flatnonzero(frame.card.isin(candidates).to_numpy())
        ti = np.flatnonzero(frame.card.isin([c for c in candidates if c != valid_card]).to_numpy())
        vi = np.flatnonzero((frame.card == valid_card).to_numpy())
        test = np.flatnonzero((frame.card == held).to_numpy())
        detail = dict(test_card=held, inner_validation_card=valid_card if inner_mode == "fixed" else candidates, inner_training_cards=[c for c in candidates if c != valid_card] if inner_mode == "fixed" else "all other outer-training cards for each inner holdout",
                      outer_training_cards=candidates, budgets={}, thresholds={}, calibration="isotonic on inner leave-card only")
        vframe, tframe = frame.iloc[vi].reset_index(drop=True), frame.iloc[test].reset_index(drop=True)
        for name in ("R0", "R1", "R2", "R2_hand", "M1", "M2", "M3"):
            began = time.monotonic()
            if inner_mode == "card-oof" and not smoke:
                raw_valid = np.full(len(frame), np.nan)
                budgets = []
                for inner in candidates:
                    it = np.flatnonzero(frame.card.isin([c for c in candidates if c != inner]).to_numpy())
                    iv = np.flatnonzero((frame.card == inner).to_numpy())
                    inner_frame = frame.iloc[iv].reset_index(drop=True)
                    if name == "R0":
                        ip = np.full(len(iv), np.average(frame.iloc[it].zero, weights=weights(frame.iloc[it].reset_index(drop=True))))
                    elif name in ("R1", "R2"):
                        ip = Lookup(name).fit(frame.iloc[it].reset_index(drop=True)).predict(inner_frame)
                    elif name == "R2_hand":
                        ip = hand_rule(inner_frame)
                    else:
                        ip, ib = fit_model(name, current, past, frame, it, iv, iv)
                        if ib:
                            budgets.append(ib)
                    raw_valid[iv] = ip
                    print(f"{held} {name} inner={inner}: complete", flush=True)
                assert np.isfinite(raw_valid[outer_train]).all()
                vframe = frame.iloc[outer_train].reset_index(drop=True)
                vp = raw_valid[outer_train]
                budget = max(1, int(np.median(budgets))) if budgets else None
                if name == "R0":
                    tp = np.full(len(test), np.average(frame.iloc[outer_train].zero, weights=weights(vframe)))
                elif name in ("R1", "R2"):
                    tp = Lookup(name).fit(vframe).predict(tframe)
                elif name == "R2_hand":
                    tp = hand_rule(tframe)
                else:
                    calibrator = IsotonicRegression(out_of_bounds="clip").fit(vp, vframe.zero, sample_weight=weights(vframe))
                    tp, _ = fit_model(name, current, past, frame, outer_train, vi, test, budget=budget)
                    vp, tp = calibrator.predict(vp), calibrator.predict(tp)
                detail.setdefault("inner_budgets", {})[name] = budgets
            elif name == "R0":
                vp = np.full(len(vi), np.average(frame.iloc[ti].zero, weights=weights(frame.iloc[ti].reset_index(drop=True))))
                tp = np.full(len(test), np.average(frame.iloc[outer_train].zero, weights=weights(frame.iloc[outer_train].reset_index(drop=True))))
                budget = None
            elif name in ("R1", "R2"):
                vp = Lookup(name).fit(frame.iloc[ti].reset_index(drop=True)).predict(vframe)
                tp = Lookup(name).fit(frame.iloc[outer_train].reset_index(drop=True)).predict(tframe)
                budget = None
            elif name == "R2_hand":
                vp, tp, budget = hand_rule(vframe), hand_rule(tframe), None
            else:
                vp, budget = fit_model(name, current, past, frame, ti, vi, vi, budget=1 if smoke and name == "M2" else 5 if smoke and name == "M1" else None)
                calibrator = IsotonicRegression(out_of_bounds="clip").fit(vp, vframe.zero, sample_weight=weights(vframe))
                tp, _ = fit_model(name, current, past, frame, outer_train, vi, test, budget=budget)
                vp, tp = calibrator.predict(vp), calibrator.predict(tp)
            threshold = choose_threshold(vframe, vp)
            detail["budgets"][name], detail["thresholds"][name] = budget, threshold
            saved = tframe[["row_id", "run", "source", "card", "start", "end", "zero", "first_zero", "avoidable_hint", "f_duration"]].copy()
            saved["model"], saved["probability"], saved["threshold"] = name, tp, threshold
            rows.append(saved)
            print(f"{held} {name}: {time.monotonic() - began:.1f}s budget={budget} threshold={threshold:.3f}", flush=True)
        metadata.append(detail)
    return pd.concat(rows, ignore_index=True), metadata


def outer_job(dataset, features, held, old_only, inner_mode, directory):
    """Bounded independent processes, each with one CPU thread and its own grouped fold."""
    torch.set_num_threads(1)
    frame = pd.read_parquet(dataset)
    with threadpool_limits(limits=1):
        predictions, folds = experiment(frame, features, False, old_only, [held], inner_mode)
    path = Path(directory) / f"fold-{held}.parquet"
    predictions.to_parquet(path, index=False)
    write_json(Path(directory) / f"fold-{held}.json", folds[0])
    return str(path), folds[0]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dataset", type=Path, default=Path("artifacts/blockage-v1/exposures.parquet"))
    p.add_argument("--out", type=Path, default=Path("artifacts/blockage-v1/train"))
    p.add_argument("--smoke", action="store_true")
    p.add_argument("--old-only", action="store_true")
    p.add_argument("--inner-mode", choices=("fixed", "card-oof"), default="card-oof")
    p.add_argument("--jobs", type=int, choices=(1, 2), default=2)
    args = p.parse_args()
    frame = pd.read_parquet(args.dataset)
    if not args.smoke:
        source_protocol = json.loads((args.dataset.parent / "protocol.json").read_text())
        if source_protocol["limit_per_run"]:
            raise ValueError("formal evaluation cannot use a truncated dataset")
    features = [c for c in frame if c.startswith("f_") and (not args.old_only or not c.startswith("f_note_"))]
    freeze(args.out, dict(stage="old_only" if args.old_only else "four_fold", seed=SEED, code=code_hashes(),
                         dataset_sha256=digest(args.dataset), features=features, history=16,
                         tree=PARAMS, gru=dict(hidden=32, head=32, learning_rate=0.001, batch=256, epochs=15, optimizer="AdamW"),
                         logistic=dict(C=1, max_iter=1000), smoke=args.smoke,
                         split="outer leave-new-card; inner every outer-training card held out, or explicitly marked fixed exploratory split; final budget median of inner early-stopping budgets",
                         inner_mode=args.inner_mode, jobs=args.jobs,
                         calibration="isotonic on concatenated inner out-of-card predictions only; no outer scores for model/threshold selection",
                         threshold="inner baseline net-saving proxy; grid 0..1 step .01 plus flag-none; conservative tie break",
                         weights="equal card mass; equal source mass per card; duration baseline/count probe",
                         gates=dict(min_passing_cards=3, reliability_bias=0.05, min_bin_count=50, brier_ratio_r2=0.9, net_saving=0.01),
                         versions={n: importlib.metadata.version(n) for n in ("numpy", "pandas", "lightgbm", "torch", "scikit-learn")}))
    torch.set_num_threads(1)
    try:
        if args.smoke or args.jobs == 1:
            with threadpool_limits(limits=1):
                predictions, folds = experiment(frame, features, args.smoke, args.old_only, inner_mode=args.inner_mode)
        else:
            held = sorted(c for c in frame.card.unique() if c.endswith("1"))
            with ProcessPoolExecutor(max_workers=args.jobs) as pool:
                futures = [pool.submit(outer_job, args.dataset, features, c, args.old_only, args.inner_mode, args.out) for c in held]
                completed = [future.result() for future in futures]
            predictions = pd.concat([pd.read_parquet(path) for path, _ in completed], ignore_index=True)
            folds = [fold for _, fold in completed]
        predictions.to_parquet(args.out / "predictions.parquet", index=False)
        write_json(args.out / "folds.json", folds)
        summary = report(predictions, folds)
        summary["smoke_only"] = args.smoke
        write_json(args.out / "report.json", summary)
        print(json.dumps({m: dict(verdict=r["verdict"], passing_cards=r["passing_cards"], means=r["macro_mean"]) for m, r in summary["models"].items()}, indent=2), flush=True)
    except Exception as e:
        write_json(args.out / "failure.json", dict(error=repr(e)))
        raise


if __name__ == "__main__":
    main()

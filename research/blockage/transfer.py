"""Old-card transfer: fit once, evaluate all four new cards, with old-card OOF calibration."""
from __future__ import annotations

import argparse
import importlib.metadata
import json
from pathlib import Path

import pandas as pd
import torch
from threadpoolctl import threadpool_limits

from .common import PARAMS, SEED, code_hashes, digest, freeze, write_json
from .evaluate import report
from .train import experiment


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dataset", type=Path, default=Path("artifacts/blockage-v1/exposures.parquet"))
    p.add_argument("--out", type=Path, default=Path("artifacts/blockage-v2/old-only"))
    args = p.parse_args()
    frame = pd.read_parquet(args.dataset)
    if json.loads((args.dataset.parent / "protocol.json").read_text())["limit_per_run"]:
        raise ValueError("transfer evaluation requires full dataset")
    features = [c for c in frame if c.startswith("f_") and not c.startswith("f_note_")]
    freeze(args.out, dict(stage="old_card_transfer", code=code_hashes(), dataset_sha256=digest(args.dataset), seed=SEED,
                         features=features, history=16, tree=PARAMS,
                         gru=dict(hidden=32, head=32, epochs=15, optimizer="AdamW", learning_rate=0.001, batch=256),
                         logistic=dict(C=1, max_iter=1000), split="train only old A-D; inner leave each old card out; all new cards test only",
                         calibration="isotonic on old-card OOF predictions; final budget median of inner best budgets",
                         optimization="one identical old-card model per structure, evaluated on all new cards without refitting duplicates",
                         threshold="old-card OOF proxy net saving, same for all new cards; 0..1 step .01 plus flag-none",
                         weights="equal cards and source mass; baseline duration / probe count",
                         gates=dict(min_passing_cards=3, reliability_bias=0.05, min_bin_count=50, brier_ratio_r2=0.9, net_saving=0.01),
                         versions={n: importlib.metadata.version(n) for n in ("numpy", "pandas", "lightgbm", "torch", "scikit-learn")}))
    torch.set_num_threads(1)
    try:
        original = dict(zip(frame.row_id, frame.card))
        new_cards = sorted(c for c in frame.card.unique() if c.endswith("1"))
        # Grouping field only, absent from all model features. Histories retain original run IDs.
        # This permits one shared fit; recover real card identity before any evaluation.
        frame.loc[frame.card.isin(new_cards), "card"] = "transfer-test1"
        with threadpool_limits(limits=1):
            predictions, folds = experiment(frame, features, old_only=True)
        predictions["card"] = predictions.row_id.map(original)
        folds[0]["test_card"] = new_cards
        predictions.to_parquet(args.out / "predictions.parquet", index=False)
        write_json(args.out / "folds.json", folds)
        results = report(predictions, folds)
        results["old_only"] = True
        write_json(args.out / "report.json", results)
        print(results["overall_verdict"], flush=True)
    except Exception as e:
        write_json(args.out / "failure.json", dict(error=repr(e)))
        raise


if __name__ == "__main__":
    main()

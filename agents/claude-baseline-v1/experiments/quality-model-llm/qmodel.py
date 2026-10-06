import json, sys, numpy as np
from pathlib import Path
from research.observing_conditions import reliability_data as rd
out = Path("artifacts/observing_conditions-reliability")
data = rd.load(out, "test")
z = np.load(out / "test_predictions.npz")
y = data["y"]; hold = data["hold"]; meta = data["meta"]
print("rows", len(y), "hold shape", hold.shape, "meta keys", sorted(meta[0].keys())[:40])
print("example meta", {k: meta[0][k] for k in list(meta[0])[:25]})
assert np.allclose(z["y"], y)
sel = "gru64/fresh_hybrid_12/endpoint_raw"
def stats(p, mask):
    q = y[mask, 0]; b = y[mask, 3].astype(int)
    mae = np.abs(q - p[mask, 0]).mean()
    acc = (p[mask, 9:12].argmax(1) == b).mean()
    brier = ((p[mask, 9:12] - np.eye(3)[b]) ** 2).sum(1).mean()
    cov = ((p[mask, 1] <= q) & (q <= p[mask, 3])).mean()
    width = (p[mask, 3] - p[mask, 1]).mean()
    blk = ((p[mask, 8] - y[mask, 2]) ** 2).mean()
    return mae, acc, brier, cov, width, blk
names = [("hold", hold), ("gru64", z[sel]), ("tree64", z["tree64/previous"])]
keys = sorted({str(m.get("horizon")) for m in meta}); print("horizons", keys, "band counts", np.bincount(y[:, 3].astype(int)))
groups = [("all", np.ones(len(y), bool))] + [(f"horizon {k}", np.array([str(m.get("horizon")) == k for m in meta])) for k in keys]
for g, mask in groups:
    print(f"== {g}: n={mask.sum()}")
    for name, p in names:
        mae, acc, brier, cov, width, blk = stats(p, mask)
        print(f"  {name:7s} quality MAE {mae:.4f} | band accuracy {acc:.1%} | band Brier {brier:.4f} | 90% range coverage {cov:.1%} width {width:.3f} | blocked Brier {blk:.4f}")

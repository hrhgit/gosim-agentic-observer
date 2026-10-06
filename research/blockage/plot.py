"""Render an existing report; plotting never changes any gate or probability."""
import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--report", type=Path, default=Path("artifacts/blockage-v2/train/report.json"))
    args = p.parse_args()
    result = json.loads(args.report.read_text(encoding="utf-8"))
    font_path = Path("C:/Windows/Fonts/msyh.ttc")
    font = FontProperties(fname=str(font_path)) if font_path.exists() else FontProperties()
    fig, axes = plt.subplots(2, 2, figsize=(8.6, 7.5), layout="constrained")
    for ax, card in zip(axes.flat, ("a1", "b1", "c1", "d1")):
        ax.plot([0, 1], [0, 1], color="#77818e", lw=1)
        ax.fill_between([0, 1], [-.05, .95], [.05, 1.05], color="#dae4ed", alpha=.5)
        bins = [b for b in result["models"]["M1"]["cards"][card]["reliability"]["bin_details"] if b["qualifying"]]
        ax.scatter([b["predicted"] for b in bins], [b["observed"] for b in bins],
                   s=[min(130, 20 + b["count"] ** .5 * 3) for b in bins],
                   c=["#16754d" if b["passes"] else "#c44b3e" for b in bins], edgecolors="white", lw=.6)
        worst = max(bins, key=lambda b: abs(b["bias"]))
        ax.annotate(str(worst["count"]), (worst["predicted"], worst["observed"]), xytext=(5, 5), textcoords="offset points", fontsize=8)
        ax.set(xlim=(0, 1), ylim=(0, 1), title=card.upper())
        ax.set_xlabel("预测全零概率", fontproperties=font)
        ax.set_ylabel("实测全零比例", fontproperties=font)
        ax.grid(alpha=.15)
    fig.suptitle("M1 留卡检验：蓝灰带为 ±5 个百分点门槛；红点未通过", fontproperties=font, fontsize=12)
    for extension in ("png", "svg"):
        fig.savefig(args.report.parent / f"reliability-M1.{extension}", dpi=180)


if __name__ == "__main__":
    main()

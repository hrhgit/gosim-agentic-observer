#!/usr/bin/env python3
"""Run several quick_sim variants in parallel and print one comparison table.

    python3.12 tools/sweep.py --name kappa --cards a c --variant kappa=0.5 --variant kappa=0.9 [--nights N]

Each --variant is a comma-separated list of knob=value; "base" (always included unless
--no-base) is the agent as checked in. Results land in results/sweeps/<name>.jsonl and .md.
Development tool: the scores come from the in-process run (see quick_sim.py), not from the
judge's subprocess protocol.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PYTHON = os.environ.get("OBSERVER_PYTHON", "python3.12")


def run(card: str, variant: str, nights: int) -> dict:
    command = [PYTHON, str(REPO / "tools" / "quick_sim.py"), "--card", card, "--quiet",
               "--out", tempfile.mkdtemp(prefix=f"sweep-{card}-")]
    if nights:
        command += ["--nights", str(nights)]
    if variant != "base":
        for pair in variant.split(","):
            command += ["--set", pair]
    proc = subprocess.run(command, capture_output=True, text=True)
    try:
        row = json.loads(proc.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError):
        row = {"card": card, "error": proc.stderr[-800:]}
    row["variant"] = variant
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--name", required=True)
    parser.add_argument("--cards", nargs="+", default=["a", "c"])
    parser.add_argument("--variant", action="append", default=[])
    parser.add_argument("--nights", type=int, default=0)
    parser.add_argument("--jobs", type=int, default=3)
    parser.add_argument("--no-base", action="store_true")
    args = parser.parse_args()
    variants = ([] if args.no_base else ["base"]) + args.variant
    jobs = [(card, variant) for variant in variants for card in args.cards]
    rows = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = [pool.submit(run, card, variant, args.nights) for card, variant in jobs]
        for future in concurrent.futures.as_completed(futures):
            row = future.result()
            rows.append(row)
            print(f"[{args.name}] {row['variant']} {row['card']}: {row.get('total', row.get('error'))}",
                  file=sys.stderr, flush=True)
    out = REPO / "results" / "sweeps"
    out.mkdir(parents=True, exist_ok=True)
    with (out / f"{args.name}.jsonl").open("w") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    lines = [f"### sweep {args.name}" + (f" (first {args.nights} nights)" if args.nights else " (full season)"), "",
             "| variant | card | total | sum_best | req missing | requests | reports | observes | ms/decision | gain/pred |",
             "|---|---|---:|---:|---:|---|---|---:|---:|---:|"]
    for variant in variants:
        for card in args.cards:
            row = next((r for r in rows if r["variant"] == variant and r["card"] == card), None)
            if row is None or "total" not in row:
                lines.append(f"| {variant} | {card.upper()} | failed | | | | | | | |")
                continue
            lines.append(f"| {variant} | {card.upper()} | {row['total']:.1f} | {row['sum_best_scores']:.1f} | "
                         f"{row['required_missing']} | {row['requests']} | {row['reports']} | {row['observe_actions']} | "
                         f"{row['ms_per_decision']} | {row.get('gain_vs_predicted', '')} |")
        totals = [r["total"] for r in rows if r["variant"] == variant and "total" in r]
        if len(totals) == len(args.cards) and len(totals) > 1:
            lines.append(f"| **{variant}** | sum | **{sum(totals):.1f}** | | | | | | | |")
    text = "\n".join(lines) + "\n"
    (out / f"{args.name}.md").write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

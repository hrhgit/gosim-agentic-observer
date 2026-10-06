#!/usr/bin/env python3
"""Compare two note-reader outputs (e.g. thinking off vs low) item by item.

    python3 tools/compare_extractions.py results/extract-a-off.jsonl results/extract-a-low.jsonl

Reports, for the notes present in both files, how many extracted items agree exactly:
fault statements (kind, action, onset), closed windows, other windows, ridge heights.
It measures agreement between two readings, not correctness; disagreements are printed for review.
"""
from __future__ import annotations

import json
import sys


def load(path: str) -> dict[str, dict]:
    with open(path, encoding="utf-8") as handle:
        return {row["id"]: row for row in map(json.loads, handle) if row.get("origin") == "model"}


def items(row: dict) -> dict[str, set[str]]:
    closed = {w for w in row["windows"] if "/closed/" in w}
    return {
        "faults": {f for f in row["faults"] if "watch" not in f and "earthquake" not in f},
        "closed windows": {w.rsplit("/", 1)[0] for w in closed},
        "other windows": {w.rsplit("/", 1)[0] for w in row["windows"] if w not in closed},
        "ridges": set(row["terrain"]),
    }


def main() -> int:
    first, second = load(sys.argv[1]), load(sys.argv[2])
    common = sorted(set(first) & set(second))
    totals: dict[str, list[int]] = {}
    for note in common:
        a, b = items(first[note]), items(second[note])
        for name in a:
            both, only_a, only_b = a[name] & b[name], a[name] - b[name], b[name] - a[name]
            row = totals.setdefault(name, [0, 0, 0])
            row[0] += len(both)
            row[1] += len(only_a)
            row[2] += len(only_b)
            if name in ("faults", "closed windows") and (only_a or only_b):
                print(f"{note} {name}:")
                for item in sorted(only_a):
                    print(f"    only in first : {item}")
                for item in sorted(only_b):
                    print(f"    only in second: {item}")
    print(f"\n{len(common)} notes read by both")
    for name, (both, only_a, only_b) in totals.items():
        union = both + only_a + only_b
        print(f"  {name:15s} agree {both:4d}  only first {only_a:3d}  only second {only_b:3d}  agreement {both / union:.0%}" if union else f"  {name:15s} none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

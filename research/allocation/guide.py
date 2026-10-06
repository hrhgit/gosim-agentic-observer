"""Omniscient diagnostic only; copied into a temporary baseline, never deployed."""
from __future__ import annotations

import bisect
import os

import numpy as np


class Guide:
    def __init__(self, catalog):
        path = os.environ.get("BASE_GUIDE")
        self.data = None
        if not path:
            return
        self.data = np.load(path)
        if list(map(tuple, self.data["zone_keys"])) != catalog.zkeys:
            raise ValueError("oracle allocation and baseline zone IDs differ")
        self.starts, self.ends = self.data["bin_start"], self.data["bin_end"]
        self.x = self.data["x"]
        self.maximum = self.x.max(axis=0)
        self.strength = os.environ.get("BASE_GUIDE_STRENGTH", "1")

    def factor(self, z, t, protected):
        if self.data is None:
            return 1
        b = bisect.bisect_right(self.starts, t) - 1
        if b < 0 or t >= self.ends[b]:
            return 1
        allocated = self.x[z, b]
        if self.strength == "hard":
            return int(protected or allocated >= 0.02)
        maximum = self.maximum[b]
        return 1 + float(self.strength) * allocated / maximum if maximum > 0 else 1

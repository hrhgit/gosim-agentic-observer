import importlib.util
import math
from pathlib import Path

import numpy as np
import pytest

from research.allocation.guide import Guide


def test_guide_bin_edges_protection_and_zone_identity(tmp_path, monkeypatch):
    """Explicitly self-generated diagnostic allocation."""
    p = tmp_path / "generated-allocation.npz"
    np.savez(p, zone_keys=np.array([[1, 2], [3, 4]]), bin_start=[10, 30], bin_end=[20, 40], x=[[1, 0], [0, 1]])
    class Catalog:
        zkeys = [(1, 2), (3, 4)]
    monkeypatch.setenv("BASE_GUIDE", str(p))
    monkeypatch.setenv("BASE_GUIDE_STRENGTH", "hard")
    guide = Guide(Catalog())
    assert guide.factor(1, 10, False) == 0
    assert guide.factor(1, 10, True) == 1
    assert guide.factor(1, 20, False) == 1  # gap between bins, no oracle restriction
    assert guide.factor(1, 30, False) == 1
    monkeypatch.setenv("BASE_GUIDE_STRENGTH", "3")
    assert Guide(Catalog()).factor(0, 10, False) == 4
    Catalog.zkeys = [(3, 4), (1, 2)]
    with pytest.raises(ValueError, match="zone IDs"):
        Guide(Catalog())


def test_no_guide_is_exact_identity(monkeypatch):
    monkeypatch.delenv("BASE_GUIDE", raising=False)
    assert Guide(None).factor(500, 999, False) == 1


def test_coordinate_roundtrip():
    import datetime as dt
    from research.blockage.dataset import altaz
    path = Path(__file__).resolve().parents[3] / "agents/claude-baseline-v1/tools/compare_allocation.py"
    spec = importlib.util.spec_from_file_location("allocation_compare_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    now = dt.datetime(2026, 10, 2, tzinfo=dt.timezone.utc)
    for ra, dec in [(359, 0), (90, -20), (180, 60)]:
        alt, az = altaz(ra, dec, -24.6157, -70.3976, now)
        r, d = module.equatorial(alt, az, now.timestamp(), -24.6157, -70.3976)
        assert abs((r - ra + 180) % 360 - 180) < 1e-6 and abs(d - dec) < 1e-6


def test_soft_guidance_preserves_request_bonus_at_runtime(tmp_path, monkeypatch):
    """Self-generated two-zone choice: boosting a request bonus would choose the wrong zone."""
    import sys
    from types import SimpleNamespace as NS
    from research.allocation import guide as guide_module
    from research.allocation.run_gates import BASE, make_copy
    temporary = make_copy(tmp_path)
    monkeypatch.syspath_prepend(str(BASE / "agent"))
    monkeypatch.setitem(sys.modules, "core.allocation_guide", guide_module)
    spec = importlib.util.spec_from_file_location("core.test_allocation_planner", temporary / "agent/core/planner.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cat = NS(nz=2, zcx=[0, 0], zcy=[0, 0], zcz=[1, 1], zcoarse=[0, 0], flux=[1], zmembers=[[0], [1]])
    site = NS(frame=lambda t: (0, (0, 0, 1), (0, 1, 0), (1, 0, 0)), moon=lambda t: (0, 0, 0, 0), atab=[0, 1])
    sky = NS(levels=lambda: (1, 1), directional=False)
    survey = NS(cat=cat, site=site, sky=sky, nights=[(0, 3600)], t_max=3600, t_min=60,
                price=0, knobs={"deep_weight": 0.1}, zpot=[1, 5], zflag=[True, False],
                zreq=[[], []], zdeep=[0, 0], zextra={0: [0]}, extra={0: (2, 1000, 1)},
                need_req=[], T_REF=60, f0t0=1, sci_urgency=[1])
    planner = module.Planner.__new__(module.Planner)
    planner.sv, planner.tab, planner.sin_scan = survey, 1, 0.1
    planner.allocation_guide = NS(factor=lambda z, t, protected: 2 if z == 0 else 1)
    chosen = []
    planner._evaluate = lambda z, ctx: (chosen.append(z), None)[1]
    planner.plan(0, 0, 1, 60, 3600, False)
    # Correct science-only scores: zone0=1*2+2=4, zone1=5.
    # Multiplying the complete score would give zone0=(1+2)*2=6.
    assert chosen == [1]

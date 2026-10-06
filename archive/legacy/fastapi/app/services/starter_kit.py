"""Builds the downloadable starter kit zip from the repository files (always in sync with the platform)."""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

from ..config import ROOT

KIT_DIR = ROOT / "starter_kit"
SCORING_DIR = ROOT / "scoring"


def build_starter_kit_zip(skill_text: str, readme_text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        base = "agent-observer-starter-kit/"
        zf.writestr(base + "README.md", readme_text)
        zf.writestr(base + "SKILL.md", skill_text)
        for name in ("agent.py", "local_runner.py", "sac_submit.py"):
            zf.write(KIT_DIR / name, base + name)
        for name in ("scorer.py", "protocol.py", "score_config.json"):
            zf.write(SCORING_DIR / name, base + name)
        zf.write(KIT_DIR / "generate_example_data.py", base + "generate_example_data.py")
        for p in sorted((KIT_DIR / "example").iterdir()):
            if p.is_file() and p.name != "decisions.csv":
                zf.write(p, base + "example/" + p.name)
    return buf.getvalue()

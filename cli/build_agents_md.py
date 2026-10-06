"""Writes cli/AGENTS.md (English, then Chinese) from the website's guide web/src/content/cli.{en,zh}.md.

Run after editing the guide: python3 cli/build_agents_md.py
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = "https://create.gosim.org/survey26/platform/"


def render() -> str:
    parts = [(ROOT / "web" / "src" / "content" / f"cli.{lang}.md").read_text("utf-8").strip() for lang in ("en", "zh")]
    head = "<!-- Generated from web/src/content/cli.{en,zh}.md by cli/build_agents_md.py; edit those files. -->\n\n"
    return head + "\n\n---\n\n".join(parts).replace("__BASE_URL__", SITE) + "\n"


if __name__ == "__main__":
    (ROOT / "cli" / "AGENTS.md").write_text(render(), "utf-8")

"""The v4 practice cards (Practice α β γ δ, cards/v4-practice-*/card.json): the committed facts are
seedless and complete, and every card page is rendered from them."""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _script():
    spec = importlib.util.spec_from_file_location("build_v4_practice_cards", ROOT / "scripts" / "build-v4-practice-cards.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = _script()
CARDS = MODULE.card_files()
KEYS = {"card_id", "slug", "phase", "stress", "wallclock", "site", "first_night", "last_night", "nights", "targets",
        "required", "regions", "area", "sun_limit", "min_alt", "fibers", "fiber_area", "exposure", "f0", "t0", "checksum"}


def test_four_practice_cards_alpha_to_delta():
    cards = {slug: json.loads(path.read_text(encoding="utf-8")) for slug, path in CARDS.items()}
    assert list(cards) == ["v4-practice-alpha", "v4-practice-beta", "v4-practice-delta", "v4-practice-gamma"]
    for slug, card in cards.items():
        assert set(card) == KEYS, slug
        assert card["slug"] == slug == f"v4-practice-{card['card_id']}" and card["phase"] == "practice-projects"
        assert card["wallclock"] == 900 and card["nights"] > 0 and 0 < card["required"] < card["targets"]
        assert re.fullmatch(r"[0-9a-f]{64}", card["checksum"])
        assert "seed" not in json.dumps(card).lower(), slug
    assert {card["card_id"] for card in cards.values()} == {"alpha", "beta", "gamma", "delta"}
    assert sorted(path.name for path in (ROOT / "cards").glob("v4-practice-*/*")) == ["card.json"] * 4


def test_card_pages_are_rendered_from_their_facts():
    # A page edit that is not in the renderer (or a card.json change without --pages) fails here.
    for path in CARDS.values():
        card_id = MODULE.facts(path)["card_id"]
        for language in ("en", "zh"):
            assert MODULE.page_path(card_id, language).read_text(encoding="utf-8") == MODULE.render_page(path, language), \
                f"{card_id}/{language}: run scripts/build-v4-practice-cards.py --pages"


COMPARATIVE = re.compile(r"新卡|旧卡|老卡|替换|换成|新版|旧版|改版|更新后|原来的|new card|old card|replac|previous (?:card|version)|"
                         r"updated card|new version|no longer", re.I)


def test_card_pages_describe_each_card_on_its_own():
    # Participant pages state what a card is; they never compare it with an earlier set of cards.
    for path in CARDS.values():
        card_id = MODULE.facts(path)["card_id"]
        for language in ("en", "zh"):
            match = COMPARATIVE.search(MODULE.page_path(card_id, language).read_text(encoding="utf-8"))
            assert match is None, f"{card_id}/{language}: {match.group(0)!r}"


def test_facts_are_read_from_a_bundle(tmp_path):
    # --facts reads config/ and public/ of a built bundle; the demo card stands in for a practice bundle.
    from challenge.v4_bundle import build_card_bundle
    root = build_card_bundle(tmp_path / "card", {
        "name": "v4-practice-alpha", "card_id": "alpha", "scenario_slug": "v4-practice-alpha", "phase": "practice-projects",
        "seed": 20261002, "start_date": "2026-10-04", "end_date": "2026-10-07", "targets": 600, "area_deg2": 900.0,
        "wallclock_seconds": 900})
    f = MODULE.bundle_facts(root)
    assert set(f) == KEYS and (f["card_id"], f["slug"], f["stress"]) == ("alpha", "v4-practice-alpha", False)
    assert f["targets"] == 600 and f["regions"] == 2 and abs(f["area"] - 900.0) <= 10
    assert (f["first_night"], f["nights"]) == ("2026-10-04", 3)

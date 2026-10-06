"""Checks for the v4 participant task cards (web/src/content/taskcard.*.v4.*.md) and the rules page.

The practice card pages ship with the site (/cards, web/src/lib/taskCardSource.ts); the hackathon cards
A-D are read from the scenarios bucket once released, and the hidden cards E-H never reach the site.
These checks keep the pages consistent: matching zh/en pairs, stage separation (the same patterns as
tests/test_current_competition_browser.py), the fixed facts, no hidden-truth vocabulary, and rules that
state the v4 numbers.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

CONTENT = Path(__file__).resolve().parents[1] / "web" / "src" / "content"
FORMAL_WORDS = re.compile(r"正式赛|正式比赛|线上比赛|online competition|finals-preview|competition scenarios|正式大会|オンライン大会|compétition en ligne", re.I)
PRACTICE_WORDS = re.compile(r"练习赛|练习场景|Playground|\bpractice\b|練習|entraînement", re.I)
HIDDEN_WORDS = re.compile(r"seed|种子|pointing offset|指向偏差|efficiency multiplier|效率乘数|window_max_fraction|magnitude|震级", re.I)
PRACTICE_CARDS = ("alpha", "beta", "gamma", "delta")


def body(name: str) -> str:
    text = (CONTENT / name).read_text(encoding="utf-8")
    return re.sub(r"<!--.*?-->", "", text, flags=re.S)  # the organizer note is not rendered


def headings(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith("#")]


@pytest.mark.parametrize("stem", ["template", *PRACTICE_CARDS])
def test_zh_and_en_pages_exist_and_have_the_same_shape(stem):
    en, zh = body(f"taskcard.{stem}.v4.en.md"), body(f"taskcard.{stem}.v4.zh.md")
    assert len(headings(en)) == len(headings(zh)) >= 8
    assert en.count("|") == zh.count("|")  # same tables
    for text in (en, zh):
        assert re.search(r"Latitude −?\d+\.\d\d°|纬度 −?\d+\.\d\d°", text)  # every page names its site
        assert "1.08" in text and "0.90" in text  # the worked example
        assert "`observe`" in text and "`wait`" in text and "`report`" in text and "`finish`" in text


@pytest.mark.parametrize("card", PRACTICE_CARDS)
@pytest.mark.parametrize("language", ["en", "zh"])
def test_practice_cards_use_only_practice_wording(card, language):
    text = body(f"taskcard.{card}.v4.{language}.md")
    match = FORMAL_WORDS.search(text)
    assert match is None, f"formal-stage wording on a Playground card: {match.group(0)!r}"
    assert "900" in text
    assert "{{" not in text, "unfilled card field"
    # Practice cards keep their weather hidden; the agent only gets briefings and forecasts step by step.
    assert ("Not public" in text and "briefing" in text) if language == "en" else ("不公开" in text and "简报" in text)


@pytest.mark.parametrize("language", ["en", "zh"])
def test_template_is_stage_neutral(language):
    text = body(f"taskcard.template.v4.{language}.md")
    for pattern in (FORMAL_WORDS, PRACTICE_WORDS):
        match = pattern.search(text)
        assert match is None, f"stage wording in the neutral template: {match.group(0)!r}"


@pytest.mark.parametrize("name", sorted(p.name for p in CONTENT.glob("taskcard.*.v4.*.md")))
def test_no_hidden_truth_vocabulary(name):
    match = HIDDEN_WORDS.search(body(name))
    assert match is None, f"{name}: {match.group(0)!r}"


def test_only_practice_card_pages_live_in_the_site_sources():
    # Everything in web/src/content can end up in the public bundle: A-D come from the bucket at the start,
    # E-H never. A formal or hidden card page here would publish it early.
    stems = {p.name.split(".")[1] for p in CONTENT.glob("taskcard.*.v4.*.md")}
    assert stems <= {"template", "alpha", "beta", "gamma", "delta"}, sorted(stems)


RULES_V4_FACTS = ("900", "1.20", "1.12", "1.06", "α", "β", "γ", "δ", "E–H", "A–D", "/cards", "50", "200", "+100", "−150")
RULES_V3_LEFTOVERS = re.compile(r"18000|5 小时|5 hours|0\.25|0\.15|0\.08|时限 3600|limit of 3600|三个正式场景|three formal scenarios"
                                r"|一个隐藏场景|one hidden scenario|10 个批次|10 batches|challenge-score-v3|每天 5 次|5 evaluations per day", re.I)


@pytest.mark.parametrize("language", ["en", "zh"])
def test_rules_state_the_v4_cards(language):
    text = (CONTENT / f"rules.{language}.md").read_text(encoding="utf-8")
    for fact in RULES_V4_FACTS:
        assert fact in text, f"rules.{language}.md lacks {fact!r}"
    # The decisions.csv warm-up still names the v3 scoring once; nothing else of v3 remains.
    match = RULES_V3_LEFTOVERS.search(text)
    assert match is None, f"v3 rule left in rules.{language}.md: {match.group(0)!r}"
    assert HIDDEN_WORDS.search(text) is None


def test_rules_have_the_same_shape_in_both_languages():
    en, zh = ((CONTENT / f"rules.{lang}.md").read_text(encoding="utf-8") for lang in ("en", "zh"))
    assert headings(en) and len(headings(en)) == len(headings(zh))
    assert en.count("|") == zh.count("|")
    assert re.findall(r"^\d+\.", en, re.M) == re.findall(r"^\d+\.", zh, re.M)

"""Tiny i18n: nested JSON dictionaries per locale, dotted-key lookup."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

LOCALES = ("zh", "en")
I18N_DIR = Path(__file__).resolve().parent / "i18n"


@lru_cache
def load_locale(locale: str) -> dict:
    if locale not in LOCALES:
        locale = "zh"
    with (I18N_DIR / f"{locale}.json").open("r", encoding="utf-8") as fh:
        return json.load(fh)


def reload() -> None:
    load_locale.cache_clear()


def lookup(locale: str, key: str, default: Any = None) -> Any:
    node: Any = load_locale(locale)
    for part in key.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            if default is not None:
                return default
            # fall back to the other locale, then the key itself
            other = "en" if locale == "zh" else "zh"
            node2: Any = load_locale(other)
            for p2 in key.split("."):
                if isinstance(node2, dict) and p2 in node2:
                    node2 = node2[p2]
                else:
                    return key
            return node2
    return node


class Translator:
    def __init__(self, locale: str):
        self.locale = locale if locale in LOCALES else "zh"

    def __call__(self, key: str, **fmt: Any) -> Any:
        value = lookup(self.locale, key)
        if fmt and isinstance(value, str):
            try:
                return value.format(**fmt)
            except (KeyError, IndexError):
                return value
        return value

    def pick(self, en: Any, zh: Any) -> Any:
        return zh if self.locale == "zh" else en


def negotiate_locale(query: str | None, cookie: str | None, accept_language: str | None, default: str = "zh") -> str:
    for candidate in (query, cookie):
        if candidate in LOCALES:
            return candidate
    if accept_language:
        for part in accept_language.split(","):
            tag = part.split(";")[0].strip().lower()
            if tag.startswith("zh"):
                return "zh"
            if tag.startswith("en"):
                return "en"
    return default if default in LOCALES else "zh"

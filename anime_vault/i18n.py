r"""Язык интерфейса: русский (на нём написан код) или английский.

Видимые строки в коде — по-русски и обёрнуты в _(): при английском языке _() берёт перевод из словаря EN
(anime_vault\en.py); перевода нет — остаётся русский текст (tests\test_i18n.py следит, чтобы переведено было всё).
Строки с подстановками — _("скачано {count}").format(count=…): перевод — тоже шаблон с теми же {полями}.

Язык выбирается так: config.json → "language" ("ru" / "en") → env ANIME_VAULT_LANG (окно передаёт его командам)
→ язык интерфейса Windows (русский → ru, любой другой → en).
"""
from __future__ import annotations

import json
import os

from anime_vault.paths import SETTINGS

LANGUAGES = {"ru": "Русский", "en": "English"}


def detect() -> str:
    try:
        language = json.loads(SETTINGS.read_text(encoding="utf-8")).get("language")
    except (OSError, ValueError, AttributeError):
        language = None
    language = language or os.environ.get("ANIME_VAULT_LANG")
    if language in LANGUAGES:
        return language
    try:
        import ctypes

        # Младшие 10 бит LANGID — основной язык; 0x19 — русский.
        return "ru" if ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF == 0x19 else "en"
    except (AttributeError, OSError):
        return "en"


LANG = detect()


def _(text: str) -> str:
    if LANG == "ru":
        return text
    from anime_vault.en import EN

    return EN.get(text, text)


def set_language(language: str) -> None:
    """Сменить язык в этом процессе (тесты; окно при смене языка перезапускается)."""
    global LANG
    LANG = language if language in LANGUAGES else "en"

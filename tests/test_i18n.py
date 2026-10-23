"""У каждой видимой строки есть английский перевод с теми же {полями}; переключение языка работает."""
from __future__ import annotations

import string

from anime_vault import i18n
from anime_vault.en import EN
from anime_vault.gui.app import PAGES
from anime_vault.gui.runner import WORDS, option_labels
from tests.i18n_strings import translatable


def fields(text: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


def test_every_string_is_translated():
    # Подписи страниц и «Да/Нет» переводятся через переменную — их тоже проверить.
    extra = {title: "app.py PAGES" for _key, title, _icon in PAGES} | {word: "runner.py WORDS" for word in WORDS.values()}
    missing = {text: where for text, where in (translatable() | extra).items() if text not in EN}
    assert not missing, f"нет перевода: {missing}"


def test_translations_keep_fields():
    broken = {text: value for text, value in EN.items() if fields(text) != fields(value)}
    assert not broken, f"разные {{поля}}: {broken}"


def test_switch_language():
    try:
        i18n.set_language("en")
        assert i18n._("Перенести?") == "Move the files?"
        assert option_labels("Move the files?", "дн") == [("д", "Yes"), ("н", "No")]
        i18n.set_language("ru")
        assert i18n._("Перенести?") == "Перенести?"
    finally:
        i18n.set_language("ru")

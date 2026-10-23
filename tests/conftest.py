"""Тесты пишутся под русский интерфейс: без этого язык берётся из Windows (на сервере CI она английская)."""
import pytest

from anime_vault import i18n


@pytest.fixture(autouse=True)
def russian():
    i18n.set_language("ru")
    yield
    i18n.set_language("ru")

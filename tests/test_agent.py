"""Фоновая синхронизация: текст уведомления (anime_vault\\agent.py)."""
from anime_vault.agent import report_text


def test_report_shows_only_nonzero_lines():
    last = {"added_pc": 0, "added_drive": 74, "deleted_pc": 0, "deleted_drive": 25, "moved": 0, "errors": 0}
    assert report_text(last) == "Добавлено Drive: 74\nУдалено Drive: 25"


def test_report_empty_when_nothing_changed():
    assert report_text({"added_pc": 0, "added_drive": 0, "deleted_pc": 0, "deleted_drive": 0, "errors": 0}) == ""
    assert report_text({"uploaded": 3}) == ""          # старый формат итога — без новых полей


def test_report_all_lines_in_order():
    last = {"added_pc": 1, "added_drive": 2, "deleted_pc": 3, "deleted_drive": 4, "moved": 5, "errors": 6}
    assert report_text(last).splitlines() == ["Добавлено ПК: 1", "Добавлено Drive: 2", "Удалено ПК: 3",
                                              "Удалено Drive: 4", "Перемещено: 5", "Ошибок: 6"]

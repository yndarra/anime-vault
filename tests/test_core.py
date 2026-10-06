"""Проверки логики без сети и без окна: ключи пинов, номера dataN, доски «только хранить», раскладка,
разбор вывода команд для окна (ANSI-цвета, варианты ответа)."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from anime_vault import console, distribute, manifest
from anime_vault.gui.runner import option_labels, split_ansi
from anime_vault.known import pin_key


@pytest.fixture
def collection(tmp_path, monkeypatch):
    """Маленькая коллекция: манифест, config.json проекта (в tmp), две доски (вторая — «только хранить»)."""
    root = tmp_path / "Anime"
    project = tmp_path / "project"
    target = root / "data" / "pins"
    for folder in (root / "dataN" / "data7", root / "test-dataN", target / "01. Main", target / "02. Keep",
                   root / "add"):
        folder.mkdir(parents=True)
    (root / manifest.MANIFEST).write_text(json.dumps({
        "batches": "dataN", "results": "test-dataN",
        # «02. Keep» указана в sources по ошибке — раскладка всё равно должна её пропустить
        "sources": ["data/pins/01. Main", "data/pins/02. Keep", "add"],
    }), encoding="utf-8")
    settings = project / "config.json"
    project.mkdir()
    settings.write_text(json.dumps({"batch_size": 2, "pinterest": {
        "user": "someone", "target": "data/pins", "browser_cookies": "opera",
        "boards": [{"name": "01. Main", "slug": "main"}, {"name": "02. Keep", "slug": "keep", "keep": True}],
    }}), encoding="utf-8")
    monkeypatch.setattr(manifest, "SETTINGS", settings)
    monkeypatch.setattr(manifest, "CACHE", project / "cache" / "hashes.json")
    monkeypatch.setattr(manifest, "LOGS", project / "logs")
    (project / "logs").mkdir()
    return root


def touch(path: Path, data: bytes, mtime: int) -> None:
    path.write_bytes(data)
    os.utime(path, (mtime, mtime))


def test_pin_key():
    assert pin_key("pinterest_911697518295594812.jpg") == "911697518295594812"
    assert pin_key("pinterest_9116975_2ac9f0.png") == "9116975_2ac9f0"
    assert pin_key("pinterest_AUzDQDil_yHMVNLh-Wt_Q.png") == "AUzDQDil_yHMVNLh-Wt_Q"   # буквенный id пина
    assert pin_key("Screenshot 2026.png") is None


def test_next_number(tmp_path):
    for name in ("data3", "data12-other", "data5", "notes"):
        (tmp_path / name).mkdir()
    assert distribute.next_number(tmp_path) == 13
    assert distribute.next_number(tmp_path / "missing") == 1


def test_keep_board_is_not_a_source(collection):
    loaded = manifest.load(collection)
    assert [path.name for path in loaded.sources] == ["01. Main", "add"]
    assert [path.name for path in loaded.kept] == ["02. Keep"]
    assert [board.keep for board in loaded.boards] == [False, True]


def test_distribute_moves_new_files_and_skips_duplicates(collection, monkeypatch):
    main = collection / "data" / "pins" / "01. Main"
    touch(collection / "dataN" / "data7" / "pinterest_1.jpg", b"old pin", 1)
    touch(main / "pinterest_1.jpg", b"same pin", 10)                # пин уже в data7 → дубль
    touch(main / "pinterest_2.jpg", b"pin 2", 11)
    touch(main / "pinterest_3.jpg", b"pin 3", 12)
    touch(collection / "add" / "shot.png", b"screenshot", 13)
    touch(collection / "add" / "copy.png", b"screenshot", 14)       # та же картинка → дубль
    touch(collection / "data" / "pins" / "02. Keep" / "pinterest_9.jpg", b"kept", 15)
    monkeypatch.setattr(console, "ask", lambda prompt, options: "д")

    assert distribute.run(manifest.load(collection)) == 0

    assert sorted(p.name for p in (collection / "dataN" / "data8").iterdir()) == ["pinterest_2.jpg", "pinterest_3.jpg"]
    assert [p.name for p in (collection / "dataN" / "data9").iterdir()] == ["shot.png"]
    duplicates = [p.name for p in (collection / "duplicates").rglob("*") if p.is_file()]
    assert sorted(duplicates) == ["01. Main__pinterest_1.jpg", "add__copy.png"]
    assert (collection / "data" / "pins" / "02. Keep" / "pinterest_9.jpg").exists()   # «только хранить» не тронута
    journal = json.loads(next(manifest.LOGS.glob("distribute_*.json")).read_text(encoding="utf-8"))
    assert list(journal["batches"]) == ["data8", "data9"]
    # Пара «дубль → оригинал»: оригинал скриншота уехал в data9 — в журнале его новое место.
    originals = {Path(item["file"]).name: Path(item["original"]) for item in journal["duplicates"]}
    assert originals["01. Main__pinterest_1.jpg"] == collection / "dataN" / "data7" / "pinterest_1.jpg"
    assert originals["add__copy.png"] == collection / "dataN" / "data9" / "shot.png"
    from anime_vault.gui import collection as gui_collection

    monkeypatch.setattr(gui_collection, "LOGS", manifest.LOGS)
    found = gui_collection.duplicates(collection)
    assert sorted(Path(d.original).name for d in found) == ["pinterest_1.jpg", "shot.png"]


def test_distribute_cancel_moves_nothing(collection, monkeypatch):
    touch(collection / "add" / "shot.png", b"screenshot", 1)
    monkeypatch.setattr(console, "ask", lambda prompt, options: "н")
    assert distribute.run(manifest.load(collection)) == 0
    assert (collection / "add" / "shot.png").exists()
    assert not (collection / "dataN" / "data8").exists()


def test_split_ansi():
    assert split_ansi("plain") == [("plain", None)]
    assert split_ansi("\x1b[91mError\x1b[0m: fix") == [("Error", "91"), (": fix", None)]


def test_option_labels():
    assert option_labels("Перенести?", "дн") == [("д", "Да"), ("н", "Нет")]
    assert option_labels("Что дальше (р — повторить, п — пропустить)?", "рп") == [("р", "Повторить"), ("п", "Пропустить")]

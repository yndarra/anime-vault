r"""Манифест коллекции и настройки проекта.

anime-paths.json в корне папки с картинками (Pictures\Anime) — пути коллекции (относительно корня: переименование
или перенос корня ничего не ломает) и пути к проектам; его читают и anime-vault, и anime-sort.
config.json в папке проекта anime-vault (личный, не в git; образец — config.example.json) — то, что нужно только
anime-vault: размер пачки и доски Pinterest. Старые anime-paths.json с этими параметрами тоже читаются.
Служебное anime-vault — тоже в проекте: cache\hashes.json (кэш хэшей), logs\ (журналы distribute).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from anime_vault.console import UserError

MANIFEST = "anime-paths.json"
PROJECT = Path(__file__).resolve().parents[1]
SETTINGS = PROJECT / "config.json"
CACHE = PROJECT / "cache" / "hashes.json"
LOGS = PROJECT / "logs"


@dataclass
class Board:
    name: str            # как доска называется в Pinterest и папка, куда её скачивать
    slug: str            # часть адреса доски (кириллица — в %-кодировке)
    keep: bool = False   # «только хранить»: доска обновляется с Pinterest, но раскладка (distribute) её не трогает


@dataclass
class Manifest:
    root: Path
    batch_size: int
    batches: Path                         # dataN — папки по batch_size файлов для anime-sort
    results: Path                         # test-dataN — обработанные anime-sort наборы
    sources: list[Path]                   # откуда distribute берёт новые файлы (без досок «только хранить»)
    pinterest_user: str
    pinterest_target: Path                # куда скачиваются доски (по папке на доску)
    browser_cookies: str                  # профиль браузера для gallery-dl --cookies-from-browser
    boards: list[Board] = field(default_factory=list)
    kept: list[Path] = field(default_factory=list)   # папки досок «только хранить» — раскладке заблокированы


def find_root(start: Path) -> Path:
    """Корень коллекции — ближайшая папка вверх от start, где лежит anime-paths.json."""
    for folder in [start, *start.parents]:
        if (folder / MANIFEST).exists():
            return folder
    raise UserError(f"не найден {MANIFEST} ни в {start}, ни выше",
                    f"запускайте .bat-файлы из папки коллекции (там, где лежит {MANIFEST}), или передайте --root")


def load(root: Path) -> Manifest:
    path = root / MANIFEST
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise UserError(f"нет файла {path}", f"скопируйте anime-paths.example.json из проекта в {root} под именем {MANIFEST}")
    except ValueError as exc:
        raise UserError(f"{path} — ошибка JSON: {exc}", "проверьте запятые и кавычки (пути — с прямыми слэшами /)")
    if SETTINGS.exists():
        try:
            data = {**data, **json.loads(SETTINGS.read_text(encoding="utf-8"))}
        except ValueError as exc:
            raise UserError(f"{SETTINGS} — ошибка JSON: {exc}", "проверьте запятые и кавычки")
    try:
        pinterest = data["pinterest"]
        target = root / pinterest["target"]
        boards = [Board(item["name"], item["slug"], bool(item.get("keep"))) for item in pinterest["boards"]]
        # Доска «только хранить» не раскладывается, даже если по ошибке указана в sources.
        kept = [target / board.name for board in boards if board.keep]
        blocked = {path.resolve() for path in kept}
        return Manifest(
            root=root,
            batch_size=int(data.get("batch_size", 500)),
            batches=root / data["batches"],
            results=root / data["results"],
            sources=[root / item for item in data["sources"] if (root / item).resolve() not in blocked],
            pinterest_user=pinterest["user"],
            pinterest_target=target,
            browser_cookies=pinterest["browser_cookies"],
            boards=boards,
            kept=kept,
        )
    except (KeyError, TypeError) as exc:
        raise UserError(f"не хватает параметра {exc} ({path} или {SETTINGS})",
                        "сравните с anime-paths.example.json и config.example.json в проекте")

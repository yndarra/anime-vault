r"""Сведения о коллекции для окна anime-vault: где она, что ждёт раскладки, что на досках, последняя dataN, дубли.

Корень коллекции (папка с anime-paths.json): config.json → "collection"; если не задан — текущая папка и выше
(как у .bat-файлов), потом Pictures\Anime. Счёт файлов — тем же обходом, что у команд (known.image_entries).
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

from anime_vault import manifest as manifest_module
from anime_vault.known import image_entries

PROJECT = manifest_module.PROJECT


def find_root() -> Path | None:
    data = read_settings()
    candidates = [Path(data["collection"])] if data.get("collection") else []
    try:
        candidates.append(manifest_module.find_root(Path.cwd()))
    except Exception:
        pass
    candidates.append(Path.home() / "Pictures" / "Anime")
    for path in candidates:
        if (path / manifest_module.MANIFEST).exists():
            return path
    return None


def read_settings() -> dict:
    for path in (manifest_module.SETTINGS, PROJECT / "config.example.json"):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    return {}


def save_settings(data: dict) -> None:
    """config.json — атомарно (сначала .tmp), ключи "//" сохраняются как есть."""
    path = manifest_module.SETTINGS
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def count(folder: Path) -> int:
    return sum(1 for _ in image_entries(folder)) if folder.is_dir() else 0


@dataclass
class Snapshot:
    root: Path
    batch_size: int
    sources: list[tuple[str, int]] = field(default_factory=list)      # (подпись, файлов) — ждут раскладки
    boards: list[tuple[str, int, bool]] = field(default_factory=list)  # (доска, файлов в её папке, «только хранить»)
    batches: int = 0                                                  # сколько папок dataN
    last_batch: str = ""
    next_number: int = 1
    duplicates: int = 0
    last_journal: str = ""                                            # «03.10 17:50 — перенесено 4088»
    error: str = ""

    @property
    def waiting(self) -> int:
        return sum(n for _, n in self.sources)

    @property
    def planned_folders(self) -> int:
        return math.ceil(self.waiting / self.batch_size) if self.waiting and self.batch_size else 0


def snapshot(root: Path | None = None) -> Snapshot:
    """Всё для страницы «Обзор» и «Разложить» (считает файлы — вызывать не в потоке окна)."""
    from anime_vault.distribute import BATCH_RE, next_number

    root = root or find_root()
    if root is None:
        return Snapshot(Path("."), 500, error="коллекция не найдена: укажите папку с anime-paths.json в «Настройках»")
    try:
        data = manifest_module.load(root)
    except Exception as exc:
        return Snapshot(root, 500, error=getattr(exc, "what", str(exc)))
    shot = Snapshot(root, data.batch_size)
    for source in data.sources:
        shot.sources.append((str(source.relative_to(root)), count(source)))
    for board in data.boards:
        shot.boards.append((board.name, count(data.pinterest_target / board.name), board.keep))
    names = sorted((p.name for p in data.batches.iterdir() if p.is_dir() and BATCH_RE.match(p.name)),
                   key=lambda n: int(BATCH_RE.match(n).group(1))) if data.batches.is_dir() else []
    shot.batches = len(names)
    shot.last_batch = names[-1] if names else ""
    shot.next_number = next_number(data.batches)
    shot.duplicates = count(root / "duplicates")
    journals = sorted((PROJECT / "logs").glob("distribute_*.json"))
    if journals:
        try:
            moved = json.loads(journals[-1].read_text(encoding="utf-8"))
            total = sum(len(files) for files in (moved.get("batches") or {}).values())
        except (OSError, ValueError):
            total = 0
        stamp = journals[-1].stem.removeprefix("distribute_")
        shot.last_journal = f"{stamp[8:10]}.{stamp[5:7]} {stamp[11:13]}:{stamp[14:16]} — перенесено {total}"
    return shot


def opera_running() -> bool:
    from anime_vault.download import opera_running as running

    return running()

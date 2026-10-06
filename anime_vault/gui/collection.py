r"""Сведения о коллекции для окна anime-vault: где она, что ждёт раскладки, что на досках, последняя dataN, дубли.

Корень коллекции: config.json → "collection" (любая существующая папка — anime-paths.json не обязателен);
если не задан — текущая папка и выше, где есть anime-paths.json (как у .bat-файлов), потом Pictures\Anime.
Счёт файлов — тем же обходом, что у команд (known.image_entries).
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from pathlib import Path

from anime_vault import browsers
from anime_vault import manifest as manifest_module
from anime_vault.i18n import _
from anime_vault.known import file_sha1, image_entries
from anime_vault.paths import EXAMPLE, LOGS, PROJECT, SETTINGS  # noqa: F401 — PROJECT нужен окну

DUPLICATES = "duplicates"


def find_root() -> Path | None:
    data = read_settings()
    if data.get("collection") and Path(data["collection"]).is_dir() and SETTINGS.exists():
        return Path(data["collection"])
    try:
        return manifest_module.find_root(Path.cwd())
    except Exception:
        pass
    default = Path.home() / "Pictures" / "Anime"
    return default if (default / manifest_module.MANIFEST).exists() else None


def read_settings() -> dict:
    """config.json, а пока его нет (первый запуск) — образец config.example.json."""
    for path in (SETTINGS, EXAMPLE):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    return {}


def save_settings(data: dict) -> None:
    """config.json — атомарно (сначала .tmp), ключи "//" сохраняются как есть."""
    temporary = SETTINGS.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(SETTINGS)


def count(folder: Path) -> int:
    return sum(1 for _entry in image_entries(folder)) if folder.is_dir() else 0


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
    distribute: bool = True
    cookies: str = ""                                                 # pinterest.browser_cookies
    error: str = ""

    @property
    def waiting(self) -> int:
        return sum(n for _name, n in self.sources)

    @property
    def planned_folders(self) -> int:
        return math.ceil(self.waiting / self.batch_size) if self.waiting and self.batch_size else 0


def snapshot(root: Path | None = None) -> Snapshot:
    """Всё для страниц «Обзор» и «Разложить» (считает файлы — вызывать не в потоке окна)."""
    from anime_vault.distribute import BATCH_RE, next_number

    root = root or find_root()
    if root is None:
        return Snapshot(Path("."), 500, error=_("Коллекция не найдена: укажи её папку в «Настройках»"))
    try:
        data = manifest_module.load(root)
    except Exception as exc:
        return Snapshot(root, 500, error=getattr(exc, "what", str(exc)))
    shot = Snapshot(root, data.batch_size, distribute=data.distribute, cookies=data.browser_cookies)
    for source in data.sources:
        try:
            label = str(source.relative_to(root))
        except ValueError:
            label = str(source)
        shot.sources.append((label, count(source)))
    for board in data.boards:
        shot.boards.append((board.name, count(data.pinterest_target / board.name), board.keep))
    names = sorted((p.name for p in data.batches.iterdir() if p.is_dir() and BATCH_RE.match(p.name)),
                   key=lambda n: int(BATCH_RE.match(n).group(1))) if data.batches.is_dir() else []
    shot.batches = len(names)
    shot.last_batch = names[-1] if names else ""
    shot.next_number = next_number(data.batches)
    shot.duplicates = count(root / DUPLICATES)
    journals = sorted(LOGS.glob("distribute_*.json"))
    if journals:
        try:
            moved = json.loads(journals[-1].read_text(encoding="utf-8"))
            total = sum(len(files) for files in (moved.get("batches") or {}).values())
        except (OSError, ValueError):
            total = 0
        stamp = journals[-1].stem.removeprefix("distribute_")
        shot.last_journal = _("{date} {time} — перенесено {count}").format(
            date=f"{stamp[8:10]}.{stamp[5:7]}", time=f"{stamp[11:13]}:{stamp[14:16]}", count=total)
    return shot


# ---------------------------------------------------------------- дубли

@dataclass
class Duplicate:
    file: Path            # где дубль лежит сейчас (duplicates\<время>\…)
    source: str = ""      # откуда его убрала раскладка
    original: str = ""    # такой же файл, который уже был в коллекции ("" — старый журнал без этих сведений)


def duplicates(root: Path) -> list[Duplicate]:
    r"""Все файлы в duplicates\ (новые раскладки — сверху) + сведения из журналов distribute: откуда и какой оригинал."""
    info: dict[str, dict] = {}
    for journal in LOGS.glob("distribute_*.json"):
        try:
            data = json.loads(journal.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for item in data.get("duplicates") or []:
            if isinstance(item, dict) and item.get("file"):
                info[os.path.normcase(item["file"])] = item
    found = []
    folder = root / DUPLICATES
    by_hash: dict[str, list[str]] | None = None
    by_name: dict[str, str] | None = None
    for entry in image_entries(folder):
        item = info.get(os.path.normcase(entry.path), {})
        original = item.get("original", "")
        if not original or not Path(original).exists():
            # Журнал старой версии (без оригинала) — найти такой же файл по кэшу хэшей (cache\hashes.json:
            # «путь|размер|время» → SHA-1), сам кэш раскладка уже собрала, поэтому это быстро.
            if by_hash is None:
                by_hash = hash_index()
            twins = [path for path in by_hash.get(file_sha1(entry.path), [])
                     if os.path.normcase(path) != os.path.normcase(entry.path) and DUPLICATES not in Path(path).parts and Path(path).exists()]
            original = twins[0] if twins else ""
            if not original:
                # Оригинал мог переехать (раскладка в том же запуске унесла его в dataN) — искать по имени файла.
                if by_name is None:
                    by_name = name_index(root)
                original = by_name.get(entry.name.split("__", 1)[-1].casefold(), "")
        found.append(Duplicate(Path(entry.path), item.get("source", ""), original))
    found.sort(key=lambda d: (d.file.parent.name, d.file.name), reverse=True)
    return found


def name_index(root: Path) -> dict[str, str]:
    """Имя файла (без регистра) → путь, по папкам dataN (test-dataN огромные и хранят копии — их не обходить)."""
    try:
        data = manifest_module.load(root)
    except Exception:
        return {}
    index: dict[str, str] = {}
    for folder in (data.batches,):
        for entry in image_entries(folder):
            index.setdefault(entry.name.casefold(), entry.path)
    return index


def hash_index() -> dict[str, list[str]]:
    """SHA-1 → пути из кэша хэшей (cache\\hashes.json)."""
    from anime_vault.paths import CACHE

    try:
        cache = json.loads(CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    index: dict[str, list[str]] = {}
    for key, digest in cache.items():
        index.setdefault(digest, []).append(key.rsplit("|", 2)[0])
    return index


def to_trash(paths: list[Path]) -> int:
    """В Корзину (не насовсем) — потом из папки duplicates убираются опустевшие подпапки."""
    from send2trash import send2trash

    done = 0
    for path in paths:
        try:
            send2trash(str(path))
            done += 1
        except OSError:
            continue
    for path in {p.parent for p in paths}:
        try:
            if path.is_dir() and not any(path.iterdir()):
                path.rmdir()
        except OSError:
            pass
    return done


# ---------------------------------------------------------------- браузер

def browser_state(spec: str) -> tuple[browsers.Cookies, bool]:
    """(откуда куки, запущен ли браузер) — для индикатора на странице «Скачать»."""
    cookies = browsers.parse(spec)
    return cookies, browsers.is_running(cookies) if cookies.must_close else False

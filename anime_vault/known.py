r"""Что уже есть в коллекции — чтобы не скачивать и не раскладывать повторно.

Пины Pinterest узнаются по имени файла: pinterest_<id пина>[_<id медиа>].<расширение> — один пин один раз,
где бы он ни лежал (скачанные доски, dataN, test-dataN\*\in). Остальные файлы (скриншоты, сохранёнки из
Anime ADD) — по содержимому (SHA-1); хэши кэшируются в <проект>\cache\hashes.json по имени, размеру и
времени изменения, поэтому повторный запуск не перечитывает тысячи файлов.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

PIN_RE = re.compile(r"^pinterest_(\d+)(?:_([0-9a-f]+))?\.[^.]+$", re.I)
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".jfif", ".heic", ".mp4", ".m4v"}


def pin_key(name: str) -> str | None:
    """«911697518295594812» или «911697518301335618_2ac9…» — ключ пина по имени файла; не пин — None."""
    match = PIN_RE.match(name)
    if not match:
        return None
    pin, media = match.groups()
    return f"{pin}_{media}" if media else pin


def walk_files(folder: Path):
    """Все файлы под folder (быстро, через os.scandir); пропускает служебные папки (.download-work и т. п.)."""
    if not folder.is_dir():
        return
    stack = [str(folder)]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    if entry.is_dir(follow_symlinks=False):
                        if not entry.name.startswith("."):
                            stack.append(entry.path)
                    elif entry.is_file(follow_symlinks=False):
                        yield entry
        except OSError:
            continue


def image_entries(folder: Path):
    for entry in walk_files(folder):
        if os.path.splitext(entry.name)[1].lower() in IMAGE_EXTENSIONS:
            yield entry


class Known:
    """Индекс того, что уже есть: пины по ключу, остальное по хэшу."""

    def __init__(self, root: Path):
        from anime_vault.manifest import CACHE

        self.cache_path = CACHE   # кэш — в папке проекта (cache\\hashes.json), коллекция остаётся чистой
        try:
            self.cache: dict[str, str] = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.cache = {}
        self.pins: set[str] = set()
        self.hashes: set[str] = set()

    def sha1(self, entry) -> str:
        stat = entry.stat()
        key = f"{entry.path}|{stat.st_size}|{int(stat.st_mtime)}"
        if key not in self.cache:
            digest = hashlib.sha1()
            with open(entry.path, "rb") as handle:
                for block in iter(lambda: handle.read(1 << 20), b""):
                    digest.update(block)
            self.cache[key] = digest.hexdigest()
        return self.cache[key]

    def add_folder(self, folder: Path, hash_others: bool = True) -> int:
        count = 0
        for entry in image_entries(folder):
            key = pin_key(entry.name)
            if key:
                self.pins.add(key)
            elif hash_others:
                self.hashes.add(self.sha1(entry))
            count += 1
        return count

    def is_known(self, entry) -> bool:
        key = pin_key(entry.name)
        return key in self.pins if key else self.sha1(entry) in self.hashes

    def remember(self, entry) -> None:
        key = pin_key(entry.name)
        if key:
            self.pins.add(key)
        else:
            self.hashes.add(self.sha1(entry))

    def save(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.cache_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.cache), encoding="utf-8")
        temporary.replace(self.cache_path)

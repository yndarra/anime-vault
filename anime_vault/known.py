r"""Что уже есть в коллекции — чтобы не скачивать и не раскладывать повторно.

Пины Pinterest узнаются по имени файла: pinterest_<id пина>[_<id медиа>].<расширение> — один пин один раз,
где бы он ни лежал (скачанные доски, dataN, test-dataN\*\in). Так же — файлы с других сайтов, скачанные
anime-vault: <сайт>_<id>[_<номер>].<расширение> (danbooru_123.jpg, twitter_456_2.png). Остальные файлы
(скриншоты, сохранёнки из Anime ADD) — по содержимому (SHA-1); хэши кэшируются в <проект>\cache\hashes.json по имени,
размеру и времени изменения, поэтому повторный запуск не перечитывает тысячи файлов.
Для каждого ключа и хэша запоминается, где лежит первый такой файл, — раскладка пишет в журнал пару «дубль → оригинал»,
и окно показывает их рядом (страница «Дубли»).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

# Ключ пина — всё имя после «pinterest_» (как запись в архиве gallery-dl: {id}[_{media_id}]). id бывает и числом,
# и буквенным («AUzDQDil_yHMV…_Q» — сам с «_» внутри), поэтому имя не разбирается на части.
PIN_RE = re.compile(r"^pinterest_(.+)\.[^.]+$", re.I)
# Сайты gallery-dl, чьи файлы anime-vault называет «<сайт>_<id>…»; другие имена с «_» (Screenshot_2024…) — не ключи.
SITES = ("danbooru", "gelbooru", "safebooru", "yandere", "konachan", "sankaku", "zerochan", "e621", "rule34",
         "pixiv", "twitter", "bluesky", "artstation", "deviantart", "tumblr", "reddit", "instagram", "kemonoparty")
SITE_RE = re.compile(rf"^({'|'.join(SITES)})_(\d+)(?:_(\w+))?\.[^.]+$", re.I)
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".jfif", ".heic", ".mp4", ".m4v"}


def pin_key(name: str) -> str | None:
    """Ключ файла по имени: пин — «911697518295594812», «911697518301335618_2ac9…» или «AUzDQDil_…» (как в архиве gallery-dl),
    файл другого сайта — «danbooru_123» / «twitter_456_2»; иначе None."""
    match = PIN_RE.match(name)
    if match:
        return match.group(1)
    match = SITE_RE.match(name)
    if match:
        site, number, part = match.groups()
        return f"{site.casefold()}_{number}" + (f"_{part}" if part else "")
    return None


def file_sha1(path: str) -> str:
    digest = hashlib.sha1()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


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
    """Индекс того, что уже есть: пины и файлы сайтов по ключу, остальное по хэшу; where — где лежит первый такой."""

    def __init__(self, root: Path):
        from anime_vault.manifest import CACHE

        self.cache_path = CACHE   # кэш — в папке проекта (cache\\hashes.json), коллекция остаётся чистой
        try:
            self.cache: dict[str, str] = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.cache = {}
        self.pins: set[str] = set()
        self.hashes: set[str] = set()
        self.where: dict[str, str] = {}

    def sha1(self, entry) -> str:
        stat = entry.stat()
        key = f"{entry.path}|{stat.st_size}|{int(stat.st_mtime)}"
        if key not in self.cache:
            self.cache[key] = file_sha1(entry.path)
        return self.cache[key]

    def add_folder(self, folder: Path, hash_others: bool = True) -> int:
        count = 0
        for entry in image_entries(folder):
            key = pin_key(entry.name)
            if key:
                self.pins.add(key)
                self.where.setdefault(key, entry.path)
            elif hash_others:
                digest = self.sha1(entry)
                self.hashes.add(digest)
                self.where.setdefault(digest, entry.path)
            count += 1
        return count

    def is_known(self, entry) -> bool:
        key = pin_key(entry.name)
        return key in self.pins if key else self.sha1(entry) in self.hashes

    def original(self, entry) -> str | None:
        """Где лежит уже известный такой же файл (для журнала дублей)."""
        key = pin_key(entry.name) or self.sha1(entry)
        return self.where.get(key)

    def remember(self, entry) -> None:
        key = pin_key(entry.name)
        if key:
            self.pins.add(key)
            self.where.setdefault(key, entry.path)
        else:
            digest = self.sha1(entry)
            self.hashes.add(digest)
            self.where.setdefault(digest, entry.path)

    def save(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.cache_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.cache), encoding="utf-8")
        temporary.replace(self.cache_path)

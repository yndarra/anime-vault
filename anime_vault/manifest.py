r"""Манифест коллекции и настройки проекта.

anime-paths.json в корне папки с картинками (Pictures\Anime) — пути коллекции (относительно корня: переименование
или перенос корня ничего не ломает) и пути к проектам; его читают и anime-vault, и anime-sort.
Без anime-paths.json тоже можно (anime-vault сам по себе, без anime-sort): корень — config.json → "collection",
пачки — <корень>\dataN, источники раскладки — папки досок, кроме «только хранить».
config.json в папке проекта anime-vault (личный, не в git; образец — config.example.json) — то, что нужно только
anime-vault: размер пачки, язык, доски / источники скачивания. Старые anime-paths.json с этими параметрами тоже читаются.
Служебное anime-vault — тоже в проекте: cache\hashes.json (кэш хэшей), logs\ (журналы distribute).

Доска (источник скачивания) задаётся либо как доска Pinterest — "slug" (часть адреса; "user" — если доска чужая),
либо ссылкой "url" на что угодно, что качает gallery-dl (Danbooru, Pixiv, X/Twitter …). В окне — просто ссылкой:
parse_address разбирает её обратно в slug / user / url.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, unquote

from anime_vault.console import UserError
from anime_vault.i18n import _
from anime_vault.paths import CACHE, LOGS, PROJECT, SETTINGS  # noqa: F401 — CACHE/LOGS/PROJECT берут отсюда

MANIFEST = "anime-paths.json"
PINTEREST_RE = re.compile(r"^https?://(?:[a-z]{2,3}\.)?pinterest\.[a-z.]+/([^/?#]+)/([^/?#]+)", re.I)
# Такие первые части адреса — не пользователь, а служебные страницы Pinterest (пин, поиск, идеи).
PINTEREST_PAGES = {"pin", "search", "ideas", "today", "settings", "_"}


@dataclass
class Board:
    name: str            # папка, куда качать (для Pinterest — обычно как доска называется)
    slug: str = ""       # доска Pinterest: часть адреса (кириллица — в %-кодировке)
    keep: bool = False   # «только хранить»: доска обновляется с сайта, но раскладка (distribute) её не трогает
    user: str = ""       # доска Pinterest другого пользователя (пусто — общий "user" из настроек)
    url: str = ""        # любой другой источник gallery-dl — полной ссылкой

    def address(self, default_user: str) -> str:
        if self.url:
            return self.url
        return f"https://www.pinterest.com/{self.user or default_user}/{self.slug}/"


def parse_address(text: str, default_user: str = "") -> dict:
    """Ссылка (или часть адреса доски) из окна → поля доски для config.json: {"slug"[, "user"]} или {"url"}.
    Ссылка на доску Pinterest своего пользователя — короче, одним slug (как было в config.json всегда)."""
    text = text.strip()
    if not text:
        raise ValueError(_("пустой адрес"))
    match = PINTEREST_RE.match(text)
    if match:
        user, slug = match.groups()
        if user.casefold() in PINTEREST_PAGES:
            raise ValueError(_("это ссылка на пин или страницу Pinterest, а не на доску — откройте доску и скопируйте её адрес"))
        slug = quote(unquote(slug), safe="-_.~")
        return {"slug": slug} if not default_user or user.casefold() == default_user.casefold() else {"slug": slug, "user": user}
    if re.match(r"^https?://", text, re.I):
        if re.match(r"^https?://pin\.it/", text, re.I):
            raise ValueError(_("короткая ссылка pin.it — откройте её в браузере и скопируйте полный адрес доски"))
        return {"url": text}
    if "/" in text or " " in text:
        raise ValueError(_("не похоже на адрес — вставьте ссылку целиком (https://…)"))
    return {"slug": quote(unquote(text), safe="-_.~")}


def pinterest_user(text: str) -> str:
    """Пользователь из ссылки на доску Pinterest (пусто — не доска Pinterest)."""
    match = PINTEREST_RE.match(text.strip())
    return match.group(1) if match and match.group(1).casefold() not in PINTEREST_PAGES else ""


def name_from_address(text: str) -> str:
    """Имя папки по ссылке: доска Pinterest — её slug словами («01-anime» → «01 anime»), иначе — последняя часть адреса."""
    match = PINTEREST_RE.match(text.strip())
    tags = re.search(r"[?&](?:tags|q|word)=([^&#]+)", text)    # поиск по тегам (Danbooru, Gelbooru, Pixiv …)
    if match:
        part = match.group(2)
    elif tags:
        part = tags.group(1)
    else:
        parts = [p for p in re.split(r"[/?#=&]+", re.sub(r"^https?://", "", text.strip(), flags=re.I)) if p]
        part = "-".join(parts[-2:]) if len(parts) > 1 else (parts[0] if parts else "")
    name = re.sub(r"[-_+]+", " ", unquote(part)).strip()
    return re.sub(r'[<>:"/\\|?*]+', " ", name).strip()[:80]


@dataclass
class Manifest:
    root: Path
    batch_size: int
    batches: Path                         # dataN — папки по batch_size файлов для anime-sort
    results: Path                         # test-dataN — обработанные anime-sort наборы
    sources: list[Path]                   # откуда distribute берёт новые файлы (без досок «только хранить»)
    pinterest_user: str
    pinterest_target: Path                # куда скачиваются доски (по папке на доску)
    browser_cookies: str                  # профиль браузера для gallery-dl --cookies-from-browser (или файл cookies.txt)
    boards: list[Board] = field(default_factory=list)
    kept: list[Path] = field(default_factory=list)   # папки досок «только хранить» — раскладке заблокированы
    distribute: bool = True               # раскладка по пачкам включена (выключена — только скачивание)


def find_root(start: Path) -> Path:
    """Корень коллекции — ближайшая папка вверх от start, где лежит anime-paths.json."""
    for folder in [start, *start.parents]:
        if (folder / MANIFEST).exists():
            return folder
    raise UserError(_("не найден {manifest} ни в {start}, ни выше").format(manifest=MANIFEST, start=start),
                    _("запускайте .bat-файлы из папки коллекции (там, где лежит {manifest}), или передайте --root").format(manifest=MANIFEST))


def read_board(item: dict) -> Board:
    return Board(item["name"], item.get("slug", ""), bool(item.get("keep")), item.get("user", ""), item.get("url", ""))


def load(root: Path) -> Manifest:
    path = root / MANIFEST
    data: dict = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise UserError(_("{path} — ошибка JSON: {error}").format(path=path, error=exc),
                            _("проверьте запятые и кавычки (пути — с прямыми слэшами /)"))
    elif not root.is_dir():
        raise UserError(_("нет папки коллекции {root}").format(root=root), _("укажите папку коллекции в «Настройках»"))
    if SETTINGS.exists():
        try:
            data = {**data, **json.loads(SETTINGS.read_text(encoding="utf-8"))}
        except ValueError as exc:
            raise UserError(_("{path} — ошибка JSON: {error}").format(path=SETTINGS, error=exc), _("проверьте запятые и кавычки"))
    try:
        pinterest = data["pinterest"]
        target = root / pinterest.get("target", "downloads")
        boards = [read_board(item) for item in pinterest["boards"]]
        for board in boards:
            if not board.slug and not board.url:
                raise UserError(_("у доски «{name}» нет адреса").format(name=board.name), _("впишите ссылку на доску в «Настройках»"))
        # Доска «только хранить» не раскладывается, даже если по ошибке указана в sources.
        kept = [target / board.name for board in boards if board.keep]
        blocked = {path.resolve() for path in kept}
        # Нет anime-paths.json — раскладываются все доски, кроме «только хранить».
        sources = data.get("sources") or [str((target / b.name).relative_to(root)) for b in boards if not b.keep]
        return Manifest(
            root=root,
            batch_size=int(data.get("batch_size", 500)),
            batches=root / data.get("batches", "dataN"),
            results=root / data.get("results", "test-dataN"),
            sources=[root / item for item in sources if (root / item).resolve() not in blocked],
            pinterest_user=pinterest.get("user", ""),
            pinterest_target=target,
            browser_cookies=pinterest.get("browser_cookies", ""),
            boards=boards,
            kept=kept,
            distribute=bool(data.get("distribute", True)),
        )
    except (KeyError, TypeError) as exc:
        raise UserError(_("не хватает параметра {name} ({path} или {settings})").format(name=exc, path=path, settings=SETTINGS),
                        _("сравните с anime-paths.example.json и config.example.json в проекте"))

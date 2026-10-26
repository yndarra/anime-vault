r"""Синхронизация папки Waifu с Google Drive в обе стороны (команда sync).

Как сверяется:
    Обе стороны сканируются целиком (на Драйве — один общий список файлов аккаунта, без обхода папок по одной),
    и каждый файл сравнивается с тем, каким он был после прошлой синхронизации (cache\drive_state.json) —
    так понятно, на какой стороне что изменилось:
        новый файл на одной стороне                  → копия на другую;
        изменён на одной стороне                     → новая версия на другую;
        удалён на одной стороне (а другая не менялась) → на другой — в Корзину (на ПК — Корзина Windows,
                                                        на Драйве — его корзина, 30 дней);
        изменён на обеих                              → ПК главнее: его версия уходит на Драйв, версия с Драйва
                                                        скачивается рядом как «имя (Drive).jpg»;
        перенесён в другую папку / переименован       → узнаётся по MD5 и переносится, а не «удалить + залить заново».
    Ключ файла — путь без номеров в начале имён папок («102. 86 — Eighty Six» → «86 — eighty six»): anime-sort
    перенумеровывает тайтлы и персонажей по размеру, и смена номера — это только переименование папки на Драйве
    (имена папок на Драйве берутся с ПК).

Перед изменениями показывается план и спрашивается «Синхронизировать?» (sync --yes — без вопроса, для автосинка).
Защита: если удалений много (больше 50 и больше 10 % файлов), автосинк останавливается, а ручной спрашивает ещё раз;
если пропала сама папка на Драйве — ничего не удаляется. Прогресс сохраняется каждые 100 файлов: прерванная
синхронизация продолжается с того же места. Загрузка и скачивание — в 6 потоков.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path

from anime_vault import console, gdrive
from anime_vault.console import UserError
from anime_vault.i18n import _
from anime_vault.paths import PROJECT

STATE = PROJECT / "cache" / "drive_state.json"
NUMBER_RE = re.compile(r"^\d+\.\s+")
IGNORED = {"desktop.ini", "thumbs.db", ".ds_store"}
WORKERS = 6
CHUNK = 8 * 1024 * 1024
SAVE_EVERY = 100


def norm(segment: str) -> str:
    """Часть ключа: имя папки без номера в начале, без регистра."""
    return NUMBER_RE.sub("", segment).strip().casefold()


def file_key(parts: list[str]) -> str:
    """Ключ файла: папки — без номеров, имя файла — как есть (без регистра)."""
    return "/".join([norm(p) for p in parts[:-1]] + [parts[-1].casefold()])


def folder_key(parts: list[str]) -> str:
    return "/".join(norm(p) for p in parts)


def skip(name: str) -> bool:
    return name.startswith((".", "~$")) or name.casefold() in IGNORED or name.endswith((".tmp", ".part"))


@dataclass
class Item:
    key: str
    path: str               # настоящий путь относительно корня: «102. 86 — Eighty Six/74. Lena/a.jpg»
    size: int
    mtime: int = 0          # ПК: st_mtime_ns
    md5: str = ""           # Драйв: md5Checksum; ПК — считается, только когда нужно (поиск переносов)
    id: str = ""            # Драйв: id файла
    parent: str = ""        # Драйв: id папки


@dataclass
class Plan:
    upload: list = field(default_factory=list)         # (Item ПК, id файла на Драйве или "" — новый)
    download: list = field(default_factory=list)       # (Item Драйв, путь на ПК — куда)
    conflict: list = field(default_factory=list)       # (Item ПК, Item Драйв): Драйв → «(Drive)» рядом, ПК → на Драйв
    trash_remote: list = field(default_factory=list)   # Item Драйв
    trash_local: list = field(default_factory=list)    # Item ПК
    move_remote: list = field(default_factory=list)    # (Item Драйв, Item ПК — куда его поставить)
    move_local: list = field(default_factory=list)     # (Item ПК, Item Драйв — куда его поставить)
    link: list = field(default_factory=list)           # (Item ПК, Item Драйв) — уже одинаковые, только запомнить

    @property
    def deletions(self) -> int:
        return len(self.trash_remote) + len(self.trash_local)

    @property
    def empty(self) -> bool:
        return not (self.upload or self.download or self.conflict or self.trash_remote or self.trash_local
                    or self.move_remote or self.move_local)


# ---------------------------------------------------------------- сканирование

def local_root(collection: Path) -> Path:
    """Папка Waifu: config.json → drive.local, иначе anime-paths.json → waifu, иначе <коллекция>\\Waifu."""
    from anime_vault.paths import SETTINGS

    def read(path: Path) -> dict:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    local = (read(SETTINGS).get("drive") or {}).get("local")
    if local:
        return Path(local)
    return collection / (read(collection / "anime-paths.json").get("waifu") or "Waifu")


def settings() -> dict:
    from anime_vault.paths import SETTINGS

    try:
        data = json.loads(SETTINGS.read_text(encoding="utf-8")).get("drive") or {}
    except (OSError, ValueError):
        data = {}
    return {"folder": data.get("folder") or "Waifu", "auto": bool(data.get("auto")), "interval": int(data.get("interval") or 30)}


def scan_local(root: Path) -> tuple[dict[str, Item], dict[str, str]]:
    """→ (ключ → файл, ключ папки → путь папки)."""
    files, folders = {}, {}
    for current, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if not skip(d)]
        relative = Path(current).relative_to(root)
        parts = list(relative.parts)
        if parts:
            folders[folder_key(parts)] = relative.as_posix()
        for name in names:
            if skip(name):
                continue
            full = os.path.join(current, name)
            try:
                stat = os.stat(full)
            except OSError:
                continue
            key = file_key([*parts, name])
            files[key] = Item(key, (relative / name).as_posix(), stat.st_size, stat.st_mtime_ns)
    return files, folders


def find_root_folder(name: str, create: bool = False) -> str:
    """Папка name в корне «Моего диска» → id ("" — нет и create=False)."""
    service = gdrive.service()
    safe = name.replace("\\", "\\\\").replace("'", "\\'")
    found = service.files().list(q=f"name='{safe}' and mimeType='{gdrive.FOLDER}' and 'root' in parents and trashed=false",
                                 fields="files(id)", pageSize=10).execute(num_retries=3).get("files", [])
    if found:
        return found[0]["id"]
    if not create:
        return ""
    return service.files().create(body={"name": name, "mimeType": gdrive.FOLDER, "parents": ["root"]},
                                  fields="id").execute(num_retries=3)["id"]


def scan_remote(root_id: str) -> tuple[dict[str, Item], dict[str, tuple[str, str]]]:
    """Все файлы аккаунта одним списком → дерево от root_id. → (ключ → файл, ключ папки → (id, путь))."""
    service = gdrive.service()
    children: dict[str, list[dict]] = {}
    token = None
    while True:
        response = service.files().list(
            q="trashed=false and 'me' in owners", pageSize=1000, pageToken=token,
            fields="nextPageToken, files(id,name,mimeType,parents,md5Checksum,size,modifiedTime)").execute(num_retries=3)
        for item in response.get("files", []):
            for parent in item.get("parents") or []:
                children.setdefault(parent, []).append(item)
        token = response.get("nextPageToken")
        if not token:
            break
    files: dict[str, Item] = {}
    folders: dict[str, tuple[str, str]] = {}
    stack = [(root_id, [])]
    while stack:
        folder_id, parts = stack.pop()
        for item in children.get(folder_id, []):
            name = item["name"]
            if skip(name):
                continue
            if item["mimeType"] == gdrive.FOLDER:
                sub = [*parts, name]
                key = folder_key(sub)
                if key not in folders:                      # две папки с одним ключом — берётся первая
                    folders[key] = (item["id"], "/".join(sub))
                    stack.append((item["id"], sub))
            elif item["mimeType"].startswith("application/vnd.google-apps."):
                continue                                     # Google Документы/Таблицы — не файлы
            else:
                key = file_key([*parts, name])
                if key not in files:
                    files[key] = Item(key, "/".join([*parts, name]), int(item.get("size", 0)), md5=item.get("md5Checksum", ""),
                                      id=item["id"], parent=folder_id)
    return files, folders


def md5_of(path: Path) -> str:
    digest = hashlib.md5()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


# ---------------------------------------------------------------- план

def make_plan(local: dict[str, Item], remote: dict[str, Item], base: dict[str, dict], local_md5=None) -> Plan:
    """Что сделать, чтобы обе стороны совпали (чистая функция — без сети и диска, кроме local_md5).
    local_md5(item) → MD5 файла на ПК: нужен только для поиска переносов (размер совпал с удаляемым)."""
    plan = Plan()
    new_local, new_remote = [], []
    for key in sorted(set(local) | set(remote)):
        mine, theirs, was = local.get(key), remote.get(key), base.get(key)
        if mine and theirs:
            if was is None:
                if mine.size == theirs.size:
                    plan.link.append((mine, theirs))
                else:
                    plan.conflict.append((mine, theirs))
                continue
            local_changed = (mine.size, mine.mtime) != (was.get("size"), was.get("mtime"))
            remote_changed = (theirs.md5 or theirs.size) != (was.get("md5") or was.get("size"))
            if local_changed and remote_changed:
                plan.conflict.append((mine, theirs))
            elif local_changed:
                plan.upload.append((mine, theirs.id))
            elif remote_changed:
                plan.download.append((theirs, mine.path))
            elif mine.path != was.get("path") or theirs.id != was.get("id"):
                plan.link.append((mine, theirs))              # только путь/имя папки сменились — запомнить
        elif mine:
            if was is None:
                new_local.append(mine)
            elif (mine.size, mine.mtime) == (was.get("size"), was.get("mtime")):
                plan.trash_local.append(mine)                 # удалён на Драйве, на ПК не менялся
            else:
                plan.upload.append((mine, ""))                # удалён на Драйве, но на ПК изменён — вернуть
        else:
            if was is None:
                new_remote.append(theirs)
            elif (theirs.md5 or theirs.size) == (was.get("md5") or was.get("size")):
                plan.trash_remote.append(theirs)              # удалён на ПК, на Драйве не менялся
            else:
                plan.download.append((theirs, ""))            # удалён на ПК, но на Драйве изменён — вернуть

    # Переносы на ПК: новый файл на ПК = файл, который «удалён на ПК» (на Драйве по старому пути) → перенести на Драйве.
    by_md5 = {}
    for item in plan.trash_remote:
        if item.md5:
            by_md5.setdefault((item.size, item.md5), []).append(item)
    sizes = {size for size, _md5 in by_md5}
    for mine in new_local:
        match = None
        if mine.size in sizes and local_md5 is not None:
            mine.md5 = local_md5(mine)
            candidates = by_md5.get((mine.size, mine.md5))
            if candidates:
                match = candidates.pop()
        if match:
            plan.trash_remote.remove(match)
            plan.move_remote.append((match, mine))
        else:
            plan.upload.append((mine, ""))

    # Переносы на Драйве: новый файл на Драйве = файл, «удалённый на Драйве» (на ПК по старому пути) → перенести на ПК.
    by_md5 = {}
    for item in plan.trash_local:
        was = base.get(item.key) or {}
        if was.get("md5"):
            by_md5.setdefault((item.size, was["md5"]), []).append(item)
    for theirs in new_remote:
        candidates = by_md5.get((theirs.size, theirs.md5))
        if candidates:
            match = candidates.pop()
            plan.trash_local.remove(match)
            plan.move_local.append((match, theirs))
        else:
            plan.download.append((theirs, ""))
    return plan


# ---------------------------------------------------------------- выполнение

class Syncer:
    def __init__(self, root: Path, root_id: str, state: dict, remote_folders: dict[str, tuple[str, str]],
                 local_folders: dict[str, str]):
        self.root = root
        self.root_id = root_id
        self.state = state
        self.base: dict[str, dict] = state.setdefault("files", {})
        self.remote_folders = dict(remote_folders)
        self.local_folders = dict(local_folders)
        self.lock = threading.Lock()
        self.done = 0
        self.errors: list[str] = []

    # ---- состояние
    def remember(self, key: str, local: Item | None, md5: str, file_id: str, local_path: str) -> None:
        with self.lock:
            stat = os.stat(self.root / local_path)
            self.base[key] = {"path": local_path, "size": stat.st_size, "mtime": stat.st_mtime_ns, "md5": md5, "id": file_id}
            self.done += 1

    def forget(self, key: str) -> None:
        with self.lock:
            self.base.pop(key, None)
            self.done += 1

    def save(self) -> None:
        with self.lock:
            save_state(self.state)

    # ---- папки
    def remote_folder(self, local_path: str) -> str:
        """id папки на Драйве для папки ПК (создаёт недостающие; только из главного потока)."""
        parts = [p for p in local_path.split("/") if p]
        parent = self.root_id
        for index in range(len(parts)):
            key = folder_key(parts[:index + 1])
            if key not in self.remote_folders:
                created = gdrive.service().files().create(
                    body={"name": parts[index], "mimeType": gdrive.FOLDER, "parents": [parent]}, fields="id").execute(num_retries=3)
                self.remote_folders[key] = (created["id"], "/".join(parts[:index + 1]))
            parent = self.remote_folders[key][0]
        return parent

    def local_folder(self, remote_path: str) -> str:
        """Путь папки на ПК для папки Драйва: если такая (по ключу) уже есть — её имя с ПК."""
        parts = [p for p in remote_path.split("/") if p]
        result = []
        for index in range(len(parts)):
            key = folder_key(parts[:index + 1])
            known = self.local_folders.get(key)
            result = known.split("/") if known else [*result, parts[index]]
            self.local_folders[key] = "/".join(result)
        return "/".join(result)

    # ---- операции (в потоках)
    def upload(self, item: Item, file_id: str, parent: str) -> None:
        from googleapiclient.http import MediaFileUpload

        path = self.root / item.path
        media = MediaFileUpload(str(path), resumable=item.size > CHUNK, chunksize=CHUNK)
        files = gdrive.service().files()
        fields = "id,md5Checksum"
        if file_id:
            result = files.update(fileId=file_id, media_body=media, fields=fields).execute(num_retries=3)
        else:
            result = files.create(body={"name": path.name, "parents": [parent]}, media_body=media, fields=fields).execute(num_retries=3)
        self.remember(item.key, item, result.get("md5Checksum", ""), result["id"], item.path)

    def download(self, item: Item, local_path: str, key: str | None = None) -> None:
        from googleapiclient.http import MediaIoBaseDownload

        target = self.root / local_path
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".part")
        with open(temporary, "wb") as handle:
            downloader = MediaIoBaseDownload(handle, gdrive.service().files().get_media(fileId=item.id), chunksize=CHUNK)
            finished = False
            while not finished:
                _status, finished = downloader.next_chunk(num_retries=3)
        os.replace(temporary, target)
        self.remember(key or item.key, None, item.md5, item.id, local_path)

    def run_parallel(self, jobs: list, total_bytes: int, title: str) -> None:
        """jobs: [(функция, аргументы, размер)] — в WORKERS потоков, с прогрессом и сохранением состояния."""
        if not jobs:
            return
        started, last, moved, count = time.time(), 0.0, 0, 0
        console.step(title)
        with ThreadPoolExecutor(WORKERS) as pool:
            pending = {pool.submit(function, *args): size for function, args, size in jobs}
            while pending:
                finished, _rest = wait(pending, return_when=FIRST_COMPLETED)
                for future in finished:
                    size = pending.pop(future)
                    try:
                        future.result()
                    except Exception as exc:
                        self.errors.append(f"{type(exc).__name__}: {exc}"[:300])
                        console.warn("  " + _("сбой: {error}").format(error=self.errors[-1]))
                    moved += size
                    count += 1
                    if count % SAVE_EVERY == 0:
                        self.save()
                now = time.time()
                if now - last > 5 or not pending:
                    last = now
                    speed = moved / max(now - started, 1)
                    left = (total_bytes - moved) / speed if speed else 0
                    percent = int(100 * moved / total_bytes) if total_bytes else 100
                    # Одна строка — всё, что нужно понять: сколько уже, сколько осталось, скорость и сбои
                    text = _("{percent}% · готово {done} из {total} файлов ({moved} из {size}) · осталось {rest} файлов, ~{left} · {speed}/с") \
                        if pending else _("{percent}% · готово {done} из {total} файлов ({moved} из {size})")
                    if self.errors:
                        text += " · " + _("сбоев: {errors}").format(errors=len(self.errors))
                    console.info("  " + text.format(percent=percent, done=count, total=len(jobs), moved=gigabytes(moved),
                                                    size=gigabytes(total_bytes), rest=len(jobs) - count, left=duration(left),
                                                    speed=gigabytes(int(speed)), errors=len(self.errors)))
        self.save()


def gigabytes(size: int) -> str:
    return _("{value} ГБ").format(value=f"{size / 1e9:.2f}") if size >= 1e8 else _("{value} МБ").format(value=f"{size / 1e6:.1f}")


def duration(seconds: float) -> str:
    minutes = int(seconds // 60)
    return _("{hours} ч {minutes} мин").format(hours=minutes // 60, minutes=minutes % 60) if minutes >= 60 \
        else _("{minutes} мин").format(minutes=max(minutes, 1))


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    temporary.replace(STATE)


def conflict_name(path: str) -> str:
    stem, dot, ext = path.rpartition(".")
    return f"{stem} (Drive).{ext}" if dot else f"{path} (Drive)"


def run(collection: Path, yes: bool = False) -> int:
    from send2trash import send2trash

    root = local_root(collection)
    folder_name = settings()["folder"]
    console.title(_("Синхронизация {local} ⇄ Google Drive «{folder}»").format(local=root.name, folder=folder_name))
    if not root.is_dir():
        raise UserError(_("нет папки {path}").format(path=root), _("укажите папку в «Настройках» (Драйв → папка на ПК)"))
    account = gdrive.account()
    console.info(_("Аккаунт: {email}").format(email=account["email"]))
    state = load_state()
    if state.get("account") != account["email"] or state.get("folder") != folder_name:
        state = {"account": account["email"], "folder": folder_name, "files": {}}   # другой аккаунт/папка — с нуля
    base = state.setdefault("files", {})

    root_id = find_root_folder(folder_name)
    if not root_id and base:
        raise UserError(_("на Драйве пропала папка «{folder}» — синхронизация остановлена, ничего не удалено").format(folder=folder_name),
                        _("восстановите папку из корзины Драйва; если удалили нарочно — удалите cache\\drive_state.json и синхронизируйте заново"))

    console.step(_("Сканирую папку на ПК…"))
    local, local_folders = scan_local(root)
    console.info("  " + _("файлов {count}, {size}").format(count=len(local), size=gigabytes(sum(i.size for i in local.values()))))
    console.step(_("Сканирую Драйв…"))
    remote, remote_folders = scan_remote(root_id) if root_id else ({}, {})
    console.info("  " + _("файлов {count}, {size}").format(count=len(remote), size=gigabytes(sum(i.size for i in remote.values()))))

    plan = make_plan(local, remote, base, local_md5=lambda item: md5_of(root / item.path))
    up_bytes = sum(item.size for item, _id in plan.upload) + sum(mine.size for mine, _theirs in plan.conflict)
    down_bytes = sum(item.size for item, _path in plan.download) + sum(theirs.size for _mine, theirs in plan.conflict)
    renames = [(remote_folders[key][0], local_folders[key].rsplit("/", 1)[-1]) for key in local_folders
               if key in remote_folders and remote_folders[key][1].rsplit("/", 1)[-1] != local_folders[key].rsplit("/", 1)[-1]]

    console.title(_("План"))
    rows = [(_("На Драйв"), len(plan.upload), up_bytes, ""), (_("С Драйва на ПК"), len(plan.download), down_bytes, ""),
            (_("Изменены с обеих сторон (обе версии сохранятся)"), len(plan.conflict), 0, ""),
            (_("Перенести на Драйве"), len(plan.move_remote), 0, ""), (_("Перенести на ПК"), len(plan.move_local), 0, ""),
            (_("В корзину Драйва (удалены на ПК)"), len(plan.trash_remote), 0, console.YELLOW),
            (_("В Корзину на ПК (удалены на Драйве)"), len(plan.trash_local), 0, console.YELLOW),
            (_("Переименовать папки на Драйве"), len(renames), 0, "")]
    if plan.link:                               # после прерванной заливки — сколько уже на месте
        console.say("  " + _("Уже есть с обеих сторон: {count}").format(count=len(plan.link)))
    for title, count, size, color in rows:
        if count:
            console.say(f"  {title}: {count}" + (f" ({gigabytes(size)})" if size else ""), color)
    if plan.empty and not renames:
        for mine, theirs in plan.link:          # одинаковые с обеих сторон / сменился только путь — запомнить
            base[mine.key] = {"path": mine.path, "size": mine.size, "mtime": mine.mtime, "md5": theirs.md5, "id": theirs.id}
        state["folders"] = sorted(set(local_folders) & set(remote_folders))
        finish(state, {"uploaded": 0, "downloaded": 0, "deleted": 0, "errors": 0})
        console.ok(_("Всё совпадает — синхронизировать нечего."))
        return 0
    if account["limit"] and up_bytes > account["limit"] - account["used"]:
        raise UserError(_("на Драйве не хватит места: нужно {need}, свободно {free}").format(
            need=gigabytes(up_bytes), free=gigabytes(account["limit"] - account["used"])),
            _("освободите место на Драйве или подключите аккаунт, где места больше"))
    many = plan.deletions > 50 and plan.deletions > 0.1 * max(len(base), 1)
    if many:
        console.warn(_("Удалений много: {count}. Проверьте, что папка на ПК и на Драйве те, что нужно.").format(count=plan.deletions))
        if yes:
            console.error(_("автосинхронизация остановлена из-за большого числа удалений"), _("запустите синхронизацию вручную и проверьте план"))
            return 1
    if not yes and console.ask(_("Синхронизировать?"), "дн") != "д":
        console.warn(_("Отменено — ничего не изменено."))
        return 0

    if not root_id:
        root_id = find_root_folder(folder_name, create=True)
    syncer = Syncer(root, root_id, state, remote_folders, local_folders)
    service = gdrive.service()

    # 1. Уже одинаковые — запомнить; переносы — дёшево, по одному.
    for mine, theirs in plan.link:
        base[mine.key] = {"path": mine.path, "size": mine.size, "mtime": mine.mtime, "md5": theirs.md5, "id": theirs.id}
    for theirs, mine in plan.move_remote:
        try:
            parent = syncer.remote_folder(mine.path.rpartition("/")[0])
            service.files().update(fileId=theirs.id, addParents=parent, removeParents=theirs.parent,
                                   body={"name": mine.path.rsplit("/", 1)[-1]}, fields="id").execute(num_retries=3)
            base.pop(theirs.key, None)
            syncer.remember(mine.key, mine, theirs.md5, theirs.id, mine.path)
        except Exception as exc:
            syncer.errors.append(str(exc)[:300])
    for mine, theirs in plan.move_local:
        try:
            folder = syncer.local_folder(theirs.path.rpartition("/")[0])
            target = f"{folder}/{theirs.path.rsplit('/', 1)[-1]}" if folder else theirs.path.rsplit("/", 1)[-1]
            (root / target).parent.mkdir(parents=True, exist_ok=True)
            os.replace(root / mine.path, root / target)
            base.pop(mine.key, None)
            syncer.remember(theirs.key, None, theirs.md5, theirs.id, target)
        except Exception as exc:
            syncer.errors.append(str(exc)[:300])

    # 2. Переименования папок на Драйве (номера тайтлов/персонажей).
    for folder_id, name in renames:
        try:
            service.files().update(fileId=folder_id, body={"name": name}, fields="id").execute(num_retries=3)
        except Exception as exc:
            syncer.errors.append(str(exc)[:300])

    # 3. Загрузка и скачивание — папки на Драйве создаются заранее (в одном потоке), сами файлы — параллельно.
    #    Конфликты: сначала версия с Драйва скачивается рядом («(Drive)»), и только потом её место занимает версия ПК.
    jobs = [(syncer.download, (theirs, conflict_name(mine.path), file_key(conflict_name(mine.path).split("/"))), theirs.size)
            for mine, theirs in plan.conflict]
    syncer.run_parallel(jobs, sum(t.size for _m, t in plan.conflict), _("Сохраняю версии с Драйва, изменённые с обеих сторон…"))
    # Сначала все недостающие папки на Драйве (по одной — их могут быть тысячи, поэтому с прогрессом).
    needed = {mine.path.rpartition("/")[0] for mine, file_id in plan.upload if not file_id}
    missing = {folder_key(path.split("/")[:index + 1]) for path in needed if path
               for index in range(path.count("/") + 1)} - set(syncer.remote_folders)
    if missing:
        console.step(_("Создаю папки на Драйве: {count}…").format(count=len(missing)))
        last, before = time.time(), len(syncer.remote_folders)
        for path in sorted(needed):
            syncer.remote_folder(path)
            if time.time() - last > 5:
                last = time.time()
                console.info("  " + _("папок {done} из {total}").format(done=len(syncer.remote_folders) - before, total=len(missing)))
        console.info("  " + _("папок {done} из {total}").format(done=len(syncer.remote_folders) - before, total=len(missing)))
    jobs = []
    for mine, file_id in plan.upload:
        parent = "" if file_id else syncer.remote_folder(mine.path.rpartition("/")[0])
        jobs.append((syncer.upload, (mine, file_id, parent), mine.size))
    jobs += [(syncer.upload, (mine, theirs.id, ""), mine.size) for mine, theirs in plan.conflict]
    syncer.run_parallel(jobs, up_bytes, _("Загружаю на Драйв…"))
    jobs = []
    for theirs, local_path in plan.download:
        if not local_path:
            folder = syncer.local_folder(theirs.path.rpartition("/")[0])
            name = theirs.path.rsplit("/", 1)[-1]
            local_path = f"{folder}/{name}" if folder else name
        jobs.append((syncer.download, (theirs, local_path), theirs.size))
    syncer.run_parallel(jobs, sum(t.size for t, _p in plan.download), _("Скачиваю с Драйва…"))

    # 4. Удаления — только в корзины.
    for theirs in plan.trash_remote:
        try:
            service.files().update(fileId=theirs.id, body={"trashed": True}, fields="id").execute(num_retries=3)
            syncer.forget(theirs.key)
        except Exception as exc:
            syncer.errors.append(str(exc)[:300])
    for mine in plan.trash_local:
        try:
            send2trash(str(root / mine.path))
            syncer.forget(mine.key)
        except Exception as exc:
            syncer.errors.append(str(exc)[:300])
    cleanup_folders(root, syncer, local_folders, remote_folders)
    syncer.save()

    summary = {"uploaded": len(plan.upload) + len(plan.conflict), "downloaded": len(plan.download) + len(plan.conflict),
               "deleted": plan.deletions, "errors": len(syncer.errors)}
    finish(state, summary)
    if syncer.errors:
        console.warn(_("Готово с ошибками: {count}. Запустите синхронизацию ещё раз — несделанное повторится.").format(count=len(syncer.errors)))
        return 1
    console.ok(_("Готово: на Драйв {uploaded}, на ПК {downloaded}, в корзины {deleted}.").format(**summary))
    return 0


def cleanup_folders(root: Path, syncer: Syncer, local_folders: dict, remote_folders: dict) -> None:
    """Папки, которые были при прошлой синхронизации (state["folders"]) и пропали с одной стороны: на другой стороне
    удаляются, только если опустели (на ПК — rmdir пустой папки, на Драйве — в корзину). Новые пустые папки не трогаются."""
    known = set(syncer.state.get("folders") or [])
    removed = set()
    for key in sorted((known & set(local_folders)) - set(remote_folders), key=lambda k: -k.count("/")):
        path = root / local_folders[key]
        try:
            if path.is_dir() and not any(path.iterdir()):
                path.rmdir()
                removed.add(key)
        except OSError:
            pass
    service = gdrive.service()
    for key in sorted((known & set(remote_folders)) - set(local_folders), key=lambda k: -k.count("/")):
        folder_id = remote_folders[key][0]
        try:
            inside = service.files().list(q=f"'{folder_id}' in parents and trashed=false", fields="files(id)",
                                          pageSize=1).execute(num_retries=3).get("files", [])
            if not inside:
                service.files().update(fileId=folder_id, body={"trashed": True}, fields="id").execute(num_retries=3)
                removed.add(key)
        except Exception as exc:
            syncer.errors.append(str(exc)[:300])
    # Запомнить папки, которые теперь есть с обеих сторон, — для следующей чистки.
    syncer.state["folders"] = sorted((set(syncer.local_folders) & set(syncer.remote_folders)) - removed)


def finish(state: dict, summary: dict) -> None:
    state["last"] = {"time": time.strftime("%Y-%m-%d %H:%M"), **summary}
    save_state(state)

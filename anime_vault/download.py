r"""Скачивание НОВЫХ пинов с досок Pinterest (anime-paths.json → pinterest.boards) через gallery-dl.

Как работает:
    1. Пошаговая инструкция: браузер (Opera GX) должен быть залогинен в Pinterest и ЗАКРЫТ —
       gallery-dl берёт из него куки (доски приватные, без входа Pinterest отвечает 403).
    2. Пины, которые уже есть в любой dataN или в источниках раскладки (доски, которые раскладываются, —
       «01. An i me», — и Anime ADD\processed_data), добавляются в архив gallery-dl (.gallery-dl-archive.sqlite3 в
       папке скачивания) — такие пины не скачиваются. Доска «только хранить» («02. Шедевры») качает своё как
       обычно: что уже в ней, помнит сам архив gallery-dl.
    3. Каждая доска качается во временную папку .download-work\staging\<номер>, затем у каждой картинки
       текст пина (title / description из .json рядом) вшивается в саму картинку (EXIF у jpg, текст у png —
       его читает anime-sort), и картинка переносится прямо в <папка скачивания>\<доска> (разделы доски не
       нужны — все фото доски в одной папке).
    4. Прервали (Ctrl+C, сбой сети) — следующий запуск продолжит: недоразобранное из staging доносится,
       скачанное не качается заново.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import piexif
from PIL import Image, PngImagePlugin

from anime_vault import console
from anime_vault.console import UserError
from anime_vault.known import Known, pin_key
from anime_vault.manifest import Board, Manifest

GALLERY_DL = Path(sys.executable).with_name("gallery-dl.exe")
EMBED_EXTENSIONS = {".jpg", ".jpeg", ".png"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


# ---------- текст пина внутрь картинки ----------

def pin_text(data: dict) -> tuple[str, str]:
    """(заголовок, описание) — только полезные поля, весь JSON в картинку не пишется."""
    def text(value) -> str:
        return "" if value is None else str(value).strip()

    title = text(data.get("title") or data.get("grid_title"))
    description = text(data.get("description"))
    alt = text(data.get("seo_alt_text") or data.get("alt_text"))
    parts = [description] if description else []
    if alt and alt.casefold() not in {description.casefold(), title.casefold()}:
        parts.append(alt)
    return title, "\n".join(parts)


def embed(image: Path, sidecar: Path) -> bool:
    """Вшить текст пина в jpg/png. False — не получилось (картинка всё равно переносится, текст остаётся без неё)."""
    if image.suffix.lower() not in EMBED_EXTENSIONS:
        return True
    try:
        title, description = pin_text(json.loads(sidecar.read_text(encoding="utf-8")))
        if not (title or description):
            return True
        if image.suffix.lower() in {".jpg", ".jpeg"}:
            try:
                exif = piexif.load(str(image))
            except Exception:
                exif = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}, "thumbnail": None}
            exif["0th"][piexif.ImageIFD.ImageDescription] = description.encode("utf-8")
            exif["0th"][piexif.ImageIFD.XPTitle] = title.encode("utf-16le") + b"\x00\x00"
            exif["0th"][piexif.ImageIFD.XPComment] = description.encode("utf-16le") + b"\x00\x00"
            piexif.insert(piexif.dump(exif), str(image))
        else:
            with Image.open(image) as picture:
                info = PngImagePlugin.PngInfo()
                for key, value in picture.info.items():
                    if isinstance(value, str) and key not in {"Title", "Description", "Comment"}:
                        info.add_text(key, value)
                info.add_text("Title", title)
                info.add_text("Description", description)
                if description:
                    info.add_text("Comment", description)
                picture.save(image, pnginfo=info)
        return True
    except Exception as exc:
        console.warn(f"текст пина не вшит в {image.name}: {type(exc).__name__}: {exc}")
        return False


# ---------- gallery-dl ----------

def seed_archive(archive: Path, known: Known) -> int:
    """Все известные пины — в архив gallery-dl (формат записи: <id>[_<id медиа>], как у pin_key)."""
    archive.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(archive)
    connection.execute("CREATE TABLE IF NOT EXISTS archive (entry TEXT PRIMARY KEY) WITHOUT ROWID")
    before = connection.execute("SELECT COUNT(*) FROM archive").fetchone()[0]
    connection.executemany("INSERT OR IGNORE INTO archive(entry) VALUES (?)", ((pin,) for pin in known.pins))
    connection.commit()
    after = connection.execute("SELECT COUNT(*) FROM archive").fetchone()[0]
    connection.close()
    return after - before


def gallery_config(path: Path, archive: Path) -> None:
    # Без подпапок-разделов: всё скачанное с доски — прямо в её папку.
    path.write_text(json.dumps({"extractor": {"pinterest": {
        "filename": "pinterest_{id}{media_id|page_id:?_//}.{extension}",
        "directory": [],
        "archive": str(archive),
        "archive-prefix": "",
        "archive-format": "{id}{media_id|page_id:?_//}",
        "archive-event": "file",
    }}}, ensure_ascii=False, indent=2), encoding="utf-8")


def board_url(manifest: Manifest, board: Board) -> str:
    return f"https://www.pinterest.com/{manifest.pinterest_user}/{board.slug}/"


def explain_failure(output: str) -> tuple[str, str]:
    """Текст ошибки gallery-dl → (что случилось, что сделать)."""
    low = output.casefold()
    if "cookie" in low and ("lock" in low or "permission" in low or "database" in low or "unable to" in low):
        return ("не удалось прочитать куки браузера",
                "полностью закройте Opera GX (и в трее тоже) и запустите снова")
    if "403" in low or "private" in low or "forbidden" in low:
        return ("Pinterest не пускает к доске (403 / приватная)",
                "откройте Opera GX, войдите в Pinterest под своим аккаунтом, закройте браузер и запустите снова")
    if "404" in low or "not found" in low:
        return ("доска не найдена (404)", "проверьте user и slug доски в anime-paths.json — адрес доски из браузера")
    if "429" in low or "rate" in low:
        return ("Pinterest ограничил запросы (слишком часто)", "подождите 15–30 минут и запустите снова — скачанное не потеряется")
    if "connection" in low or "timed out" in low or "resolve" in low:
        return ("нет связи с Pinterest", "проверьте интернет (и обходчик блокировок, если он нужен) и запустите снова")
    return ("gallery-dl завершился с ошибкой (текст выше)", "запустите снова — уже скачанное не качается повторно")


def run_gallery(manifest: Manifest, board: Board, stage: Path, config: Path, first: bool) -> tuple[int, str, int]:
    """gallery-dl по одной доске → (код выхода, весь вывод, сколько файлов скачано)."""
    command = [str(GALLERY_DL), "--config-json", str(config), "--cookies-from-browser", manifest.browser_cookies,
               "--write-metadata", "--retries", "5", "--sleep-request", "0.5-1.5", "--no-colors",
               "-d", str(stage), board_url(manifest, board)]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                               encoding="utf-8", errors="replace",
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))   # без мелькающего окна
    lines, downloaded, last_report = [], 0, time.time()
    for line in process.stdout:
        line = line.rstrip()
        if first and not lines:
            # Первая строка gallery-dl — куки уже прочитаны.
            console.ok("Куки прочитаны — Opera GX можно снова открыть.")
        lines.append(line)
        if line.startswith("#"):
            continue                                            # «# файл» — уже есть в архиве
        if line.lower().endswith(tuple(IMAGE_EXTENSIONS)) or line.lower().endswith(".mp4"):
            downloaded += 1
            if downloaded <= 3 or time.time() - last_report > 10:
                console.info(f"  скачано {downloaded}: {Path(line).name}")
                last_report = time.time()
        elif "[error]" in line.lower() or "[warning]" in line.lower():
            console.warn(f"  {line}")
    return process.wait(), "\n".join(lines[-40:]), downloaded


def collect(stage: Path, target: Path, known: Known) -> tuple[int, int, int]:
    """Из staging доски — в её папку: вшить текст, перенести; дубли (уже есть в коллекции) — удалить.
    → (перенесено, дублей, без текста)."""
    moved = duplicates = unembedded = 0
    if not stage.exists():
        return 0, 0, 0
    for image in sorted(path for path in stage.rglob("*") if path.is_file() and path.suffix.lower() != ".json"):
        sidecar = image.with_name(image.name + ".json")
        key = pin_key(image.name)
        destination = target / image.name   # разделы доски не нужны — прямо в папку доски
        if (key and key in known.pins) or destination.exists():
            duplicates += 1
            image.unlink()
        else:
            if sidecar.exists() and not embed(image, sidecar):
                unembedded += 1
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(image), str(destination))
            if key:
                known.pins.add(key)
            moved += 1
        if sidecar.exists():
            sidecar.unlink()
    shutil.rmtree(stage, ignore_errors=True)
    return moved, duplicates, unembedded


# ---------- команда ----------

def opera_running() -> bool:
    """Запущена ли Opera GX — по списку процессов Windows (CreateToolhelp32Snapshot), без запуска tasklist:
    консольная программа, запущенная из окна anime-vault, мелькала бы консольным окном при каждой проверке."""
    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_void_p), ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)   # TH32CS_SNAPPROCESS
    if not snapshot or snapshot == wintypes.HANDLE(-1).value:
        return False
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        found = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while found:
            if entry.szExeFile.casefold() == "opera.exe":
                return True
            found = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
        return False
    finally:
        kernel32.CloseHandle(snapshot)


def instructions(manifest: Manifest) -> None:
    console.title("Скачивание новых пинов с Pinterest")
    console.say("Доски: " + ", ".join(board.name for board in manifest.boards))
    console.say(f"Куда:  {manifest.pinterest_target}")
    console.say()
    console.say("Перед началом:", console.WHITE)
    console.say("  1. Откройте Opera GX и убедитесь, что вы вошли в Pinterest (аккаунт с этими досками).")
    console.say("  2. Полностью закройте Opera GX (и значок в трее) — иначе куки не прочитать.")
    console.say("  3. Нажмите Enter здесь. Когда начнётся скачивание первой доски, браузер можно снова открыть.")
    console.say()
    while True:
        console.wait("Готово? Нажмите Enter…")
        if not opera_running():
            return
        console.warn("Opera GX ещё запущена. Закройте её полностью (проверьте трей) и нажмите Enter снова.")


def run(manifest: Manifest) -> int:
    if not GALLERY_DL.exists():
        raise UserError(f"не найден {GALLERY_DL}", "в папке проекта выполните: venv\\Scripts\\pip install -r requirements.txt")
    instructions(manifest)
    target = manifest.pinterest_target
    work = target / ".download-work"
    archive = target / ".gallery-dl-archive.sqlite3"
    config = work / "gallery-dl-config.json"
    work.mkdir(parents=True, exist_ok=True)

    console.step("Собираю, что уже есть (dataN и источники раскладки), чтобы не качать повторно…")
    known = Known(manifest.root)
    for folder in (manifest.batches, *manifest.sources):
        count = known.add_folder(folder, hash_others=False)
        console.info(f"  {folder.name}: файлов {count}")
    added = seed_archive(archive, known)
    console.info(f"  известных пинов {len(known.pins)}, новых записей в архиве gallery-dl {added}")
    gallery_config(config, archive)

    totals, failed = {}, []
    for index, board in enumerate(manifest.boards, 1):
        stage = work / "staging" / str(index)
        board_dir = target / board.name
        # Остатки прерванного запуска — сначала донести их.
        if stage.exists():
            moved, duplicates, _ = collect(stage, board_dir, known)
            if moved or duplicates:
                console.info(f"  {board.name}: доразобран прошлый запуск — новых {moved}, дублей {duplicates}")
        console.step(f"Доска «{board.name}»: скачиваю новое…")
        code, output, downloaded = run_gallery(manifest, board, stage, config, first=index == 1)
        moved, duplicates, unembedded = collect(stage, board_dir, known)
        totals[board.name] = moved
        if code in (0, 4):
            console.ok(f"Доска «{board.name}»: новых файлов {moved}" + (f", дублей отброшено {duplicates}" if duplicates else "")
                       + (f", без вшитого текста {unembedded}" if unembedded else ""))
            if code == 4:
                console.warn("  часть файлов Pinterest не отдал — они докачаются при следующем запуске")
        else:
            what, fix = explain_failure(output)
            console.error(f"доска «{board.name}»: {what}", fix)
            failed.append(board.name)
    known.save()
    console.title("Итог")
    for name, count in totals.items():
        console.say(f"  {name}: новых {count}", console.GREEN if count else console.GREY)
    if failed:
        console.warn("Не докачаны: " + ", ".join(failed) + " — исправьте причину выше и запустите снова.")
        return 1
    console.ok("Готово. Новое лежит в папках досок; разложить по dataN — distribute.bat в корне коллекции.")
    return 0

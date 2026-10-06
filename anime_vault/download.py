r"""Скачивание НОВОГО с досок Pinterest (и других сайтов gallery-dl) — config.json → pinterest.boards.

Как работает:
    1. Пошаговая инструкция: браузер должен быть залогинен на сайте и (кроме Firefox) ЗАКРЫТ —
       gallery-dl берёт из него куки (доски приватные, без входа Pinterest отвечает 403). Вместо браузера можно
       указать файл cookies.txt — тогда закрывать ничего не надо (anime_vault\browsers.py).
    2. Пины, которые уже есть в любой dataN или в источниках раскладки (доски, которые раскладываются, —
       «01. An i me», — и Anime ADD\processed_data), добавляются в архив gallery-dl (.gallery-dl-archive.sqlite3 в
       папке скачивания) — такие пины не скачиваются. Доска «только хранить» («02. Шедевры») качает своё как
       обычно: что уже в ней, помнит сам архив gallery-dl. Файлы других сайтов — так же, по ключу <сайт>_<id>.
    3. Каждая доска качается во временную папку .download-work\staging\<номер>, затем у каждой картинки
       текст (заголовок / описание пина, теги персонажей и тайтла у Danbooru-подобных — из .json рядом) вшивается
       в саму картинку (EXIF у jpg, текст у png — его читает anime-sort), и картинка переносится прямо
       в <папка скачивания>\<доска> (разделы доски не нужны — все фото доски в одной папке).
    4. Прервали (Ctrl+C, сбой сети) — следующий запуск продолжит: недоразобранное из staging доносится,
       скачанное не качается заново.
gallery-dl запускается отдельным процессом: из исходников — python -m gallery_dl, из собранного .exe —
тот же anime-vault.exe с ключом --gallery-dl (anime_vault\__main__.py).
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import piexif
from PIL import Image, PngImagePlugin

from anime_vault import browsers, console
from anime_vault.console import UserError
from anime_vault.i18n import _
from anime_vault.known import Known, pin_key
from anime_vault.manifest import Board, Manifest
from anime_vault.paths import FROZEN

EMBED_EXTENSIONS = {".jpg", ".jpeg", ".png"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4"}
# Имена файлов и записи архива: у пинов — как всегда было (архив прошлых запусков остаётся в силе),
# у других сайтов — <сайт>_<id>[_<номер>] (известные такие файлы узнаются по имени — known.pin_key).
SITE_NAME = "{category}_{id}{num:?_//}"
PIN_NAME = "{id}{media_id|page_id:?_//}"


def gallery_command() -> list[str]:
    if FROZEN:
        return [sys.executable, "--gallery-dl"]
    return [sys.executable, "-m", "gallery_dl"]


# ---------- текст пина внутрь картинки ----------

def pin_text(data: dict) -> tuple[str, str]:
    """(заголовок, описание) — только полезные поля, весь JSON в картинку не пишется.
    Pinterest: title / description / alt; X, Pixiv и т. п.: content / caption; Danbooru и похожие: теги персонажей и тайтла."""
    def text(value) -> str:
        return "" if value is None else str(value).strip()

    title = text(data.get("title") or data.get("grid_title"))
    description = text(data.get("description") or data.get("content") or data.get("caption"))
    alt = text(data.get("seo_alt_text") or data.get("alt_text"))
    parts = [description] if description else []
    if alt and alt.casefold() not in {description.casefold(), title.casefold()}:
        parts.append(alt)
    characters = text(data.get("tag_string_character") or data.get("tags_character"))
    series = text(data.get("tag_string_copyright") or data.get("tags_copyright"))
    if characters:
        parts.append(f"Characters: {characters}")
    if series:
        parts.append(f"Copyright: {series}")
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
        console.warn(_("текст не вшит в {name}: {error}").format(name=image.name, error=f"{type(exc).__name__}: {exc}"))
        return False


# ---------- gallery-dl ----------

def seed_archive(archive: Path, known: Known) -> int:
    """Все известные пины и файлы сайтов — в архив gallery-dl (запись — как pin_key: <id>[_<медиа>] / <сайт>_<id>…)."""
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
    common = {"directory": [], "archive": str(archive), "archive-prefix": "", "archive-event": "file"}
    path.write_text(json.dumps({"extractor": {
        **common,
        "filename": SITE_NAME + ".{extension}",
        "archive-format": SITE_NAME,
        "pinterest": {**common, "filename": "pinterest_" + PIN_NAME + ".{extension}", "archive-format": PIN_NAME},
    }}, ensure_ascii=False, indent=2), encoding="utf-8")


def explain_failure(output: str, cookies: browsers.Cookies) -> tuple[str, str]:
    """Текст ошибки gallery-dl → (что случилось, что сделать)."""
    low = output.casefold()
    browser = cookies.name or _("браузер")
    if "cookie" in low and ("decrypt" in low or "app-bound" in low or "dpapi" in low):
        return (_("не удалось расшифровать куки браузера {browser}").format(browser=browser),
                _("новые Chrome / Edge не отдают куки другим программам — сохраните cookies.txt расширением браузера и укажите файл в «Настройках» (или войдите через Firefox)"))
    if "cookie" in low and ("lock" in low or "permission" in low or "database" in low or "unable to" in low):
        return (_("не удалось прочитать куки браузера"),
                _("полностью закройте {browser} (и в трее тоже) и запустите снова").format(browser=browser))
    if "403" in low or "private" in low or "forbidden" in low or "401" in low:
        return (_("сайт не пускает к доске (403 / приватная)"),
                _("войдите на сайт в браузере {browser} под своим аккаунтом, закройте браузер и запустите снова").format(browser=browser))
    if "404" in low or "not found" in low:
        return (_("доска не найдена (404)"), _("проверьте ссылку на доску в «Настройках» — скопируйте её из адресной строки браузера"))
    if "unsupported url" in low or "no suitable extractor" in low:
        return (_("gallery-dl не умеет качать с этого адреса"), _("проверьте ссылку; список сайтов — github.com/mikf/gallery-dl, docs/supportedsites.md"))
    if "429" in low or "rate" in low:
        return (_("сайт ограничил запросы (слишком часто)"), _("подождите 15–30 минут и запустите снова — скачанное не потеряется"))
    if "connection" in low or "timed out" in low or "resolve" in low:
        return (_("нет связи с сайтом"), _("проверьте интернет (и обходчик блокировок, если он нужен) и запустите снова"))
    return (_("gallery-dl завершился с ошибкой (текст выше)"), _("запустите снова — уже скачанное не качается повторно"))


def run_gallery(manifest: Manifest, board: Board, stage: Path, config: Path, first: bool,
                cookies: browsers.Cookies) -> tuple[int, str, int]:
    """gallery-dl по одной доске → (код выхода, весь вывод, сколько файлов скачано)."""
    command = [*gallery_command(), "--config-json", str(config), *cookies.arguments(),
               "--write-metadata", "--retries", "5", "--sleep-request", "0.5-1.5", "--no-colors",
               "-d", str(stage), board.address(manifest.pinterest_user)]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                               text=True, encoding="utf-8", errors="replace",
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))   # без мелькающего окна
    lines, downloaded, last_report = [], 0, time.time()
    for line in process.stdout:
        line = line.rstrip()
        if first and not lines and cookies.must_close:
            # Первая строка gallery-dl — куки уже прочитаны.
            console.ok(_("Куки прочитаны — {browser} можно снова открыть.").format(browser=cookies.name))
        lines.append(line)
        if line.startswith("#"):
            continue                                            # «# файл» — уже есть в архиве
        if line.lower().endswith(tuple(IMAGE_EXTENSIONS)):
            downloaded += 1
            if downloaded <= 3 or time.time() - last_report > 10:
                console.info("  " + _("скачано {count}: {name}").format(count=downloaded, name=Path(line).name))
                last_report = time.time()
        elif "[error]" in line.lower() or "[warning]" in line.lower():
            console.warn(f"  {line}")
    return process.wait(), "\n".join(lines[-40:]), downloaded


def collect(stage: Path, target: Path, known: Known) -> tuple[int, int, int]:
    """Из staging доски — в её папку: вшить текст, перенести; дубли (уже есть в коллекции) — удалить
    (это свежескачанные копии того, что уже лежит в коллекции). → (перенесено, дублей, без текста)."""
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
    """Запущена ли Opera (старое имя — для совместимости; вообще — browsers.is_running)."""
    return browsers.is_running(browsers.parse("opera"))


def instructions(manifest: Manifest, cookies: browsers.Cookies) -> None:
    console.title(_("Скачивание нового с досок"))
    console.say(_("Доски: {boards}").format(boards=", ".join(board.name for board in manifest.boards)))
    console.say(_("Куда:  {target}").format(target=manifest.pinterest_target))
    console.say()
    if not cookies.must_close:
        console.say(browsers.describe(cookies), console.GREY)
        return
    console.say(_("Перед началом:"), console.WHITE)
    console.say("  " + _("1. Откройте {browser} и убедитесь, что вы вошли на сайт (аккаунт с этими досками).").format(browser=cookies.name))
    console.say("  " + _("2. Полностью закройте {browser} (и значок в трее) — иначе куки не прочитать.").format(browser=cookies.name))
    console.say("  " + _("3. Нажмите Enter здесь. Когда начнётся скачивание первой доски, браузер можно снова открыть."))
    console.say()
    while True:
        console.wait(_("Готово? Нажмите Enter…"))
        if not browsers.is_running(cookies):
            return
        console.warn(_("{browser} ещё запущен. Закройте его полностью (проверьте трей) и нажмите Enter снова.").format(browser=cookies.name))


def run(manifest: Manifest) -> int:
    if not FROZEN and importlib.util.find_spec("gallery_dl") is None:
        raise UserError(_("не установлен gallery-dl"), _("в папке проекта выполните: venv\\Scripts\\pip install -r requirements.txt"))
    if not manifest.boards:
        raise UserError(_("не задано ни одной доски"), _("добавьте ссылки на доски в «Настройках»"))
    for board in manifest.boards:
        if not board.url and not (board.user or manifest.pinterest_user):
            raise UserError(_("у доски «{name}» не указан пользователь Pinterest").format(name=board.name),
                            _("впишите в «Настройках» полную ссылку на доску или пользователя Pinterest"))
    cookies = browsers.parse(manifest.browser_cookies)
    instructions(manifest, cookies)
    target = manifest.pinterest_target
    work = target / ".download-work"
    archive = target / ".gallery-dl-archive.sqlite3"
    config = work / "gallery-dl-config.json"
    work.mkdir(parents=True, exist_ok=True)

    console.step(_("Собираю, что уже есть (dataN и источники раскладки), чтобы не качать повторно…"))
    known = Known(manifest.root)
    for folder in (manifest.batches, *manifest.sources):
        count = known.add_folder(folder, hash_others=False)
        console.info("  " + _("{folder}: файлов {count}").format(folder=folder.name, count=count))
    added = seed_archive(archive, known)
    console.info("  " + _("известных файлов {known}, новых записей в архиве gallery-dl {added}").format(known=len(known.pins), added=added))
    gallery_config(config, archive)

    totals, failed = {}, []
    for index, board in enumerate(manifest.boards, 1):
        stage = work / "staging" / str(index)
        board_dir = target / board.name
        # Остатки прерванного запуска — сначала донести их.
        if stage.exists():
            moved, duplicates, _unused = collect(stage, board_dir, known)
            if moved or duplicates:
                console.info("  " + _("{board}: доразобран прошлый запуск — новых {moved}, дублей {duplicates}").format(
                    board=board.name, moved=moved, duplicates=duplicates))
        console.step(_("Доска «{board}»: скачиваю новое…").format(board=board.name))
        code, output, downloaded = run_gallery(manifest, board, stage, config, index == 1, cookies)
        moved, duplicates, unembedded = collect(stage, board_dir, known)
        totals[board.name] = moved
        if code in (0, 4):
            text = _("Доска «{board}»: новых файлов {moved}").format(board=board.name, moved=moved)
            if duplicates:
                text += _(", дублей отброшено {count}").format(count=duplicates)
            if unembedded:
                text += _(", без вшитого текста {count}").format(count=unembedded)
            console.ok(text)
            if code == 4:
                console.warn("  " + _("часть файлов сайт не отдал — они докачаются при следующем запуске"))
        else:
            what, fix = explain_failure(output, cookies)
            console.error(_("доска «{board}»: {what}").format(board=board.name, what=what), fix)
            failed.append(board.name)
    known.save()
    console.title(_("Итог"))
    for name, count in totals.items():
        console.say("  " + _("{board}: новых {count}").format(board=name, count=count), console.GREEN if count else console.GREY)
    if failed:
        console.warn(_("Не докачаны: {boards} — исправьте причину выше и запустите снова.").format(boards=", ".join(failed)))
        return 1
    console.ok(_("Готово. Новое лежит в папках досок; разложить по пачкам — «Разложить» в окне или distribute.bat."))
    return 0

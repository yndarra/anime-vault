r"""Раскладка нового в dataN пачками по batch_size (anime-paths.json) — для anime-sort.

Источники — anime-paths.json → sources (скачанные доски Pinterest, Anime ADD\processed_data). Файлы ПЕРЕНОСЯТСЯ
(копии место не занимают), в порядке появления (по времени изменения), в новые папки dataN: номера идут
дальше самого большого существующего (data81 → data82, data83 …). Последняя папка может быть меньше
batch_size — следующий запуск её не дополняет, а начинает новую (старая к тому времени может быть уже обработана).

Дубли (такой пин или такая же картинка уже есть в dataN или test-dataN) не раскладываются, а переносятся
в <корень>\duplicates\<дата> — посмотреть и удалить (окно: страница «Дубли», там же видно оригинал рядом).
Перед переносом показывается план и спрашивается подтверждение. Список перенесённого — <проект>\logs\distribute_<время>.json:
{"time", "batches": {папка: [откуда]}, "duplicates": [{"file": где дубль теперь, "source": откуда, "original": такой же файл}]}.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time
from pathlib import Path

from anime_vault import console
from anime_vault.i18n import _
from anime_vault.known import Known, image_entries
from anime_vault.manifest import Manifest

# dataN и dataN-other (папки из «Other» Waifu — их делает anime-sort, команда other): номер у них общий.
BATCH_RE = re.compile(r"^data(\d+)(?:-other)?$")


def next_number(batches: Path) -> int:
    numbers = [int(match.group(1)) for path in batches.iterdir() if path.is_dir()
               for match in [BATCH_RE.match(path.name)] if match] if batches.is_dir() else []
    return max(numbers, default=0) + 1


def run(manifest: Manifest) -> int:
    console.title(_("Раскладка нового по dataN"))
    console.step(_("Собираю, что уже есть в dataN и test-dataN…"))
    known = Known(manifest.root)
    for folder in (manifest.batches, manifest.results):
        console.info("  " + _("{folder}: файлов {count}").format(folder=folder.name, count=known.add_folder(folder)))

    console.step(_("Ищу новое в источниках…"))
    new, duplicates = [], []          # duplicates: (путь, такой же уже известный файл)
    for source in manifest.sources:
        found = dup = 0
        for entry in sorted(image_entries(source), key=lambda item: (item.stat().st_mtime, item.name)):
            if known.is_known(entry):
                duplicates.append((Path(entry.path), known.original(entry)))
                dup += 1
            else:
                known.remember(entry)          # одинаковые файлы в двух источниках — второй уже дубль
                new.append(Path(entry.path))
                found += 1
        console.info("  " + _("{source}: новых {found}, дублей {dup}").format(source=source.relative_to(manifest.root), found=found, dup=dup))
    known.save()

    if not new and not duplicates:
        console.ok(_("Нового нет — раскладывать нечего."))
        return 0
    size = manifest.batch_size
    first = next_number(manifest.batches)
    groups = [new[start:start + size] for start in range(0, len(new), size)]
    console.title(_("План"))
    for offset, group in enumerate(groups):
        console.say("  " + _("data{number}: {count} файлов").format(number=first + offset, count=len(group)))
    if duplicates:
        console.say("  " + _("дубли: {count} → duplicates (удалить вручную после просмотра)").format(count=len(duplicates)), console.YELLOW)
    if console.ask(_("Перенести?"), "дн") != "д":
        console.warn(_("Отменено — ничего не перенесено."))
        return 0

    stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
    journal = {"time": stamp, "batches": {}, "duplicates": []}
    moved_to: dict[str, str] = {}      # откуда → куда: оригинал дубля мог только что уехать в dataN
    for offset, group in enumerate(groups):
        folder = manifest.batches / f"data{first + offset}"
        folder.mkdir(parents=True)
        journal["batches"][folder.name] = []
        for path in group:
            target = folder / path.name
            if target.exists():                                 # то же имя у разных файлов (скриншоты)
                target = folder / f"{path.stem}_{os.urandom(3).hex()}{path.suffix}"
            shutil.move(str(path), str(target))
            moved_to[str(path)] = str(target)
            journal["batches"][folder.name].append(str(path))
        console.ok("  " + _("{folder}: перенесено {count}").format(folder=folder.name, count=len(group)))
    if duplicates:
        bin_folder = manifest.root / "duplicates" / stamp
        bin_folder.mkdir(parents=True, exist_ok=True)
        for path, original in duplicates:
            target = bin_folder / f"{path.parent.name}__{path.name}"
            shutil.move(str(path), str(target))
            journal["duplicates"].append({"file": str(target), "source": str(path),
                                          "original": moved_to.get(original or "", original or "")})
        console.warn("  " + _("дубли перенесены в {folder}").format(folder=bin_folder))
    from anime_vault.manifest import LOGS

    LOGS.mkdir(parents=True, exist_ok=True)
    log = LOGS / f"distribute_{stamp}.json"   # журналы — в папке проекта (logs\\)
    log.write_text(json.dumps(journal, ensure_ascii=False, indent=1), encoding="utf-8")
    console.ok(_("Готово: {count} файлов в {folders} папках (data{first}…data{last}).").format(
        count=len(new), folders=len(groups), first=first, last=first + len(groups) - 1))
    console.say(_("Дальше — anime-sort: prepare.bat подхватит новые папки."), console.GREY)
    return 0

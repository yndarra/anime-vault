r"""Сборка anime-vault.exe (PyInstaller, папкой — так быстрее запуск) и архива для GitHub Releases.

    venv\Scripts\pip install -r requirements.txt pyinstaller
    venv\Scripts\python packaging\build.py          → build\dist\anime-vault\anime-vault.exe и build\anime-vault-windows.zip

В архиве: anime-vault.exe с библиотеками (_internal\), config.example.json, README.md, LICENSE. config.json, cache\ и logs\
программа создаёт рядом с собой (anime_vault\paths.py). gallery-dl встроен: окно запускает «anime-vault.exe --gallery-dl …».
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"
DIST = BUILD / "dist" / "anime-vault"


def main() -> int:
    shutil.rmtree(BUILD, ignore_errors=True)
    command = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed",
        "--name", "anime-vault",
        "--icon", str(ROOT / "assets" / "icon.ico"),
        "--add-data", f"{ROOT / 'assets' / 'icon.ico'};assets",
        "--collect-all", "customtkinter",
        "--collect-submodules", "gallery_dl",       # сайты gallery-dl подключаются по имени — сам PyInstaller их не найдёт
        "--hidden-import", "send2trash",
        "--paths", str(ROOT),
        "--distpath", str(BUILD / "dist"), "--workpath", str(BUILD / "work"), "--specpath", str(BUILD),
        str(ROOT / "packaging" / "entry.py"),
    ]
    subprocess.run(command, check=True, cwd=ROOT)
    for name in ("config.example.json", "README.md", "LICENSE"):
        shutil.copy(ROOT / name, DIST / name)
    archive = shutil.make_archive(str(BUILD / "anime-vault-windows"), "zip", DIST.parent, "anime-vault")
    print(f"готово: {DIST / 'anime-vault.exe'}\nархив:  {archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

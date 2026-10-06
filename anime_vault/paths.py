r"""Где лежит служебное anime-vault: config.json, cache\, logs\.

Из исходников — папка проекта (рядом с пакетом anime_vault). В собранном .exe (PyInstaller, sys.frozen) — папка,
где лежит сам anime-vault.exe: распаковал архив куда угодно — настройки и журналы рядом с программой.
"""
from __future__ import annotations

import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))
PROJECT = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parents[1]
SETTINGS = PROJECT / "config.json"
EXAMPLE = PROJECT / "config.example.json"
CACHE = PROJECT / "cache" / "hashes.json"
LOGS = PROJECT / "logs"

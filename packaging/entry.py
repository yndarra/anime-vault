"""Точка входа собранного anime-vault.exe (PyInstaller): та же, что python -m anime_vault (окно / команда / --gallery-dl)."""
from anime_vault.__main__ import main

raise SystemExit(main())

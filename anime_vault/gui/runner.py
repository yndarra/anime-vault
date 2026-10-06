r"""Запуск команд anime-vault (download / distribute) из окна: подпроцесс, его вывод и вопросы.

Команда идёт консольным python того же venv с env ANIME_VAULT_GUI=1: anime_vault\console.py тогда шлёт вопросы
служебной строкой «\x1eASK\t<вопрос>\t<варианты>» (или «\x1eWAIT\t<текст>»), а окно показывает их кнопками и
отвечает в stdin (Runner.answer). Вывод с ANSI-цветами режется на куски (текст, код цвета) — окно красит их
тегами. Чтение идёт в отдельном потоке, окно забирает события из очереди в своём потоке (Tkinter иначе нельзя).
"""
from __future__ import annotations

import os
import queue
import re
import subprocess
import sys
import threading
from pathlib import Path

from anime_vault import i18n
from anime_vault.i18n import _
from anime_vault.paths import FROZEN, PROJECT

GUI_MARK = "\x1e"
ANSI_RE = re.compile(r"\x1b\[([0-9;]*)m")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
WORDS = {"д": "Да", "н": "Нет"}


def python_exe() -> str:
    """Консольный python того же venv (окно само идёт под pythonw)."""
    exe = Path(sys.executable)
    console = exe.with_name("python.exe")
    return str(console if console.exists() else exe)


def split_ansi(line: str) -> list[tuple[str, str | None]]:
    """Строка с ANSI-цветами → [(кусок текста, код цвета или None)]."""
    parts, tag, position = [], None, 0
    for match in ANSI_RE.finditer(line):
        if match.start() > position:
            parts.append((line[position:match.start()], tag))
        codes = [code for code in match.group(1).split(";") if code]
        tag = None if not codes or codes[-1] == "0" else codes[-1]
        position = match.end()
    if position < len(line):
        parts.append((line[position:], tag))
    return parts


def option_labels(prompt: str, options: str) -> list[tuple[str, str]]:
    """Варианты ответа → [(буква, надпись)]: «д» → «Да» и т. п.; «р — повторить» берёт надпись из вопроса."""
    labels = []
    for letter in options:
        match = re.search(rf"(?:^|[\s,(]){re.escape(letter)}\s*[—-]\s*([^,;)\]]+)", prompt)
        text = match.group(1).strip() if match else _(WORDS[letter]) if letter in WORDS else letter.upper()
        labels.append((letter, text[:1].upper() + text[1:]))
    return labels


class Runner:
    """Одна команда за раз. events — очередь: ("line", [(текст, цвет)…]), ("ask", вопрос, [(буква, надпись)…]),
    ("wait", текст), ("exit", код)."""

    def __init__(self):
        self.process: subprocess.Popen | None = None
        self.events: queue.Queue = queue.Queue()
        self.title = ""

    @property
    def busy(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def start(self, title: str, command: str, root: Path) -> None:
        if self.busy:
            raise RuntimeError(_("уже идёт другая команда"))
        self.title = title
        env = dict(os.environ, ANIME_VAULT_GUI="1", ANIME_VAULT_LANG=i18n.LANG, PYTHONIOENCODING="utf-8",
                   PYTHONUNBUFFERED="1", PYTHONPATH=str(PROJECT))
        # Собранный .exe запускает сам себя с командой (anime_vault\__main__.py), исходники — python -m anime_vault.
        program = [sys.executable] if FROZEN else [python_exe(), "-m", "anime_vault"]
        self.process = subprocess.Popen([*program, command, "--root", str(root)],
                                        cwd=str(PROJECT), env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, creationflags=NO_WINDOW)
        threading.Thread(target=self.read, args=(self.process,), daemon=True).start()

    def read(self, process: subprocess.Popen) -> None:
        assert process.stdout
        for raw in iter(process.stdout.readline, b""):
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if line.startswith(GUI_MARK):
                kind, _, rest = line[1:].partition("\t")
                prompt, _, options = rest.partition("\t")
                if kind == "ASK" and options:
                    self.events.put(("ask", prompt, option_labels(prompt, options)))
                else:
                    self.events.put(("wait", prompt))
            else:
                self.events.put(("line", split_ansi(line)))
        self.events.put(("exit", process.wait()))

    def answer(self, text: str) -> None:
        if self.busy and self.process and self.process.stdin:
            try:
                self.process.stdin.write((text + "\n").encode("utf-8"))
                self.process.stdin.flush()
            except OSError:
                pass

    def stop(self) -> None:
        if self.busy and self.process:
            subprocess.run(["taskkill", "/PID", str(self.process.pid), "/T", "/F"], capture_output=True,
                           creationflags=NO_WINDOW)

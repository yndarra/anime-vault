r"""Консоль для пользователя: цветные строки, понятные ошибки и окно, которое не закрывается само.

Все команды anime-vault запускаются .bat-файлами двойным щелчком, поэтому:
    - любая ошибка выводится красным с подсказкой «что сделать» и окно ждёт Enter;
    - в конце работы окно тоже ждёт Enter — результат можно прочитать.
"""
from __future__ import annotations

import ctypes
import os
import sys
import time
import traceback

from anime_vault.i18n import _

RED, GREEN, YELLOW, CYAN, GREY, WHITE, RESET = "\x1b[91m", "\x1b[92m", "\x1b[93m", "\x1b[96m", "\x1b[90m", "\x1b[97m", "\x1b[0m"


class UserError(Exception):
    """Ошибка, которую может исправить пользователь: текст «что случилось» и подсказка «что сделать»."""

    def __init__(self, what: str, fix: str = ""):
        super().__init__(what)
        self.what = what
        self.fix = fix


def attach_std() -> None:
    """Собранный .exe — оконная программа: у неё sys.stdout/stdin = None, даже когда окно anime-vault запускает
    её командой с каналами (pipe). Тогда потоки открываются прямо по дескрипторам Windows (GetStdHandle)."""
    if sys.stdout is not None and sys.stdin is not None:
        return
    try:
        import msvcrt

        kernel32 = ctypes.windll.kernel32
        kernel32.GetStdHandle.restype = ctypes.c_void_p
        for name, number, mode in (("stdin", -10, "r"), ("stdout", -11, "w"), ("stderr", -12, "w")):
            if getattr(sys, name) is not None:
                continue
            handle = kernel32.GetStdHandle(number)
            if not handle or handle == ctypes.c_void_p(-1).value:
                continue
            descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY if mode == "r" else os.O_WRONLY)
            setattr(sys, name, open(descriptor, mode, encoding="utf-8", errors="replace", buffering=1, closefd=False))
    except (AttributeError, OSError, ImportError):
        pass


def enable_colors() -> None:
    """ANSI-цвета в обычной консоли Windows + вывод в UTF-8 (кириллица, ✓)."""
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except (AttributeError, OSError):
        pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def say(text: str = "", color: str = "") -> None:
    print(f"{color}{text}{RESET}" if color else text, flush=True)


def step(text: str) -> None:
    say(f"[{time.strftime('%H:%M:%S')}] {text}", WHITE)


def info(text: str) -> None:
    say(f"[{time.strftime('%H:%M:%S')}] {text}", GREY)


def ok(text: str) -> None:
    say(f"[{time.strftime('%H:%M:%S')}] {text}", GREEN)


def warn(text: str) -> None:
    say(f"[{time.strftime('%H:%M:%S')}] {text}", YELLOW)


def error(what: str, fix: str = "") -> None:
    say(f"[{time.strftime('%H:%M:%S')}] {_('ОШИБКА')}: {what}", RED)
    if fix:
        say(f"           {_('Что сделать')}: {fix}", YELLOW)


def title(text: str) -> None:
    say()
    say(text, CYAN)
    say("─" * len(text), CYAN)


# Под окном anime-vault (anime_vault\gui, env ANIME_VAULT_GUI=1) вопросы идут служебной строкой:
# «\x1eASK\t<вопрос>\t<варианты>» или «\x1eWAIT\t<текст>»; окно показывает их кнопками и пишет ответ в stdin.
GUI_MARK = "\x1e"
# Варианты ответа в коде — русскими буквами (д/н); в английском интерфейсе консоль показывает y/n
# и принимает их (а латинские y/n понимаются всегда — английская раскладка, ввод через pipe).
LATIN = {"д": "y", "н": "n"}


def gui() -> bool:
    return os.environ.get("ANIME_VAULT_GUI") == "1"


def wait(prompt: str = "", options: str = "") -> str:
    prompt = prompt or _("Нажмите Enter, чтобы продолжить…")
    try:
        if gui():
            print(f"{GUI_MARK}{f'ASK{chr(9)}{prompt}{chr(9)}{options}' if options else f'WAIT{chr(9)}{prompt}'}", flush=True)
            return input()
        return input(f"{YELLOW}{prompt}{RESET} ")
    except EOFError:
        return ""


def ask(prompt: str, options: str = "дн") -> str:
    """Вопрос с вариантами по первой букве (д/н, п/с/в …). Повторяет, пока не ответят правильно."""
    aliases = {latin: letter for letter, latin in LATIN.items()}
    from anime_vault.i18n import LANG

    shown = "/".join(LATIN.get(letter, letter) if LANG == "en" else letter for letter in options)
    while True:
        text = prompt if gui() else f"{prompt} [{shown}]:"
        answer = wait(text, options if gui() else "").strip().lstrip("﻿").casefold()[:1]
        answer = aliases.get(answer, answer)
        if answer in options:
            return answer
        warn(_("Ответьте одной буквой: {letters}").format(letters=shown.replace("/", ", ")))


def guarded(main) -> int:
    """Запуск команды: ошибки — понятным текстом, окно ждёт Enter в любом исходе."""
    enable_colors()
    code = 1
    try:
        code = main() or 0
    except UserError as exc:
        error(exc.what, exc.fix)
    except KeyboardInterrupt:
        warn(_("Остановлено (Ctrl+C). Всё сделанное сохранено — можно запустить снова."))
    except Exception:
        error(_("непредвиденный сбой программы (подробности ниже)"),
              _("пришлите текст ниже разработчику; запуск ещё раз безопасен — сделанное не теряется"))
        say(traceback.format_exc().rstrip(), RED)
    if not gui():   # в окне anime-vault ждать Enter не нужно — вывод и так остаётся на экране
        say()
        wait(_("Готово. Нажмите Enter, чтобы закрыть окно…"))
    return code

r"""Откуда gallery-dl берёт куки (вход на сайт для приватных досок) и открыт ли этот браузер.

config.json → pinterest.browser_cookies:
    "opera:<папка профиля>" / "chrome" / "firefox" … — куки из браузера (gallery-dl --cookies-from-browser);
    "C:/…/cookies.txt"                              — файл куки в формате Netscape (gallery-dl --cookies);
    ""                                              — без входа (только публичные доски).
Браузеры на Chromium держат базу куки закрытой, пока запущены, — их надо закрыть перед скачиванием;
Firefox отдаёт куки и открытым. Chrome / Edge новых версий шифруют куки так, что сторонние программы их не читают, —
для них надёжнее файл cookies.txt (расширение браузера «Get cookies.txt LOCALLY» и т. п.) или Firefox.
"""
from __future__ import annotations

from dataclasses import dataclass

from anime_vault.i18n import _

# Ключ gallery-dl → (название, процессы Windows).
BROWSERS = {
    "opera": ("Opera", ("opera.exe",)),
    "chrome": ("Chrome", ("chrome.exe",)),
    "edge": ("Edge", ("msedge.exe",)),
    "brave": ("Brave", ("brave.exe",)),
    "vivaldi": ("Vivaldi", ("vivaldi.exe",)),
    "chromium": ("Chromium", ("chrome.exe", "chromium.exe")),
    "firefox": ("Firefox", ("firefox.exe",)),
}
OPEN_IS_FINE = {"firefox"}


@dataclass
class Cookies:
    kind: str           # "browser" / "file" / "none"
    browser: str = ""   # ключ из BROWSERS
    profile: str = ""   # папка профиля (пусто — профиль по умолчанию)
    file: str = ""      # путь к cookies.txt

    @property
    def name(self) -> str:
        if self.kind != "browser":
            return ""
        title = BROWSERS.get(self.browser, (self.browser.title(), ()))[0]
        return "Opera GX" if self.browser == "opera" and "gx" in self.profile.casefold() else title

    @property
    def must_close(self) -> bool:
        return self.kind == "browser" and self.browser not in OPEN_IS_FINE

    def arguments(self) -> list[str]:
        """Аргументы gallery-dl для этих куки."""
        if self.kind == "file":
            return ["--cookies", self.file]
        if self.kind == "browser":
            return ["--cookies-from-browser", f"{self.browser}:{self.profile}" if self.profile else self.browser]
        return []

    def spec(self) -> str:
        """Обратно в строку для config.json."""
        if self.kind == "file":
            return self.file
        if self.kind == "browser":
            return f"{self.browser}:{self.profile}" if self.profile else self.browser
        return ""


def parse(text: str) -> Cookies:
    text = (text or "").strip()
    if not text:
        return Cookies("none")
    head, _sep, rest = text.partition(":")
    # «C:/…/cookies.txt» — тоже с двоеточием, поэтому сначала: ключ браузера до двоеточия?
    if head.casefold() in BROWSERS:
        return Cookies("browser", head.casefold(), rest.strip())
    return Cookies("file", file=text)


def running_processes() -> set[str]:
    """Имена запущенных процессов (в нижнем регистре) — списком процессов Windows (CreateToolhelp32Snapshot),
    без запуска tasklist: консольная программа, запущенная из окна anime-vault, мелькала бы консольным окном."""
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
        return set()
    names = set()
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        found = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while found:
            names.add(entry.szExeFile.casefold())
            found = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return names


def is_running(cookies: Cookies) -> bool:
    if cookies.kind != "browser":
        return False
    return bool(set(BROWSERS.get(cookies.browser, ("", (f"{cookies.browser}.exe",)))[1]) & running_processes())


def describe(cookies: Cookies) -> str:
    """Подпись для окна: откуда куки."""
    if cookies.kind == "file":
        return _("Куки из файла — браузер закрывать не нужно")
    if cookies.kind == "none":
        return _("Без входа — только публичные доски")
    return cookies.name

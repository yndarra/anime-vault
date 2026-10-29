r"""Фоновая синхронизация Waifu ⇄ Google Drive: значок в трее, запуск вместе с Windows, почти ноль нагрузки.

Как устроено (python -m anime_vault.agent; у .exe — anime-vault.exe --agent):
    * Папка на ПК — Windows сама будит процесс, когда в папке что-то меняется (FindFirstChangeNotification).
      Пока изменений нет, поток спит в WaitForMultipleObjects и не тратит процессор вообще. После изменения
      ждём, пока в папке 30 с тихо (anime-sort пишет пачками), и только тогда синхронизируем.
    * Драйв — раз в N минут (config.json → drive.interval, по умолчанию 5) один маленький запрос к журналу
      изменений Драйва (changes.list). Полная сверка — только если изменилось что-то внутри папки Waifu
      (по id её папок — cache\drive_folders.json). Раз в сутки и при запуске — полная сверка на всякий случай.
    * Сама синхронизация — отдельным процессом (python -m anime_vault sync --yes): тяжёлые библиотеки Google
      живут только пока она идёт, а фон весит ~15 МБ. Вывод — logs\drive_agent.log (последний запуск).
    * После синхронизации, если что-то изменилось, — уведомление Windows одним отчётом за всю пачку:
      «Добавлено ПК: N / Добавлено Drive: N / Удалено ПК: N / Удалено Drive: N» (только ненулевые строки).
    * Только стандартная библиотека Python (ctypes, urllib): ни Tk, ни Pillow, ни клиента Google API.

Меню значка: «Синхронизировать сейчас», «Открыть anime-vault», «Пауза», «Выход». Двойной щелчок — открыть окно.
Второй экземпляр не запускается (именованный мьютекс). Окно anime-vault выключает фон событием QUIT_EVENT.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as w
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from anime_vault import drive_sync, gdrive
from anime_vault.i18n import _
from anime_vault.paths import FROZEN, LOGS, PROJECT

MUTEX = "Local\\anime-vault-drive-agent"        # «фон уже запущен»
QUIT_EVENT = "Local\\anime-vault-drive-quit"    # окно anime-vault просит фон закрыться
QUIET = 30                                      # секунд тишины в папке перед синхронизацией
FULL_EVERY = 24 * 3600                          # полная сверка раз в сутки
FIRST_DELAY = 90                                # первая сверка — через 1,5 мин после входа в Windows
API = "https://www.googleapis.com/drive/v3"

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, w.HWND, w.UINT, w.WPARAM, w.LPARAM)
WAIT_TIMEOUT, INFINITE = 0x102, 0xFFFFFFFF
NOTIFY_FLAGS = 0x1 | 0x2 | 0x8 | 0x10           # имена файлов и папок, размер, время записи
WM_DESTROY, WM_COMMAND, WM_CLOSE, WM_USER = 0x0002, 0x0111, 0x0010, 0x0400
WM_TRAY, WM_RBUTTONUP, WM_LBUTTONDBLCLK = WM_USER + 1, 0x0205, 0x0203
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x1, 0x2, 0x4, 0x10
NIIF_INFO, NIIF_WARNING = 0x1, 0x2
MF_STRING, MF_SEPARATOR, MF_CHECKED = 0x0, 0x800, 0x8
TPM_RIGHTBUTTON, TPM_RETURNCMD = 0x2, 0x100
CMD_SYNC, CMD_OPEN, CMD_PAUSE, CMD_QUIT = 1, 2, 3, 4


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", w.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", w.HINSTANCE), ("hIcon", w.HICON), ("hCursor", w.HANDLE), ("hbrBackground", w.HBRUSH),
                ("lpszMenuName", w.LPCWSTR), ("lpszClassName", w.LPCWSTR)]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [("cbSize", w.DWORD), ("hWnd", w.HWND), ("uID", w.UINT), ("uFlags", w.UINT),
                ("uCallbackMessage", w.UINT), ("hIcon", w.HICON), ("szTip", w.WCHAR * 128), ("dwState", w.DWORD),
                ("dwStateMask", w.DWORD), ("szInfo", w.WCHAR * 256), ("uVersion", w.UINT), ("szInfoTitle", w.WCHAR * 64),
                ("dwInfoFlags", w.DWORD), ("guidItem", ctypes.c_byte * 16), ("hBalloonIcon", w.HICON)]


# Типы аргументов: без них 64-битные дескрипторы обрезаются до int.
user32.DefWindowProcW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.CreateWindowExW.argtypes = [w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, w.HWND, w.HMENU, w.HINSTANCE, w.LPVOID]
user32.CreateWindowExW.restype = w.HWND
user32.LoadImageW.argtypes = [w.HINSTANCE, w.LPCWSTR, w.UINT, ctypes.c_int, ctypes.c_int, w.UINT]
user32.LoadImageW.restype = w.HANDLE
user32.LoadIconW.argtypes = [w.HINSTANCE, w.LPVOID]
user32.LoadIconW.restype = w.HICON
user32.TrackPopupMenu.argtypes = [w.HMENU, w.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, w.HWND, w.LPVOID]
user32.AppendMenuW.argtypes = [w.HMENU, w.UINT, ctypes.c_size_t, w.LPCWSTR]
user32.CreatePopupMenu.restype = w.HMENU
user32.DestroyMenu.argtypes = [w.HMENU]
user32.PostMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
user32.SetForegroundWindow.argtypes = [w.HWND]
user32.DestroyWindow.argtypes = [w.HWND]
user32.GetMessageW.argtypes = [ctypes.POINTER(w.MSG), w.HWND, w.UINT, w.UINT]
shell32.Shell_NotifyIconW.argtypes = [w.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
kernel32.CreateEventW.argtypes = [w.LPVOID, w.BOOL, w.BOOL, w.LPCWSTR]
kernel32.CreateEventW.restype = w.HANDLE
kernel32.CreateMutexW.argtypes = [w.LPVOID, w.BOOL, w.LPCWSTR]
kernel32.CreateMutexW.restype = w.HANDLE
kernel32.OpenMutexW.argtypes = [w.DWORD, w.BOOL, w.LPCWSTR]
kernel32.OpenMutexW.restype = w.HANDLE
kernel32.OpenEventW.argtypes = [w.DWORD, w.BOOL, w.LPCWSTR]
kernel32.OpenEventW.restype = w.HANDLE
kernel32.SetEvent.argtypes = [w.HANDLE]
kernel32.ResetEvent.argtypes = [w.HANDLE]
kernel32.CloseHandle.argtypes = [w.HANDLE]
kernel32.FindFirstChangeNotificationW.argtypes = [w.LPCWSTR, w.BOOL, w.DWORD]
kernel32.FindFirstChangeNotificationW.restype = w.HANDLE
kernel32.FindNextChangeNotification.argtypes = [w.HANDLE]
kernel32.FindCloseChangeNotification.argtypes = [w.HANDLE]
kernel32.WaitForMultipleObjects.argtypes = [w.DWORD, ctypes.POINTER(w.HANDLE), w.BOOL, w.DWORD]
kernel32.WaitForMultipleObjects.restype = w.DWORD
INVALID = w.HANDLE(-1).value


# ---------------------------------------------------------------- для окна anime-vault

def running() -> bool:
    """Запущен ли фон (есть его мьютекс)."""
    handle = kernel32.OpenMutexW(0x00100000, False, MUTEX)       # SYNCHRONIZE
    if handle:
        kernel32.CloseHandle(handle)
    return bool(handle)


def venv_python(windowless: bool) -> str:
    """python(w).exe из venv проекта — для синхронизации и окна (им нужны библиотеки Google и CustomTkinter)."""
    name = "pythonw.exe" if windowless else "python.exe"
    venv = PROJECT / "venv" / "Scripts" / name
    return str(venv if venv.exists() else Path(sys.executable).with_name(name))


def command() -> list[str]:
    """Как запустить фон: сам .exe с --agent или «голый» pythonw без venv (фону хватает стандартной библиотеки,
    а pythonw из venv — это ещё один процесс-посредник, лишние ~7 МБ)."""
    if FROZEN:
        return [sys.executable, "--agent"]
    base = Path(getattr(sys, "_base_executable", sys.executable)).with_name("pythonw.exe")
    return [str(base) if base.exists() else venv_python(True), "-m", "anime_vault.agent"]


def start() -> None:
    if not running():
        subprocess.Popen(command(), cwd=PROJECT, creationflags=0x00000008 | 0x00000200,   # DETACHED | NEW_GROUP
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def stop() -> None:
    handle = kernel32.OpenEventW(0x0002, False, QUIT_EVENT)           # EVENT_MODIFY_STATE
    if handle:
        kernel32.SetEvent(handle)
        kernel32.CloseHandle(handle)


def startup_file() -> str:
    """Файл в «Автозагрузке» Windows (маленький .vbs: запускает фон без окна консоли)."""
    return os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs", "Startup",
                        "anime-vault-drive.vbs")


def set_autostart(enabled: bool) -> None:
    path = startup_file()
    if enabled:
        line = " ".join(f'""{part}""' for part in command())
        text = ("' anime-vault: background Google Drive sync (tray icon). Created by the anime-vault window.\r\n"
                'Set shell = CreateObject("WScript.Shell")\r\n'
                f'shell.CurrentDirectory = "{PROJECT}"\r\n'
                f'shell.Run "{line}", 0, False\r\n')
        with open(path, "w", encoding="ascii", errors="replace", newline="") as handle:
            handle.write(text)
    elif os.path.exists(path):
        os.remove(path)


def report_text(last: dict) -> str:
    """Текст уведомления за всю пачку: только строки, где число не 0 («» — ничего не изменилось)."""
    lines = [(_("Добавлено ПК"), last.get("added_pc")), (_("Добавлено Drive"), last.get("added_drive")),
             (_("Удалено ПК"), last.get("deleted_pc")), (_("Удалено Drive"), last.get("deleted_drive")),
             (_("Перемещено"), last.get("moved")), (_("Ошибок"), last.get("errors"))]
    return "\n".join(f"{title}: {count}" for title, count in lines if count)


# ---------------------------------------------------------------- Драйв без клиента Google API

class Drive:
    """Журнал изменений Драйва обычными HTTPS-запросами (urllib). Ключ берётся из google-token.json."""

    def __init__(self):
        self.access = ""
        self.expires = 0.0
        self.page = ""

    def token(self) -> str:
        if self.access and time.time() < self.expires - 60:
            return self.access
        data = json.loads(gdrive.TOKEN.read_text(encoding="utf-8"))
        body = urllib.parse.urlencode({"client_id": data["client_id"], "client_secret": data["client_secret"],
                                       "refresh_token": data["refresh_token"], "grant_type": "refresh_token"}).encode()
        with urllib.request.urlopen(data.get("token_uri") or "https://oauth2.googleapis.com/token", body, timeout=30) as response:
            answer = json.loads(response.read())
        self.access, self.expires = answer["access_token"], time.time() + int(answer.get("expires_in", 3600))
        return self.access

    def get(self, path: str, **params) -> dict:
        request = urllib.request.Request(f"{API}/{path}?{urllib.parse.urlencode(params)}",
                                         headers={"Authorization": f"Bearer {self.token()}"})
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())

    def reset(self) -> None:
        """Запомнить «сейчас» — всё, что было до этого (в том числе наша же синхронизация), уже учтено."""
        self.page = self.get("changes/startPageToken")["startPageToken"]

    def changed(self, folders: set[str]) -> bool:
        """Было ли с прошлого раза что-то внутри папки Waifu (новый, удалённый, перенесённый файл или папка)."""
        if not self.page:
            self.reset()
            return False
        found, page = False, self.page
        while page:
            try:
                answer = self.get("changes", pageToken=page, pageSize=1000, spaces="drive", includeRemoved="true",
                                  fields="nextPageToken,newStartPageToken,changes(fileId,file(parents))")
            except urllib.error.HTTPError as exc:
                if exc.code in (400, 404, 410):              # журнал устарел — проще сверить целиком
                    self.page = ""
                    return True
                raise
            for change in answer.get("changes", []):
                parents = (change.get("file") or {}).get("parents") or []
                if change.get("fileId") in folders or folders.intersection(parents):
                    found = True
            page = answer.get("nextPageToken")
            if answer.get("newStartPageToken"):
                self.page = answer["newStartPageToken"]
        return found


# ---------------------------------------------------------------- фон

class Agent:
    def __init__(self):
        self.quit = kernel32.CreateEventW(None, True, False, QUIT_EVENT)     # «выход» (меню или окно anime-vault)
        kernel32.ResetEvent(self.quit)
        self.wake = kernel32.CreateEventW(None, False, False, None)          # «синхронизировать сейчас»
        self.paused = False
        self.pending = False          # во время паузы что-то изменилось — сверить после неё
        self.failed = ""              # текст последней ошибки — чтобы не повторять одно уведомление
        self.blocked = 0              # о скольких удалениях уже предупредили
        self.last_full = 0.0
        self.drive = Drive()
        self.hwnd = None
        self.nid = None
        self.icon = None
        self.taskbar = user32.RegisterWindowMessageW("TaskbarCreated")      # проводник перезапустился — вернуть значок
        self.proc = WNDPROC(self.window_proc)                               # ссылка, иначе сборщик мусора съест

    # ---- значок в трее
    def build_window(self) -> None:
        instance = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSW(lpfnWndProc=self.proc, hInstance=instance, lpszClassName="AnimeVaultDriveAgent")
        user32.RegisterClassW(ctypes.byref(wc))
        self.hwnd = user32.CreateWindowExW(0, "AnimeVaultDriveAgent", "anime-vault drive", 0, 0, 0, 0, 0, None, None, instance, None)
        icon = PROJECT / "assets" / "icon.ico"
        if FROZEN:
            icon = Path(getattr(sys, "_MEIPASS", PROJECT)) / "assets" / "icon.ico"
        size = user32.GetSystemMetrics(49)                                  # SM_CXSMICON
        self.icon = user32.LoadImageW(None, str(icon), 1, size, size, 0x10) or user32.LoadIconW(None, ctypes.c_void_p(32512))
        self.add_icon()

    def add_icon(self) -> None:
        self.nid = NOTIFYICONDATAW(cbSize=ctypes.sizeof(NOTIFYICONDATAW), hWnd=self.hwnd, uID=1,
                                   uFlags=NIF_MESSAGE | NIF_ICON | NIF_TIP, uCallbackMessage=WM_TRAY, hIcon=self.icon)
        self.nid.szTip = self.tip()
        shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(self.nid))

    def tip(self) -> str:
        try:
            last = json.loads(drive_sync.LAST.read_text(encoding="utf-8"))
            when = _("последняя синхронизация {time}").format(time=last.get("time", "—"))
        except (OSError, ValueError):
            when = _("синхронизаций ещё не было")
        state = _("пауза") if self.paused else when
        return f"anime-vault: Waifu ⇄ Drive — {state}"[:127]

    def update_tip(self) -> None:
        if self.nid:
            self.nid.uFlags = NIF_TIP
            self.nid.szTip = self.tip()
            shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(self.nid))

    def notify(self, title: str, text: str, warning: bool = False) -> None:
        if self.nid:
            self.nid.uFlags = NIF_INFO
            self.nid.szInfoTitle = title[:63]
            self.nid.szInfo = text[:255]
            self.nid.dwInfoFlags = NIIF_WARNING if warning else NIIF_INFO
            shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(self.nid))

    def window_proc(self, hwnd, message, wparam, lparam):
        if message == WM_TRAY:
            if lparam == WM_RBUTTONUP:
                self.menu()
            elif lparam == WM_LBUTTONDBLCLK:
                self.open_window()
            return 0
        if message == self.taskbar and self.taskbar:
            self.add_icon()
            return 0
        if message == WM_CLOSE:
            user32.DestroyWindow(hwnd)
            return 0
        if message == WM_DESTROY:
            if self.nid:
                shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self.nid))
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, message, wparam, lparam)

    def menu(self) -> None:
        menu = user32.CreatePopupMenu()
        user32.AppendMenuW(menu, MF_STRING, CMD_SYNC, _("Синхронизировать сейчас"))
        user32.AppendMenuW(menu, MF_STRING, CMD_OPEN, _("Открыть anime-vault"))
        user32.AppendMenuW(menu, MF_STRING | (MF_CHECKED if self.paused else 0), CMD_PAUSE, _("Пауза"))
        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(menu, MF_STRING, CMD_QUIT, _("Выход (до следующего входа в Windows)"))
        point = w.POINT()
        user32.GetCursorPos(ctypes.byref(point))
        user32.SetForegroundWindow(self.hwnd)                    # иначе меню не закрывается щелчком мимо
        chosen = user32.TrackPopupMenu(menu, TPM_RIGHTBUTTON | TPM_RETURNCMD, point.x, point.y, 0, self.hwnd, None)
        user32.PostMessageW(self.hwnd, 0, 0, 0)
        user32.DestroyMenu(menu)
        if chosen == CMD_SYNC:
            kernel32.SetEvent(self.wake)
        elif chosen == CMD_OPEN:
            self.open_window()
        elif chosen == CMD_PAUSE:
            self.paused = not self.paused
            if not self.paused and self.pending:
                kernel32.SetEvent(self.wake)
            self.update_tip()
        elif chosen == CMD_QUIT:
            kernel32.SetEvent(self.quit)

    def open_window(self) -> None:
        program = [sys.executable] if FROZEN else [venv_python(True), "-m", "anime_vault"]
        subprocess.Popen([*program, "--page", "drive"], cwd=PROJECT)

    # ---- наблюдение
    def worker(self) -> None:
        root = drive_sync.local_root(self.collection())
        watch = self.open_watch(root)
        next_poll = time.monotonic() + FIRST_DELAY
        while True:
            handles = [self.quit, self.wake] + ([watch] if watch else [])
            array = (w.HANDLE * len(handles))(*handles)
            timeout = int(max(0.0, next_poll - time.monotonic()) * 1000)
            result = kernel32.WaitForMultipleObjects(len(handles), array, False, timeout)   # сон без процессора
            if result == 0:
                break
            if result == 0xFFFFFFFF:                                    # WAIT_FAILED — не крутиться вхолостую
                time.sleep(30)
                watch = self.open_watch(drive_sync.local_root(self.collection()))
                continue
            reason = ""
            if result == 1:
                reason = "manual"
            elif result == 2:
                if not self.settle(watch):
                    break
                reason = "local"
            elif result == WAIT_TIMEOUT:
                next_poll = time.monotonic() + drive_sync.settings()["interval"] * 60
                if time.time() - self.last_full > FULL_EVERY:
                    reason = "full"
                else:
                    try:
                        reason = "drive" if self.drive.changed(self.folder_ids()) else ""
                    except (OSError, ValueError, KeyError):
                        reason = ""                                  # нет сети / нет входа — попробуем позже
            if not reason:
                continue
            if self.paused and reason != "manual":
                self.pending = True
                continue
            if not self.sync():
                next_poll = min(next_poll, time.monotonic() + 120)    # занято другой синхронизацией — позже
                continue
            if watch:                                              # свои же изменения во время синхронизации — забыть
                kernel32.FindCloseChangeNotification(watch)
            root = drive_sync.local_root(self.collection())
            watch = self.open_watch(root)
        if watch:
            kernel32.FindCloseChangeNotification(watch)
        user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)

    def settle(self, watch) -> bool:
        """Дождаться, пока в папке QUIET секунд ничего не меняется. False — пора выходить."""
        while True:
            kernel32.FindNextChangeNotification(watch)
            array = (w.HANDLE * 2)(self.quit, watch)
            result = kernel32.WaitForMultipleObjects(2, array, False, QUIET * 1000)
            if result == 0:
                return False
            if result == WAIT_TIMEOUT:
                return True

    @staticmethod
    def open_watch(root):
        if not root.is_dir():
            return None
        handle = kernel32.FindFirstChangeNotificationW(str(root), True, NOTIFY_FLAGS)
        return handle if handle and handle != INVALID else None

    @staticmethod
    def collection():
        from anime_vault.gui import collection

        return collection.find_root() or PROJECT

    @staticmethod
    def folder_ids() -> set[str]:
        try:
            return set(json.loads(drive_sync.FOLDERS.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return set()

    # ---- синхронизация отдельным процессом
    def sync(self) -> bool:
        lock = drive_sync.try_lock()
        if lock is None:
            return False                                         # уже идёт (окно anime-vault)
        lock.close()
        self.pending = False
        started = time.time()
        program = [sys.executable] if FROZEN else [venv_python(False), "-m", "anime_vault"]
        LOGS.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONPATH=str(PROJECT))
        with open(LOGS / "drive_agent.log", "w", encoding="utf-8") as log:
            code = subprocess.call([*program, "sync", "--root", str(self.collection()), "--yes"], cwd=PROJECT, env=env,
                                   stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.report(code, started)
        try:
            self.drive.reset()
        except (OSError, ValueError, KeyError):
            self.drive.page = ""
        self.update_tip()
        return True

    def report(self, code: int, started: float) -> None:
        try:
            last = json.loads(drive_sync.LAST.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            last = {}
        fresh = last.get("run", 0) >= started
        if fresh and last.get("blocked"):
            if last["blocked"] != self.blocked:
                self.blocked = last["blocked"]
                self.notify(_("Waifu ⇄ Drive: синхронизация остановлена"),
                            _("Удалений слишком много: {count}. Открой anime-vault → «Драйв» и проверь план.").format(count=last["blocked"]),
                            warning=True)
            return
        if fresh:
            self.blocked = 0
            self.failed = ""
            self.last_full = time.time()
            text = report_text(last)
            if text:
                self.notify(_("Waifu ⇄ Drive"), text, warning=bool(last.get("errors")))
            return
        # Синхронизация не дошла до конца (нет сети, нет входа, пропала папка) — сказать один раз.
        error = self.error_line() or _("код {code}").format(code=code)
        if error != self.failed:
            self.failed = error
            self.notify(_("Waifu ⇄ Drive: не получилось"), error, warning=True)

    @staticmethod
    def error_line() -> str:
        try:
            text = (LOGS / "drive_agent.log").read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        import re

        text = re.sub(r"\x1b\[[0-9;]*m", "", text)
        lines = [line.split("] ", 1)[-1].strip() for line in text.splitlines() if _("ОШИБКА") in line]
        return lines[-1] if lines else ""

    # ---- запуск
    def run(self) -> int:
        self.build_window()
        threading.Thread(target=self.worker, daemon=True).start()
        message = w.MSG()
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:      # ждёт сообщений — процессор не тратит
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))
        return 0


def main() -> int:
    mutex = kernel32.CreateMutexW(None, False, MUTEX)
    if ctypes.get_last_error() == 183:                       # ERROR_ALREADY_EXISTS — фон уже работает
        return 0
    try:
        return Agent().run()
    finally:
        kernel32.CloseHandle(mutex)


if __name__ == "__main__":
    raise SystemExit(main())

r"""Окно anime-vault (CustomTkinter) — вместо .bat-файлов: скачать новое, разложить его по dataN, дубли, настройки.

Страницы:
    Обзор       сколько ждёт раскладки, папки dataN, файлы на досках, дубли на просмотр, последняя раскладка
    Скачать     что сделать перед началом (вход на сайт, закрыть браузер — живой индикатор), доски, запуск
    Разложить   источники и прикидка, сколько выйдет папок dataN, запуск (скрыта, если раскладка выключена)
    Дубли       что раскладка убрала в duplicates: миниатюра дубля рядом с оригиналом, в Корзину по одному или все
    Настройки   config.json: язык, папка коллекции, раскладка, вход (браузер / cookies.txt), доски — ссылками
Внизу — журнал команды (цвета как в консоли) и её вопросы кнопками. Команды — те же, что у .bat-файлов
(python -m anime_vault download|distribute), окно только запускает их и отвечает на вопросы (gui\runner.py).

    python -m anime_vault.gui            (или anime-vault.vbs в папке проекта / ярлык anime-vault в коллекции / anime-vault.exe)
    python -m anime_vault.gui --page settings
"""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from anime_vault import browsers, i18n, manifest
from anime_vault.gui import collection
from anime_vault.gui.runner import Runner, python_exe
from anime_vault.i18n import _
from anime_vault.paths import FROZEN, PROJECT

# Палитра — как у Пульта anime-sort (тёмная, фиолетовый акцент).
BG, SIDE, PANEL, PANEL2, FIELD, LINE = "#0e0f13", "#0b0c10", "#16181f", "#1c1f28", "#12141a", "#262a36"
TEXT, MUTED, FAINT = "#e6e8ef", "#8b90a0", "#5d6272"
ACCENT, ACCENT_HOVER, TEAL, GREEN, YELLOW, RED = "#8b7cff", "#7a6af0", "#5ad1c4", "#3ecf8e", "#f2c94c", "#ff6b7a"
ANSI = {"31": RED, "91": RED, "32": GREEN, "92": GREEN, "33": YELLOW, "93": YELLOW, "36": TEAL, "96": TEAL,
        "90": FAINT, "97": "#ffffff", "37": TEXT}
PAGES = [("overview", "Обзор", "▦"), ("download", "Скачать", "⬇"), ("distribute", "Разложить", "⇢"),
         ("duplicates", "Дубли", "⧉"), ("settings", "Настройки", "⚙")]
THUMB = 150          # сторона миниатюры на странице «Дубли»
PAGE_SIZE = 30       # столько дублей показывается за раз («Показать ещё»)
# Варианты входа на странице «Настройки»: ключ браузера gallery-dl, "file" — cookies.txt, "none" — без входа.
COOKIE_CHOICES = [*browsers.BROWSERS, "file", "none"]


def font(size: int = 13, weight: str = "normal") -> ctk.CTkFont:
    return ctk.CTkFont(family="Segoe UI", size=size, weight=weight)


def number(value: int) -> str:
    return f"{value:,}".replace(",", " ")


def resource(name: str) -> Path:
    """Файл из assets\\ — и из исходников, и из собранного .exe (PyInstaller распаковывает их в sys._MEIPASS)."""
    return Path(getattr(sys, "_MEIPASS", PROJECT)) / "assets" / name


def cookie_label(key: str) -> str:
    if key == "file":
        return _("Файл cookies.txt")
    if key == "none":
        return _("Без входа (публичные доски)")
    return browsers.BROWSERS[key][0]


class Card(ctk.CTkFrame):
    def __init__(self, parent, title: str = "", **options):
        super().__init__(parent, fg_color=PANEL, corner_radius=14, border_width=1, border_color=LINE, **options)
        if title:
            # Заголовки карточек — капсом, но имя папок dataN остаётся как есть.
            ctk.CTkLabel(self, text=title.upper().replace("DATAN", "dataN"), font=font(11, "bold"), text_color=MUTED).pack(anchor="w", padx=18, pady=(14, 4))


class Stat(Card):
    """Карточка-показатель: заголовок, крупное число, подпись."""

    def __init__(self, parent, title: str, color: str):
        super().__init__(parent, title)
        self.value = ctk.CTkLabel(self, text="—", font=font(28, "bold"), text_color=TEXT)
        self.value.pack(anchor="w", padx=18)
        self.hint = ctk.CTkLabel(self, text="", font=font(12), text_color=MUTED, wraplength=230, justify="left")
        self.hint.pack(anchor="w", padx=18, pady=(0, 14))
        ctk.CTkFrame(self, height=3, fg_color=color, corner_radius=2).pack(fill="x", padx=18, pady=(0, 14))

    def set(self, value: str, hint: str) -> None:
        self.value.configure(text=value)
        self.hint.configure(text=hint)


def button(parent, text: str, command, kind: str = "normal", **options) -> ctk.CTkButton:
    colors = {"primary": (ACCENT, ACCENT_HOVER, "#ffffff"), "danger": ("#3a1a20", "#4a2028", "#ffb3bc"),
              "normal": (PANEL2, "#262a36", TEXT), "ghost": ("transparent", PANEL2, MUTED)}[kind]
    settings = dict(fg_color=colors[0], hover_color=colors[1], text_color=colors[2], corner_radius=10, height=36, font=font(13),
                    border_width=0 if kind != "normal" else 1, border_color=LINE)
    settings.update(options)   # переданное (height, width …) важнее значений по умолчанию
    return ctk.CTkButton(parent, text=text, command=command, **settings)


class App(ctk.CTk):
    def __init__(self, page: str = "overview"):
        super().__init__(fg_color=BG)
        ctk.set_appearance_mode("dark")
        # env ANIME_VAULT_TEST=1 — проверочный экземпляр (снимки экрана): «(проверка)» в заголовке.
        self.title(f"anime-vault ({_('проверка')})" if os.environ.get("ANIME_VAULT_TEST") else "anime-vault")
        try:
            self.iconbitmap(str(resource("icon.ico")))
        except tk.TclError:
            pass
        self.geometry("1320x860")
        self.minsize(1100, 720)
        self.runner = Runner()
        self.results: queue.Queue = queue.Queue()
        self.snapshot: collection.Snapshot | None = None
        self.current = ""
        self.dup_items: list[collection.Duplicate] = []
        self.dup_shown = 0
        self.thumbs: list[ctk.CTkImage] = []      # ссылки на картинки — иначе их съест сборщик мусора
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.build_side()
        self.build_main()
        self.show(page)
        self.refresh()
        self.after(100, self.poll)
        self.after(500, self.poll_browser)

    # ================================================================ каркас

    def build_side(self) -> None:
        side = ctk.CTkFrame(self, fg_color=SIDE, corner_radius=0, width=220)
        side.grid(row=0, column=0, sticky="nsw")
        side.grid_propagate(False)
        brand = ctk.CTkFrame(side, fg_color="transparent")
        brand.pack(fill="x", padx=16, pady=(20, 18))
        ctk.CTkLabel(brand, text="AV", width=36, height=36, corner_radius=10, fg_color=TEAL, text_color=BG,
                     font=font(14, "bold")).pack(side="left")
        names = ctk.CTkFrame(brand, fg_color="transparent")
        names.pack(side="left", padx=10)
        ctk.CTkLabel(names, text="anime-vault", font=font(15, "bold"), text_color=TEXT).pack(anchor="w")
        ctk.CTkLabel(names, text=_("Доски → пачки"), font=font(11), text_color=MUTED).pack(anchor="w")
        self.nav = {}
        for key, title, icon in PAGES:
            nav = ctk.CTkButton(side, text=f"  {icon}   {_(title)}", anchor="w", height=38, corner_radius=10, font=font(13),
                                fg_color="transparent", hover_color="#14161d", text_color=MUTED,
                                command=lambda key=key: self.show(key))
            nav.pack(fill="x", padx=12, pady=2)
            self.nav[key] = nav
        foot = ctk.CTkFrame(side, fg_color=PANEL, corner_radius=12, border_width=1, border_color=LINE)
        foot.pack(side="bottom", fill="x", padx=12, pady=14)
        self.foot_state = ctk.CTkLabel(foot, text="● " + _("Команда не идёт"), font=font(12), text_color=MUTED, anchor="w")
        self.foot_state.pack(fill="x", padx=12, pady=(10, 2))
        self.foot_root = ctk.CTkLabel(foot, text="", font=font(11), text_color=FAINT, anchor="w", wraplength=180, justify="left")
        self.foot_root.pack(fill="x", padx=12, pady=(0, 10))

    def build_main(self) -> None:
        main = ctk.CTkFrame(self, fg_color=BG, corner_radius=0)
        main.grid(row=0, column=1, sticky="nsew")
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(1, weight=1)
        header = ctk.CTkFrame(main, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=26, pady=(20, 10))
        self.page_title = ctk.CTkLabel(header, text="", font=font(20, "bold"), text_color=TEXT)
        self.page_title.pack(anchor="w")
        self.page_sub = ctk.CTkLabel(header, text="", font=font(12), text_color=MUTED)
        self.page_sub.pack(anchor="w")
        self.body = ctk.CTkFrame(main, fg_color="transparent")
        self.body.grid(row=1, column=0, sticky="nsew", padx=26)
        self.body.grid_columnconfigure(0, weight=1)
        self.body.grid_rowconfigure(0, weight=1)
        self.pages = {"overview": self.page_overview(), "download": self.page_download(),
                      "distribute": self.page_distribute(), "duplicates": self.page_duplicates(),
                      "settings": self.page_settings()}
        self.build_log(main)

    def build_log(self, main) -> None:
        log = Card(main)
        log.grid(row=2, column=0, sticky="ew", padx=26, pady=(12, 20))
        head = ctk.CTkFrame(log, fg_color="transparent")
        head.pack(fill="x", padx=16, pady=(10, 4))
        self.log_title = ctk.CTkLabel(head, text=_("Журнал").upper(), font=font(11, "bold"), text_color=MUTED)
        self.log_title.pack(side="left")
        self.stop_button = button(head, "■ " + _("Остановить"), self.runner_stop, "danger", width=120, height=28)
        button(head, _("Очистить"), self.clear_log, "ghost", width=90, height=28).pack(side="right")
        self.text = ctk.CTkTextbox(log, height=190, fg_color=FIELD, text_color="#c9ccd6", corner_radius=10,
                                   font=ctk.CTkFont(family="Cascadia Code", size=12), wrap="word", border_width=0)
        self.text.pack(fill="x", padx=14, pady=(0, 14))
        for code, color in ANSI.items():
            self.text.tag_config(f"c{code}", foreground=color)
        self.text.configure(state="disabled")
        # Строка вопроса команды — появляется, только когда команда спрашивает (пустая рамка CTk иначе 200 px).
        self.question = ctk.CTkFrame(log, fg_color="transparent", height=1)
        self.say(_("Здесь будет вывод команд. Вопросы команд появятся кнопками под журналом."), "90")

    def show(self, key: str) -> None:
        key = key if key in self.pages else "overview"
        for name, nav in self.nav.items():
            active = name == key
            nav.configure(fg_color=PANEL2 if active else "transparent", text_color=TEXT if active else MUTED)
        for frame in self.pages.values():
            frame.grid_forget()
        self.pages[key].grid(row=0, column=0, sticky="nsew")
        titles = {"overview": (_("Обзор"), _("Что скачано, что ждёт раскладки, папки dataN")),
                  "download": (_("Скачать новое"), _("Доски → папки досок (gallery-dl, вход через куки браузера)")),
                  "distribute": (_("Разложить по dataN"), _("Новое из источников → папки dataN по размеру пачки, дубли — в duplicates")),
                  "duplicates": (_("Дубли"), _("Что раскладка не стала класть в dataN: такой же файл уже есть в коллекции")),
                  "settings": (_("Настройки"), _("config.json рядом с программой — личное, не в git"))}
        self.page_title.configure(text=titles[key][0])
        self.page_sub.configure(text=titles[key][1])
        self.current = key
        if key == "duplicates":
            self.load_duplicates()

    # ================================================================ страницы

    def page_overview(self) -> ctk.CTkFrame:
        page = ctk.CTkFrame(self.body, fg_color="transparent")
        page.grid_columnconfigure((0, 1, 2, 3), weight=1, uniform="stat")
        self.stat_waiting = Stat(page, _("Ждёт раскладки"), ACCENT)
        self.stat_batches = Stat(page, _("Папок dataN"), TEAL)
        self.stat_boards = Stat(page, _("На досках"), YELLOW)
        self.stat_dups = Stat(page, _("Дубли на просмотр"), RED)
        self.stats = (self.stat_waiting, self.stat_batches, self.stat_boards, self.stat_dups)
        for column, stat in enumerate(self.stats):
            stat.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else 8, 0 if column == 3 else 8))
        actions = Card(page, _("Что дальше"))
        actions.grid(row=1, column=0, columnspan=4, sticky="ew", pady=16)
        row = ctk.CTkFrame(actions, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=(4, 16))
        button(row, "⬇  " + _("Скачать новое"), lambda: self.show("download"), "primary", width=180).pack(side="left")
        self.overview_distribute = button(row, "⇢  " + _("Разложить по dataN"), lambda: self.show("distribute"), width=200)
        self.overview_distribute.pack(side="left", padx=8)
        button(row, _("Открыть коллекцию"), self.open_root, "ghost", width=160).pack(side="left")
        button(row, "↻ " + _("Обновить"), self.refresh, "ghost", width=110).pack(side="right")
        self.journal_label = ctk.CTkLabel(actions, text="", font=font(12), text_color=MUTED)
        self.journal_label.pack(anchor="w", padx=18, pady=(0, 14))
        return page

    def page_download(self) -> ctk.CTkFrame:
        page = ctk.CTkFrame(self.body, fg_color="transparent")
        page.grid_columnconfigure((0, 1), weight=1, uniform="half")
        steps = Card(page, _("Перед началом"))
        steps.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self.steps_box = ctk.CTkFrame(steps, fg_color="transparent")
        self.steps_box.pack(fill="x")
        self.browser_label = ctk.CTkLabel(steps, text=_("Проверяю браузер…"), font=font(13, "bold"), text_color=MUTED,
                                          corner_radius=8, fg_color=PANEL2, height=32)
        self.browser_label.pack(anchor="w", padx=18, pady=(10, 6))
        self.download_button = button(steps, "⬇  " + _("Начать скачивание"), self.start_download, "primary", width=220, height=40)
        self.download_button.pack(anchor="w", padx=18, pady=(4, 18))
        self.cookies_key = None
        boards = Card(page, _("Доски"))
        boards.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        self.boards_box = ctk.CTkFrame(boards, fg_color="transparent")
        self.boards_box.pack(fill="both", expand=True, padx=18, pady=(4, 16))
        return page

    def fill_steps(self, cookies: browsers.Cookies) -> None:
        """Шаги «Перед началом» — под выбранный способ входа (браузер, который надо закрыть, или файл куки)."""
        key = cookies.spec()
        if key == self.cookies_key:
            return
        self.cookies_key = key
        for child in self.steps_box.winfo_children():
            child.destroy()
        if cookies.must_close:
            texts = (_("Открой {browser} и проверь, что вошёл на сайт — в аккаунт с этими досками.").format(browser=cookies.name),
                     _("Полностью закрой {browser}, вместе со значком в трее: у открытого браузера куки не прочитать.").format(browser=cookies.name),
                     _("Нажми «Начать скачивание». Когда начнётся первая доска, браузер можно снова открыть."))
        elif cookies.kind == "browser":
            texts = (_("Проверь, что в {browser} выполнен вход на сайт — в аккаунт с этими досками.").format(browser=cookies.name),
                     _("Нажми «Начать скачивание» — {browser} можно не закрывать.").format(browser=cookies.name))
        elif cookies.kind == "file":
            texts = (_("Куки берутся из файла cookies.txt — проверь, что он свежий (сохранён после входа на сайт)."),
                     _("Нажми «Начать скачивание»."))
        else:
            texts = (_("Вход не настроен — скачаются только публичные доски (приватные — в «Настройках» выбери браузер)."),
                     _("Нажми «Начать скачивание»."))
        for index, text in enumerate(texts, 1):
            line = ctk.CTkFrame(self.steps_box, fg_color="transparent")
            line.pack(fill="x", padx=18, pady=5)
            ctk.CTkLabel(line, text=str(index), width=28, height=28, corner_radius=8, fg_color="#24213a", text_color=ACCENT,
                         font=font(13, "bold")).pack(side="left", anchor="n")
            ctk.CTkLabel(line, text=text, font=font(13), text_color=TEXT, wraplength=430, justify="left").pack(side="left", padx=10)

    def page_distribute(self) -> ctk.CTkFrame:
        page = ctk.CTkFrame(self.body, fg_color="transparent")
        page.grid_columnconfigure((0, 1), weight=1, uniform="half")
        sources = Card(page, _("Источники — ждут раскладки"))
        sources.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self.sources_box = ctk.CTkFrame(sources, fg_color="transparent")
        self.sources_box.pack(fill="both", expand=True, padx=18, pady=(4, 16))
        plan = Card(page, _("План"))
        plan.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        self.plan_label = ctk.CTkLabel(plan, text="", font=font(14), text_color=TEXT, wraplength=470, justify="left")
        self.plan_label.pack(anchor="w", padx=18, pady=(6, 6))
        ctk.CTkLabel(plan, text=_("Точный план покажет сама команда: она отсеет дубли (тот же пин или та же картинка уже есть в dataN / test-dataN) и спросит «Перенести?». Файлы ПЕРЕНОСЯТСЯ, дубли — в duplicates."),
                     font=font(12), text_color=MUTED, wraplength=470, justify="left").pack(anchor="w", padx=18, pady=(0, 10))
        self.distribute_button = button(plan, "⇢  " + _("Разложить"), self.start_distribute, "primary", width=200, height=40)
        self.distribute_button.pack(anchor="w", padx=18, pady=(4, 18))
        return page

    def page_duplicates(self) -> ctk.CTkFrame:
        page = ctk.CTkFrame(self.body, fg_color="transparent")
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(1, weight=1)
        bar = ctk.CTkFrame(page, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        self.dup_label = ctk.CTkLabel(bar, text="", font=font(13), text_color=MUTED)
        self.dup_label.pack(side="left")
        button(bar, "↻ " + _("Обновить"), self.load_duplicates, "ghost", width=110).pack(side="right")
        button(bar, _("Открыть папку"), self.open_duplicates, "ghost", width=130).pack(side="right", padx=6)
        self.dup_all = button(bar, _("Всё в Корзину"), self.trash_all, "danger", width=150)
        self.dup_all.pack(side="right", padx=6)
        self.dup_list = ctk.CTkScrollableFrame(page, fg_color="transparent")
        self.dup_list.grid(row=1, column=0, sticky="nsew")
        self.dup_list.grid_columnconfigure(0, weight=1)
        return page

    def page_settings(self) -> ctk.CTkFrame:
        page = ctk.CTkScrollableFrame(self.body, fg_color="transparent")
        data = collection.read_settings()
        self.settings_data = data
        pinterest = data.setdefault("pinterest", {})
        card = Card(page, _("Общее"))
        card.pack(fill="x", pady=(0, 14))
        self.var_language = tk.StringVar(value=i18n.LANGUAGES[i18n.LANG])
        self.choice(card, _("Язык / Language"), self.var_language, list(i18n.LANGUAGES.values()), _("Окно перезапустится на выбранном языке"))
        self.var_root = tk.StringVar(value=data.get("collection", "") if collection.SETTINGS.exists() else str(collection.find_root() or ""))
        self.field(card, _("Папка коллекции"), self.var_root, _("Куда всё качается и где лежат пачки dataN (anime-paths.json — для связки с anime-sort, не обязателен)"), browse="folder")
        self.var_distribute = tk.BooleanVar(value=bool(data.get("distribute", True)))
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=5)
        ctk.CTkLabel(row, text="", width=170).pack(side="left")
        ctk.CTkCheckBox(row, text=_("Раскладывать новое по пачкам dataN"), variable=self.var_distribute, font=font(13), text_color=TEXT,
                        fg_color=ACCENT, hover_color=ACCENT_HOVER, border_color=LINE, checkbox_width=20, checkbox_height=20).pack(side="left")
        ctk.CTkLabel(card, text=_("Выключено — anime-vault только скачивает, страница «Разложить» скрыта"), font=font(11),
                     text_color=FAINT, anchor="w").pack(fill="x", padx=(206, 18))
        self.var_batch = tk.StringVar(value=str(data.get("batch_size", 500)))
        self.field(card, _("Файлов в одной dataN"), self.var_batch, _("Последняя папка может быть меньше"))

        card = Card(page, _("Вход на сайт"))
        card.pack(fill="x", pady=(0, 14))
        cookies = browsers.parse(pinterest.get("browser_cookies", ""))
        current = cookies.browser if cookies.kind == "browser" else cookies.kind
        self.var_cookie_kind = tk.StringVar(value=cookie_label(current if current in COOKIE_CHOICES else "none"))
        self.choice(card, _("Куки"), self.var_cookie_kind, [cookie_label(key) for key in COOKIE_CHOICES],
                    _("Opera / Firefox — надёжно; новые Chrome и Edge шифруют куки — для них лучше файл cookies.txt"),
                    command=lambda _value: self.update_cookie_hint())
        self.var_cookie_path = tk.StringVar(value=cookies.profile if cookies.kind == "browser" else cookies.file)
        self.cookie_row, self.cookie_hint = self.field(card, _("Профиль / файл"), self.var_cookie_path, "", browse="cookies")
        self.var_user = tk.StringVar(value=pinterest.get("user", ""))
        self.field(card, _("Пользователь Pinterest"), self.var_user, _("Подставится сам из первой ссылки на доску (pinterest.com/<имя>/<доска>)"))
        self.var_target = tk.StringVar(value=pinterest.get("target", "downloads"))
        self.field(card, _("Куда качать"), self.var_target, _("Относительно папки коллекции; по папке на доску"))
        self.update_cookie_hint()

        self.boards_card = Card(page, _("Доски"))
        self.boards_card.pack(fill="x", pady=(0, 14))
        ctk.CTkLabel(self.boards_card, text=_("Вставь ссылку на доску Pinterest (или на что угодно, что качает gallery-dl: Danbooru, Pixiv, X …) — имя папки подставится само."),
                     font=font(12), text_color=MUTED, anchor="w", wraplength=900, justify="left").pack(fill="x", padx=18, pady=(0, 6))
        self.board_rows: list[tuple[tk.StringVar, tk.StringVar, ctk.CTkFrame, tk.BooleanVar]] = []
        user = pinterest.get("user", "")
        for item in pinterest.get("boards", []):
            board = manifest.read_board({"name": "", **item})
            self.add_board_row(board.name, board.address(user) if (board.url or board.slug) else "", board.keep)
        self.boards_add = button(self.boards_card, "+ " + _("Доска"), lambda: self.add_board_row("", ""), width=110, height=30)
        self.boards_add.pack(anchor="w", padx=18, pady=(4, 16))
        bar = ctk.CTkFrame(page, fg_color="transparent")
        bar.pack(fill="x", pady=(0, 10))
        button(bar, _("Сохранить"), self.save_settings, "primary", width=160).pack(side="left")
        self.settings_note = ctk.CTkLabel(bar, text="", font=font(12), text_color=MUTED, wraplength=700, justify="left")
        self.settings_note.pack(side="left", padx=12)
        return page

    def field(self, parent, label: str, variable: tk.StringVar, hint: str, browse: str = ""):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=5)
        ctk.CTkLabel(row, text=label, width=170, anchor="w", font=font(13), text_color=MUTED).pack(side="left")
        entry = ctk.CTkEntry(row, textvariable=variable, fg_color=FIELD, border_color=LINE, corner_radius=9, height=34,
                             font=font(13), text_color=TEXT)
        entry.pack(side="left", fill="x", expand=True)
        if browse:
            button(row, _("Выбрать…"), lambda: self.browse(variable, browse), width=100, height=34).pack(side="left", padx=(8, 0))
        note = ctk.CTkLabel(parent, text=hint, font=font(11), text_color=FAINT, anchor="w", wraplength=760, justify="left")
        note.pack(fill="x", padx=(206, 18))
        return row, note

    def choice(self, parent, label: str, variable: tk.StringVar, values: list[str], hint: str, command=None) -> None:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=5)
        ctk.CTkLabel(row, text=label, width=170, anchor="w", font=font(13), text_color=MUTED).pack(side="left")
        ctk.CTkOptionMenu(row, variable=variable, values=values, fg_color=PANEL2, button_color=PANEL2, button_hover_color=LINE,
                          dropdown_fg_color=PANEL2, text_color=TEXT, font=font(13), dropdown_font=font(13), corner_radius=9,
                          height=34, width=260, command=command).pack(side="left")
        ctk.CTkLabel(parent, text=hint, font=font(11), text_color=FAINT, anchor="w").pack(fill="x", padx=(206, 18))

    def cookie_choice(self) -> str:
        labels = {cookie_label(key): key for key in COOKIE_CHOICES}
        return labels.get(self.var_cookie_kind.get(), "none")

    def update_cookie_hint(self) -> None:
        key = self.cookie_choice()
        hints = {"file": _("Путь к cookies.txt (формат Netscape) — сохрани его расширением браузера после входа на сайт"),
                 "none": _("Не нужно"),
                 "opera": _("Папка профиля, например …/AppData/Roaming/Opera Software/Opera GX Stable; пусто — обычная Opera")}
        self.cookie_hint.configure(text=hints.get(key, _("Пусто — профиль по умолчанию; или папка профиля")))

    def add_board_row(self, name: str, address: str, keep: bool = False) -> None:
        row = ctk.CTkFrame(self.boards_card, fg_color="transparent")
        if hasattr(self, "boards_add"):
            row.pack(fill="x", padx=18, pady=4, before=self.boards_add)
        else:
            row.pack(fill="x", padx=18, pady=4)
        name_var, address_var = tk.StringVar(value=name), tk.StringVar(value=address)
        ctk.CTkEntry(row, textvariable=name_var, placeholder_text=_("Папка (имя доски)"), fg_color=FIELD, border_color=LINE,
                     corner_radius=9, height=32, width=240).pack(side="left")
        address_entry = ctk.CTkEntry(row, textvariable=address_var, placeholder_text=_("Ссылка на доску — https://…"), fg_color=FIELD,
                                     border_color=LINE, corner_radius=9, height=32)
        address_entry.pack(side="left", fill="x", expand=True, padx=8)

        def fill_name(*_args) -> None:
            # Имя папки — само по ссылке, если пользователь его ещё не вписал.
            if not name_var.get().strip() and address_var.get().strip():
                name_var.set(manifest.name_from_address(address_var.get()))

        address_var.trace_add("write", fill_name)
        keep_var = tk.BooleanVar(value=keep)
        ctk.CTkCheckBox(row, text=_("Только хранить"), variable=keep_var, font=font(12), text_color=MUTED, fg_color=ACCENT,
                        hover_color=ACCENT_HOVER, border_color=LINE, checkbox_width=20, checkbox_height=20).pack(side="left", padx=(0, 8))
        entry = (name_var, address_var, row, keep_var)
        button(row, "✕", lambda: self.remove_board_row(entry), "ghost", width=36, height=32).pack(side="left")
        self.board_rows.append(entry)

    def remove_board_row(self, entry) -> None:
        entry[2].destroy()
        self.board_rows.remove(entry)

    # ================================================================ данные

    def refresh(self) -> None:
        """Сведения о коллекции — в фоновом потоке (счёт файлов), результат забирает poll()."""
        threading.Thread(target=lambda: self.results.put(("snapshot", collection.snapshot())), daemon=True).start()

    def apply_snapshot(self, shot: collection.Snapshot) -> None:
        self.snapshot = shot
        self.foot_root.configure(text=str(shot.root) if not shot.error else shot.error)
        self.fill_steps(browsers.parse(shot.cookies))
        self.set_distribute_visible(shot.distribute or shot.error != "")
        if shot.error:
            self.stat_waiting.set("—", shot.error)
            return
        first, last = shot.next_number, shot.next_number + max(shot.planned_folders, 1) - 1
        self.stat_waiting.set(number(shot.waiting), _("≈ {folders} папок по {size}").format(folders=shot.planned_folders, size=shot.batch_size)
                              if shot.waiting else _("Всё разложено"))
        self.stat_batches.set(number(shot.batches), _("Последняя {name}").format(name=shot.last_batch) if shot.last_batch else _("Пока нет"))
        self.stat_boards.set(number(sum(b[1] for b in shot.boards)),
                             " · ".join(f"{name}: {number(n)}{' (' + _('хранится') + ')' if keep else ''}" for name, n, keep in shot.boards))
        self.stat_dups.set(number(shot.duplicates), _("Страница «Дубли» — посмотреть и удалить") if shot.duplicates else _("Нет"))
        self.journal_label.configure(text=_("Последняя раскладка: {journal}").format(journal=shot.last_journal) if shot.last_journal
                                     else _("Раскладок ещё не было"))
        for box, rows in ((self.boards_box, shot.boards), (self.sources_box, [(name, n, False) for name, n in shot.sources])):
            for child in box.winfo_children():
                child.destroy()
            for name, count, keep in rows:
                line = ctk.CTkFrame(box, fg_color=PANEL2, corner_radius=10)
                line.pack(fill="x", pady=4)
                ctk.CTkLabel(line, text=name, font=font(13), text_color=TEXT, anchor="w").pack(side="left", padx=12, pady=8)
                ctk.CTkLabel(line, text=number(count), font=font(13, "bold"), text_color=ACCENT if count else FAINT).pack(side="right", padx=12)
                if keep:
                    ctk.CTkLabel(line, text=" " + _("Только хранить — не раскладывается") + " ", font=font(11), text_color=YELLOW,
                                 fg_color="#33291a", corner_radius=8).pack(side="right", padx=4)
        self.plan_label.configure(
            text=_("{count} файлов → около {folders} папок: data{first}…data{last} по {size}.").format(
                count=number(shot.waiting), folders=shot.planned_folders, first=first, last=last, size=shot.batch_size)
            if shot.waiting else _("Раскладывать нечего — сначала скачайте новое."))

    def set_distribute_visible(self, visible: bool) -> None:
        """Раскладка выключена в настройках — скрыть её страницу, кнопку и карточки «Ждёт раскладки» / «Папок dataN»."""
        nav = self.nav["distribute"]
        if visible and not nav.winfo_ismapped():
            nav.pack(fill="x", padx=12, pady=2, before=self.nav["duplicates"])
            self.overview_distribute.pack(side="left", padx=8, after=self.overview_distribute.master.winfo_children()[0])
            for column, stat in enumerate(self.stats):
                stat.grid(row=0, column=column, columnspan=1)
        elif not visible and nav.winfo_ismapped():
            nav.pack_forget()
            self.overview_distribute.pack_forget()
            self.stat_waiting.grid_remove()
            self.stat_batches.grid_remove()
            self.stat_boards.grid(row=0, column=0, columnspan=2)
            self.stat_dups.grid(row=0, column=2, columnspan=2)
            if self.current == "distribute":
                self.show("overview")

    def poll(self) -> None:
        """События команды и фоновых подсчётов — в потоке окна."""
        while True:
            try:
                kind, value = self.results.get_nowait()
            except queue.Empty:
                break
            if kind == "snapshot":
                self.apply_snapshot(value)
            elif kind == "browser":
                self.show_browser(*value)
            elif kind == "duplicates":
                self.show_duplicates(value)
            elif kind == "thumb":
                self.set_thumb(*value)
        while True:
            try:
                event = self.runner.events.get_nowait()
            except queue.Empty:
                break
            self.handle(event)
        self.after(100, self.poll)

    def poll_browser(self) -> None:
        if self.current == "download" and self.snapshot is not None:
            spec = self.snapshot.cookies
            threading.Thread(target=lambda: self.results.put(("browser", collection.browser_state(spec))), daemon=True).start()
        self.after(2000, self.poll_browser)

    def show_browser(self, cookies: browsers.Cookies, running: bool) -> None:
        if not cookies.must_close:
            self.browser_label.configure(text=f"  {browsers.describe(cookies)}  ", text_color=MUTED, fg_color=PANEL2)
            return
        text = (_("{browser} открыт — закрой его полностью") if running else _("{browser} закрыт — можно начинать")).format(browser=cookies.name)
        self.browser_label.configure(text=f"  {text}  ", text_color="#ffb3bc" if running else "#a6ecc7",
                                     fg_color="#3a1a20" if running else "#13281f")

    # ================================================================ дубли

    def load_duplicates(self) -> None:
        root = self.snapshot.root if self.snapshot and not self.snapshot.error else collection.find_root()
        if root is None:
            return
        self.dup_label.configure(text=_("Ищу дубли…"))
        threading.Thread(target=lambda: self.results.put(("duplicates", collection.duplicates(root))), daemon=True).start()

    def show_duplicates(self, items: list[collection.Duplicate]) -> None:
        self.dup_items = items
        self.dup_shown = 0
        self.thumbs.clear()
        for child in self.dup_list.winfo_children():
            child.destroy()
        self.dup_label.configure(text=_("Дублей: {count}").format(count=number(len(items))) if items
                                 else _("Дублей нет — всё чисто."))
        self.dup_all.configure(state="normal" if items else "disabled")
        self.more_duplicates()

    def more_duplicates(self) -> None:
        for child in self.dup_list.winfo_children():
            if getattr(child, "is_more", False):
                child.destroy()
        batch = self.dup_items[self.dup_shown:self.dup_shown + PAGE_SIZE]
        for item in batch:
            self.duplicate_row(item)
        self.dup_shown += len(batch)
        left = len(self.dup_items) - self.dup_shown
        if left > 0:
            more = button(self.dup_list, _("Показать ещё ({count})").format(count=number(left)), self.more_duplicates, width=200)
            more.is_more = True
            more.pack(anchor="w", pady=10)
        paths = [(item.file, item.original) for item in batch]
        threading.Thread(target=self.load_thumbs, args=(paths,), daemon=True).start()

    def duplicate_row(self, item: collection.Duplicate) -> None:
        row = ctk.CTkFrame(self.dup_list, fg_color=PANEL, corner_radius=12, border_width=1, border_color=LINE)
        row.pack(fill="x", pady=5)
        row.item = item
        pictures = []
        for title, path in ((_("Дубль"), item.file), (_("Оригинал"), item.original)):
            cell = ctk.CTkFrame(row, fg_color="transparent")
            cell.pack(side="left", padx=(12, 0), pady=10)
            picture = ctk.CTkLabel(cell, text=_("Нет файла") if not path else "…", width=THUMB, height=THUMB, fg_color=FIELD,
                                   corner_radius=8, text_color=FAINT, font=font(11))
            picture.pack()
            ctk.CTkLabel(cell, text=title, font=font(11), text_color=MUTED).pack()
            pictures.append((str(path), picture))
        row.pictures = pictures
        info = ctk.CTkFrame(row, fg_color="transparent")
        info.pack(side="left", fill="both", expand=True, padx=16, pady=10)
        ctk.CTkLabel(info, text=item.file.name, font=font(13, "bold"), text_color=TEXT, anchor="w").pack(fill="x")
        lines = [_("Откуда: {path}").format(path=item.source) if item.source else "",
                 _("Оригинал: {path}").format(path=item.original) if item.original
                 else _("Оригинал: не записан в журнале (раскладка старой версии) или уже удалён")]
        ctk.CTkLabel(info, text="\n".join(line for line in lines if line), font=font(11), text_color=MUTED, anchor="w",
                     justify="left", wraplength=560).pack(fill="x", pady=(4, 8))
        actions = ctk.CTkFrame(info, fg_color="transparent")
        actions.pack(fill="x")
        button(actions, _("В Корзину"), lambda: self.trash_row(row), "danger", width=110, height=30).pack(side="left")
        button(actions, _("Открыть"), lambda: os.startfile(str(item.file)), "ghost", width=90, height=30).pack(side="left", padx=6)  # noqa: S606
        if item.original:
            button(actions, _("Оригинал в папке"), lambda: subprocess.Popen(["explorer", f"/select,{item.original}"]),
                   "ghost", width=140, height=30).pack(side="left")

    def load_thumbs(self, paths) -> None:
        """Миниатюры — в фоновом потоке (чтение картинок); в окно их кладёт poll() → set_thumb."""
        from PIL import Image

        for pair in paths:
            for path in pair:
                if not path:
                    continue
                try:
                    with Image.open(path) as image:
                        image.thumbnail((THUMB, THUMB))
                        self.results.put(("thumb", (str(path), image.convert("RGB"))))
                except Exception:
                    self.results.put(("thumb", (str(path), None)))

    def set_thumb(self, path: str, image) -> None:
        for row in self.dup_list.winfo_children():
            for row_path, label in getattr(row, "pictures", []):
                if row_path == path and label.winfo_exists():
                    if image is None:
                        label.configure(text=_("Не картинка"))
                        continue
                    picture = ctk.CTkImage(light_image=image, dark_image=image, size=image.size)
                    self.thumbs.append(picture)
                    label.configure(image=picture, text="")

    def trash_row(self, row) -> None:
        collection.to_trash([row.item.file])
        self.dup_items.remove(row.item)
        self.dup_shown -= 1
        row.destroy()
        self.dup_label.configure(text=_("Дублей: {count}").format(count=number(len(self.dup_items))) if self.dup_items
                                 else _("Дублей нет — всё чисто."))
        self.refresh()

    def trash_all(self) -> None:
        if not self.dup_items:
            return
        if not messagebox.askyesno(_("Всё в Корзину"), _("Отправить в Корзину все дубли ({count})? Их можно будет восстановить из Корзины.").format(
                count=number(len(self.dup_items))), parent=self):
            return
        done = collection.to_trash([item.file for item in self.dup_items])
        self.say(_("В Корзину отправлено дублей: {count}").format(count=number(done)), "92")
        self.load_duplicates()
        self.refresh()

    def open_duplicates(self) -> None:
        if self.snapshot and not self.snapshot.error:
            folder = self.snapshot.root / collection.DUPLICATES
            os.startfile(str(folder if folder.exists() else self.snapshot.root))  # noqa: S606 — открыть папку в проводнике

    # ================================================================ команды

    def start_download(self) -> None:
        self.start(_("Скачивание"), "download")

    def start_distribute(self) -> None:
        self.start(_("Раскладка"), "distribute")

    def start(self, title: str, command: str) -> None:
        if self.runner.busy:
            self.say(_("Уже идёт другая команда — дождись её или останови."), "93")
            return
        root = self.snapshot.root if self.snapshot and not self.snapshot.error else collection.find_root()
        if root is None:
            self.say(_("Коллекция не найдена — укажи папку в «Настройках»."), "91")
            return
        self.clear_log()
        self.runner.start(title, command, root)
        self.set_running(True, title)

    def set_running(self, running: bool, title: str = "") -> None:
        self.foot_state.configure(text="● " + (_("Идёт: {title}").format(title=title) if running else _("Команда не идёт")),
                                  text_color=ACCENT if running else MUTED)
        self.log_title.configure(text=(_("Журнал") + (f" — {title}" if running else "")).upper())
        for widget in (self.download_button, self.distribute_button):
            widget.configure(state="disabled" if running else "normal")
        if running:
            self.stop_button.pack(side="right", padx=(8, 0))
        else:
            self.stop_button.pack_forget()

    def handle(self, event) -> None:
        kind = event[0]
        if kind == "line":
            self.write(event[1])
        elif kind == "ask":
            self.write([(event[1], "93")])
            self.ask(event[1], event[2])
        elif kind == "wait":
            self.write([(event[1], "93")])
            ready = event[1].startswith(_("Готово"))
            self.ask(event[1], [("", _("Готово — браузер закрыт") if ready else _("Продолжить"))])
        elif kind == "exit":
            self.clear_question()
            self.write([(_("Команда завершена.") if event[1] == 0 else _("Команда завершилась с ошибкой (код {code}).").format(code=event[1]),
                         "92" if event[1] == 0 else "91")])
            self.set_running(False)
            self.refresh()

    def ask(self, prompt: str, options) -> None:
        self.clear_question()
        self.question.pack(fill="x", padx=16, pady=(0, 14))
        ctk.CTkLabel(self.question, text=prompt, font=font(13, "bold"), text_color=TEXT).pack(side="left", padx=(0, 10))
        for index, (key, label) in enumerate(options):
            button(self.question, label, lambda key=key, label=label: self.reply(key, label),
                   "primary" if index == 0 else "normal", height=32).pack(side="left", padx=4)

    def reply(self, key: str, label: str) -> None:
        self.clear_question()
        self.write([(f"→ {label}", "90")])
        self.runner.answer(key)

    def clear_question(self) -> None:
        for child in self.question.winfo_children():
            child.destroy()
        self.question.pack_forget()

    def runner_stop(self) -> None:
        self.runner.stop()
        self.write([(_("Остановлено. Всё сделанное сохранено — можно запустить снова."), "93")])

    # ================================================================ журнал

    def write(self, parts) -> None:
        self.text.configure(state="normal")
        for chunk, code in parts:
            self.text.insert("end", chunk, (f"c{code}",) if code else ())
        self.text.insert("end", "\n")
        self.text.configure(state="disabled")
        self.text.see("end")

    def say(self, text: str, code: str | None = None) -> None:
        self.write([(text, code)])

    def clear_log(self) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")

    # ================================================================ настройки

    def browse(self, variable: tk.StringVar, kind: str) -> None:
        if kind == "cookies" and self.cookie_choice() == "file":
            path = filedialog.askopenfilename(parent=self, filetypes=[("cookies.txt", "*.txt"), ("*", "*.*")])
        else:
            path = filedialog.askdirectory(initialdir=variable.get() or str(Path.home()), parent=self)
        if path:
            variable.set(path)

    def note(self, text: str, ok: bool = False) -> None:
        self.settings_note.configure(text=text, text_color=GREEN if ok else RED)

    def save_settings(self) -> None:
        data = self.settings_data
        root = self.var_root.get().strip()
        if not root or not Path(root).is_dir():
            self.note(_("Укажи существующую папку коллекции."))
            return
        try:
            batch = int(self.var_batch.get())
            if batch < 1:
                raise ValueError
        except ValueError:
            self.note(_("Размер пачки — целое число больше нуля."))
            return
        user = self.var_user.get().strip()
        boards, names = [], set()
        for name_var, address_var, _row, keep_var in self.board_rows:
            address = address_var.get().strip()
            if not address:
                continue
            user = user or manifest.pinterest_user(address)       # пользователь — из первой ссылки на доску
            try:
                fields = manifest.parse_address(address, user)
            except ValueError as exc:
                self.note(_("Доска «{address}»: {error}").format(address=address, error=exc))
                return
            name = name_var.get().strip() or manifest.name_from_address(address)
            if name.casefold() in names:
                self.note(_("Две доски с одной папкой «{name}» — переименуй одну.").format(name=name))
                return
            names.add(name.casefold())
            # keep: «только хранить» — доска обновляется с сайта, раскладка её не трогает.
            boards.append({"name": name, **fields, **({"keep": True} if keep_var.get() else {})})
        key = self.cookie_choice()
        path = self.var_cookie_path.get().strip()
        if key == "file" and not Path(path).is_file():
            self.note(_("Файл куки не найден: {path}").format(path=path or "—"))
            return
        cookies = browsers.Cookies("file", file=path) if key == "file" else browsers.Cookies("none") if key == "none" \
            else browsers.Cookies("browser", key, path)
        language = {title: code for code, title in i18n.LANGUAGES.items()}.get(self.var_language.get(), i18n.LANG)
        data["language"] = language
        data["collection"] = root.replace("\\", "/")
        data["distribute"] = bool(self.var_distribute.get())
        data["batch_size"] = batch
        pinterest = data.setdefault("pinterest", {})
        pinterest["user"] = user
        pinterest["target"] = self.var_target.get().strip() or "downloads"
        pinterest["browser_cookies"] = cookies.spec()
        pinterest["boards"] = boards
        collection.save_settings(data)
        self.var_user.set(user)
        if language != i18n.LANG:
            self.restart()
            return
        self.note(_("Сохранено в config.json."), ok=True)
        self.refresh()

    def restart(self) -> None:
        """Новый язык — окно открывается заново (тексты строятся один раз при создании страниц)."""
        if self.runner.busy:
            self.note(_("Язык сохранён — применится, когда закончится команда и окно откроется заново."), ok=True)
            return
        program = [sys.executable] if FROZEN else [str(Path(python_exe()).with_name("pythonw.exe")), "-m", "anime_vault.gui"]
        subprocess.Popen([*program, "--page", "settings"], cwd=str(PROJECT))
        self.destroy()

    def open_root(self) -> None:
        if self.snapshot and not self.snapshot.error:
            os.startfile(str(self.snapshot.root))  # noqa: S606 — открыть папку в проводнике


def main() -> int:
    page = sys.argv[sys.argv.index("--page") + 1] if "--page" in sys.argv else "overview"
    App(page).mainloop()
    return 0

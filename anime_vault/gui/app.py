r"""Окно anime-vault (CustomTkinter) — вместо .bat-файлов: скачать новые пины, разложить их по dataN, настройки.

Страницы:
    Обзор       сколько ждёт раскладки, папки dataN, файлы на досках, дубли на просмотр, последняя раскладка
    Скачать     что сделать перед началом (вход в Pinterest, закрыть Opera GX — живой индикатор), доски, запуск
    Разложить   источники и прикидка, сколько выйдет папок dataN, запуск
    Настройки   config.json: папка коллекции, размер пачки, пользователь Pinterest, профиль браузера, доски
Внизу — журнал команды (цвета как в консоли) и её вопросы кнопками. Команды — те же, что у .bat-файлов
(python -m anime_vault download|distribute), окно только запускает их и отвечает на вопросы (gui\runner.py).

    python -m anime_vault.gui            (или anime-vault.vbs в папке проекта / ярлык anime-vault в коллекции)
    python -m anime_vault.gui --page settings
"""
from __future__ import annotations

import os
import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk

from anime_vault.gui import collection
from anime_vault.gui.runner import Runner

# Палитра — как у Пульта anime-sort (тёмная, фиолетовый акцент).
BG, SIDE, PANEL, PANEL2, FIELD, LINE = "#0e0f13", "#0b0c10", "#16181f", "#1c1f28", "#12141a", "#262a36"
TEXT, MUTED, FAINT = "#e6e8ef", "#8b90a0", "#5d6272"
ACCENT, ACCENT_HOVER, TEAL, GREEN, YELLOW, RED = "#8b7cff", "#7a6af0", "#5ad1c4", "#3ecf8e", "#f2c94c", "#ff6b7a"
ANSI = {"31": RED, "91": RED, "32": GREEN, "92": GREEN, "33": YELLOW, "93": YELLOW, "36": TEAL, "96": TEAL,
        "90": FAINT, "97": "#ffffff", "37": TEXT}
PAGES = [("overview", "Обзор", "▦"), ("download", "Скачать", "⬇"), ("distribute", "Разложить", "⇢"),
         ("settings", "Настройки", "⚙")]


def font(size: int = 13, weight: str = "normal") -> ctk.CTkFont:
    return ctk.CTkFont(family="Segoe UI", size=size, weight=weight)


def number(value: int) -> str:
    return f"{value:,}".replace(",", " ")


class Card(ctk.CTkFrame):
    def __init__(self, parent, title: str = "", **options):
        super().__init__(parent, fg_color=PANEL, corner_radius=14, border_width=1, border_color=LINE, **options)
        if title:
            ctk.CTkLabel(self, text=title.upper(), font=font(11, "bold"), text_color=MUTED).pack(anchor="w", padx=18, pady=(14, 4))


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
        self.title("anime-vault (проверка)" if os.environ.get("ANIME_VAULT_TEST") else "anime-vault")
        self.geometry("1320x860")
        self.minsize(1100, 720)
        self.runner = Runner()
        self.results: queue.Queue = queue.Queue()
        self.snapshot: collection.Snapshot | None = None
        self.opera = None
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.build_side()
        self.build_main()
        self.show(page)
        self.refresh()
        self.after(100, self.poll)
        self.after(500, self.poll_opera)

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
        ctk.CTkLabel(names, text="Pinterest → dataN", font=font(11), text_color=MUTED).pack(anchor="w")
        self.nav = {}
        for key, title, icon in PAGES:
            nav = ctk.CTkButton(side, text=f"  {icon}   {title}", anchor="w", height=38, corner_radius=10, font=font(13),
                                fg_color="transparent", hover_color="#14161d", text_color=MUTED,
                                command=lambda key=key: self.show(key))
            nav.pack(fill="x", padx=12, pady=2)
            self.nav[key] = nav
        foot = ctk.CTkFrame(side, fg_color=PANEL, corner_radius=12, border_width=1, border_color=LINE)
        foot.pack(side="bottom", fill="x", padx=12, pady=14)
        self.foot_state = ctk.CTkLabel(foot, text="● Команда не идёт", font=font(12), text_color=MUTED, anchor="w")
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
                      "distribute": self.page_distribute(), "settings": self.page_settings()}
        self.build_log(main)

    def build_log(self, main) -> None:
        log = Card(main)
        log.grid(row=2, column=0, sticky="ew", padx=26, pady=(12, 20))
        head = ctk.CTkFrame(log, fg_color="transparent")
        head.pack(fill="x", padx=16, pady=(10, 4))
        self.log_title = ctk.CTkLabel(head, text="ЖУРНАЛ", font=font(11, "bold"), text_color=MUTED)
        self.log_title.pack(side="left")
        self.stop_button = button(head, "■ Остановить", self.runner_stop, "danger", width=120, height=28)
        button(head, "Очистить", self.clear_log, "ghost", width=90, height=28).pack(side="right")
        self.text = ctk.CTkTextbox(log, height=190, fg_color=FIELD, text_color="#c9ccd6", corner_radius=10,
                                   font=ctk.CTkFont(family="Cascadia Code", size=12), wrap="word", border_width=0)
        self.text.pack(fill="x", padx=14, pady=(0, 14))
        for code, color in ANSI.items():
            self.text.tag_config(f"c{code}", foreground=color)
        self.text.configure(state="disabled")
        # Строка вопроса команды — появляется, только когда команда спрашивает (пустая рамка CTk иначе 200 px).
        self.question = ctk.CTkFrame(log, fg_color="transparent", height=1)
        self.say("Здесь будет вывод команд. Вопросы команд появятся кнопками под журналом.", "90")

    def show(self, key: str) -> None:
        key = key if key in self.pages else "overview"
        for name, nav in self.nav.items():
            active = name == key
            nav.configure(fg_color=PANEL2 if active else "transparent", text_color=TEXT if active else MUTED)
        for frame in self.pages.values():
            frame.grid_forget()
        self.pages[key].grid(row=0, column=0, sticky="nsew")
        titles = {"overview": ("Обзор", "Что скачано, что ждёт раскладки, папки dataN"),
                  "download": ("Скачать новые пины", "Доски Pinterest → папки досок (gallery-dl, вход через куки браузера)"),
                  "distribute": ("Разложить по dataN", "Новое из источников → папки dataN по размеру пачки, дубли — в duplicates"),
                  "settings": ("Настройки", "config.json проекта anime-vault — не в git")}
        self.page_title.configure(text=titles[key][0])
        self.page_sub.configure(text=titles[key][1])
        self.current = key

    # ================================================================ страницы

    def page_overview(self) -> ctk.CTkFrame:
        page = ctk.CTkFrame(self.body, fg_color="transparent")
        page.grid_columnconfigure((0, 1, 2, 3), weight=1, uniform="stat")
        self.stat_waiting = Stat(page, "Ждёт раскладки", ACCENT)
        self.stat_batches = Stat(page, "Папок dataN", TEAL)
        self.stat_boards = Stat(page, "На досках", YELLOW)
        self.stat_dups = Stat(page, "Дубли на просмотр", RED)
        for column, stat in enumerate((self.stat_waiting, self.stat_batches, self.stat_boards, self.stat_dups)):
            stat.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else 8, 0 if column == 3 else 8))
        actions = Card(page, "Что дальше")
        actions.grid(row=1, column=0, columnspan=4, sticky="ew", pady=16)
        row = ctk.CTkFrame(actions, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=(4, 16))
        button(row, "⬇  Скачать новое", lambda: self.show("download"), "primary", width=180).pack(side="left")
        button(row, "⇢  Разложить по dataN", lambda: self.show("distribute"), width=200).pack(side="left", padx=8)
        button(row, "Открыть коллекцию", self.open_root, "ghost", width=160).pack(side="left")
        button(row, "↻ Обновить", self.refresh, "ghost", width=110).pack(side="right")
        self.journal_label = ctk.CTkLabel(actions, text="", font=font(12), text_color=MUTED)
        self.journal_label.pack(anchor="w", padx=18, pady=(0, 14))
        return page

    def page_download(self) -> ctk.CTkFrame:
        page = ctk.CTkFrame(self.body, fg_color="transparent")
        page.grid_columnconfigure((0, 1), weight=1, uniform="half")
        steps = Card(page, "Перед началом")
        steps.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        for index, text in enumerate(("Открой Opera GX и проверь, что вошёл в Pinterest — аккаунт с этими досками.",
                                      "Полностью закрой Opera GX, вместе со значком в трее: у открытого браузера куки не прочитать.",
                                      "Нажми «Начать скачивание». Когда начнётся первая доска, браузер можно снова открыть."), 1):
            line = ctk.CTkFrame(steps, fg_color="transparent")
            line.pack(fill="x", padx=18, pady=5)
            ctk.CTkLabel(line, text=str(index), width=28, height=28, corner_radius=8, fg_color="#24213a", text_color=ACCENT,
                         font=font(13, "bold")).pack(side="left", anchor="n")
            ctk.CTkLabel(line, text=text, font=font(13), text_color=TEXT, wraplength=430, justify="left").pack(side="left", padx=10)
        self.opera_label = ctk.CTkLabel(steps, text="Проверяю Opera GX…", font=font(13, "bold"), text_color=MUTED,
                                        corner_radius=8, fg_color=PANEL2, height=32)
        self.opera_label.pack(anchor="w", padx=18, pady=(10, 6))
        self.download_button = button(steps, "⬇  Начать скачивание", self.start_download, "primary", width=220, height=40)
        self.download_button.pack(anchor="w", padx=18, pady=(4, 18))
        boards = Card(page, "Доски")
        boards.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        self.boards_box = ctk.CTkFrame(boards, fg_color="transparent")
        self.boards_box.pack(fill="both", expand=True, padx=18, pady=(4, 16))
        return page

    def page_distribute(self) -> ctk.CTkFrame:
        page = ctk.CTkFrame(self.body, fg_color="transparent")
        page.grid_columnconfigure((0, 1), weight=1, uniform="half")
        sources = Card(page, "Источники — ждут раскладки")
        sources.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self.sources_box = ctk.CTkFrame(sources, fg_color="transparent")
        self.sources_box.pack(fill="both", expand=True, padx=18, pady=(4, 16))
        plan = Card(page, "План")
        plan.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        self.plan_label = ctk.CTkLabel(plan, text="", font=font(14), text_color=TEXT, wraplength=470, justify="left")
        self.plan_label.pack(anchor="w", padx=18, pady=(6, 6))
        ctk.CTkLabel(plan, text="Точный план покажет сама команда: она отсеет дубли (тот же пин или та же картинка уже есть в "
                                "dataN / test-dataN) и спросит «Перенести?». Файлы ПЕРЕНОСЯТСЯ, дубли — в duplicates.",
                     font=font(12), text_color=MUTED, wraplength=470, justify="left").pack(anchor="w", padx=18, pady=(0, 10))
        self.distribute_button = button(plan, "⇢  Разложить", self.start_distribute, "primary", width=200, height=40)
        self.distribute_button.pack(anchor="w", padx=18, pady=(4, 18))
        return page

    def page_settings(self) -> ctk.CTkFrame:
        page = ctk.CTkScrollableFrame(self.body, fg_color="transparent")
        data = collection.read_settings()
        self.settings_data = data
        pinterest = data.setdefault("pinterest", {})
        card = Card(page, "Коллекция и раскладка")
        card.pack(fill="x", pady=(0, 14))
        self.var_root = tk.StringVar(value=data.get("collection", str(collection.find_root() or "")))
        self.var_batch = tk.StringVar(value=str(data.get("batch_size", 500)))
        self.field(card, "Папка коллекции", self.var_root, "где лежит anime-paths.json", browse=True)
        self.field(card, "Файлов в одной dataN", self.var_batch, "последняя папка может быть меньше")
        card = Card(page, "Pinterest")
        card.pack(fill="x", pady=(0, 14))
        self.var_user = tk.StringVar(value=pinterest.get("user", ""))
        self.var_target = tk.StringVar(value=pinterest.get("target", ""))
        self.var_cookies = tk.StringVar(value=pinterest.get("browser_cookies", ""))
        self.field(card, "Пользователь", self.var_user, "чьи доски качать (имя в адресе pinterest.com/<имя>/…)")
        self.field(card, "Куда качать", self.var_target, "относительно папки коллекции; по папке на доску")
        self.field(card, "Профиль браузера", self.var_cookies, "для куки gallery-dl: «opera:<папка профиля Opera GX>»")
        self.boards_card = Card(page, "Доски")
        self.boards_card.pack(fill="x", pady=(0, 14))
        self.board_rows: list[tuple[tk.StringVar, tk.StringVar, ctk.CTkFrame, tk.BooleanVar]] = []
        for board in pinterest.get("boards", []):
            self.add_board_row(board.get("name", ""), board.get("slug", ""), bool(board.get("keep")))
        self.boards_add = button(self.boards_card, "+ Доска", lambda: self.add_board_row("", ""), width=110, height=30)
        self.boards_add.pack(anchor="w", padx=18, pady=(4, 16))
        bar = ctk.CTkFrame(page, fg_color="transparent")
        bar.pack(fill="x", pady=(0, 10))
        button(bar, "Сохранить", self.save_settings, "primary", width=160).pack(side="left")
        self.settings_note = ctk.CTkLabel(bar, text="", font=font(12), text_color=MUTED)
        self.settings_note.pack(side="left", padx=12)
        return page

    def field(self, parent, label: str, variable: tk.StringVar, hint: str, browse: bool = False) -> None:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=5)
        ctk.CTkLabel(row, text=label, width=170, anchor="w", font=font(13), text_color=MUTED).pack(side="left")
        entry = ctk.CTkEntry(row, textvariable=variable, fg_color=FIELD, border_color=LINE, corner_radius=9, height=34,
                             font=font(13), text_color=TEXT)
        entry.pack(side="left", fill="x", expand=True)
        if browse:
            button(row, "Выбрать…", lambda: self.browse(variable), width=100, height=34).pack(side="left", padx=(8, 0))
        ctk.CTkLabel(parent, text=hint, font=font(11), text_color=FAINT, anchor="w").pack(fill="x", padx=(206, 18))

    def add_board_row(self, name: str, slug: str, keep: bool = False) -> None:
        row = ctk.CTkFrame(self.boards_card, fg_color="transparent")
        if hasattr(self, "boards_add"):
            row.pack(fill="x", padx=18, pady=4, before=self.boards_add)
        else:
            row.pack(fill="x", padx=18, pady=4)
        name_var, slug_var = tk.StringVar(value=name), tk.StringVar(value=slug)
        ctk.CTkEntry(row, textvariable=name_var, placeholder_text="название доски (и папки)", fg_color=FIELD, border_color=LINE,
                     corner_radius=9, height=32, width=260).pack(side="left")
        ctk.CTkEntry(row, textvariable=slug_var, placeholder_text="часть адреса доски", fg_color=FIELD, border_color=LINE,
                     corner_radius=9, height=32).pack(side="left", fill="x", expand=True, padx=8)
        keep_var = tk.BooleanVar(value=keep)
        ctk.CTkCheckBox(row, text="только хранить", variable=keep_var, font=font(12), text_color=MUTED, fg_color=ACCENT,
                        hover_color=ACCENT_HOVER, border_color=LINE, checkbox_width=20, checkbox_height=20).pack(side="left", padx=(0, 8))
        entry = (name_var, slug_var, row, keep_var)
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
        if shot.error:
            self.stat_waiting.set("—", shot.error)
            return
        first, last = shot.next_number, shot.next_number + max(shot.planned_folders, 1) - 1
        self.stat_waiting.set(number(shot.waiting), f"≈ {shot.planned_folders} папок по {shot.batch_size}" if shot.waiting else "всё разложено")
        self.stat_batches.set(number(shot.batches), f"последняя {shot.last_batch}" if shot.last_batch else "пока нет")
        self.stat_boards.set(number(sum(b[1] for b in shot.boards)),
                             " · ".join(f"{name}: {number(n)}{' (хранится)' if keep else ''}" for name, n, keep in shot.boards))
        self.stat_dups.set(number(shot.duplicates), "папка duplicates — посмотреть и удалить" if shot.duplicates else "нет")
        self.journal_label.configure(text=f"Последняя раскладка: {shot.last_journal}" if shot.last_journal else "Раскладок ещё не было")
        for box, rows in ((self.boards_box, shot.boards), (self.sources_box, [(name, n, False) for name, n in shot.sources])):
            for child in box.winfo_children():
                child.destroy()
            for name, count, keep in rows:
                line = ctk.CTkFrame(box, fg_color=PANEL2, corner_radius=10)
                line.pack(fill="x", pady=4)
                ctk.CTkLabel(line, text=name, font=font(13), text_color=TEXT, anchor="w").pack(side="left", padx=12, pady=8)
                ctk.CTkLabel(line, text=number(count), font=font(13, "bold"), text_color=ACCENT if count else FAINT).pack(side="right", padx=12)
                if keep:
                    ctk.CTkLabel(line, text=" только хранить — не раскладывается ", font=font(11), text_color=YELLOW,
                                 fg_color="#33291a", corner_radius=8).pack(side="right", padx=4)
        self.plan_label.configure(
            text=f"{number(shot.waiting)} файлов → около {shot.planned_folders} папок: data{first}…data{last} по {shot.batch_size}."
            if shot.waiting else "Раскладывать нечего — сначала скачайте новое.")

    def poll(self) -> None:
        """События команды и фоновых подсчётов — в потоке окна."""
        while True:
            try:
                kind, value = self.results.get_nowait()
            except queue.Empty:
                break
            if kind == "snapshot":
                self.apply_snapshot(value)
            elif kind == "opera":
                self.show_opera(value)
        while True:
            try:
                event = self.runner.events.get_nowait()
            except queue.Empty:
                break
            self.handle(event)
        self.after(100, self.poll)

    def poll_opera(self) -> None:
        if self.current == "download":
            threading.Thread(target=lambda: self.results.put(("opera", collection.opera_running())), daemon=True).start()
        self.after(2000, self.poll_opera)

    def show_opera(self, running: bool) -> None:
        self.opera = running
        self.opera_label.configure(text="  Opera GX открыта — закрой её полностью  " if running else "  Opera GX закрыта — можно начинать  ",
                                   text_color="#ffb3bc" if running else "#a6ecc7", fg_color="#3a1a20" if running else "#13281f")

    # ================================================================ команды

    def start_download(self) -> None:
        self.start("Скачивание", "download")

    def start_distribute(self) -> None:
        self.start("Раскладка", "distribute")

    def start(self, title: str, command: str) -> None:
        if self.runner.busy:
            self.say("Уже идёт другая команда — дождись её или останови.", "93")
            return
        root = self.snapshot.root if self.snapshot and not self.snapshot.error else collection.find_root()
        if root is None:
            self.say("Коллекция не найдена — укажи папку в «Настройках».", "91")
            return
        self.clear_log()
        self.runner.start(title, command, root)
        self.set_running(True, title)

    def set_running(self, running: bool, title: str = "") -> None:
        self.foot_state.configure(text=f"● Идёт: {title}" if running else "● Команда не идёт", text_color=ACCENT if running else MUTED)
        self.log_title.configure(text=f"ЖУРНАЛ — {title.upper()}" if running else "ЖУРНАЛ")
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
            self.ask(event[1], [("", "Готово — браузер закрыт" if "Готово" in event[1] else "Продолжить")])
        elif kind == "exit":
            self.clear_question()
            self.write([("Команда завершена." if event[1] == 0 else f"Команда завершилась с ошибкой (код {event[1]}).",
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
        self.write([("Остановлено. Всё сделанное сохранено — можно запустить снова.", "93")])

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

    def browse(self, variable: tk.StringVar) -> None:
        folder = filedialog.askdirectory(initialdir=variable.get() or str(Path.home()), parent=self)
        if folder:
            variable.set(folder)

    def save_settings(self) -> None:
        data = self.settings_data
        root = self.var_root.get().strip()
        if root and not (Path(root) / "anime-paths.json").exists():
            self.settings_note.configure(text="В этой папке нет anime-paths.json — это не папка коллекции.", text_color=RED)
            return
        try:
            batch = int(self.var_batch.get())
            if batch < 1:
                raise ValueError
        except ValueError:
            self.settings_note.configure(text="Размер пачки — целое число больше нуля.", text_color=RED)
            return
        if root:
            data["collection"] = root.replace("\\", "/")
        data["batch_size"] = batch
        pinterest = data.setdefault("pinterest", {})
        pinterest["user"] = self.var_user.get().strip()
        pinterest["target"] = self.var_target.get().strip()
        pinterest["browser_cookies"] = self.var_cookies.get().strip()
        # keep: «только хранить» — доска обновляется с Pinterest, раскладка её не трогает.
        pinterest["boards"] = [{"name": n.get().strip(), "slug": s.get().strip(), **({"keep": True} if k.get() else {})}
                               for n, s, _, k in self.board_rows if n.get().strip() and s.get().strip()]
        collection.save_settings(data)
        self.settings_note.configure(text="Сохранено в config.json.", text_color=GREEN)
        self.refresh()

    def open_root(self) -> None:
        if self.snapshot and not self.snapshot.error:
            os.startfile(str(self.snapshot.root))  # noqa: S606 — открыть папку в проводнике


def main() -> int:
    page = sys.argv[sys.argv.index("--page") + 1] if "--page" in sys.argv else "overview"
    App(page).mainloop()
    return 0

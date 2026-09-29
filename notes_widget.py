from __future__ import annotations

import argparse
import ctypes
import os
import re
import sys
import tkinter as tk
import uuid
import webbrowser
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from tkinter import messagebox
from typing import Any, Callable

from PIL import Image

from keep_sync import KeepSyncClient, NoteRecord, SettingsStore
from token_flow import detect_google_email, obtain_master_token
from windows_integration import WindowsStartup


APP_NAME = "StickyOmelet"
APP_ID = "StickyOmelet.justdataplease.1"
TOKEN_HELP_URL = "https://gkeepapi.readthedocs.io/en/latest/#obtaining-a-master-token"
PROJECT_URL = "https://justdataplease.com"
MIN_WINDOW_WIDTH = 340
MIN_WINDOW_HEIGHT = 420
BUBBLE_SIZE = 64
CLICK_AWAY_POLL_MS = 125


class Palette:
    BG = "#0D1422"
    PANEL = "#162135"
    PANEL_HOVER = "#1D2A41"
    PANEL_PRESSED = "#263650"
    INPUT = "#0B1220"
    BORDER = "#293750"
    BORDER_FOCUS = "#DDB84F"
    TEXT = "#F8FAFC"
    MUTED = "#A4B1C3"
    DIM = "#6F7E94"
    ACCENT = "#F4C95D"
    ACCENT_HOVER = "#FFDA78"
    ACCENT_TEXT = "#33280B"
    ACCENT_SOFT = "#41371D"
    DANGER = "#F87171"
    DANGER_BG = "#40242D"
    DANGER_HOVER = "#55303A"
    DANGER_PRESSED = "#5A2935"
    GREEN = "#4ADE80"
    ACCENT_SOFT_HOVER = "#514523"

    KEEP_COLORS = {
        "White": "#CBD5E1",
        "Yellow": "#F4C95D",
        "Green": "#66C88A",
        "Teal": "#52C7B8",
        "Blue": "#61A9E8",
        "DarkBlue": "#6B8FE3",
        "Purple": "#A785E6",
        "Pink": "#E884B5",
        "Red": "#E87979",
        "Orange": "#E99A56",
        "Brown": "#AA8069",
        "Gray": "#8491A3",
    }

    THEMES = {
        "dark": {
            "BG": "#0D1422", "PANEL": "#162135", "PANEL_HOVER": "#1D2A41", "PANEL_PRESSED": "#263650",
            "INPUT": "#0B1220", "BORDER": "#293750", "BORDER_FOCUS": "#DDB84F", "TEXT": "#F8FAFC",
            "MUTED": "#A4B1C3", "DIM": "#6F7E94", "ACCENT": "#F4C95D", "ACCENT_HOVER": "#FFDA78",
            "ACCENT_TEXT": "#33280B", "ACCENT_SOFT": "#41371D", "ACCENT_SOFT_HOVER": "#514523",
            "DANGER": "#F87171", "DANGER_BG": "#40242D", "DANGER_HOVER": "#55303A", "DANGER_PRESSED": "#5A2935",
            "GREEN": "#4ADE80",
        },
        "light": {
            "BG": "#F4F7FB", "PANEL": "#E7EDF6", "PANEL_HOVER": "#DCE5F1", "PANEL_PRESSED": "#CDD9E9",
            "INPUT": "#FFFFFF", "BORDER": "#CBD5E1", "BORDER_FOCUS": "#C99B20", "TEXT": "#172033",
            "MUTED": "#52627A", "DIM": "#7A879B", "ACCENT": "#E9B83F", "ACCENT_HOVER": "#DDAA2F",
            "ACCENT_TEXT": "#33280B", "ACCENT_SOFT": "#FFF1BD", "ACCENT_SOFT_HOVER": "#FFE6A1",
            "DANGER": "#C2414C", "DANGER_BG": "#FDE7EA", "DANGER_HOVER": "#F8CBD1", "DANGER_PRESSED": "#F3B9C1",
            "GREEN": "#218A52",
        },
    }

    @classmethod
    def snapshot(cls) -> dict[str, str]:
        return {key: str(getattr(cls, key)) for key in cls.THEMES["dark"]}

    @classmethod
    def apply_theme(cls, name: str) -> str:
        selected = name if name in cls.THEMES else "dark"
        for key, value in cls.THEMES[selected].items():
            setattr(cls, key, value)
        return selected


def note_title(note: NoteRecord) -> str:
    title = note.title.strip()
    if title:
        return title
    lines = note.body.strip().splitlines()
    return lines[0][:55].lstrip("☐☑ ") if lines else "Untitled note"


def friendly_time(value: datetime) -> str:
    then = value.astimezone()
    now = datetime.now(timezone.utc).astimezone()
    delta = now - then
    if delta.total_seconds() < 60:
        return "now"
    if delta.total_seconds() < 3600:
        return f"{max(1, int(delta.total_seconds() // 60))}m"
    if then.date() == now.date():
        return then.strftime("%H:%M")
    if (now.date() - then.date()).days == 1:
        return "Yesterday"
    if then.year == now.year:
        return then.strftime("%d %b")
    return then.strftime("%d %b %Y")


def clamp_bubble_position(x: int, y: int, screen_width: int, screen_height: int) -> tuple[int, int]:
    """Keep the draggable bubble fully visible on the primary display."""
    return clamp_bubble_to_work_area(x, y, (0, 0, screen_width, screen_height))


def clamp_bubble_to_work_area(x: int, y: int, work_area: tuple[int, int, int, int]) -> tuple[int, int]:
    """Keep the draggable bubble fully visible inside a monitor's work area."""
    left, top, right, bottom = work_area
    return (
        min(max(left, x), max(left, right - BUBBLE_SIZE)),
        min(max(top, y), max(top, bottom - BUBBLE_SIZE)),
    )


def clamp_window_position(
    x: int,
    y: int,
    width: int,
    height: int,
    work_area: tuple[int, int, int, int],
) -> tuple[int, int]:
    """Keep a window's top-left corner inside a monitor work area.

    The widget has no native title bar, so a window dragged past the screen
    edge could not be dragged back. Clamping keeps it reachable.
    """
    left, top, right, bottom = work_area
    return (
        min(max(left, x), max(left, right - width)),
        min(max(top, y), max(top, bottom - height)),
    )


def clamp_window_geometry(geometry: str, work_area: tuple[int, int, int, int]) -> str:
    """Keep a saved window geometry fully inside the selected monitor work area."""
    match = re.fullmatch(r"(\d+)x(\d+)([+-]\d+)([+-]\d+)", geometry)
    if not match:
        return geometry
    width, height, x, y = (int(value) for value in match.groups())
    x, y = clamp_window_position(x, y, width, height, work_area)
    return f"{width}x{height}{x:+d}{y:+d}"


def clamp_window_size(width: int, height: int) -> tuple[int, int]:
    return max(MIN_WINDOW_WIDTH, width), max(MIN_WINDOW_HEIGHT, height)


def selection_colors() -> dict[str, str]:
    return {"selectbackground": Palette.ACCENT_SOFT, "selectforeground": "#000000"}


class HoverButton(tk.Label):
    def __init__(
        self,
        master: tk.Misc,
        text: str,
        command: Callable[[], None],
        *,
        bg: str = Palette.PANEL,
        hover: str = Palette.PANEL_HOVER,
        fg: str = Palette.MUTED,
        font: tuple[str, int, str] | tuple[str, int] = ("Segoe UI", 10),
        padx: int = 10,
        pady: int = 7,
        pressed: str | None = None,
        anchor: str = "center",
        image: tk.PhotoImage | None = None,
    ) -> None:
        super().__init__(master, text=text, image=image, bg=bg, fg=fg, font=font, padx=padx, pady=pady, cursor="hand2", takefocus=True, anchor=anchor)
        self.normal_bg = bg
        self.hover_bg = hover
        self._custom_pressed = pressed is not None
        self.pressed_bg = pressed or (Palette.ACCENT_SOFT if bg == Palette.ACCENT else Palette.PANEL_PRESSED)
        self.command = command
        self._pressed = False
        self.bind("<Enter>", lambda _event: self.configure(bg=self.hover_bg))
        self.bind("<Leave>", self._leave)
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Return>", lambda _event: self.command())
        self.bind("<space>", lambda _event: self.command())

    def _leave(self, _event: tk.Event) -> None:
        self._pressed = False
        self.configure(bg=self.normal_bg)

    def _press(self, _event: tk.Event) -> None:
        self._pressed = True
        self.configure(bg=self.pressed_bg)

    def _release(self, event: tk.Event) -> None:
        was_pressed = self._pressed
        self._pressed = False
        inside = 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height()
        self.configure(bg=self.hover_bg if inside else self.normal_bg)
        if was_pressed and inside:
            self.command()

    def set_colors(self, bg: str, hover: str, fg: str | None = None) -> None:
        self.normal_bg = bg
        self.hover_bg = hover
        if not self._custom_pressed:
            self.pressed_bg = Palette.ACCENT_SOFT if bg == Palette.ACCENT else Palette.PANEL_PRESSED
        options: dict[str, Any] = {"bg": bg}
        if fg:
            options["fg"] = fg
        self.configure(**options)


class ThemeChoice(tk.Frame):
    def __init__(self, master: tk.Misc, text: str, active: bool, command: Callable[[], None]) -> None:
        super().__init__(master, bg=Palette.PANEL, cursor="hand2", takefocus=True)
        self.command = command
        self._pressed = False
        color = Palette.ACCENT if active else Palette.TEXT
        self.marker = tk.Label(self, text="✓" if active else "", width=2, anchor="center", bg=Palette.PANEL, fg=Palette.ACCENT, font=("Segoe UI Semibold", 9), cursor="hand2")
        self.marker.pack(side="left", padx=(8, 2), pady=8)
        self.label = tk.Label(self, text=text, anchor="w", bg=Palette.PANEL, fg=color, font=("Segoe UI Semibold", 9), cursor="hand2")
        self.label.pack(side="left", fill="x", expand=True, padx=(0, 10), pady=8)
        for widget in (self, self.marker, self.label):
            widget.bind("<Enter>", self._enter)
            widget.bind("<Leave>", self._leave)
            widget.bind("<ButtonPress-1>", self._press)
            widget.bind("<ButtonRelease-1>", self._release)
        self.bind("<Return>", lambda _event: self.command())
        self.bind("<space>", lambda _event: self.command())

    def _set_background(self, color: str) -> None:
        self.configure(bg=color)
        self.marker.configure(bg=color)
        self.label.configure(bg=color)

    def _enter(self, _event: tk.Event) -> None:
        self._set_background(Palette.PANEL_HOVER)

    def _leave(self, _event: tk.Event) -> None:
        x, y = self.winfo_pointerxy()
        inside = self.winfo_rootx() <= x < self.winfo_rootx() + self.winfo_width() and self.winfo_rooty() <= y < self.winfo_rooty() + self.winfo_height()
        if not inside:
            self._pressed = False
            self._set_background(Palette.PANEL)

    def _press(self, _event: tk.Event) -> None:
        self._pressed = True
        self._set_background(Palette.PANEL_PRESSED)

    def _release(self, _event: tk.Event) -> None:
        x, y = self.winfo_pointerxy()
        inside = self.winfo_rootx() <= x < self.winfo_rootx() + self.winfo_width() and self.winfo_rooty() <= y < self.winfo_rooty() + self.winfo_height()
        was_pressed = self._pressed
        self._pressed = False
        self._set_background(Palette.PANEL_HOVER if inside else Palette.PANEL)
        if was_pressed and inside:
            self.command()


class Tooltip:
    def __init__(self, widget: tk.Widget, text: str) -> None:
        self.widget = widget
        self.text = text
        self.job: str | None = None
        self.tip: tk.Toplevel | None = None
        widget.bind("<Enter>", self._queue, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<ButtonPress-1>", self.hide, add="+")

    def _queue(self, _event: tk.Event) -> None:
        self.hide()
        self.job = self.widget.after(550, self.show)

    def show(self) -> None:
        self.job = None
        if self.tip or not self.widget.winfo_exists():
            return
        self.tip = tk.Toplevel(self.widget)
        self.tip.overrideredirect(True)
        self.tip.attributes("-topmost", True)
        label = tk.Label(self.tip, text=self.text, bg=Palette.PANEL_PRESSED, fg=Palette.TEXT, font=("Segoe UI", 8), padx=8, pady=5, relief="solid", bd=1)
        label.pack()
        x = self.widget.winfo_rootx() + self.widget.winfo_width() // 2 - label.winfo_reqwidth() // 2
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 5
        self.tip.geometry(f"+{max(4, x)}+{y}")

    def hide(self, _event: tk.Event | None = None) -> None:
        if self.job:
            try:
                self.widget.after_cancel(self.job)
            except tk.TclError:
                pass
            self.job = None
        if self.tip:
            self.tip.destroy()
            self.tip = None


class UndoHistory:
    """Snapshot-based undo/redo history for a note's body text.

    Tkinter's built-in Text undo stack lives inside the widget, so it is thrown
    away every time the editor is torn down and rebuilt (a refresh from Google
    Keep, a colour change, pinning a note…). That is how earlier edits could
    disappear with no way back. Keeping the history here, one per note id, lets
    the user step back many edits even across those rebuilds.
    """

    def __init__(self, initial: str = "", capacity: int = 300) -> None:
        self.capacity = max(2, capacity)
        self.snapshots: list[str] = [initial]
        self.index = 0

    @property
    def current(self) -> str:
        return self.snapshots[self.index]

    def record(self, text: str) -> None:
        """Add a new state, dropping any redo tail and the oldest overflow."""
        if text == self.snapshots[self.index]:
            return
        del self.snapshots[self.index + 1 :]
        self.snapshots.append(text)
        if len(self.snapshots) > self.capacity:
            del self.snapshots[: len(self.snapshots) - self.capacity]
        self.index = len(self.snapshots) - 1

    def can_undo(self) -> bool:
        return self.index > 0

    def can_redo(self) -> bool:
        return self.index < len(self.snapshots) - 1

    def undo(self) -> str | None:
        if not self.can_undo():
            return None
        self.index -= 1
        return self.snapshots[self.index]

    def redo(self) -> str | None:
        if not self.can_redo():
            return None
        self.index += 1
        return self.snapshots[self.index]


class NotesWidget:
    def __init__(self, root: tk.Tk, requested_note: str | None = None) -> None:
        self.root = root
        self.settings = SettingsStore()
        self.theme = Palette.apply_theme(str(self.settings.data.get("theme", "dark")))
        self.client = KeepSyncClient()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="keep-sync")
        self.records: list[NoteRecord] = []
        self.selected_note: NoteRecord | None = None
        self.requested_note = requested_note
        self.current_mode = "connect"
        self.save_job: str | None = None
        self.snapshot_job: str | None = None
        self.search_job: str | None = None
        self.scroll_job: str | None = None
        self.undo_histories: dict[str, UndoHistory] = {}
        self.body_history: UndoHistory | None = None
        self._suspend_body_history = False
        self.scroll_target = 0.0
        self.card_hover_jobs: dict[str, str] = {}
        self.list_filter = str(self.settings.data.get("list_filter", "all"))
        self.drag_start: tuple[int, int, int, int] | None = None
        self.resize_start: tuple[int, int, int, int] | None = None
        self.bubble_drag_start: tuple[int, int, int, int] | None = None
        self.bubble_drag_position: tuple[int, int] | None = None
        self.bubble_canvas: tk.Canvas | None = None
        self.bubble_image: tk.PhotoImage | None = None
        self.brand_image: tk.PhotoImage | None = None
        self.bubble_moved = False
        self.minimized_to_bubble = False
        self.normal_geometry = ""
        self.normal_ex_style: int | None = None
        self.normal_class_style: int | None = None
        self._bubble_surface_cache: dict[tuple[int, int], bytes] = {}
        self._bubble_repaint_job: str | None = None
        self.pending_operations = 0
        self.closing = False
        self.click_away_job: str | None = None
        # The window starts in the foreground on a normal launch. Treat it as
        # armed immediately so a very fast click into another app is not missed.
        self._own_window_was_foreground = True
        self.editor_revision = 0
        self.new_menu: tk.Toplevel | None = None
        self.settings_menu: tk.Toplevel | None = None
        self.checklist_items: list[dict[str, Any]] = []
        self.checklist_entries: dict[str, tk.Entry] = {}

        self._configure_root()
        self._build_shell()
        self._bind_shortcuts()
        self.root.after(50, self._apply_windows_style)
        self.click_away_job = self.root.after(CLICK_AWAY_POLL_MS, self._monitor_click_away)

        credentials = self.settings.credentials()
        if credentials:
            email, token, device_id = credentials
            self.show_connecting(email)
            self._authenticate(email, token, remember=False, device_id=device_id)
        else:
            self.show_connect()

    def _configure_root(self) -> None:
        self.root.title(APP_NAME)
        self.root.configure(bg=Palette.BORDER)
        self._set_window_icon()
        self.root.minsize(MIN_WINDOW_WIDTH, MIN_WINDOW_HEIGHT)
        # A monitor that has been unplugged or rearranged since the last run can
        # leave the saved position outside every screen, and there is no title
        # bar to drag the window back with.
        geometry = str(self.settings.data.get("geometry", "400x540+80+80"))
        self.root.geometry(clamp_window_geometry(geometry, self._work_area_for_geometry(geometry)))
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", bool(self.settings.data.get("topmost", True)))
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def _set_window_icon(self) -> None:
        icon_path = self._asset_path("stickyomelet.ico")
        try:
            if icon_path.exists():
                self.root.iconbitmap(default=str(icon_path))
        except (OSError, tk.TclError):
            pass

    @staticmethod
    def _asset_path(name: str) -> Path:
        base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
        return base / "assets" / name

    def _build_shell(self) -> None:
        self.border = tk.Frame(self.root, bg=Palette.BORDER, padx=1, pady=1)
        self.border.pack(fill="both", expand=True)
        self.shell = tk.Frame(self.border, bg=Palette.BG)
        self.shell.pack(fill="both", expand=True)

        self.header = tk.Frame(self.shell, bg=Palette.BG, height=49)
        self.header.pack(fill="x")
        self.header.pack_propagate(False)
        self.header.bind("<ButtonPress-1>", self._start_drag)
        self.header.bind("<B1-Motion>", self._drag)
        self.header.bind("<ButtonRelease-1>", self._end_drag)

        brand = tk.Frame(self.header, bg=Palette.BG)
        brand.pack(side="left", padx=(7, 0), fill="y")
        brand.bind("<ButtonPress-1>", self._start_drag)
        brand.bind("<B1-Motion>", self._drag)
        brand.bind("<ButtonRelease-1>", self._end_drag)
        try:
            self.brand_image = tk.PhotoImage(file=str(self._asset_path("dot-mark.png")))
        except tk.TclError:
            self.brand_image = None
        self.brand_mark = tk.Label(
            brand,
            text="●" if self.brand_image is None else "",
            image=self.brand_image if self.brand_image is not None else "",
            bg=Palette.BG,
            fg=Palette.ACCENT,
            font=("Segoe UI Emoji", 12),
            padx=6,
            pady=9,
            cursor="fleur",
        )
        self.brand_mark.pack(side="left", pady=2)
        # The dot is a drag handle for the window, not a link.
        self.brand_mark.bind("<ButtonPress-1>", self._start_drag)
        self.brand_mark.bind("<B1-Motion>", self._drag)
        self.brand_mark.bind("<ButtonRelease-1>", self._end_drag)
        Tooltip(self.brand_mark, "Drag to move StickyOmelet")
        self.view_toggle_button = HoverButton(brand, "⇄", self.toggle_mode, bg=Palette.BG, hover=Palette.PANEL_HOVER, fg=Palette.DIM, font=("Segoe UI Symbol", 12), padx=6, pady=8)
        Tooltip(self.view_toggle_button, "Toggle notes list and selected note (Ctrl+L)")

        self.window_buttons = tk.Frame(self.header, bg=Palette.BG)
        self.window_buttons.pack(side="right", padx=(0, 4), fill="y")
        header_icon_font = ("Segoe UI Symbol", 11)
        self.add_button = HoverButton(self.window_buttons, "✚", self.show_new_menu, bg=Palette.BG, hover=Palette.PANEL_HOVER, fg=Palette.ACCENT, font=header_icon_font, padx=4, pady=9)
        self.sync_button = HoverButton(self.window_buttons, "↻", self.refresh_from_keep, bg=Palette.BG, hover=Palette.PANEL_HOVER, fg=Palette.DIM, font=header_icon_font, padx=4, pady=9)
        self.settings_button = HoverButton(self.window_buttons, "☰", self.show_settings_menu, bg=Palette.BG, hover=Palette.PANEL_HOVER, fg=Palette.MUTED, font=header_icon_font, padx=4, pady=9)
        for button in (self.add_button, self.sync_button, self.settings_button):
            button.configure(width=2)
        self.settings_button.pack(side="left")
        self.pin_button = HoverButton(
            self.window_buttons,
            "◆" if self.settings.data.get("topmost", True) else "◇",
            self.toggle_topmost,
            bg=Palette.BG,
            hover=Palette.PANEL_HOVER,
            fg=Palette.ACCENT if self.settings.data.get("topmost", True) else Palette.DIM,
            font=header_icon_font,
            padx=4,
            pady=9,
        )
        self.pin_button.configure(width=2)
        self.pin_button.pack(side="left")
        self.minimize_button = HoverButton(self.window_buttons, "−", self.minimize, bg=Palette.BG, hover=Palette.PANEL_HOVER, fg=Palette.MUTED, font=header_icon_font, padx=4, pady=9)
        self.minimize_button.configure(width=2)
        self.minimize_button.pack(side="left")
        self.close_button = HoverButton(self.window_buttons, "×", self.close, bg=Palette.BG, hover=Palette.DANGER_BG, fg=Palette.MUTED, font=header_icon_font, padx=4, pady=9, pressed=Palette.DANGER_PRESSED)
        self.close_button.configure(width=2)
        self.close_button.pack(side="left")
        Tooltip(self.sync_button, "Refresh from Google Keep (F5)")
        Tooltip(self.settings_button, "Appearance and startup")
        Tooltip(self.pin_button, "Toggle always on top")
        Tooltip(self.minimize_button, "Minimize to dot bubble")
        Tooltip(self.close_button, "Close StickyOmelet")
        Tooltip(self.add_button, "Create a text note or checklist")

        self.content = tk.Frame(self.shell, bg=Palette.BG)

        self.footer = tk.Frame(self.shell, bg=Palette.BG, height=31)
        self.footer.pack(fill="x", side="bottom", padx=(16, 7), pady=(5, 5))
        self.footer.pack_propagate(False)
        self.grip = tk.Label(self.footer, text="◢", bg=Palette.BG, fg=Palette.DIM, font=("Segoe UI Symbol", 10), cursor="size_nw_se")
        self.grip.pack(side="right", padx=(8, 0), pady=4)
        self.grip.bind("<ButtonPress-1>", self._start_resize)
        self.grip.bind("<B1-Motion>", self._resize)
        Tooltip(self.grip, "Drag to resize width and height")
        self.sync_identity = tk.Label(self.footer, text="", bg=Palette.BG, fg=Palette.GREEN, font=("Segoe UI Semibold", 7), anchor="e")
        self.sync_identity.pack(side="right", padx=(6, 0), pady=6)
        Tooltip(self.sync_identity, "Google Keep account and synchronization status")
        self.status = tk.Label(self.footer, text="", bg=Palette.BG, fg=Palette.DIM, font=("Segoe UI", 8), anchor="w")
        self.status.pack(side="left", fill="x", expand=True, pady=6)

        # Pack the expanding content last so it yields space to the footer when
        # the window is made shorter.
        self.content.pack(fill="both", expand=True, padx=14)

    def _set_sync_identity(self, state: str, color: str, email: str | None = None) -> None:
        account = (email if email is not None else self.client.email).strip()
        self.sync_identity.configure(text=f"{state}  •  {account}" if account else "", fg=color)

    def open_project_website(self) -> None:
        webbrowser.open(PROJECT_URL)

    def _bind_shortcuts(self) -> None:
        self.root.bind_all("<Control-n>", lambda _event: self.new_note() if self.client.connected else None)
        self.root.bind_all("<Control-Shift-N>", lambda _event: self.new_checklist() if self.client.connected else None)
        self.root.bind_all("<Control-l>", lambda _event: self.toggle_mode() if self.client.connected else None)
        self.root.bind_all("<Control-f>", lambda _event: self.focus_search() if self.client.connected else None)
        self.root.bind_all("<Control-s>", lambda _event: self.flush_save())
        self.root.bind_all("<Control-plus>", lambda _event: self.change_font_size(1))
        self.root.bind_all("<Control-equal>", lambda _event: self.change_font_size(1))
        self.root.bind_all("<Control-minus>", lambda _event: self.change_font_size(-1))
        self.root.bind_all("<Control-0>", lambda _event: self.reset_font_size())
        self.root.bind_all("<F5>", lambda _event: self.refresh_from_keep())
        self.root.bind_all("<Escape>", lambda _event: self.show_list() if self.current_mode == "note" else None)
        self.root.bind("<Map>", self._restore_override)
        # When "always on top" is off a borderless window has no taskbar button,
        # so clicking another app would bury it with no way back. Collapse to the
        # always-on-top dot bubble instead of letting it vanish.
        self.root.bind("<Deactivate>", self._on_root_deactivate)

    def _clear_content(self) -> None:
        self.root.unbind_all("<MouseWheel>")
        if self.scroll_job:
            try:
                self.root.after_cancel(self.scroll_job)
            except tk.TclError:
                pass
            self.scroll_job = None
        for job in self.card_hover_jobs.values():
            try:
                self.root.after_cancel(job)
            except tk.TclError:
                pass
        self.card_hover_jobs.clear()
        for child in self.content.winfo_children():
            child.destroy()

    def _show_connected_controls(self, visible: bool) -> None:
        self.view_toggle_button.pack_forget()
        self.add_button.pack_forget()
        self.sync_button.pack_forget()
        if visible:
            self.view_toggle_button.pack(side="left", padx=(2, 0), pady=2)
            self.add_button.pack(side="left", before=self.settings_button)
            self.sync_button.pack(side="left", before=self.pin_button)

    def show_connect(self, error: str = "") -> None:
        self.current_mode = "connect"
        self._show_connected_controls(False)
        self._set_sync_identity("", Palette.DIM, "")
        self._clear_content()
        panel = tk.Frame(self.content, bg=Palette.BG)
        panel.pack(fill="both", expand=True, padx=6, pady=(18, 8))
        tk.Label(panel, text="Connect Google Keep", bg=Palette.BG, fg=Palette.TEXT, font=("Segoe UI Semibold", 18), anchor="w").pack(fill="x")
        tk.Label(panel, text="Your real Keep notes, inside this custom widget.", bg=Palette.BG, fg=Palette.MUTED, font=("Segoe UI", 10), anchor="w").pack(fill="x", pady=(5, 20))

        tk.Label(panel, text="GOOGLE ACCOUNT EMAIL", bg=Palette.BG, fg=Palette.DIM, font=("Segoe UI Semibold", 8), anchor="w").pack(fill="x", pady=(0, 5))
        email_shell = tk.Frame(panel, bg=Palette.INPUT, highlightthickness=1, highlightbackground=Palette.BORDER)
        email_shell.pack(fill="x")
        known_email = str(self.settings.data.get("email", "")) or detect_google_email()
        self.email_var = tk.StringVar(value=known_email)
        self.email_entry = tk.Entry(email_shell, textvariable=self.email_var, bg=Palette.INPUT, fg=Palette.TEXT, insertbackground=Palette.TEXT, relief="flat", bd=0, font=("Segoe UI", 10), **selection_colors())
        self.email_entry.pack(fill="x", padx=12, pady=10)

        self.browser_connect_button = HoverButton(panel, "Connect securely through Chrome", self.connect_through_chrome, bg=Palette.ACCENT, hover=Palette.ACCENT_HOVER, fg=Palette.ACCENT_TEXT, font=("Segoe UI Semibold", 10), padx=14, pady=11)
        self.browser_connect_button.pack(fill="x", pady=(13, 4))
        tk.Label(panel, text="Recommended • sign in with Google in a separate Chrome window", bg=Palette.BG, fg=Palette.MUTED, font=("Segoe UI", 8), anchor="w").pack(fill="x")

        divider = tk.Frame(panel, bg=Palette.BG)
        divider.pack(fill="x", pady=(12, 9))
        tk.Frame(divider, bg=Palette.BORDER, height=1).pack(side="left", fill="x", expand=True, pady=7)
        tk.Label(divider, text="  OR USE AN EXISTING TOKEN  ", bg=Palette.BG, fg=Palette.DIM, font=("Segoe UI Semibold", 7)).pack(side="left")
        tk.Frame(divider, bg=Palette.BORDER, height=1).pack(side="left", fill="x", expand=True, pady=7)

        tk.Label(panel, text="KEEP MASTER TOKEN", bg=Palette.BG, fg=Palette.DIM, font=("Segoe UI Semibold", 8), anchor="w").pack(fill="x", pady=(14, 5))
        token_shell = tk.Frame(panel, bg=Palette.INPUT, highlightthickness=1, highlightbackground=Palette.BORDER)
        token_shell.pack(fill="x")
        self.token_var = tk.StringVar()
        self.token_entry = tk.Entry(token_shell, textvariable=self.token_var, show="•", bg=Palette.INPUT, fg=Palette.TEXT, insertbackground=Palette.TEXT, relief="flat", bd=0, font=("Segoe UI", 10), **selection_colors())
        self.token_entry.pack(side="left", fill="x", expand=True, padx=(12, 5), pady=10)
        self.reveal_button = HoverButton(token_shell, "Show", self.toggle_token_visibility, bg=Palette.INPUT, hover=Palette.PANEL_HOVER, fg=Palette.MUTED, font=("Segoe UI Semibold", 8), padx=9, pady=9)
        self.reveal_button.pack(side="right", padx=(0, 4))

        help_row = tk.Frame(panel, bg=Palette.BG)
        help_row.pack(fill="x", pady=(8, 15))
        help_link = tk.Label(help_row, text="How to obtain a Keep token ↗", bg=Palette.BG, fg=Palette.ACCENT, font=("Segoe UI Semibold", 8), cursor="hand2")
        help_link.pack(side="left")
        help_link.bind("<Button-1>", lambda _event: webbrowser.open(TOKEN_HELP_URL))

        security = tk.Frame(panel, bg=Palette.PANEL, highlightthickness=1, highlightbackground=Palette.BORDER)
        security.pack(fill="x", pady=(0, 14))
        tk.Label(security, text="SECURED BY WINDOWS", bg=Palette.PANEL, fg=Palette.GREEN, font=("Segoe UI Semibold", 8), anchor="w").pack(fill="x", padx=12, pady=(10, 3))
        tk.Label(security, text="The token is encrypted with Windows DPAPI for your user account. It is never stored as plain text or sent anywhere except Google.", bg=Palette.PANEL, fg=Palette.MUTED, font=("Segoe UI", 8), justify="left", wraplength=345, anchor="w").pack(fill="x", padx=12, pady=(0, 10))

        self.connect_error = tk.Label(panel, text=error, bg=Palette.BG, fg=Palette.DANGER, font=("Segoe UI", 8), anchor="w", wraplength=350, justify="left")
        self.connect_error.pack(fill="x", pady=(0, 7))
        self.connect_button = HoverButton(panel, "Connect and load my notes", self.connect_from_form, bg=Palette.ACCENT, hover=Palette.ACCENT_HOVER, fg=Palette.ACCENT_TEXT, font=("Segoe UI Semibold", 10), padx=14, pady=11)
        self.connect_button.pack(fill="x")
        self.status.configure(text="Not connected")

    def show_connecting(self, email: str, message: str = "Loading your Google Keep notes") -> None:
        self.current_mode = "connecting"
        self._show_connected_controls(False)
        self._set_sync_identity("Connecting", Palette.ACCENT, email)
        self._clear_content()
        holder = tk.Frame(self.content, bg=Palette.BG)
        holder.pack(fill="both", expand=True)
        tk.Label(holder, text="↻", bg=Palette.BG, fg=Palette.ACCENT, font=("Segoe UI Symbol", 28)).pack(pady=(105, 12))
        tk.Label(holder, text=message, bg=Palette.BG, fg=Palette.TEXT, font=("Segoe UI Semibold", 13), wraplength=350, justify="center").pack()
        tk.Label(holder, text=email, bg=Palette.BG, fg=Palette.MUTED, font=("Segoe UI", 9)).pack(pady=(6, 0))
        self.status.configure(text="Connecting securely…")

    def toggle_token_visibility(self) -> None:
        hidden = self.token_entry.cget("show") != ""
        self.token_entry.configure(show="" if hidden else "•")
        self.reveal_button.configure(text="Hide" if hidden else "Show")

    def connect_from_form(self) -> None:
        email = self.email_var.get().strip()
        token = self.token_var.get().strip()
        if "@" not in email or not token:
            self.connect_error.configure(text="Enter your Google email and Keep master token.")
            return
        self.show_connecting(email)
        self._authenticate(email, token, remember=True)

    def connect_through_chrome(self) -> None:
        email = self.email_var.get().strip()
        if "@" not in email:
            self.connect_error.configure(text="Enter your Google account email first.")
            self.email_entry.focus_set()
            return
        self.show_connecting(email, "Complete Google sign-in in the Chrome window")
        self.status.configure(text="Waiting for Google sign-in…")

        def token_ready(result: tuple[str, str]) -> None:
            master_token, device_id = result
            self.status.configure(text="Token received • loading Keep notes…")
            self._authenticate(email, master_token, remember=True, device_id=device_id)

        def failed(error: Exception) -> None:
            message = str(error).strip() or error.__class__.__name__
            self.show_connect(f"Google sign-in did not complete: {message}")

        self._run(lambda: obtain_master_token(email), token_ready, failed)

    def _authenticate(self, email: str, token: str, remember: bool, device_id: str = "") -> None:
        def connected(records: list[NoteRecord]) -> None:
            if remember:
                try:
                    self.settings.save_credentials(email, token, device_id)
                except OSError as error:
                    self.show_connect(f"Connected, but Windows could not protect the token: {error}")
                    return
            self.records = records
            self._show_connected_controls(True)
            target = self._find_record(self.requested_note) or self._find_record(str(self.settings.data.get("selected_note_id", "")))
            if target and (self.requested_note or self.settings.data.get("mode") == "note"):
                self.open_note(target)
            else:
                self.show_list()
            self._set_sync_identity("Synced", Palette.GREEN, email)
            self.status.configure(text="Google Keep ready")

        def failed(error: Exception) -> None:
            self.client.disconnect()
            message = str(error).strip() or error.__class__.__name__
            self.show_connect(f"Google Keep could not connect: {message}")

        self._run(lambda: self.client.authenticate(email, token, device_id), connected, failed)

    def open_account_from_settings(self) -> None:
        self.dismiss_settings_menu()
        self.show_account()

    def show_account(self) -> None:
        if not self.client.connected:
            self.show_connect()
            return
        self.flush_save()
        self.current_mode = "account"
        self._clear_content()
        panel = tk.Frame(self.content, bg=Palette.BG)
        panel.pack(fill="both", expand=True, padx=7, pady=(15, 0))
        tk.Label(panel, text="Google Keep connection", bg=Palette.BG, fg=Palette.TEXT, font=("Segoe UI Semibold", 16), anchor="w").pack(fill="x")
        tk.Label(panel, text=self.client.email, bg=Palette.BG, fg=Palette.GREEN, font=("Segoe UI", 10), anchor="w").pack(fill="x", pady=(6, 20))
        card = tk.Frame(panel, bg=Palette.PANEL, highlightthickness=1, highlightbackground=Palette.BORDER)
        card.pack(fill="x")
        tk.Label(card, text="Two-way synchronization", bg=Palette.PANEL, fg=Palette.TEXT, font=("Segoe UI Semibold", 10), anchor="w").pack(fill="x", padx=13, pady=(12, 4))
        tk.Label(card, text="Changes made here are saved to Google Keep. Use the refresh button after editing the same note on another device.", bg=Palette.PANEL, fg=Palette.MUTED, font=("Segoe UI", 8), justify="left", wraplength=340, anchor="w").pack(fill="x", padx=13, pady=(0, 12))
        HoverButton(panel, "Refresh from Google Keep", self.refresh_from_keep, bg=Palette.ACCENT, hover=Palette.ACCENT_HOVER, fg=Palette.ACCENT_TEXT, font=("Segoe UI Semibold", 9), padx=12, pady=10).pack(fill="x", pady=(15, 8))
        HoverButton(panel, "Disconnect this account", self.disconnect_account, bg=Palette.DANGER_BG, hover=Palette.DANGER_HOVER, fg=Palette.DANGER, font=("Segoe UI Semibold", 9), padx=12, pady=10).pack(fill="x")
        HoverButton(panel, "‹  Back to notes", self.show_list, bg=Palette.BG, hover=Palette.PANEL_HOVER, fg=Palette.MUTED, font=("Segoe UI Semibold", 9), padx=8, pady=7).pack(anchor="w", pady=(14, 0))

    def disconnect_account(self) -> None:
        if not messagebox.askyesno("Disconnect Google Keep", "Remove the saved Google Keep credential from this PC?\n\nYour notes will remain in Google Keep.", parent=self.root):
            return
        self.settings.clear_credentials()
        self.client.disconnect()
        self.records = []
        self.selected_note = None
        self.show_connect()

    def _set_mode_buttons(self, mode: str) -> None:
        self.view_toggle_button.configure(fg=Palette.ACCENT if mode == "note" else Palette.DIM)

    def show_list(self) -> None:
        if not self.client.connected:
            self.show_connect()
            return
        self.flush_save()
        self.status.configure(fg=Palette.DIM)
        self.current_mode = "list"
        self.settings.data["mode"] = "list"
        self.settings.save()
        self._set_mode_buttons("list")
        self._clear_content()

        self.search_frame = tk.Frame(self.content, bg=Palette.INPUT, highlightthickness=1, highlightbackground=Palette.BORDER)
        self.search_frame.pack(fill="x", pady=(0, 10))
        tk.Label(self.search_frame, text="⌕", bg=Palette.INPUT, fg=Palette.DIM, font=("Segoe UI Symbol", 13)).pack(side="left", padx=(10, 4), pady=5)
        self.search_var = tk.StringVar()
        self.search_entry = tk.Entry(self.search_frame, textvariable=self.search_var, bg=Palette.INPUT, fg=Palette.DIM, insertbackground=Palette.TEXT, relief="flat", bd=0, font=("Segoe UI", 9), **selection_colors())
        self.search_entry.pack(side="left", fill="x", expand=True, padx=(2, 4), pady=6)
        self.search_entry.insert(0, "Search Google Keep")
        self.search_entry.bind("<FocusIn>", self._search_focus_in)
        self.search_entry.bind("<FocusOut>", self._search_focus_out)
        self.search_var.trace_add("write", lambda *_args: self._queue_render_notes())
        self.search_clear = HoverButton(self.search_frame, "×", self.clear_search, bg=Palette.INPUT, hover=Palette.PANEL_HOVER, fg=Palette.DIM, font=("Segoe UI", 10), padx=7, pady=4)
        self.search_clear.pack(side="right", padx=(0, 4), pady=2)

        filter_row = tk.Frame(self.content, bg=Palette.BG)
        filter_row.pack(fill="x", pady=(0, 10))
        self.filter_buttons: dict[str, HoverButton] = {}
        for key, label in (("all", "All"), ("pinned", "Pinned"), ("notes", "Notes"), ("lists", "Lists")):
            button = HoverButton(filter_row, label, lambda value=key: self.set_list_filter(value), bg=Palette.PANEL, hover=Palette.PANEL_HOVER, fg=Palette.MUTED, font=("Segoe UI Semibold", 8), padx=10, pady=5)
            button.pack(side="left", padx=(0, 5))
            self.filter_buttons[key] = button
        self.list_count_label = tk.Label(filter_row, text="", bg=Palette.BG, fg=Palette.DIM, font=("Segoe UI", 8))
        self.list_count_label.pack(side="right", pady=6)
        self._update_filter_styles()

        list_shell = tk.Frame(self.content, bg=Palette.BG)
        list_shell.pack(fill="both", expand=True)
        self.list_canvas = tk.Canvas(list_shell, bg=Palette.BG, bd=0, highlightthickness=0, yscrollincrement=1)
        scrollbar = tk.Scrollbar(list_shell, orient="vertical", command=self._scrollbar_move, width=7, bg=Palette.PANEL, troughcolor=Palette.BG, activebackground=Palette.ACCENT, bd=0, highlightthickness=0, relief="flat")
        self.cards = tk.Frame(self.list_canvas, bg=Palette.BG)
        self.card_previews: list[tk.Label] = []
        self.cards_window = self.list_canvas.create_window((0, 0), window=self.cards, anchor="nw")
        self.list_canvas.configure(yscrollcommand=scrollbar.set)
        self.list_canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y", padx=(5, 0))
        self.cards.bind("<Configure>", lambda _event: self.list_canvas.configure(scrollregion=self.list_canvas.bbox("all")))
        self.list_canvas.bind("<Configure>", self._resize_card_area)
        self.list_canvas.bind_all("<MouseWheel>", self._on_mousewheel)
        self.scroll_target = 0.0
        self.render_notes()

    def _search_focus_in(self, _event: tk.Event) -> None:
        self.search_frame.configure(highlightbackground=Palette.BORDER_FOCUS)
        if self.search_entry.get() == "Search Google Keep":
            self.search_entry.delete(0, "end")
            self.search_entry.configure(fg=Palette.TEXT)

    def _search_focus_out(self, _event: tk.Event) -> None:
        self.search_frame.configure(highlightbackground=Palette.BORDER)
        if not self.search_entry.get():
            self.search_entry.insert(0, "Search Google Keep")
            self.search_entry.configure(fg=Palette.DIM)

    def clear_search(self) -> None:
        self.search_entry.delete(0, "end")
        self.search_entry.focus_set()

    def set_list_filter(self, value: str) -> None:
        if value not in {"all", "pinned", "notes", "lists"}:
            value = "all"
        self.list_filter = value
        self.settings.data["list_filter"] = value
        self.settings.save()
        self._update_filter_styles()
        self.render_notes()

    def _update_filter_styles(self) -> None:
        if not hasattr(self, "filter_buttons"):
            return
        for key, button in self.filter_buttons.items():
            if key == self.list_filter:
                button.set_colors(Palette.ACCENT_SOFT, Palette.ACCENT_SOFT_HOVER, Palette.ACCENT)
            else:
                button.set_colors(Palette.PANEL, Palette.PANEL_HOVER, Palette.MUTED)

    def _queue_render_notes(self) -> None:
        if self.search_job:
            self.root.after_cancel(self.search_job)
        self.search_job = self.root.after(80, self.render_notes)

    def _resize_card_area(self, event: tk.Event) -> None:
        self.list_canvas.itemconfigure(self.cards_window, width=event.width)
        wrap = max(220, event.width - 55)
        for preview in self.card_previews:
            if preview.winfo_exists():
                preview.configure(wraplength=wrap)

    def render_notes(self) -> None:
        if not hasattr(self, "cards") or not self.cards.winfo_exists():
            return
        self.search_job = None
        for child in self.cards.winfo_children():
            child.destroy()
        self.card_previews.clear()
        query = self.search_var.get().strip().casefold()
        if query == "search google keep":
            query = ""
        notes = self.records
        if self.list_filter == "pinned":
            notes = [note for note in notes if note.pinned]
        elif self.list_filter == "notes":
            notes = [note for note in notes if not note.is_list]
        elif self.list_filter == "lists":
            notes = [note for note in notes if note.is_list]
        if query:
            notes = [note for note in notes if query in (note_title(note) + " " + note.body + " " + " ".join(note.labels)).casefold()]
        if hasattr(self, "list_count_label") and self.list_count_label.winfo_exists():
            self.list_count_label.configure(text=f"{len(notes)} shown")
        if not notes:
            empty = tk.Frame(self.cards, bg=Palette.BG)
            empty.pack(fill="x", pady=55)
            tk.Label(empty, text="Nothing here yet", bg=Palette.BG, fg=Palette.TEXT, font=("Segoe UI Semibold", 12)).pack()
            tk.Label(empty, text="Change the filter, clear search, or create a note.", bg=Palette.BG, fg=Palette.DIM, font=("Segoe UI", 9)).pack(pady=(6, 0))
            self.status.configure(text=f"{len(self.records)} Google Keep notes")
            return
        for note in notes:
            self._make_note_card(note)
        self.status.configure(text=f"{len(notes)} Google Keep note{'s' if len(notes) != 1 else ''}")

    def _make_note_card(self, note: NoteRecord) -> None:
        card = tk.Frame(self.cards, bg=Palette.PANEL, cursor="hand2", highlightthickness=1, highlightbackground=Palette.BORDER)
        card.pack(fill="x", pady=(0, 8), padx=(0, 1))
        accent = tk.Frame(card, bg=Palette.KEEP_COLORS.get(note.color, Palette.ACCENT), width=4)
        accent.pack(side="left", fill="y")
        inner = tk.Frame(card, bg=Palette.PANEL, cursor="hand2")
        inner.pack(side="left", fill="both", expand=True, padx=14, pady=12)
        heading = tk.Frame(inner, bg=Palette.PANEL, cursor="hand2")
        heading.pack(fill="x")
        title = tk.Label(heading, text=note_title(note), bg=Palette.PANEL, fg=Palette.TEXT, font=("Segoe UI Semibold", 10), anchor="w", cursor="hand2")
        title.pack(side="left", fill="x", expand=True)
        if note.pinned:
            tk.Label(heading, text="◆", bg=Palette.PANEL, fg=Palette.ACCENT, font=("Segoe UI Symbol", 8), cursor="hand2").pack(side="right", padx=(7, 0))
        updated = tk.Label(heading, text=friendly_time(note.updated), bg=Palette.PANEL, fg=Palette.DIM, font=("Segoe UI", 8), cursor="hand2")
        updated.pack(side="right", padx=(8, 0))
        preview_text = " ".join(note.body.strip().split()) or "Empty note"
        if len(preview_text) > 145:
            preview_text = preview_text[:142].rstrip() + "…"
        preview = tk.Label(inner, text=preview_text, bg=Palette.PANEL, fg=Palette.MUTED, font=("Segoe UI", 9), anchor="w", justify="left", wraplength=310, cursor="hand2")
        preview.pack(fill="x", pady=(5, 0))
        self.card_previews.append(preview)
        meta_parts = ["CHECKLIST" if note.is_list else "NOTE"]
        meta_parts.extend(note.labels[:3])
        tk.Label(inner, text="  •  ".join(meta_parts), bg=Palette.PANEL, fg=Palette.DIM, font=("Segoe UI Semibold", 7), anchor="w", cursor="hand2").pack(fill="x", pady=(7, 0))
        for widget in self._descendants(card):
            widget.bind("<ButtonPress-1>", lambda _event, frame=card: self._card_color(frame, Palette.PANEL_PRESSED))
            widget.bind("<ButtonRelease-1>", lambda _event, frame=card, item=note: self._card_release(frame, item))
            widget.bind("<Enter>", lambda _event, frame=card, key=note.id: self._card_enter(frame, key))
            widget.bind("<Leave>", lambda _event, frame=card, key=note.id: self._card_leave(frame, key))

    def _descendants(self, widget: tk.Widget) -> list[tk.Widget]:
        result = [widget]
        for child in widget.winfo_children():
            result.extend(self._descendants(child))
        return result

    def _card_color(self, card: tk.Frame, color: str) -> None:
        if not card.winfo_exists():
            return
        for widget in self._descendants(card):
            if isinstance(widget, tk.Frame) and int(widget.cget("width") or 0) == 4:
                continue
            try:
                widget.configure(bg=color)
            except tk.TclError:
                pass

    def _pointer_inside(self, widget: tk.Widget) -> bool:
        if not widget.winfo_exists():
            return False
        pointer_x = self.root.winfo_pointerx()
        pointer_y = self.root.winfo_pointery()
        left = widget.winfo_rootx()
        top = widget.winfo_rooty()
        return left <= pointer_x < left + widget.winfo_width() and top <= pointer_y < top + widget.winfo_height()

    def _card_enter(self, card: tk.Frame, key: str) -> None:
        job = self.card_hover_jobs.pop(key, None)
        if job:
            try:
                self.root.after_cancel(job)
            except tk.TclError:
                pass
        self._card_color(card, Palette.PANEL_HOVER)

    def _card_leave(self, card: tk.Frame, key: str) -> None:
        def settle() -> None:
            self.card_hover_jobs.pop(key, None)
            if card.winfo_exists() and not self._pointer_inside(card):
                self._card_color(card, Palette.PANEL)

        old_job = self.card_hover_jobs.pop(key, None)
        if old_job:
            try:
                self.root.after_cancel(old_job)
            except tk.TclError:
                pass
        self.card_hover_jobs[key] = self.root.after(24, settle)

    def _card_release(self, card: tk.Frame, note: NoteRecord) -> None:
        if self._pointer_inside(card):
            self._card_color(card, Palette.PANEL_HOVER)
            self.open_note(note)
        elif card.winfo_exists():
            self._card_color(card, Palette.PANEL)

    def open_note(self, note: NoteRecord) -> None:
        if not self.client.connected:
            return
        self.flush_save()
        if self.snapshot_job:
            try:
                self.root.after_cancel(self.snapshot_job)
            except tk.TclError:
                pass
            self.snapshot_job = None
        self.body_history = None
        self.status.configure(fg=Palette.DIM)
        self.selected_note = note
        self.editor_revision = 0
        self.current_mode = "note"
        self.settings.data["mode"] = "note"
        self.settings.data["selected_note_id"] = note.id
        self.settings.save()
        self._set_mode_buttons("note")
        self._clear_content()

        editor_top = tk.Frame(self.content, bg=Palette.BG)
        editor_top.pack(fill="x", pady=(0, 7))
        editor_actions = tk.Frame(editor_top, bg=Palette.BG)
        editor_actions.pack(side="right")
        font_tools = tk.Frame(editor_actions, bg=Palette.PANEL)
        font_tools.pack(side="left", padx=(0, 4))
        font_down_button = HoverButton(font_tools, "A−", lambda: self.change_font_size(-1), bg=Palette.PANEL, hover=Palette.PANEL_HOVER, fg=Palette.MUTED, font=("Segoe UI Semibold", 8), padx=6, pady=5)
        font_down_button.pack(side="left")
        self.font_size_label = tk.Label(font_tools, text=str(self._editor_font_size()), bg=Palette.PANEL, fg=Palette.TEXT, font=("Segoe UI Semibold", 8), width=2, pady=5)
        self.font_size_label.pack(side="left")
        font_up_button = HoverButton(font_tools, "A+", lambda: self.change_font_size(1), bg=Palette.PANEL, hover=Palette.PANEL_HOVER, fg=Palette.MUTED, font=("Segoe UI Semibold", 8), padx=6, pady=5)
        font_up_button.pack(side="left")
        self.pin_note_button = HoverButton(editor_actions, "◆" if note.pinned else "◇", self.toggle_note_pin, bg=Palette.BG, hover=Palette.PANEL_HOVER, fg=Palette.ACCENT if note.pinned else Palette.DIM, font=("Segoe UI Symbol", 10), padx=6, pady=5)
        self.pin_note_button.configure(width=2)
        self.pin_note_button.pack(side="left")
        self.color_button = HoverButton(editor_actions, "●", self.cycle_color, bg=Palette.BG, hover=Palette.PANEL_HOVER, fg=Palette.KEEP_COLORS.get(note.color, Palette.ACCENT), font=("Segoe UI Symbol", 10), padx=6, pady=5)
        self.color_button.configure(width=2)
        self.color_button.pack(side="left")
        delete_button = HoverButton(editor_actions, "Delete", self.delete_current, bg=Palette.BG, hover=Palette.DANGER_BG, fg=Palette.DANGER, font=("Segoe UI", 9), padx=7, pady=5, pressed=Palette.DANGER_PRESSED)
        delete_button.pack(side="left")
        Tooltip(font_down_button, "Decrease editor text size (Ctrl+-)")
        Tooltip(self.font_size_label, "Current editor text size in points")
        Tooltip(font_up_button, "Increase editor text size (Ctrl++)")
        Tooltip(self.pin_note_button, "Pin or unpin this Google Keep note")
        Tooltip(self.color_button, "Change this note's Google Keep color")
        Tooltip(delete_button, "Move this note to Google Keep trash")

        if note.is_list:
            checklist_tools = tk.Frame(self.content, bg=Palette.BG)
            checklist_tools.pack(fill="x", pady=(0, 7))
            tk.Label(checklist_tools, text="CHECKLIST", bg=Palette.BG, fg=Palette.DIM, font=("Segoe UI Semibold", 7)).pack(side="left", padx=(2, 7), pady=5)
            check_all_button = HoverButton(checklist_tools, "☑  Check all", lambda: self.set_all_checklist_items(True), bg=Palette.PANEL, hover=Palette.PANEL_HOVER, fg=Palette.MUTED, font=("Segoe UI Semibold", 8), padx=8, pady=4)
            check_all_button.pack(side="left", padx=(0, 4))
            clear_all_button = HoverButton(checklist_tools, "☐  Clear all", lambda: self.set_all_checklist_items(False), bg=Palette.PANEL, hover=Palette.PANEL_HOVER, fg=Palette.MUTED, font=("Segoe UI Semibold", 8), padx=8, pady=4)
            clear_all_button.pack(side="left")
            Tooltip(check_all_button, "Mark every checklist item as completed")
            Tooltip(clear_all_button, "Mark every checklist item as not completed")

        self.editor_frame = tk.Frame(self.content, bg=Palette.INPUT, highlightthickness=1, highlightbackground=Palette.BORDER)
        self.editor_frame.pack(fill="both", expand=True, pady=(0, 3))
        self.title_var = tk.StringVar(value=note.title)
        font_size = self._editor_font_size()
        self.title_entry = tk.Entry(self.editor_frame, textvariable=self.title_var, bg=Palette.INPUT, fg=Palette.TEXT, insertbackground=Palette.TEXT, relief="flat", bd=0, font=("Segoe UI Semibold", max(15, font_size + 5)), **selection_colors())
        self.title_entry.pack(fill="x", padx=18, pady=(18, 9))
        self.title_entry.bind("<KeyRelease>", self.queue_save)
        self.title_entry.bind("<FocusIn>", lambda _event: self.editor_frame.configure(highlightbackground=Palette.BORDER_FOCUS))
        self.title_entry.bind("<FocusOut>", lambda _event: self.editor_frame.configure(highlightbackground=Palette.BORDER))
        tk.Frame(self.editor_frame, bg=Palette.BORDER, height=1).pack(fill="x", padx=18)
        if note.is_list:
            self._build_checklist_editor(note)
        else:
            self.body_text = tk.Text(self.editor_frame, bg=Palette.INPUT, fg=Palette.TEXT, insertbackground=Palette.TEXT, relief="flat", bd=0, wrap="word", undo=True, maxundo=100, spacing1=max(2, font_size // 4), spacing3=max(3, font_size // 3), font=("Segoe UI", font_size), padx=3, pady=2, **selection_colors())
            self.body_text.pack(fill="both", expand=True, padx=15, pady=(12, 15))
            self._suspend_body_history = True
            self.body_text.insert("1.0", note.body)
            self.body_text.bind("<<Modified>>", self._body_modified)
            self.body_text.bind("<FocusIn>", lambda _event: self.editor_frame.configure(highlightbackground=Palette.BORDER_FOCUS))
            self.body_text.bind("<FocusOut>", lambda _event: self.editor_frame.configure(highlightbackground=Palette.BORDER))
            for sequence in ("<Control-z>", "<Control-Z>"):
                self.body_text.bind(sequence, self._undo_body)
            for sequence in ("<Control-y>", "<Control-Y>", "<Control-Shift-Z>", "<Control-Shift-z>"):
                self.body_text.bind(sequence, self._redo_body)
            self.body_text.edit_modified(False)
            self._suspend_body_history = False
            self.body_history = self.undo_histories.get(note.id)
            if self.body_history is None:
                self.body_history = UndoHistory(note.body)
                self.undo_histories[note.id] = self.body_history
            else:
                # Reopening the same note (e.g. after a refresh) may bring a
                # different server body; keep it as a new step so earlier edits
                # stay recoverable.
                self.body_history.record(note.body)
        detail = "Checklist • click an item to complete it" if note.is_list else "Text note"
        self.status.configure(text=f"{detail}  •  synced {friendly_time(note.updated)}")
        self.root.after(30, lambda: self._focus_open_editor(note))

    def _focus_open_editor(self, note: NoteRecord) -> None:
        if not note.title:
            self.title_entry.focus_set()
        elif note.is_list and self.checklist_entries:
            next(iter(self.checklist_entries.values())).focus_set()
        elif hasattr(self, "body_text") and self.body_text.winfo_exists():
            self.body_text.focus_set()

    def _build_checklist_editor(self, note: NoteRecord) -> None:
        self.checklist_items = self._parse_checklist(note.body)
        self.checklist_entries = {}
        holder = tk.Frame(self.editor_frame, bg=Palette.INPUT)
        holder.pack(fill="both", expand=True, padx=(12, 8), pady=(10, 12))
        self.checklist_canvas = tk.Canvas(holder, bg=Palette.INPUT, bd=0, highlightthickness=0, yscrollincrement=1)
        scrollbar = tk.Scrollbar(holder, orient="vertical", command=self.checklist_canvas.yview, width=7, bg=Palette.PANEL, troughcolor=Palette.INPUT, activebackground=Palette.ACCENT, bd=0, highlightthickness=0, relief="flat")
        self.checklist_rows = tk.Frame(self.checklist_canvas, bg=Palette.INPUT)
        self.checklist_window = self.checklist_canvas.create_window((0, 0), window=self.checklist_rows, anchor="nw")
        self.checklist_canvas.configure(yscrollcommand=scrollbar.set)
        self.checklist_canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y", padx=(5, 0))
        self.checklist_rows.bind("<Configure>", lambda _event: self.checklist_canvas.configure(scrollregion=self.checklist_canvas.bbox("all")))
        self.checklist_canvas.bind("<Configure>", lambda event: self.checklist_canvas.itemconfigure(self.checklist_window, width=event.width))
        self.checklist_canvas.bind_all("<MouseWheel>", self._on_checklist_wheel)
        self.render_checklist_rows()

    def _parse_checklist(self, body: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for line in body.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            checked = stripped.startswith("☑") or bool(re.match(r"^\[[xX]\]", stripped))
            text = re.sub(r"^(?:[☐☑]|\[[ xX]\]|[-*])\s*", "", stripped)
            items.append({"key": uuid.uuid4().hex, "text": text, "checked": checked})
        return items

    def render_checklist_rows(self, focus_key: str | None = None) -> None:
        if not hasattr(self, "checklist_rows") or not self.checklist_rows.winfo_exists():
            return
        for child in self.checklist_rows.winfo_children():
            child.destroy()
        self.checklist_entries.clear()
        open_items = [item for item in self.checklist_items if not item["checked"]]
        completed_items = [item for item in self.checklist_items if item["checked"]]
        if open_items:
            for item in open_items:
                self._make_checklist_row(item)
        else:
            tk.Label(self.checklist_rows, text="All caught up", bg=Palette.INPUT, fg=Palette.MUTED, font=("Segoe UI Semibold", 10), anchor="w").pack(fill="x", padx=8, pady=(10, 6))
        add = HoverButton(self.checklist_rows, "+   Add item", self.add_checklist_item, bg=Palette.INPUT, hover=Palette.PANEL_HOVER, fg=Palette.ACCENT, font=("Segoe UI Semibold", 9), padx=10, pady=8)
        add.pack(fill="x", pady=(4, 10))

        completed_header = tk.Frame(self.checklist_rows, bg=Palette.INPUT)
        completed_header.pack(fill="x", pady=(3, 4))
        tk.Frame(completed_header, bg=Palette.BORDER, height=1).pack(side="left", fill="x", expand=True, pady=8)
        tk.Label(completed_header, text=f"  COMPLETED  {len(completed_items)}  ", bg=Palette.INPUT, fg=Palette.DIM, font=("Segoe UI Semibold", 7)).pack(side="left")
        tk.Frame(completed_header, bg=Palette.BORDER, height=1).pack(side="left", fill="x", expand=True, pady=8)
        for item in completed_items:
            self._make_checklist_row(item)
        if not completed_items:
            tk.Label(self.checklist_rows, text="Completed items will appear here", bg=Palette.INPUT, fg=Palette.DIM, font=("Segoe UI", 8), anchor="w").pack(fill="x", padx=8, pady=(3, 8))
        if focus_key:
            self.root.after(20, lambda: self._focus_checklist_item(focus_key))

    def _make_checklist_row(self, item: dict[str, Any]) -> None:
        checked = bool(item["checked"])
        row = tk.Frame(self.checklist_rows, bg=Palette.INPUT, highlightthickness=1, highlightbackground=Palette.INPUT)
        row.pack(fill="x", pady=2)
        box = HoverButton(row, "✓" if checked else "", lambda key=str(item["key"]): self.toggle_checklist_item(key), bg=Palette.ACCENT_SOFT if checked else Palette.PANEL, hover=Palette.PANEL_HOVER, fg=Palette.ACCENT if checked else Palette.MUTED, font=("Segoe UI Semibold", 10), padx=7, pady=6)
        box.configure(width=2)
        box.pack(side="left", padx=(2, 7))
        variable = tk.StringVar(value=str(item["text"]))
        entry = tk.Entry(row, textvariable=variable, bg=Palette.INPUT, fg=Palette.DIM if checked else Palette.TEXT, insertbackground=Palette.TEXT, relief="flat", bd=0, font=("Segoe UI", self._editor_font_size()), **selection_colors())
        entry.pack(side="left", fill="x", expand=True, pady=7)
        entry.bind("<KeyRelease>", lambda _event, key=str(item["key"]), var=variable: self._checklist_text_changed(key, var.get()))
        entry.bind("<Return>", lambda _event, key=str(item["key"]): self._checklist_enter(key))
        entry.bind("<FocusIn>", lambda _event, frame=row: frame.configure(highlightbackground=Palette.BORDER_FOCUS))
        entry.bind("<FocusOut>", lambda _event, frame=row: frame.configure(highlightbackground=Palette.INPUT))
        self.checklist_entries[str(item["key"])] = entry
        remove = HoverButton(row, "×", lambda key=str(item["key"]): self.remove_checklist_item(key), bg=Palette.INPUT, hover=Palette.DANGER_BG, fg=Palette.DIM, font=("Segoe UI", 11), padx=8, pady=5, pressed=Palette.DANGER_PRESSED)
        remove.pack(side="right", padx=(4, 2))

    def _focus_checklist_item(self, key: str) -> None:
        entry = self.checklist_entries.get(key)
        if entry and entry.winfo_exists():
            entry.focus_set()
            entry.icursor("end")

    def _checklist_text_changed(self, key: str, value: str) -> None:
        item = self._checklist_item(key)
        if item is not None:
            item["text"] = value
            self.queue_save()

    def _checklist_item(self, key: str) -> dict[str, Any] | None:
        return next((item for item in self.checklist_items if item["key"] == key), None)

    def toggle_checklist_item(self, key: str) -> None:
        item = self._checklist_item(key)
        if item is None:
            return
        item["checked"] = not bool(item["checked"])
        self.render_checklist_rows()
        self.queue_save()
        completed = sum(bool(entry["checked"]) for entry in self.checklist_items)
        self.status.configure(text=f"{completed} completed • waiting to sync")

    def add_checklist_item(self, after_key: str | None = None) -> None:
        new_item = {"key": uuid.uuid4().hex, "text": "", "checked": False}
        if after_key:
            index = next((index for index, item in enumerate(self.checklist_items) if item["key"] == after_key), len(self.checklist_items) - 1)
            self.checklist_items.insert(index + 1, new_item)
        else:
            completed_index = next((index for index, item in enumerate(self.checklist_items) if item["checked"]), len(self.checklist_items))
            self.checklist_items.insert(completed_index, new_item)
        self.render_checklist_rows(focus_key=str(new_item["key"]))

    def _checklist_enter(self, key: str) -> str:
        self.add_checklist_item(after_key=key)
        return "break"

    def remove_checklist_item(self, key: str) -> None:
        self.checklist_items[:] = [item for item in self.checklist_items if item["key"] != key]
        self.render_checklist_rows()
        self.queue_save()

    def _serialize_checklist(self) -> str:
        return "\n".join(f"{'☑' if item['checked'] else '☐'} {str(item['text']).strip()}" for item in self.checklist_items if str(item["text"]).strip())

    def _on_checklist_wheel(self, event: tk.Event) -> str | None:
        if self.current_mode == "note" and self.selected_note and self.selected_note.is_list and hasattr(self, "checklist_canvas") and self.checklist_canvas.winfo_exists():
            self.checklist_canvas.yview_scroll(int(-event.delta / 120), "units")
            return "break"
        return None

    def set_all_checklist_items(self, checked: bool) -> None:
        if not self.selected_note or not self.selected_note.is_list:
            return
        for item in self.checklist_items:
            item["checked"] = checked
        self.render_checklist_rows()
        self.queue_save()
        self.status.configure(text="All items checked • waiting to sync" if checked else "All items cleared • waiting to sync")

    def _editor_font_size(self) -> int:
        try:
            value = int(self.settings.data.get("editor_font_size", 11))
        except (TypeError, ValueError):
            value = 11
        return min(22, max(9, value))

    def change_font_size(self, amount: int) -> None:
        current = self._editor_font_size()
        updated = min(22, max(9, current + amount))
        if updated == current:
            return
        self.settings.data["editor_font_size"] = updated
        self.settings.save()
        if self.current_mode == "note":
            if self.selected_note and self.selected_note.is_list:
                for entry in self.checklist_entries.values():
                    if entry.winfo_exists():
                        entry.configure(font=("Segoe UI", updated))
            elif hasattr(self, "body_text") and self.body_text.winfo_exists():
                self.body_text.configure(font=("Segoe UI", updated), spacing1=max(2, updated // 4), spacing3=max(3, updated // 3))
            self.title_entry.configure(font=("Segoe UI Semibold", max(15, updated + 5)))
            if hasattr(self, "font_size_label") and self.font_size_label.winfo_exists():
                self.font_size_label.configure(text=str(updated))
            self.status.configure(text=f"Editor text size {updated} pt")

    def reset_font_size(self) -> None:
        current = self._editor_font_size()
        if current != 11:
            self.change_font_size(11 - current)

    def show_note_mode(self) -> None:
        if self.selected_note:
            refreshed = self._find_record(self.selected_note.id)
            if refreshed:
                self.open_note(refreshed)
                return
        remembered = self._find_record(str(self.settings.data.get("selected_note_id", "")))
        if remembered:
            self.open_note(remembered)
        elif self.records:
            self.open_note(self.records[0])

    def toggle_mode(self) -> None:
        self.show_note_mode() if self.current_mode == "list" else self.show_list()

    def show_settings_menu(self) -> None:
        if self.settings_menu and self.settings_menu.winfo_exists():
            self.dismiss_settings_menu()
            return
        self.dismiss_new_menu()
        menu = tk.Toplevel(self.root)
        self.settings_menu = menu
        menu.overrideredirect(True)
        menu.attributes("-topmost", bool(self.settings.data.get("topmost", True)))
        menu.configure(bg=Palette.BORDER)
        panel = tk.Frame(menu, bg=Palette.PANEL, padx=1, pady=1, highlightthickness=1, highlightbackground=Palette.BORDER)
        panel.pack(fill="both", expand=True)
        if self.client.connected:
            tk.Label(panel, text="ACCOUNT", bg=Palette.PANEL, fg=Palette.DIM, font=("Segoe UI Semibold", 7), anchor="w").pack(fill="x", padx=11, pady=(9, 3))
            account = HoverButton(
                panel,
                self.client.email or "Google Keep account",
                self.open_account_from_settings,
                bg=Palette.PANEL,
                hover=Palette.PANEL_HOVER,
                fg=Palette.TEXT,
                font=("Segoe UI", 8),
                padx=11,
                pady=6,
                anchor="w",
            )
            account.pack(fill="x")
        tk.Label(panel, text="APPEARANCE", bg=Palette.PANEL, fg=Palette.DIM, font=("Segoe UI Semibold", 7), anchor="w").pack(fill="x", padx=11, pady=(9, 3))
        for key, label in (("dark", "Dark theme"), ("light", "Light theme")):
            button = ThemeChoice(panel, label, self.theme == key, lambda value=key: self.set_theme(value))
            button.pack(fill="x")
        tk.Label(panel, text="WINDOWS", bg=Palette.PANEL, fg=Palette.DIM, font=("Segoe UI Semibold", 7), anchor="w").pack(fill="x", padx=11, pady=(8, 3))
        startup = ThemeChoice(panel, "Start with Windows", WindowsStartup.is_enabled(), self.toggle_start_with_windows)
        startup.pack(fill="x")
        tk.Label(panel, text="Saved automatically", bg=Palette.PANEL, fg=Palette.DIM, font=("Segoe UI", 7), anchor="w").pack(fill="x", padx=11, pady=(5, 3))
        credit = tk.Label(panel, text="Created by justdataplease.com", bg=Palette.PANEL, fg=Palette.ACCENT, font=("Segoe UI Semibold", 7), anchor="w", cursor="hand2")
        credit.pack(fill="x", padx=11, pady=(0, 9))
        credit.bind("<Button-1>", lambda _event: self.open_project_website())
        menu.update_idletasks()
        width = max(164, menu.winfo_reqwidth())
        x = self.settings_button.winfo_rootx() + self.settings_button.winfo_width() - width
        y = self.settings_button.winfo_rooty() + self.settings_button.winfo_height() + 5
        menu.geometry(f"{width}x{menu.winfo_reqheight()}+{max(4, x)}+{y}")
        menu.focus_force()
        menu.bind("<Escape>", lambda _event: self.dismiss_settings_menu())
        menu.bind("<FocusOut>", lambda _event: self.root.after(80, self._dismiss_settings_if_unfocused))

    def _dismiss_settings_if_unfocused(self) -> None:
        if self.settings_menu and self.settings_menu.winfo_exists():
            focused = self.root.focus_get()
            if focused is None or focused.winfo_toplevel() != self.settings_menu:
                self.dismiss_settings_menu()

    def dismiss_settings_menu(self) -> None:
        if self.settings_menu and self.settings_menu.winfo_exists():
            self.settings_menu.destroy()
        self.settings_menu = None

    def toggle_start_with_windows(self) -> None:
        enabled = not WindowsStartup.is_enabled()
        try:
            WindowsStartup.set_enabled(enabled)
        except (OSError, RuntimeError) as error:
            self.dismiss_settings_menu()
            messagebox.showerror("StickyOmelet", f"Could not update Windows startup:\n\n{error}", parent=self.root)
            return
        self.dismiss_settings_menu()
        self.status.configure(
            text="Starts with Windows" if enabled else "Windows startup disabled",
            fg=Palette.GREEN if enabled else Palette.DIM,
        )

    def set_theme(self, name: str) -> None:
        if name not in Palette.THEMES:
            return
        self.dismiss_settings_menu()
        if name == self.theme:
            return
        old_palette = Palette.snapshot()
        self.theme = Palette.apply_theme(name)
        new_palette = Palette.snapshot()
        replacements = {old_palette[key].lower(): new_palette[key] for key in old_palette}
        self._recolor_widget_tree(self.root, replacements)
        self.settings.data["theme"] = self.theme
        self.settings.save()
        self.status.configure(text=f"{self.theme.title()} theme", fg=Palette.DIM)

    def _recolor_widget_tree(self, widget: tk.Misc, replacements: dict[str, str]) -> None:
        options: dict[str, str] = {}
        for option in (
            "background", "foreground", "activebackground", "activeforeground",
            "highlightbackground", "highlightcolor", "insertbackground",
            "selectbackground", "selectforeground", "troughcolor",
        ):
            try:
                current = str(widget.cget(option))
            except tk.TclError:
                continue
            replacement = replacements.get(current.lower())
            if replacement:
                options[option] = replacement
        if options:
            try:
                widget.configure(**options)
            except tk.TclError:
                pass
        if isinstance(widget, HoverButton):
            for attribute in ("normal_bg", "hover_bg", "pressed_bg"):
                current = str(getattr(widget, attribute))
                setattr(widget, attribute, replacements.get(current.lower(), current))
        for child in widget.winfo_children():
            self._recolor_widget_tree(child, replacements)

    def show_new_menu(self) -> None:
        if not self.client.connected:
            return
        self.dismiss_settings_menu()
        if self.new_menu and self.new_menu.winfo_exists():
            self.dismiss_new_menu()
            return
        menu = tk.Toplevel(self.root)
        self.new_menu = menu
        menu.overrideredirect(True)
        menu.attributes("-topmost", bool(self.settings.data.get("topmost", True)))
        menu.configure(bg=Palette.BORDER)
        panel = tk.Frame(menu, bg=Palette.PANEL, padx=1, pady=1, highlightthickness=1, highlightbackground=Palette.BORDER)
        panel.pack(fill="both", expand=True)
        tk.Label(panel, text="CREATE", bg=Palette.PANEL, fg=Palette.DIM, font=("Segoe UI Semibold", 7), anchor="w").pack(fill="x", padx=9, pady=(7, 2))
        HoverButton(panel, "▤   Text note", lambda: self._new_menu_action(self.new_note), bg=Palette.PANEL, hover=Palette.PANEL_HOVER, fg=Palette.TEXT, font=("Segoe UI Semibold", 9), padx=9, pady=7, anchor="w").pack(fill="x")
        HoverButton(panel, "☑   Checklist", lambda: self._new_menu_action(self.new_checklist), bg=Palette.PANEL, hover=Palette.PANEL_HOVER, fg=Palette.TEXT, font=("Segoe UI Semibold", 9), padx=9, pady=7, anchor="w").pack(fill="x")
        menu.update_idletasks()
        width = max(150, menu.winfo_reqwidth())
        x = self.add_button.winfo_rootx() + self.add_button.winfo_width() - width
        y = self.add_button.winfo_rooty() + self.add_button.winfo_height() + 6
        menu.geometry(f"{width}x{menu.winfo_reqheight()}+{x}+{y}")
        menu.focus_force()
        menu.bind("<Escape>", lambda _event: self.dismiss_new_menu())
        menu.bind("<FocusOut>", lambda _event: self.root.after(80, self._dismiss_menu_if_unfocused))

    def _dismiss_menu_if_unfocused(self) -> None:
        if self.new_menu and self.new_menu.winfo_exists():
            focused = self.root.focus_get()
            if focused is None or focused.winfo_toplevel() != self.new_menu:
                self.dismiss_new_menu()

    def dismiss_new_menu(self) -> None:
        if self.new_menu and self.new_menu.winfo_exists():
            self.new_menu.destroy()
        self.new_menu = None

    def _new_menu_action(self, action: Callable[[], None]) -> None:
        self.dismiss_new_menu()
        action()

    def new_note(self) -> None:
        if not self.client.connected:
            return
        self.flush_save()
        self.status.configure(text="Creating in Google Keep…")

        def created(result: tuple[list[NoteRecord], str]) -> None:
            records, note_id = result
            self.records = records
            note = self._find_record(note_id)
            if note:
                self.open_note(note)

        self._run(self.client.create_note, created)

    def new_checklist(self) -> None:
        if not self.client.connected:
            return
        self.flush_save()
        self.status.configure(text="Creating checklist in Google Keep…")

        def created(result: tuple[list[NoteRecord], str]) -> None:
            records, note_id = result
            self.records = records
            note = self._find_record(note_id)
            if note:
                self.open_note(note)

        self._run(self.client.create_list, created)

    def _body_modified(self, _event: tk.Event) -> None:
        if self.body_text.edit_modified():
            self.body_text.edit_modified(False)
            if self._suspend_body_history:
                return
            self.queue_save()
            self._schedule_body_snapshot()

    def _schedule_body_snapshot(self) -> None:
        """Debounce edits into one undo step per burst of typing."""
        if self.snapshot_job:
            try:
                self.root.after_cancel(self.snapshot_job)
            except tk.TclError:
                pass
        self.snapshot_job = self.root.after(500, self._record_body_snapshot)

    def _record_body_snapshot(self) -> None:
        self.snapshot_job = None
        if not self.body_history or not self._body_editor_ready():
            return
        self.body_history.record(self.body_text.get("1.0", "end-1c"))

    def _body_editor_ready(self) -> bool:
        return hasattr(self, "body_text") and self.body_text.winfo_exists()

    def _undo_body(self, _event: tk.Event | None = None) -> str:
        return self._step_body_history(undo=True)

    def _redo_body(self, _event: tk.Event | None = None) -> str:
        return self._step_body_history(undo=False)

    def _step_body_history(self, *, undo: bool) -> str:
        # Return "break" so Tk's built-in (single-stack) undo never also fires.
        if not self.body_history or not self._body_editor_ready():
            return "break"
        if self.snapshot_job:
            try:
                self.root.after_cancel(self.snapshot_job)
            except tk.TclError:
                pass
            self.snapshot_job = None
        # Capture any un-snapshotted typing so it becomes recoverable via redo.
        self.body_history.record(self.body_text.get("1.0", "end-1c"))
        text = self.body_history.undo() if undo else self.body_history.redo()
        if text is None:
            return "break"
        self._suspend_body_history = True
        try:
            self.body_text.delete("1.0", "end")
            self.body_text.insert("1.0", text)
            self.body_text.edit_modified(False)
            self.body_text.mark_set("insert", "end-1c")
            self.body_text.see("insert")
        finally:
            self._suspend_body_history = False
        self.queue_save()
        return "break"

    def queue_save(self, _event: tk.Event | None = None) -> None:
        if not self.selected_note:
            return
        self.editor_revision += 1
        self._set_sync_identity("Waiting", Palette.ACCENT)
        self.status.configure(text="Waiting to sync…")
        if self.save_job:
            self.root.after_cancel(self.save_job)
        self.save_job = self.root.after(900, self.flush_save)

    def flush_save(self) -> None:
        if self.save_job:
            try:
                self.root.after_cancel(self.save_job)
            except tk.TclError:
                pass
            self.save_job = None
        if self.current_mode != "note" or not self.selected_note:
            return
        title = self.title_var.get()
        if self.selected_note.is_list:
            body = self._serialize_checklist()
        else:
            if not hasattr(self, "body_text") or not self.body_text.winfo_exists():
                return
            body = self.body_text.get("1.0", "end-1c")
        if title == self.selected_note.title and body == self.selected_note.body:
            return
        note_id = self.selected_note.id
        revision = self.editor_revision
        self.status.configure(text="Syncing to Google Keep…")

        def saved(records: list[NoteRecord]) -> None:
            self.records = records
            refreshed = self._find_record(note_id)
            still_selected = self.selected_note is not None and self.selected_note.id == note_id
            if refreshed and still_selected:
                self.selected_note = refreshed
            if still_selected and revision == self.editor_revision:
                self.status.configure(text=f"Synced to Google Keep  •  {datetime.now():%H:%M}")
            if self.current_mode == "list" and hasattr(self, "cards") and self.cards.winfo_exists():
                self.render_notes()

        self._run(lambda: self.client.update_note(note_id, title, body), saved)

    def refresh_from_keep(self) -> None:
        if not self.client.connected:
            return
        self.flush_save()
        selected_id = self.selected_note.id if self.selected_note else ""
        mode = self.current_mode
        self.status.configure(text="Refreshing from Google Keep…")

        def refreshed(records: list[NoteRecord]) -> None:
            self.records = records
            if mode == "note" and selected_id:
                note = self._find_record(selected_id)
                if note:
                    self.open_note(note)
                    return
            if mode == "account":
                self.show_account()
            else:
                self.show_list()
            self.status.configure(text=f"Refreshed  •  {datetime.now():%H:%M}")

        self._run(self.client.sync, refreshed)

    def cycle_color(self) -> None:
        if not self.selected_note:
            return
        self.flush_save()
        note_id = self.selected_note.id
        self.status.configure(text="Updating color in Google Keep…")
        self._run(lambda: self.client.cycle_color(note_id), lambda records: self._after_note_action(records, note_id))

    def toggle_note_pin(self) -> None:
        if not self.selected_note:
            return
        self.flush_save()
        note_id = self.selected_note.id
        self.status.configure(text="Updating pin in Google Keep…")
        self._run(lambda: self.client.toggle_pin(note_id), lambda records: self._after_note_action(records, note_id))

    def _after_note_action(self, records: list[NoteRecord], note_id: str) -> None:
        self.records = records
        note = self._find_record(note_id)
        if note:
            self.open_note(note)

    def delete_current(self) -> None:
        if not self.selected_note:
            return
        if not messagebox.askyesno("Move note to trash", f'Move “{note_title(self.selected_note)}” to Google Keep trash?', parent=self.root):
            return
        note_id = self.selected_note.id
        self.selected_note = None
        self.status.configure(text="Moving to Google Keep trash…")

        def deleted(records: list[NoteRecord]) -> None:
            self.records = records
            self.settings.data["selected_note_id"] = ""
            self.settings.save()
            self.show_list()

        self._run(lambda: self.client.trash_note(note_id), deleted)

    def _after_error(self, error: Exception) -> None:
        message = str(error).strip() or error.__class__.__name__
        self._set_sync_identity("Sync error", Palette.DANGER)
        self.status.configure(text=f"Sync error: {message}", fg=Palette.DANGER)
        # Autosave can finish while collapsed or behind another application.
        # A modal error dialog disables the dot until that dialog is dismissed.
        # Keep the error in the footer so the dot and editor remain usable.

    def _run(
        self,
        operation: Callable[[], Any],
        on_success: Callable[[Any], None],
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        self.pending_operations += 1
        self.status.configure(fg=Palette.DIM)
        self.sync_button.configure(fg=Palette.ACCENT)
        if self.client.connected:
            self._set_sync_identity("Syncing", Palette.ACCENT)
        future = self.executor.submit(operation)

        def poll_result() -> None:
            if self.closing:
                return
            if not future.done():
                self.root.after(25, poll_result)
                return
            deliver(future)

        def deliver(result: Future[Any]) -> None:
            self.pending_operations = max(0, self.pending_operations - 1)
            if self.pending_operations == 0:
                self.sync_button.configure(fg=Palette.DIM)
            try:
                value = result.result()
            except Exception as error:  # Network and authentication errors vary by provider.
                (on_error or self._after_error)(error)
                return
            on_success(value)
            if self.pending_operations == 0 and self.client.connected:
                self._set_sync_identity("Synced", Palette.GREEN)

        # Tk calls must stay on the main thread. Future callbacks execute on a
        # worker thread, which can make the packaged Windows app exit while an
        # autosave is completing.
        self.root.after(25, poll_result)

    def _find_record(self, identity: str | None) -> NoteRecord | None:
        if not identity:
            return None
        needle = identity.strip().casefold()
        for note in self.records:
            if note.id.casefold() == needle:
                return note
        for note in self.records:
            if note_title(note).casefold() == needle:
                return note
        for note in self.records:
            if needle in note_title(note).casefold():
                return note
        return None

    def toggle_topmost(self) -> None:
        enabled = not bool(self.settings.data.get("topmost", True))
        self.settings.data["topmost"] = enabled
        self.root.attributes("-topmost", enabled)
        if not enabled:
            # Clicking this button proves the widget is currently foreground;
            # arm the polling fallback before the user's next click.
            self._own_window_was_foreground = True
        self.pin_button.configure(text="◆" if enabled else "◇", fg=Palette.ACCENT if enabled else Palette.DIM)
        self.settings.save()
        self.status.configure(text="Always on top" if enabled else "Always on top off")

    def focus_search(self) -> None:
        if self.current_mode != "list":
            self.show_list()
        self.search_entry.focus_set()
        self.search_entry.selection_range(0, "end")

    def _scrollbar_move(self, *args: str) -> None:
        if not hasattr(self, "list_canvas") or not self.list_canvas.winfo_exists():
            return
        if self.scroll_job:
            try:
                self.root.after_cancel(self.scroll_job)
            except tk.TclError:
                pass
            self.scroll_job = None
        self.list_canvas.yview(*args)
        self.scroll_target = self.list_canvas.yview()[0]

    def _on_mousewheel(self, event: tk.Event) -> str | None:
        if self.current_mode != "list" or not hasattr(self, "list_canvas") or not self.list_canvas.winfo_exists():
            return None
        region = self.list_canvas.bbox("all")
        if not region:
            return "break"
        content_height = max(1, region[3] - region[1])
        first, last = self.list_canvas.yview()
        max_first = max(0.0, 1.0 - (last - first))
        delta_pixels = (-event.delta / 120.0) * 68.0
        self.scroll_target = min(max_first, max(0.0, self.scroll_target + delta_pixels / content_height))
        if not self.scroll_job:
            self.scroll_job = self.root.after(10, self._animate_scroll)
        return "break"

    def _animate_scroll(self) -> None:
        self.scroll_job = None
        if self.current_mode != "list" or not hasattr(self, "list_canvas") or not self.list_canvas.winfo_exists():
            return
        current = self.list_canvas.yview()[0]
        difference = self.scroll_target - current
        if abs(difference) < 0.0005:
            self.list_canvas.yview_moveto(self.scroll_target)
            return
        self.list_canvas.yview_moveto(current + difference * 0.30)
        self.scroll_job = self.root.after(10, self._animate_scroll)

    def _start_drag(self, event: tk.Event) -> None:
        self.drag_start = (event.x_root, event.y_root, self.root.winfo_x(), self.root.winfo_y())

    def _drag(self, event: tk.Event) -> None:
        if not self.drag_start:
            return
        start_x, start_y, window_x, window_y = self.drag_start
        x, y = self._clamp_window_to_visible_work_area(
            window_x + event.x_root - start_x,
            window_y + event.y_root - start_y,
        )
        self._move_window(x, y)

    def _end_drag(self, _event: tk.Event) -> None:
        """Remember where the window was dropped so it reopens in place."""
        if not self.drag_start:
            return
        self.drag_start = None
        if self.minimized_to_bubble:
            return
        self.normal_geometry = self.root.geometry()
        self.settings.data["geometry"] = self.normal_geometry
        self.settings.save()

    def _clamp_window_to_visible_work_area(self, x: int, y: int) -> tuple[int, int]:
        width, height = self.root.winfo_width(), self.root.winfo_height()
        work_area = self._work_area_for_geometry(f"{width}x{height}{x:+d}{y:+d}")
        return clamp_window_position(x, y, width, height, work_area)

    def _start_resize(self, event: tk.Event) -> None:
        self.resize_start = (event.x_root, event.y_root, self.root.winfo_width(), self.root.winfo_height())

    def _resize(self, event: tk.Event) -> None:
        if self.resize_start:
            start_x, start_y, width, height = self.resize_start
            resized_width, resized_height = clamp_window_size(
                width + event.x_root - start_x,
                height + event.y_root - start_y,
            )
            self.root.geometry(f"{resized_width}x{resized_height}")

    def _on_root_deactivate(self, _event: tk.Event) -> None:
        """Collapse to the dot bubble when the user clicks away.

        Only when "always on top" is off: a topmost window stays visible on its
        own, but an off-top borderless window has no taskbar button, so losing
        focus would strand it behind whatever was clicked.
        """
        if self.minimized_to_bubble or self.closing:
            return
        if self.settings.data.get("topmost", True):
            return
        # Let the OS settle the new foreground window, then confirm the user
        # switched to another application rather than one of our own pop-ups.
        self.root.after(100, self._collapse_if_switched_away)

    def _collapse_if_switched_away(self) -> None:
        if self.minimized_to_bubble or self.closing:
            return
        if self.settings.data.get("topmost", True):
            return
        if self._foreground_is_own_window():
            return
        self.minimize()

    def _monitor_click_away(self) -> None:
        """Catch click-away transitions that Tk's <Deactivate> event misses.

        Borderless override-redirect windows do not reliably receive native
        activation events on Windows. Polling the foreground process gives the
        unpinned widget a dependable path to its always-on-top dot bubble.
        """
        self.click_away_job = None
        if self.closing:
            return

        foreground_is_ours = self._foreground_is_own_window()
        should_collapse = (
            not self.minimized_to_bubble
            and not self.settings.data.get("topmost", True)
            and self._own_window_was_foreground
            and not foreground_is_ours
        )
        self._own_window_was_foreground = foreground_is_ours
        if should_collapse:
            self.minimize()
        elif self.minimized_to_bubble:
            self._heal_bubble_styling()

        if not self.closing:
            self.click_away_job = self.root.after(CLICK_AWAY_POLL_MS, self._monitor_click_away)

    def _foreground_is_own_window(self) -> bool:
        """True when the active window belongs to StickyOmelet (a menu, tooltip or
        dialog) rather than another application."""
        if sys.platform != "win32":
            # focus_get() is None only when no window in this app holds focus.
            return self.root.focus_get() is not None
        try:
            user32 = ctypes.windll.user32
            user32.GetForegroundWindow.restype = ctypes.c_void_p
            hwnd = user32.GetForegroundWindow()
            if not hwnd:
                return False
            pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(ctypes.c_void_p(hwnd), ctypes.byref(pid))
            return pid.value == os.getpid()
        except (AttributeError, OSError):
            return self.root.focus_get() is not None

    def minimize(self) -> None:
        if self.minimized_to_bubble:
            return
        self.dismiss_new_menu()
        self.dismiss_settings_menu()
        self.flush_save()
        self.root.update_idletasks()
        self.normal_geometry = self.root.geometry()
        self.settings.data["geometry"] = self.normal_geometry
        self.settings.save()

        self.minimized_to_bubble = True
        self.border.pack_forget()
        self.root.minsize(1, 1)
        self.root.configure(bg="#FFFFFF")
        self.root.attributes("-topmost", True)

        x, y = self._initial_bubble_position()
        self.root.geometry(f"{BUBBLE_SIZE}x{BUBBLE_SIZE}+{x}+{y}")
        self._draw_bubble()
        self.root.update_idletasks()
        self._set_native_bubble_mode(True)

    def _initial_bubble_position(self) -> tuple[int, int]:
        saved = str(self.settings.data.get("bubble_position", ""))
        match = re.fullmatch(r"(-?\d+),(-?\d+)", saved)
        if match:
            x, y = int(match.group(1)), int(match.group(2))
        else:
            x = self.root.winfo_screenwidth() - BUBBLE_SIZE - 24
            y = self.root.winfo_screenheight() - BUBBLE_SIZE - 64
        return self._clamp_bubble_to_visible_work_area(x, y)

    def _draw_bubble(self) -> None:
        canvas = tk.Canvas(
            self.root,
            width=BUBBLE_SIZE,
            height=BUBBLE_SIZE,
            bg="#FFFFFF",
            highlightthickness=0,
            bd=0,
            cursor="hand2",
        )
        self.bubble_canvas = canvas
        canvas.pack(fill="both", expand=True)
        try:
            self.bubble_image = tk.PhotoImage(file=str(self._asset_path("dot-bubble.png")))
            canvas.create_image(BUBBLE_SIZE // 2, BUBBLE_SIZE // 2, image=self.bubble_image)
        except tk.TclError:
            self.bubble_image = None
            canvas.create_oval(1, 1, BUBBLE_SIZE - 2, BUBBLE_SIZE - 2, fill="#FFFFFF", outline="#D5DBE5", width=2)
            canvas.create_text(BUBBLE_SIZE // 2, BUBBLE_SIZE // 2, text="●", fill=Palette.ACCENT, font=("Segoe UI Symbol", 20))

        canvas.bind("<ButtonPress-1>", self._start_bubble_drag)
        canvas.bind("<B1-Motion>", self._drag_bubble)
        canvas.bind("<ButtonRelease-1>", self._release_bubble)
        # Windows only asks Tk to paint the launcher when it has stopped showing
        # the per-pixel-alpha surface (a display change, sleep/resume, a style
        # reset). Left alone, that paints the white canvas inside the circular
        # region: a plain white disc stuck on the desktop. Re-present the
        # transparent surface instead.
        for sequence in ("<Expose>", "<Configure>", "<Map>", "<Visibility>"):
            canvas.bind(sequence, self._schedule_bubble_repaint, add="+")
        Tooltip(canvas, "Click to open · drag to move")

    def _heal_bubble_styling(self) -> None:
        """Cheap per-poll check that the launcher still has its native styling.

        Windows can strip WS_EX_LAYERED or the window region without telling
        Tk (display or DPI changes, sleep/resume). The launcher then shows as
        an opaque white disc. Rebuild the styling when either is missing."""
        if sys.platform != "win32" or self.closing:
            return
        try:
            user32 = ctypes.windll.user32
            user32.GetWindowLongPtrW.argtypes = (ctypes.c_void_p, ctypes.c_int)
            user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
            hwnd = self._native_window_handle()
            ex_style = int(user32.GetWindowLongPtrW(hwnd, -20))  # GWL_EXSTYLE
            if not (ex_style & 0x00080000):  # WS_EX_LAYERED
                self._schedule_bubble_repaint()
        except (AttributeError, OSError):
            pass

    def _schedule_bubble_repaint(self, _event: tk.Event | None = None) -> None:
        if not self.minimized_to_bubble or self._bubble_repaint_job:
            return
        self._bubble_repaint_job = self.root.after(30, self._repaint_bubble_surface)

    def _repaint_bubble_surface(self) -> None:
        """Restore the transparent launcher if Windows dropped its surface."""
        self._bubble_repaint_job = None
        if not self.minimized_to_bubble or self.closing or sys.platform != "win32":
            return
        try:
            user32 = ctypes.windll.user32
            user32.GetWindowLongPtrW.argtypes = (ctypes.c_void_p, ctypes.c_int)
            user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
            user32.GetWindowRgn.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
            user32.GetWindowRgn.restype = ctypes.c_int
            gdi32 = ctypes.windll.gdi32
            gdi32.CreateRectRgn.argtypes = (ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int)
            gdi32.CreateRectRgn.restype = ctypes.c_void_p
            gdi32.DeleteObject.argtypes = (ctypes.c_void_p,)
            hwnd = self._native_window_handle()
            ex_style = int(user32.GetWindowLongPtrW(hwnd, -20))  # GWL_EXSTYLE
            scratch = gdi32.CreateRectRgn(0, 0, 0, 0)
            try:
                region_kind = user32.GetWindowRgn(hwnd, scratch) if scratch else 0
            finally:
                if scratch:
                    gdi32.DeleteObject(scratch)
            if not (ex_style & 0x00080000) or region_kind == 0:  # no WS_EX_LAYERED or no region
                # The native bubble styling was reset wholesale: rebuild it.
                self._set_native_bubble_mode(True)
                return

            class WindowRect(ctypes.Structure):
                _fields_ = (("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long))

            bounds = WindowRect()
            user32.GetWindowRect.argtypes = (ctypes.c_void_p, ctypes.POINTER(WindowRect))
            user32.GetWindowRect(hwnd, ctypes.byref(bounds))
            self._paint_layered_bubble(hwnd, max(1, bounds.right - bounds.left), max(1, bounds.bottom - bounds.top))
        except (AttributeError, OSError):
            pass

    def _native_window_handle(self) -> int:
        """Return Tk's actual top-level HWND instead of its inner client HWND."""
        user32 = ctypes.windll.user32
        user32.GetAncestor.argtypes = (ctypes.c_void_p, ctypes.c_uint)
        user32.GetAncestor.restype = ctypes.c_void_p
        client = self.root.winfo_id()
        return int(user32.GetAncestor(client, 2) or client)  # GA_ROOT

    def _set_native_bubble_mode(self, enabled: bool) -> None:
        """Apply a circular region and remove DWM's rectangular frame/shadow."""
        if sys.platform != "win32":
            return
        region: int | None = None
        try:
            user32 = ctypes.windll.user32
            gdi32 = ctypes.windll.gdi32
            dwmapi = ctypes.windll.dwmapi
            user32.SetWindowRgn.argtypes = (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_bool)
            user32.GetWindowLongPtrW.argtypes = (ctypes.c_void_p, ctypes.c_int)
            user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
            user32.SetWindowLongPtrW.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t)
            user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
            user32.GetClassLongPtrW.argtypes = (ctypes.c_void_p, ctypes.c_int)
            user32.GetClassLongPtrW.restype = ctypes.c_size_t
            user32.SetClassLongPtrW.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_size_t)
            user32.SetClassLongPtrW.restype = ctypes.c_size_t
            user32.SetWindowPos.argtypes = (
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_uint,
            )
            gdi32.CreateEllipticRgn.argtypes = (ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int)
            gdi32.CreateEllipticRgn.restype = ctypes.c_void_p
            gdi32.DeleteObject.argtypes = (ctypes.c_void_p,)
            hwnd = self._native_window_handle()
            if enabled:
                style = int(user32.GetWindowLongPtrW(hwnd, -20))  # GWL_EXSTYLE
                # Re-applying bubble mode (after Windows reset the styling)
                # must not capture the bubble's own styles as the "normal" ones.
                if self.normal_ex_style is None:
                    self.normal_ex_style = style
                bubble_style = (style | 0x00080000 | 0x00000080) & ~0x00040000  # LAYERED | TOOLWINDOW, no APPWINDOW
                user32.SetWindowLongPtrW(hwnd, -20, bubble_style)
                class_style = int(user32.GetClassLongPtrW(hwnd, -26))  # GCL_STYLE
                if self.normal_class_style is None:
                    self.normal_class_style = class_style
                user32.SetClassLongPtrW(hwnd, -26, class_style & ~0x00020000)  # no CS_DROPSHADOW

                class WindowRect(ctypes.Structure):
                    _fields_ = (
                        ("left", ctypes.c_long),
                        ("top", ctypes.c_long),
                        ("right", ctypes.c_long),
                        ("bottom", ctypes.c_long),
                    )

                bounds = WindowRect()
                user32.GetWindowRect.argtypes = (ctypes.c_void_p, ctypes.POINTER(WindowRect))
                user32.GetWindowRect(hwnd, ctypes.byref(bounds))
                window_width = max(1, bounds.right - bounds.left)
                window_height = max(1, bounds.bottom - bounds.top)
                region = gdi32.CreateEllipticRgn(0, 0, window_width + 1, window_height + 1)
                non_client_policy = ctypes.c_int(1)  # DWMNCRP_DISABLED
                corner_preference = ctypes.c_int(1)  # DWMWCP_DONOTROUND
                border_color = ctypes.c_uint(0xFFFFFFFE)  # DWMWA_COLOR_NONE
            else:
                if self.normal_ex_style is not None:
                    user32.SetWindowLongPtrW(hwnd, -20, self.normal_ex_style)
                    self.normal_ex_style = None
                if self.normal_class_style is not None:
                    user32.SetClassLongPtrW(hwnd, -26, self.normal_class_style)
                    self.normal_class_style = None
                user32.SetWindowPos(
                    hwnd,
                    None,
                    0,
                    0,
                    0,
                    0,
                    0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020,
                    # NOSIZE | NOMOVE | NOZORDER | NOACTIVATE | FRAMECHANGED
                )
                non_client_policy = ctypes.c_int(0)  # DWMNCRP_USEWINDOWSTYLE
                corner_preference = ctypes.c_int(2)  # DWMWCP_ROUND
                border_color = ctypes.c_uint(0xFFFFFFFF)  # DWMWA_COLOR_DEFAULT
            dwmapi.DwmSetWindowAttribute(
                hwnd,
                2,  # DWMWA_NCRENDERING_POLICY
                ctypes.byref(non_client_policy),
                ctypes.sizeof(non_client_policy),
            )
            dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(corner_preference), ctypes.sizeof(corner_preference))
            dwmapi.DwmSetWindowAttribute(hwnd, 34, ctypes.byref(border_color), ctypes.sizeof(border_color))
            if not user32.SetWindowRgn(hwnd, region, True) and region:
                gdi32.DeleteObject(region)
            elif enabled:
                self._paint_layered_bubble(hwnd, window_width, window_height)
        except (AttributeError, OSError):
            if region:
                try:
                    ctypes.windll.gdi32.DeleteObject(region)
                except (AttributeError, OSError):
                    pass

    def _premultiplied_bubble_pixels(self, width: int, height: int) -> bytes:
        """Return the launcher art as premultiplied BGRA rows, cached per size.

        The surface is re-presented whenever Windows drops it, so decoding and
        premultiplying the PNG must be cheap enough to repeat."""
        cache = getattr(self, "_bubble_surface_cache", None)
        if cache is None:
            cache = self._bubble_surface_cache = {}
        cached = cache.get((width, height))
        if cached is not None:
            return cached
        with Image.open(self._asset_path("dot-bubble.png")) as source:
            rendered = source.convert("RGBA").resize((width, height), Image.Resampling.LANCZOS)
        pixels = bytearray(rendered.tobytes("raw", "BGRA"))
        for offset in range(0, len(pixels), 4):
            alpha = pixels[offset + 3]
            pixels[offset] = pixels[offset] * alpha // 255
            pixels[offset + 1] = pixels[offset + 1] * alpha // 255
            pixels[offset + 2] = pixels[offset + 2] * alpha // 255
        cache[(width, height)] = bytes(pixels)
        return cache[(width, height)]

    def _paint_layered_bubble(self, hwnd: int, width: int, height: int) -> None:
        """Paint the circular launcher as a premultiplied per-pixel-alpha surface."""
        class Point(ctypes.Structure):
            _fields_ = (("x", ctypes.c_long), ("y", ctypes.c_long))

        class Size(ctypes.Structure):
            _fields_ = (("cx", ctypes.c_long), ("cy", ctypes.c_long))

        class BlendFunction(ctypes.Structure):
            _fields_ = (
                ("BlendOp", ctypes.c_ubyte),
                ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte),
                ("AlphaFormat", ctypes.c_ubyte),
            )

        class BitmapInfoHeader(ctypes.Structure):
            _fields_ = (
                ("biSize", ctypes.c_uint32),
                ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long),
                ("biPlanes", ctypes.c_ushort),
                ("biBitCount", ctypes.c_ushort),
                ("biCompression", ctypes.c_uint32),
                ("biSizeImage", ctypes.c_uint32),
                ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long),
                ("biClrUsed", ctypes.c_uint32),
                ("biClrImportant", ctypes.c_uint32),
            )

        class BitmapInfo(ctypes.Structure):
            _fields_ = (("bmiHeader", BitmapInfoHeader), ("bmiColors", ctypes.c_uint32 * 3))

        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        user32.GetDC.argtypes = (ctypes.c_void_p,)
        user32.GetDC.restype = ctypes.c_void_p
        user32.ReleaseDC.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
        gdi32.CreateCompatibleDC.argtypes = (ctypes.c_void_p,)
        gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
        gdi32.CreateDIBSection.argtypes = (
            ctypes.c_void_p,
            ctypes.POINTER(BitmapInfo),
            ctypes.c_uint,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.c_void_p,
            ctypes.c_uint,
        )
        gdi32.CreateDIBSection.restype = ctypes.c_void_p
        gdi32.SelectObject.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
        gdi32.SelectObject.restype = ctypes.c_void_p
        gdi32.DeleteObject.argtypes = (ctypes.c_void_p,)
        gdi32.DeleteDC.argtypes = (ctypes.c_void_p,)
        screen_dc = user32.GetDC(0)
        memory_dc = gdi32.CreateCompatibleDC(screen_dc)
        bitmap = 0
        previous_bitmap = 0
        try:
            info = BitmapInfo()
            info.bmiHeader.biSize = ctypes.sizeof(BitmapInfoHeader)
            info.bmiHeader.biWidth = width
            info.bmiHeader.biHeight = -height  # top-down RGBA rows
            info.bmiHeader.biPlanes = 1
            info.bmiHeader.biBitCount = 32
            bits = ctypes.c_void_p()
            bitmap = gdi32.CreateDIBSection(screen_dc, ctypes.byref(info), 0, ctypes.byref(bits), None, 0)
            if not bitmap or not bits.value:
                return
            previous_bitmap = gdi32.SelectObject(memory_dc, bitmap)

            pixels = self._premultiplied_bubble_pixels(width, height)
            ctypes.memmove(bits, pixels, len(pixels))

            class WindowRect(ctypes.Structure):
                _fields_ = (("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long))

            bounds = WindowRect()
            user32.GetWindowRect.argtypes = (ctypes.c_void_p, ctypes.POINTER(WindowRect))
            user32.GetWindowRect(hwnd, ctypes.byref(bounds))
            destination = Point(bounds.left, bounds.top)
            source_point = Point(0, 0)
            size = Size(width, height)
            blend = BlendFunction(0, 0, 255, 1)  # AC_SRC_OVER, AC_SRC_ALPHA
            user32.UpdateLayeredWindow.argtypes = (
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.POINTER(Point),
                ctypes.POINTER(Size),
                ctypes.c_void_p,
                ctypes.POINTER(Point),
                ctypes.c_uint,
                ctypes.POINTER(BlendFunction),
                ctypes.c_uint,
            )
            user32.UpdateLayeredWindow.restype = ctypes.c_bool
            presented = user32.UpdateLayeredWindow(
                hwnd,
                screen_dc,
                ctypes.byref(destination),
                ctypes.byref(size),
                memory_dc,
                ctypes.byref(source_point),
                0,
                ctypes.byref(blend),
                0x00000002,  # ULW_ALPHA
            )
            attempts = getattr(self, "_bubble_repaint_attempts", 0)
            if presented:
                self._bubble_repaint_attempts = 0
            elif not self.closing and attempts < 10:
                # Windows refused the surface (typically while a style change is
                # still settling). Without it the region shows the white canvas.
                self._bubble_repaint_attempts = attempts + 1
                self._schedule_bubble_repaint()
        finally:
            if previous_bitmap:
                gdi32.SelectObject(memory_dc, previous_bitmap)
            if bitmap:
                gdi32.DeleteObject(bitmap)
            if memory_dc:
                gdi32.DeleteDC(memory_dc)
            if screen_dc:
                user32.ReleaseDC(0, screen_dc)

    def _start_bubble_drag(self, event: tk.Event) -> None:
        window_x, window_y = self.root.winfo_x(), self.root.winfo_y()
        self.bubble_drag_start = (event.x_root, event.y_root, window_x, window_y)
        self.bubble_drag_position = (window_x, window_y)
        self.bubble_moved = False
        # Keep receiving motion and release events when the pointer crosses the
        # launcher's transparent pixels or moves faster than the small window.
        if self.bubble_canvas:
            try:
                self.bubble_canvas.grab_set()
            except tk.TclError:
                pass

    def _drag_bubble(self, event: tk.Event) -> None:
        if not self.bubble_drag_start:
            return
        start_x, start_y, window_x, window_y = self.bubble_drag_start
        delta_x, delta_y = event.x_root - start_x, event.y_root - start_y
        if abs(delta_x) > 3 or abs(delta_y) > 3:
            self.bubble_moved = True
        x, y = self._clamp_bubble_to_visible_work_area(
            window_x + delta_x,
            window_y + delta_y,
        )
        self._move_bubble(x, y)

    def _move_bubble(self, x: int, y: int) -> None:
        self.bubble_drag_position = (x, y)
        self._move_window(x, y)

    def _move_window(self, x: int, y: int) -> None:
        """Move the top-level without entering a nested Windows move loop.

        Repositioning through Tk's geometry manager re-lays out the whole
        window on every motion event, which makes a drag stutter. Asking
        Windows to move the frame directly keeps it smooth for both the
        expanded note and the collapsed bubble.
        """
        if sys.platform == "win32":
            try:
                user32 = ctypes.windll.user32
                user32.SetWindowPos.argtypes = (
                    ctypes.c_void_p,
                    ctypes.c_void_p,
                    ctypes.c_int,
                    ctypes.c_int,
                    ctypes.c_int,
                    ctypes.c_int,
                    ctypes.c_uint,
                )
                user32.SetWindowPos.restype = ctypes.c_bool
                if user32.SetWindowPos(
                    self._native_window_handle(),
                    None,
                    x,
                    y,
                    0,
                    0,
                    0x0001 | 0x0004 | 0x0010,  # NOSIZE | NOZORDER | NOACTIVATE
                ):
                    return
            except (AttributeError, OSError):
                pass
        self.root.geometry(f"+{x}+{y}")

    def _clamp_bubble_to_visible_work_area(self, x: int, y: int) -> tuple[int, int]:
        geometry = f"{BUBBLE_SIZE}x{BUBBLE_SIZE}{x:+d}{y:+d}"
        return clamp_bubble_to_work_area(x, y, self._work_area_for_geometry(geometry))

    def _release_bubble(self, _event: tk.Event) -> None:
        if not self.bubble_drag_start:
            return
        self._finish_bubble_drag()

    def _finish_bubble_drag(self) -> None:
        if not self.bubble_drag_start:
            return
        _, _, window_x, window_y = self.bubble_drag_start
        self.bubble_drag_start = None
        final_x, final_y = self.bubble_drag_position or (self.root.winfo_x(), self.root.winfo_y())
        final_x, final_y = self._clamp_bubble_to_visible_work_area(final_x, final_y)
        self.bubble_drag_position = None
        if self.bubble_canvas:
            try:
                self.bubble_canvas.grab_release()
            except tk.TclError:
                pass
        if (final_x, final_y) != (self.root.winfo_x(), self.root.winfo_y()):
            self._move_bubble(final_x, final_y)
            self.bubble_drag_position = None
        if abs(final_x - window_x) > 3 or abs(final_y - window_y) > 3:
            self.bubble_moved = True
        if self.bubble_moved:
            self.settings.data["bubble_position"] = f"{final_x},{final_y}"
            self.settings.save()
            return
        self.restore_from_bubble()

    def restore_from_bubble(self) -> None:
        if not self.minimized_to_bubble:
            return
        geometry = self._restored_window_geometry()
        if self._bubble_repaint_job:
            try:
                self.root.after_cancel(self._bubble_repaint_job)
            except tk.TclError:
                pass
            self._bubble_repaint_job = None
        if self.bubble_canvas and self.bubble_canvas.winfo_exists():
            self.bubble_canvas.destroy()
        self.bubble_canvas = None
        self.bubble_image = None
        self._set_native_bubble_mode(False)
        self.root.configure(bg=Palette.BORDER)
        self.root.minsize(MIN_WINDOW_WIDTH, MIN_WINDOW_HEIGHT)
        self.border.pack(fill="both", expand=True)
        self.root.geometry(geometry)
        self.root.update_idletasks()
        # Reapply after Tk has recalculated the packed contents and Windows has
        # consumed the native style change from tool/layered window to widget.
        self.root.geometry(geometry)
        self.root.attributes("-topmost", bool(self.settings.data.get("topmost", True)))
        self.minimized_to_bubble = False
        self.root.lift()
        self.root.focus_force()
        self._own_window_was_foreground = True
        self.root.after(20, self._apply_windows_style)

    def _restored_window_geometry(self) -> str:
        geometry = self.normal_geometry or str(self.settings.data.get("geometry", "400x540+80+80"))
        return clamp_window_geometry(geometry, self._work_area_for_geometry(geometry))

    def _work_area_for_geometry(self, geometry: str) -> tuple[int, int, int, int]:
        """Return the nearest monitor's usable bounds, excluding its taskbar."""
        fallback = (0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight())
        if sys.platform != "win32":
            return fallback
        match = re.fullmatch(r"(\d+)x(\d+)([+-]\d+)([+-]\d+)", geometry)
        if not match:
            return fallback

        class WindowRect(ctypes.Structure):
            _fields_ = (("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long))

        class MonitorInfo(ctypes.Structure):
            _fields_ = (("size", ctypes.c_uint), ("monitor", WindowRect), ("work", WindowRect), ("flags", ctypes.c_uint))

        try:
            width, height, x, y = (int(value) for value in match.groups())
            window = WindowRect(x, y, x + width, y + height)
            user32 = ctypes.windll.user32
            user32.MonitorFromRect.argtypes = (ctypes.POINTER(WindowRect), ctypes.c_uint)
            user32.MonitorFromRect.restype = ctypes.c_void_p
            user32.GetMonitorInfoW.argtypes = (ctypes.c_void_p, ctypes.POINTER(MonitorInfo))
            monitor = user32.MonitorFromRect(ctypes.byref(window), 2)  # MONITOR_DEFAULTTONEAREST
            info = MonitorInfo()
            info.size = ctypes.sizeof(MonitorInfo)
            if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                return info.work.left, info.work.top, info.work.right, info.work.bottom
        except (AttributeError, OSError):
            pass
        return fallback

    def _restore_override(self, _event: tk.Event) -> None:
        if self.root.state() == "normal" and not self.minimized_to_bubble:
            self.root.after(10, lambda: self.root.overrideredirect(True))
            self.root.after(20, self._apply_windows_style)

    def _apply_windows_style(self) -> None:
        if sys.platform != "win32":
            return
        try:
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            corner_preference = ctypes.c_int(2)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(corner_preference), ctypes.sizeof(corner_preference))
        except (AttributeError, OSError):
            pass

    def close(self) -> None:
        self.flush_save()
        self.closing = True
        if self.root.state() == "normal" and not self.minimized_to_bubble:
            self.settings.data["geometry"] = self.root.geometry()
        self.settings.save()
        self.executor.shutdown(wait=False)
        self.root.destroy()


def main() -> None:
    parser = argparse.ArgumentParser(description="StickyOmelet always-on-top Google Keep notes widget.")
    parser.add_argument("--note", nargs="+", help="Open a Google Keep note by title or ID")
    args = parser.parse_args()
    requested_note = " ".join(args.note) if args.note else None
    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        except (AttributeError, OSError):
            pass
        try:
            # If the UI thread ever stalls, Windows replaces the window with a
            # frosted "ghost" copy. For the borderless dot that ghost is a
            # white disc left on the desktop with no frame to explain it.
            ctypes.windll.user32.DisableProcessWindowsGhosting()
        except (AttributeError, OSError):
            pass
    root = tk.Tk()
    WindowsStartup.migrate_legacy_entry()
    NotesWidget(root, requested_note=requested_note)
    root.mainloop()


if __name__ == "__main__":
    main()

"""Tkinter GUI for the ADS-B Stack Manager."""

from __future__ import annotations

import sys
import threading
import time
import tkinter as tk
import webbrowser
from collections.abc import Callable
from tkinter import messagebox, ttk
from typing import Any

from adsbmgr import settings as app_settings
from adsbmgr.backend import AdsBBackend
from adsbmgr.config import SSH_PORT, SSH_USER

# ── palette ────────────────────────────────────────────────────────────────
BG = "#0d1117"
BG_RAISED = "#161b22"
BG_INPUT = "#21262d"
BG_HOVER = "#30363d"
FG = "#e6edf3"
FG_DIM = "#8b949e"
CYAN = "#39d2c0"
BLUE = "#58a6ff"
GREEN = "#3fb950"
AMBER = "#f0b429"
RED = "#f85149"
BORDER = "#30363d"
CONSOLE_BG = "#010409"

UI = ("Segoe UI", 10)
UI_SM = ("Segoe UI", 9)
UI_BOLD = ("Segoe UI", 10, "bold")
TITLE = ("Segoe UI", 16, "bold")
CAPTION = ("Segoe UI", 8)
MONO = ("Consolas", 10)
STAT = ("Segoe UI", 20, "bold")


def _enable_windows_dpi() -> None:
    if sys.platform != "win32":
        return
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            from ctypes import windll
            windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _btn(
    parent: tk.Misc,
    text: str,
    command: Callable[[], None],
    kind: str = "ghost",
    width: int | None = None,
) -> tk.Button:
    palette = {
        "ghost": (BG_INPUT, FG, BG_HOVER),
        "primary": ("#238636", "#ffffff", "#2ea043"),
        "accent": ("#1f6feb", "#ffffff", "#388bfd"),
        "danger": ("#da3633", "#ffffff", "#f85149"),
        "warn": ("#9e6a03", "#ffffff", "#bb8009"),
    }
    bg, fg, active = palette[kind]
    return tk.Button(
        parent,
        text=text,
        command=command,
        bg=bg,
        fg=fg,
        activebackground=active,
        activeforeground=fg,
        disabledforeground=FG_DIM,
        relief="flat",
        bd=0,
        padx=12,
        pady=5,
        cursor="hand2",
        font=UI_SM,
        highlightthickness=0,
        width=width or 0,
    )


class ServiceRow:
    """One service line with always-visible Start / Stop / Restart."""

    def __init__(
        self,
        parent: tk.Misc,
        name: str,
        unit: str,
        on_select: Callable[["ServiceRow"], None],
        on_action: Callable[[str, str], None],
    ) -> None:
        self.name = name
        self.unit = unit
        self.state = "unknown"
        self._on_select = on_select
        self._on_action = on_action
        self._connected = False

        self.frame = tk.Frame(parent, bg=BG_RAISED, highlightthickness=0)
        self.frame.pack(fill=tk.X, padx=6, pady=2)

        self.dot = tk.Label(self.frame, text="●", font=("Segoe UI", 11), bg=BG_RAISED, fg=FG_DIM, width=2)
        self.dot.pack(side=tk.LEFT, padx=(6, 2))

        self.info = tk.Frame(self.frame, bg=BG_RAISED)
        self.info.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.name_lbl = tk.Label(self.info, text=name, font=UI_BOLD, bg=BG_RAISED, fg=FG, anchor="w")
        self.name_lbl.pack(anchor="w")
        self.meta_lbl = tk.Label(self.info, text=f"{unit}  ·  —", font=CAPTION, bg=BG_RAISED, fg=FG_DIM, anchor="w")
        self.meta_lbl.pack(anchor="w")

        btns = tk.Frame(self.frame, bg=BG_RAISED)
        btns.pack(side=tk.RIGHT, padx=6, pady=6)
        self.btn_start = _btn(btns, "Start", lambda: on_action(unit, "start"), "primary")
        self.btn_stop = _btn(btns, "Stop", lambda: on_action(unit, "stop"), "danger")
        self.btn_restart = _btn(btns, "Restart", lambda: on_action(unit, "restart"), "ghost")
        for b in (self.btn_start, self.btn_stop, self.btn_restart):
            b.pack(side=tk.LEFT, padx=2)
            b.configure(padx=8, pady=3)

        for w in (self.frame, self.dot, self.info, self.name_lbl, self.meta_lbl):
            w.bind("<Button-1>", lambda _e: on_select(self))
            w.bind("<Double-Button-1>", lambda _e: on_select(self))

        self.set_connected(False)
        self.set_selected(False)

    def set_state(self, state: str) -> None:
        self.state = state
        if state == "active":
            color, tag = GREEN, "active"
        elif state in ("activating", "reloading", "deactivating"):
            color, tag = AMBER, state
        else:
            color, tag = RED, state
        self.dot.configure(fg=color)
        self.meta_lbl.configure(text=f"{self.unit}  ·  {tag}")
        self._sync_buttons()

    def set_connected(self, connected: bool) -> None:
        self._connected = connected
        self._sync_buttons()

    def set_selected(self, selected: bool) -> None:
        bg = BG_HOVER if selected else BG_RAISED
        for w in (self.frame, self.dot, self.info, self.name_lbl, self.meta_lbl, self.btn_start.master):
            w.configure(bg=bg)

    def _sync_buttons(self) -> None:
        if not self._connected:
            for b in (self.btn_start, self.btn_stop, self.btn_restart):
                b.configure(state=tk.DISABLED)
            return
        active = self.state == "active"
        self.btn_start.configure(state=tk.DISABLED if active else tk.NORMAL)
        self.btn_stop.configure(state=tk.NORMAL if active else tk.DISABLED)
        self.btn_restart.configure(state=tk.NORMAL)


class AdsBApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("ADS-B Stack Manager")
        self.minsize(980, 680)
        self.geometry("1120x780")
        self.configure(bg=BG)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.backend = AdsBBackend(log=self._thread_log)
        self._busy = False
        self._tailing = False
        self._refresh_after: str | None = None
        self._prefs = app_settings.load()
        self._maps: dict[str, str] = {}

        self._build_styles()
        self._build()
        self._set_connected(False)
        self.log("Ready. Connect to a feeder — services and maps are discovered on that host.", "dim")
        self.log("Host, user, and map host are remembered. Passwords are never saved.", "dim")

    # ── styles ─────────────────────────────────────────────────────────────

    def _build_styles(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(".", background=BG, foreground=FG, fieldbackground=BG_INPUT)
        style.configure(
            "Treeview",
            background=BG_RAISED,
            fieldbackground=BG_RAISED,
            foreground=FG,
            rowheight=28,
            borderwidth=0,
            font=UI,
        )
        style.configure(
            "Treeview.Heading",
            background=BG_INPUT,
            foreground=CYAN,
            relief="flat",
            font=UI_BOLD,
            padding=4,
        )
        style.map(
            "Treeview",
            background=[("selected", "#1f6feb")],
            foreground=[("selected", "#ffffff")],
        )
        style.configure(
            "Dark.Vertical.TScrollbar",
            background=BG_INPUT,
            troughcolor=BG,
            bordercolor=BG,
            arrowcolor=FG_DIM,
        )
        style.configure(
            "Dark.Horizontal.TPanedwindow",
            background=BG,
        )
        style.configure(
            "Card.TLabelframe",
            background=BG_RAISED,
            foreground=CYAN,
            bordercolor=BORDER,
            relief="flat",
        )
        style.configure(
            "Card.TLabelframe.Label",
            background=BG_RAISED,
            foreground=CYAN,
            font=UI_BOLD,
        )
        style.configure(
            "TCombobox",
            fieldbackground=BG_INPUT,
            background=BG_INPUT,
            foreground=FG,
            arrowcolor=FG,
            bordercolor=BORDER,
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", BG_INPUT), ("disabled", BG_RAISED)],
            foreground=[("disabled", FG_DIM)],
        )

    # ── layout ─────────────────────────────────────────────────────────────

    def _build(self) -> None:
        self._build_header()
        self._build_connection()
        self._build_actions()

        panes = ttk.Panedwindow(self, orient=tk.VERTICAL, style="Dark.Horizontal.TPanedwindow")
        panes.pack(fill=tk.BOTH, expand=True, padx=12, pady=(8, 12))

        upper = tk.Frame(panes, bg=BG)
        lower = tk.Frame(panes, bg=BG)
        panes.add(upper, weight=3)
        panes.add(lower, weight=2)

        self._build_workspace(upper)
        self._build_console(lower)

    def _build_header(self) -> None:
        bar = tk.Frame(self, bg=BG_RAISED, highlightbackground=BORDER, highlightthickness=1)
        bar.pack(fill=tk.X, padx=12, pady=(12, 0))

        left = tk.Frame(bar, bg=BG_RAISED)
        left.pack(side=tk.LEFT, padx=16, pady=12)
        tk.Label(left, text="ADS-B STACK MANAGER", font=TITLE, bg=BG_RAISED, fg=CYAN).pack(anchor="w")
        tk.Label(
            left,
            text="Works with any SSH feeder  ·  services and maps discovered on connect",
            font=CAPTION,
            bg=BG_RAISED,
            fg=FG_DIM,
        ).pack(anchor="w")

        right = tk.Frame(bar, bg=BG_RAISED)
        right.pack(side=tk.RIGHT, padx=16)
        self.status_dot = tk.Label(right, text="●", font=("Segoe UI", 14), bg=BG_RAISED, fg=RED)
        self.status_dot.pack(side=tk.LEFT, padx=(0, 6))
        self.status_lbl = tk.Label(right, text="Disconnected", font=UI_BOLD, bg=BG_RAISED, fg=FG_DIM)
        self.status_lbl.pack(side=tk.LEFT)

    def _build_connection(self) -> None:
        row = tk.Frame(self, bg=BG)
        row.pack(fill=tk.X, padx=12, pady=(10, 0))

        def field(label: str) -> tk.Frame:
            box = tk.Frame(row, bg=BG)
            tk.Label(box, text=label, font=CAPTION, bg=BG, fg=FG_DIM).pack(anchor="w")
            return box

        host_box = field("HOST")
        host_box.pack(side=tk.LEFT, padx=(0, 8))
        recent = [h for h in self._prefs.get("recent_hosts", []) if h]
        last_host = self._prefs.get("ssh_host") or (recent[0] if recent else "")
        self.host_var = tk.StringVar(value=last_host)
        self.host_entry = ttk.Combobox(
            host_box,
            textvariable=self.host_var,
            values=recent,
            width=22,
            font=UI,
        )
        self.host_entry.pack()

        user_box = field("USER")
        user_box.pack(side=tk.LEFT, padx=8)
        self.user_var = tk.StringVar(value=self._prefs.get("ssh_user") or SSH_USER)
        self.user_entry = tk.Entry(
            user_box, textvariable=self.user_var, width=12, font=UI,
            bg=BG_INPUT, fg=FG, insertbackground=FG, relief="flat",
            highlightthickness=1, highlightbackground=BORDER, highlightcolor=CYAN,
        )
        self.user_entry.pack(ipady=4)

        pass_box = field("PASSWORD")
        pass_box.pack(side=tk.LEFT, padx=8)
        self.pass_var = tk.StringVar()
        self.pass_entry = tk.Entry(
            pass_box, textvariable=self.pass_var, width=18, font=UI, show="•",
            bg=BG_INPUT, fg=FG, insertbackground=FG, relief="flat",
            highlightthickness=1, highlightbackground=BORDER, highlightcolor=CYAN,
        )
        self.pass_entry.pack(ipady=4)
        self.pass_entry.bind("<Return>", lambda _e: self._on_connect())

        show_box = field(" ")
        show_box.pack(side=tk.LEFT)
        self.show_pass = tk.BooleanVar(value=False)
        tk.Checkbutton(
            show_box,
            text="Show",
            variable=self.show_pass,
            command=self._toggle_password,
            bg=BG,
            fg=FG_DIM,
            selectcolor=BG_INPUT,
            activebackground=BG,
            activeforeground=FG,
            highlightthickness=0,
            font=CAPTION,
        ).pack(pady=(2, 0))

        btn_box = field(" ")
        btn_box.pack(side=tk.LEFT, padx=(12, 0))
        self.connect_btn = _btn(btn_box, "Connect", self._on_connect, "primary", width=12)
        self.connect_btn.pack()

        hint = tk.Label(
            row,
            text="Keys in ~/.ssh first. Password only if key auth fails.",
            font=CAPTION,
            bg=BG,
            fg=FG_DIM,
        )
        hint.pack(side=tk.RIGHT, padx=4)

    def _build_workspace(self, parent: tk.Frame) -> None:
        wrap = tk.Frame(parent, bg=BG)
        wrap.pack(fill=tk.BOTH, expand=True)

        # Services — rows with Start / Stop / Restart always visible
        left = tk.Frame(wrap, bg=BG_RAISED, highlightbackground=BORDER, highlightthickness=1)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 6))
        svc_head = tk.Frame(left, bg=BG_RAISED)
        svc_head.pack(fill=tk.X, padx=12, pady=(10, 4))
        tk.Label(svc_head, text="SERVICES", font=UI_BOLD, bg=BG_RAISED, fg=CYAN).pack(side=tk.LEFT)
        tk.Label(
            svc_head,
            text="click a row for logs / tail",
            font=CAPTION,
            bg=BG_RAISED,
            fg=FG_DIM,
        ).pack(side=tk.RIGHT)

        list_wrap = tk.Frame(left, bg=BG_RAISED)
        list_wrap.pack(fill=tk.BOTH, expand=True, pady=(0, 8))
        self._svc_canvas = tk.Canvas(list_wrap, bg=BG_RAISED, highlightthickness=0)
        svc_scroll = ttk.Scrollbar(
            list_wrap, orient=tk.VERTICAL, command=self._svc_canvas.yview,
            style="Dark.Vertical.TScrollbar",
        )
        self._svc_list = tk.Frame(self._svc_canvas, bg=BG_RAISED)
        self._svc_win = self._svc_canvas.create_window((0, 0), window=self._svc_list, anchor="nw")
        self._svc_canvas.configure(yscrollcommand=svc_scroll.set)
        self._svc_list.bind(
            "<Configure>",
            lambda _e: self._svc_canvas.configure(scrollregion=self._svc_canvas.bbox("all")),
        )
        self._svc_canvas.bind(
            "<Configure>",
            lambda e: self._svc_canvas.itemconfigure(self._svc_win, width=e.width),
        )
        self._svc_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        svc_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.service_rows: dict[str, ServiceRow] = {}
        self._selected_row: ServiceRow | None = None
        self._svc_empty = tk.Label(
            self._svc_list,
            text="Connect to a feeder to discover its services.",
            font=UI,
            bg=BG_RAISED,
            fg=FG_DIM,
            justify="left",
            anchor="w",
            padx=12,
            pady=16,
        )
        self._svc_empty.pack(fill=tk.X)

        # Stats + map picker
        right = tk.Frame(wrap, bg=BG, width=440)
        right.pack(side=tk.RIGHT, fill=tk.BOTH, padx=(6, 0))
        right.pack_propagate(False)

        recv = tk.Frame(right, bg=BG_RAISED, highlightbackground=BORDER, highlightthickness=1)
        recv.pack(fill=tk.X)
        head = tk.Frame(recv, bg=BG_RAISED)
        head.pack(fill=tk.X, padx=12, pady=(10, 4))
        tk.Label(head, text="RECEIVER", font=UI_BOLD, bg=BG_RAISED, fg=CYAN).pack(side=tk.LEFT)

        self._build_map_picker(recv)

        stats_grid = tk.Frame(recv, bg=BG_RAISED)
        stats_grid.pack(fill=tk.X, padx=8, pady=(4, 12))
        self.stat_ac = self._stat_cell(stats_grid, 0, 0, "AIRCRAFT")
        self.stat_pos = self._stat_cell(stats_grid, 0, 1, "WITH POSITION")
        self.stat_rate = self._stat_cell(stats_grid, 0, 2, "MSG / S")
        self.stat_range = self._stat_cell(stats_grid, 1, 0, "MAX RANGE")
        self.stat_cpr = self._stat_cell(stats_grid, 1, 1, "CPR (1 MIN)")
        self.stat_total = self._stat_cell(stats_grid, 1, 2, "SESSION MSGS")

        sysf = tk.Frame(right, bg=BG_RAISED, highlightbackground=BORDER, highlightthickness=1)
        sysf.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        tk.Label(sysf, text="SYSTEM", font=UI_BOLD, bg=BG_RAISED, fg=CYAN).pack(
            anchor="w", padx=12, pady=(10, 6)
        )
        self.sys_uptime = self._kv(sysf, "Uptime")
        self.sys_temp = self._kv(sysf, "CPU temp")
        self.sys_mem = self._kv(sysf, "Memory")
        self.sys_disk = self._kv(sysf, "Disk /")

    def _build_map_picker(self, parent: tk.Frame) -> None:
        box = tk.Frame(parent, bg=BG_INPUT)
        box.pack(fill=tk.X, padx=10, pady=(0, 8))

        row1 = tk.Frame(box, bg=BG_INPUT)
        row1.pack(fill=tk.X, padx=8, pady=(8, 4))
        tk.Label(row1, text="MAP HOST", font=CAPTION, bg=BG_INPUT, fg=FG_DIM).pack(side=tk.LEFT)
        saved_web = self._prefs.get("web_host") or self._prefs.get("ssh_host") or ""
        self.web_host_var = tk.StringVar(value=saved_web)
        self.web_host_entry = ttk.Combobox(
            row1,
            textvariable=self.web_host_var,
            values=[h for h in self._prefs.get("recent_hosts", []) if h],
            width=18,
            font=UI_SM,
        )
        self.web_host_entry.pack(side=tk.LEFT, padx=(8, 0))
        self.web_host_var.trace_add("write", lambda *_: self._refresh_map_url())

        row2 = tk.Frame(box, bg=BG_INPUT)
        row2.pack(fill=tk.X, padx=8, pady=4)
        tk.Label(row2, text="OPEN", font=CAPTION, bg=BG_INPUT, fg=FG_DIM).pack(side=tk.LEFT)
        self.map_var = tk.StringVar(value="")
        self.map_combo = ttk.Combobox(
            row2,
            textvariable=self.map_var,
            values=[],
            state="readonly",
            width=22,
            font=UI_SM,
        )
        self.map_combo.pack(side=tk.LEFT, padx=(8, 8), fill=tk.X, expand=True)
        self.map_combo.bind("<<ComboboxSelected>>", lambda _e: self._refresh_map_url())
        self.map_btn = _btn(row2, "Open map", self._open_map, "accent")
        self.map_btn.pack(side=tk.RIGHT)

        self.map_url_lbl = tk.Label(box, text="", font=CAPTION, bg=BG_INPUT, fg=BLUE, anchor="w")
        self.map_url_lbl.pack(fill=tk.X, padx=8, pady=(0, 8))
        self.map_url_lbl.bind("<Button-1>", lambda _e: self._open_map())
        self._refresh_map_url()

    def _stat_cell(self, parent: tk.Frame, r: int, c: int, caption: str) -> tk.Label:
        cell = tk.Frame(parent, bg=BG_INPUT, padx=8, pady=8)
        cell.grid(row=r, column=c, sticky="nsew", padx=4, pady=4)
        parent.grid_columnconfigure(c, weight=1)
        parent.grid_rowconfigure(r, weight=1)
        value = tk.Label(cell, text="—", font=STAT, bg=BG_INPUT, fg=FG)
        value.pack()
        tk.Label(cell, text=caption, font=CAPTION, bg=BG_INPUT, fg=FG_DIM).pack()
        return value

    def _kv(self, parent: tk.Frame, key: str) -> tk.Label:
        row = tk.Frame(parent, bg=BG_RAISED)
        row.pack(fill=tk.X, padx=14, pady=3)
        tk.Label(row, text=key, font=UI_SM, bg=BG_RAISED, fg=FG_DIM, width=10, anchor="w").pack(side=tk.LEFT)
        val = tk.Label(row, text="—", font=UI, bg=BG_RAISED, fg=FG, anchor="w")
        val.pack(side=tk.LEFT, fill=tk.X, expand=True)
        return val

    def _build_actions(self) -> None:
        """Always-visible toolbar — packed above the resizable panes so it cannot be clipped."""
        wrap = tk.Frame(self, bg=BG)
        wrap.pack(fill=tk.X, padx=12, pady=(10, 0))

        bar = tk.Frame(wrap, bg=BG_RAISED, highlightbackground=BORDER, highlightthickness=1)
        bar.pack(fill=tk.X)
        inner = tk.Frame(bar, bg=BG_RAISED)
        inner.pack(fill=tk.X, padx=8, pady=8)

        left = tk.Frame(inner, bg=BG_RAISED)
        left.pack(side=tk.LEFT)
        self.btn_refresh = _btn(left, "Refresh", self._on_refresh, "accent")
        self.btn_logs = _btn(left, "Logs", self._on_logs)
        self.btn_tail = _btn(left, "Live tail", self._on_tail)
        self.btn_stop_tail = _btn(left, "Stop tail", self._on_stop_tail, "warn")
        tk.Label(left, text="lines", font=CAPTION, bg=BG_RAISED, fg=FG_DIM).pack(side=tk.LEFT, padx=(8, 4))
        self.lines_var = tk.StringVar(value="60")
        self.lines_entry = tk.Entry(
            left, textvariable=self.lines_var, width=5, font=UI_SM,
            bg=BG_INPUT, fg=FG, insertbackground=FG, relief="flat",
            highlightthickness=1, highlightbackground=BORDER, highlightcolor=CYAN,
            justify="center",
        )
        self.lines_entry.pack(side=tk.LEFT, ipady=3)
        for b in (self.btn_refresh, self.btn_logs, self.btn_tail, self.btn_stop_tail):
            b.pack(side=tk.LEFT, padx=(0, 6))

        right = tk.Frame(inner, bg=BG_RAISED)
        right.pack(side=tk.RIGHT)
        self.btn_net = _btn(right, "Network", self._on_network)
        self.btn_sdr = _btn(right, "SDR check", self._on_sdr)
        self.btn_upd = _btn(right, "Updates", self._on_updates)
        self.btn_restart_all = _btn(right, "Restart all", self._on_restart_all, "warn")
        self.btn_stop_all = _btn(right, "Stop all", self._on_stop_all, "danger")
        self.btn_reboot = _btn(right, "Reboot", self._on_reboot, "danger")
        for b in (
            self.btn_net, self.btn_sdr, self.btn_upd,
            self.btn_restart_all, self.btn_stop_all, self.btn_reboot,
        ):
            b.pack(side=tk.LEFT, padx=(0, 6))

        self._action_buttons = [
            self.btn_refresh, self.btn_logs, self.btn_tail, self.btn_net,
            self.btn_sdr, self.btn_upd, self.btn_restart_all, self.btn_stop_all,
            self.btn_reboot,
        ]

    def _build_console(self, parent: tk.Frame) -> None:
        head = tk.Frame(parent, bg=BG)
        head.pack(fill=tk.X, pady=(0, 4))
        tk.Label(head, text="CONSOLE", font=UI_BOLD, bg=BG, fg=CYAN).pack(side=tk.LEFT)
        _btn(head, "Clear", self._clear_console, "ghost").pack(side=tk.RIGHT)

        frame = tk.Frame(parent, bg=CONSOLE_BG, highlightbackground=BORDER, highlightthickness=1)
        frame.pack(fill=tk.BOTH, expand=True)
        self.console = tk.Text(
            frame,
            bg=CONSOLE_BG,
            fg=FG,
            insertbackground=FG,
            relief="flat",
            font=MONO,
            wrap="word",
            state="disabled",
            padx=10,
            pady=8,
            highlightthickness=0,
        )
        yscroll = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self.console.yview, style="Dark.Vertical.TScrollbar")
        self.console.configure(yscrollcommand=yscroll.set)
        self.console.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        yscroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.console.tag_configure("info", foreground=FG)
        self.console.tag_configure("ok", foreground=GREEN)
        self.console.tag_configure("warn", foreground=AMBER)
        self.console.tag_configure("err", foreground=RED)
        self.console.tag_configure("dim", foreground="#6e7681")
        self.console.tag_configure("cmd", foreground=CYAN)

    # ── console ────────────────────────────────────────────────────────────

    def log(self, text: str, level: str = "info") -> None:
        self.console.configure(state="normal")
        ts = time.strftime("%H:%M:%S")
        self.console.insert("end", f"{ts}  ", "dim")
        self.console.insert("end", text.rstrip() + "\n", level)
        self.console.see("end")
        self.console.configure(state="disabled")

    def _thread_log(self, msg: str, level: str = "info") -> None:
        self.after(0, lambda m=msg, lv=level: self.log(m, lv))

    def _clear_console(self) -> None:
        self.console.configure(state="normal")
        self.console.delete("1.0", "end")
        self.console.configure(state="disabled")

    # ── connection / busy ──────────────────────────────────────────────────

    def _toggle_password(self) -> None:
        self.pass_entry.configure(show="" if self.show_pass.get() else "•")

    def _set_connected(self, connected: bool) -> None:
        if connected:
            self.status_dot.configure(fg=GREEN)
            self.status_lbl.configure(text=f"{self.backend.user}@{self.backend.host}", fg=GREEN)
            self.connect_btn.configure(text="Disconnect", bg="#da3633", activebackground="#f85149")
            self.connect_btn.configure(command=self._on_disconnect)
        else:
            self.status_dot.configure(fg=RED)
            self.status_lbl.configure(text="Disconnected", fg=FG_DIM)
            self.connect_btn.configure(text="Connect", bg="#238636", activebackground="#2ea043")
            self.connect_btn.configure(command=self._on_connect)
            for row in self.service_rows.values():
                row.set_state("unknown")
                row.set_connected(False)
            for lbl in (
                self.stat_ac, self.stat_pos, self.stat_rate,
                self.stat_range, self.stat_cpr, self.stat_total,
            ):
                lbl.configure(text="—")
            for lbl in (self.sys_uptime, self.sys_temp, self.sys_mem, self.sys_disk):
                lbl.configure(text="—")
        state = tk.NORMAL if connected and not self._busy else tk.DISABLED
        for b in self._action_buttons:
            b.configure(state=state)
        for row in self.service_rows.values():
            row.set_connected(connected and not self._busy)
        self.btn_stop_tail.configure(state=tk.NORMAL if self._tailing else tk.DISABLED)
        entry_state = tk.DISABLED if connected else tk.NORMAL
        self.host_entry.configure(state=entry_state)
        self.user_entry.configure(state=entry_state)
        self.pass_entry.configure(state=entry_state)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        connected = self.backend.is_connected()
        if connected:
            self._set_connected(True)
        self.connect_btn.configure(state=tk.DISABLED if busy and not self._tailing else tk.NORMAL)

    def _selected_unit(self) -> tuple[str, str] | None:
        row = self._selected_row
        if row is None:
            messagebox.showinfo("Select a service", "Click a service in the list first.")
            return None
        return row.name, row.unit

    def _on_row_select(self, row: ServiceRow) -> None:
        self._selected_row = row
        for other in self.service_rows.values():
            other.set_selected(other is row)
        if not hasattr(self, "map_var"):
            return
        map_name = self.backend.unit_to_map.get(row.unit)
        if map_name and map_name in self._maps:
            self.map_var.set(map_name)
            self._refresh_map_url()

    def _on_row_action(self, unit: str, action: str) -> None:
        if not self.backend.is_connected():
            return
        self._run_async(
            self.backend.service_action, unit, action,
            on_done=lambda _r: self._on_refresh(),
        )

    def _rebuild_services(self, services: list[dict[str, Any]]) -> None:
        for row in list(self.service_rows.values()):
            row.frame.destroy()
        self.service_rows.clear()
        self._selected_row = None

        if not services:
            self._svc_empty.configure(text="No known ADS-B services found on this host.")
            self._svc_empty.pack(fill=tk.X)
            return
        self._svc_empty.pack_forget()

        connected = self.backend.is_connected() and not self._busy
        for svc in services:
            row = ServiceRow(
                self._svc_list, svc["name"], svc["unit"],
                self._on_row_select, self._on_row_action,
            )
            row.set_state(svc.get("state") or "unknown")
            row.set_connected(connected)
            self.service_rows[svc["unit"]] = row
        first = next(iter(self.service_rows.values()), None)
        if first:
            self._on_row_select(first)

    def _apply_maps(self, maps: dict[str, str]) -> None:
        self._maps = dict(maps)
        names = list(self._maps)
        self.map_combo.configure(values=names)
        if names and self.map_var.get() not in names:
            self.map_var.set(names[0])
        elif not names:
            self.map_var.set("")
        self._refresh_map_url()

    def _save_prefs(self) -> None:
        app_settings.save({
            "ssh_host": self.host_var.get().strip(),
            "ssh_user": self.user_var.get().strip(),
            "web_host": self.web_host_var.get().strip(),
        })
        self._prefs = app_settings.load()
        recent = list(self._prefs.get("recent_hosts") or [])
        self.host_entry.configure(values=recent)
        self.web_host_entry.configure(values=recent)

    def _map_url(self) -> str:
        host = (self.web_host_var.get() or self.host_var.get() or "localhost").strip()
        template = self._maps.get(self.map_var.get())
        if not template:
            return f"http://{host}:8080/"
        return template.format(host=host)

    def _refresh_map_url(self) -> None:
        if hasattr(self, "map_url_lbl"):
            self.map_url_lbl.configure(text=self._map_url())

    def _run_async(
        self,
        fn: Callable[..., Any],
        *args: Any,
        on_done: Callable[[Any], None] | None = None,
        busy: bool = True,
        **kwargs: Any,
    ) -> None:
        if busy:
            self._set_busy(True)

        def worker() -> None:
            err: BaseException | None = None
            result: Any = None
            try:
                result = fn(*args, **kwargs)
            except BaseException as exc:  # noqa: BLE001 — surface to UI
                err = exc

            def finish() -> None:
                if busy:
                    self._set_busy(False)
                if err is not None:
                    if isinstance(err, ConnectionError):
                        self.log(str(err), "err")
                        if not self.backend.is_connected():
                            self._set_connected(False)
                    elif isinstance(err, PermissionError):
                        self.log(str(err), "err")
                    else:
                        self.log(f"{type(err).__name__}: {err}", "err")
                    return
                if on_done:
                    on_done(result)

            self.after(0, finish)

        threading.Thread(target=worker, daemon=True).start()

    # ── actions ────────────────────────────────────────────────────────────

    def _on_connect(self) -> None:
        if self._busy:
            return
        user = self.user_var.get().strip() or SSH_USER
        host = self.host_var.get().strip()
        if not host:
            messagebox.showinfo("Host required", "Enter the feeder hostname or IP address.")
            return
        password = self.pass_var.get()
        self.log(f"Connecting to {user}@{host}:{SSH_PORT}…", "cmd")

        def work() -> tuple[str, dict[str, Any]]:
            msg = self.backend.connect(username=user, password=password or None, host=host)
            discovered = self.backend.discover()
            return msg, discovered

        def done(result: tuple[str, dict[str, Any]]) -> None:
            _msg, discovered = result
            self._set_connected(True)
            self._rebuild_services(discovered.get("services") or [])
            self._apply_maps(discovered.get("maps") or {})
            if not self.web_host_var.get().strip():
                self.web_host_var.set(self.backend.host)
            self._save_prefs()
            self._on_refresh()
            self._schedule_refresh()

        self._run_async(work, on_done=done)

    def _on_disconnect(self) -> None:
        self._cancel_refresh()
        self.backend.disconnect()
        self._tailing = False
        self._set_connected(False)

    def _on_refresh(self) -> None:
        if not self.backend.is_connected():
            return

        def work() -> tuple[dict[str, Any], dict[str, Any]]:
            return self.backend.fetch_status(), self.backend.fetch_aircraft_stats()

        def done(result: tuple[dict[str, Any], dict[str, Any]]) -> None:
            status, stats = result
            self._apply_status(status)
            self._apply_stats(stats)

        # Don't lock the toolbar — auto-refresh runs this every 20s.
        self._run_async(work, on_done=done, busy=False)

    def _apply_status(self, status: dict[str, Any]) -> None:
        for svc in status["services"]:
            row = self.service_rows.get(svc["unit"])
            if row:
                row.set_state(svc["state"])
        self.sys_uptime.configure(text=status.get("uptime") or "—")
        self.sys_temp.configure(text=status.get("temp") or "—")
        self.sys_mem.configure(text=status.get("memory") or "—")
        self.sys_disk.configure(text=status.get("disk") or "—")

    def _apply_stats(self, stats: dict[str, Any]) -> None:
        if stats.get("error"):
            self.log(stats["error"], "warn")

        def fmt(val: Any) -> str:
            if val is None:
                return "—"
            if isinstance(val, float):
                return f"{val:.1f}"
            return str(val)

        self.stat_ac.configure(text=fmt(stats.get("aircraft_total")))
        self.stat_pos.configure(text=fmt(stats.get("aircraft_with_pos")))
        self.stat_rate.configure(text=fmt(stats.get("msg_per_sec")))
        self.stat_range.configure(text=fmt(stats.get("max_range")))
        self.stat_cpr.configure(text=fmt(stats.get("cpr")))
        self.stat_total.configure(text=fmt(stats.get("total_msgs")))

    def _on_restart_all(self) -> None:
        if not messagebox.askyesno("Restart all", "Restart ALL ADS-B services?"):
            return
        self.log("Restarting all services…", "warn")
        self._run_async(self.backend.action_all, "restart", on_done=lambda _r: self._on_refresh())

    def _on_stop_all(self) -> None:
        if not messagebox.askyesno("Stop all", "Stop ALL ADS-B services?", icon="warning"):
            return
        self.log("Stopping all services…", "warn")
        self._run_async(self.backend.action_all, "stop", on_done=lambda _r: self._on_refresh())

    def _log_lines(self) -> int:
        raw = self.lines_var.get().strip()
        return int(raw) if raw.isdigit() else 60

    def _on_logs(self) -> None:
        if not self.backend.is_connected():
            return
        picked = self._selected_unit()
        if not picked:
            return
        _name, unit = picked
        self._run_async(self.backend.fetch_logs, unit, self._log_lines())

    def _on_tail(self) -> None:
        picked = self._selected_unit()
        if not picked:
            return
        if self._tailing:
            self._on_stop_tail()
        _name, unit = picked
        self._tailing = True
        self.btn_stop_tail.configure(state=tk.NORMAL)
        self.btn_tail.configure(state=tk.DISABLED)

        def emit(line: str) -> None:
            self.after(0, lambda t=line: self.log(t, "dim"))

        def work() -> None:
            self.backend.start_journal_tail(unit, emit)

        def done(_r: Any) -> None:
            self._tailing = False
            self.btn_stop_tail.configure(state=tk.DISABLED)
            if self.backend.is_connected() and not self._busy:
                self.btn_tail.configure(state=tk.NORMAL)

        self._run_async(work, on_done=done, busy=False)

    def _on_stop_tail(self) -> None:
        self.backend.stop_journal_tail()

    def _on_network(self) -> None:
        self._run_async(self.backend.fetch_network, self.web_host_var.get().strip())

    def _on_sdr(self) -> None:
        self._run_async(self.backend.check_sdr)

    def _on_updates(self) -> None:
        def after_preview(result: dict[str, Any]) -> None:
            if not result.get("ok") or not result.get("has_upgrades"):
                return
            if not messagebox.askyesno("Install upgrades", "Install the listed ADS-B package upgrades now?"):
                return
            self._run_async(self.backend.install_updates)

        self._run_async(self.backend.preview_updates, on_done=after_preview)

    def _on_reboot(self) -> None:
        if not messagebox.askyesno(
            "Reboot Pi",
            "Reboot this feeder?\nThe SSH connection will drop.",
            icon="warning",
        ):
            return
        self._cancel_refresh()

        def done(_r: Any) -> None:
            self._tailing = False
            self._set_connected(False)

        self._run_async(self.backend.reboot, on_done=done)

    def _open_map(self) -> None:
        url = self._map_url()
        self.log(f"Opening {self.map_var.get()}  →  {url}", "cmd")
        webbrowser.open(url)

    # ── auto refresh ───────────────────────────────────────────────────────

    def _schedule_refresh(self) -> None:
        self._cancel_refresh()

        def tick() -> None:
            self._refresh_after = None
            if self.backend.is_connected() and not self._busy:
                self._on_refresh()
            if self.backend.is_connected():
                self._schedule_refresh()

        self._refresh_after = self.after(20000, tick)

    def _cancel_refresh(self) -> None:
        if self._refresh_after is not None:
            self.after_cancel(self._refresh_after)
            self._refresh_after = None

    def _on_close(self) -> None:
        self._cancel_refresh()
        try:
            self._save_prefs()
        except Exception:
            pass
        try:
            self.backend.disconnect()
        except Exception:
            pass
        self.destroy()


def main() -> None:
    try:
        _enable_windows_dpi()
        app = AdsBApp()
        app.mainloop()
    except Exception as exc:
        log_path = app_settings.SETTINGS_DIR / "crash.log"
        try:
            app_settings.SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
            log_path.write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")
        except Exception:
            log_path = None
        try:
            root = tk.Tk()
            root.withdraw()
            extra = f"\n\nDetails written to:\n{log_path}" if log_path else ""
            messagebox.showerror("ADS-B Stack Manager", f"Failed to start:\n{exc}{extra}")
        except Exception:
            pass
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()

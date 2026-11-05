"""Visual theme: call apply_theme(root) before building any widgets."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

BG = "#f1f5f7"
SURFACE = "#ffffff"
ACCENT = "#0b5563"
ACCENT_LIGHT = "#12839a"
ACCENT_DARK = "#083f4a"
TEXT = "#1c2b33"
MUTED = "#5f7280"
BORDER = "#cfdae0"
TAB_BG = "#dfe8ec"
HEADER_SUB = "#c9e4ea"
CODE_BG = "#12232b"
CODE_FG = "#dbe9ee"

UI_FONT = ("Segoe UI", 10)
UI_BOLD = ("Segoe UI", 10, "bold")


def apply_theme(root: tk.Tk) -> ttk.Style:
    style = ttk.Style(root)
    style.theme_use("clam")
    root.configure(bg=BG)

    # plain Tk widgets (lists, text boxes, dialogs) are not covered by ttk styles
    for pattern, value in (
        ("*Toplevel.background", BG),
        ("*Canvas.background", BG),
        ("*Listbox.background", SURFACE),
        ("*Listbox.foreground", TEXT),
        ("*Listbox.font", UI_FONT),
        ("*Listbox.selectBackground", ACCENT_LIGHT),
        ("*Listbox.selectForeground", "white"),
        ("*Listbox.relief", "flat"),
        ("*Listbox.highlightThickness", 1),
        ("*Listbox.highlightColor", ACCENT_LIGHT),
        ("*Listbox.highlightBackground", BORDER),
        ("*Text.background", SURFACE),
        ("*Text.foreground", TEXT),
        ("*Text.font", UI_FONT),
        ("*Text.relief", "flat"),
        ("*Text.highlightThickness", 1),
        ("*Text.highlightColor", ACCENT_LIGHT),
        ("*Text.highlightBackground", BORDER),
        ("*TCombobox*Listbox.background", SURFACE),
        ("*TCombobox*Listbox.foreground", TEXT),
        ("*TCombobox*Listbox.selectBackground", ACCENT_LIGHT),
        ("*TCombobox*Listbox.selectForeground", "white"),
    ):
        root.option_add(pattern, value)

    style.configure(".", background=BG, foreground=TEXT, font=UI_FONT)
    style.configure("TFrame", background=BG)
    style.configure("TLabel", background=BG, foreground=TEXT)
    style.configure("TPanedwindow", background=BG)
    style.configure("Sash", sashthickness=6, gripcount=0)

    style.configure("TButton", padding=(10, 4), background=SURFACE, foreground=TEXT,
                    bordercolor=BORDER, lightcolor=SURFACE, darkcolor=SURFACE, focuscolor=SURFACE)
    style.map("TButton", background=[("disabled", TAB_BG), ("active", "#e3eff3")],
              foreground=[("disabled", MUTED)], bordercolor=[("active", ACCENT_LIGHT)])
    style.configure("Accent.TButton", padding=(14, 4), background=ACCENT, foreground="white", font=UI_BOLD,
                    bordercolor=ACCENT, lightcolor=ACCENT, darkcolor=ACCENT, focuscolor=ACCENT)
    style.map("Accent.TButton", background=[("disabled", TAB_BG), ("active", ACCENT_DARK)],
              foreground=[("disabled", MUTED)], bordercolor=[("active", ACCENT_DARK)])

    style.configure("TNotebook", background=BG, borderwidth=0, bordercolor=BORDER,
                    lightcolor=BG, darkcolor=BG, tabmargins=(2, 4, 2, 0))
    style.configure("TNotebook.Tab", padding=(12, 5), background=TAB_BG, foreground=MUTED, font=UI_BOLD,
                    bordercolor=BORDER, lightcolor=TAB_BG)
    style.map("TNotebook.Tab", background=[("selected", SURFACE)], foreground=[("selected", ACCENT)],
              lightcolor=[("selected", ACCENT_LIGHT)])

    field = {"fieldbackground": SURFACE, "bordercolor": BORDER, "lightcolor": SURFACE, "darkcolor": SURFACE, "padding": 3}
    style.configure("TEntry", **field)
    style.map("TEntry", bordercolor=[("focus", ACCENT_LIGHT)], lightcolor=[("focus", ACCENT_LIGHT)])
    style.configure("TCombobox", arrowcolor=ACCENT, background=SURFACE, **field)
    style.map("TCombobox", fieldbackground=[("readonly", SURFACE)], bordercolor=[("focus", ACCENT_LIGHT)],
              selectbackground=[("readonly", SURFACE)], selectforeground=[("readonly", TEXT)])
    style.configure("TCheckbutton", background=BG, foreground=TEXT, focuscolor=BG)
    style.map("TCheckbutton", background=[("active", BG)], indicatorcolor=[("selected", ACCENT), ("!selected", SURFACE)])

    style.configure("Treeview", background=SURFACE, fieldbackground=SURFACE, foreground=TEXT, rowheight=24,
                    bordercolor=BORDER)
    style.map("Treeview", background=[("selected", ACCENT_LIGHT)], foreground=[("selected", "white")])
    style.configure("Treeview.Heading", background=TAB_BG, foreground=ACCENT, font=("Segoe UI", 9, "bold"),
                    bordercolor=BORDER, lightcolor=TAB_BG, darkcolor=TAB_BG)
    style.map("Treeview.Heading", background=[("active", "#cfe0e6")])

    style.configure("TScrollbar", background=TAB_BG, troughcolor=BG, bordercolor=BG, lightcolor=TAB_BG,
                    darkcolor=TAB_BG, arrowcolor=MUTED)
    style.map("TScrollbar", background=[("active", "#c5d6dc")])

    style.configure("Header.TFrame", background=ACCENT)
    style.configure("HeaderTitle.TLabel", background=ACCENT, foreground="white", font=("Segoe UI Semibold", 17))
    style.configure("HeaderSub.TLabel", background=ACCENT, foreground=HEADER_SUB, font=("Segoe UI", 9))
    style.configure("HeaderName.TLabel", background=ACCENT, foreground="white", font=("Segoe UI Semibold", 11))
    style.configure("HeaderLink.TLabel", background=ACCENT, foreground=HEADER_SUB, font=("Segoe UI", 9, "underline"))
    style.configure("Muted.TLabel", background=BG, foreground=MUTED, font=("Segoe UI", 9))
    return style

"""Tkinter front end: generate a macro from a template or pick one of Cisco's sample macros; preview/edit, export or push."""
from __future__ import annotations

import json
import queue
import re
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from .about import ABOUT_TEXT, APP_NAME, AUTHOR, COPYRIGHT, LICENSE, TAGLINE, VERSION
from .core import Field, Template
from .device import push_bundle
from .profile import generate, load_profile, save_profile
from .samples import (
    SAMPLE_PREFIX, Sample, SampleSession, all_tags, download_remote, get_sample, load_catalog, option_key,
    read_data_file, readme_to_text, search,
)
from .templates import TEMPLATES
from .theme import CODE_BG, CODE_FG, apply_theme

SETTINGS_FILE = Path(__file__).resolve().parent.parent / "settings.json"
LICENSE_FILE = Path(__file__).resolve().parent.parent / "LICENSE"
REFRESH_DELAY_MS = 250
SECRET_NAME = re.compile(r"password|token|secret|pin", re.I)
ALL_TAGS = "All tags"


class RowDialog(tk.Toplevel):
    """Add / edit one table row."""

    def __init__(self, parent, field: Field, row: dict[str, str] | None):
        super().__init__(parent)
        self.title(f"{field.label} - {'edit' if row else 'add'} row")
        self.transient(parent)
        self.result: dict[str, str] | None = None
        self._entries: dict[str, ttk.Entry] = {}
        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)
        for i, col in enumerate(field.columns):
            ttk.Label(body, text=col.label).grid(row=i, column=0, sticky="w", pady=3, padx=(0, 8))
            entry = ttk.Entry(body, width=60)
            entry.insert(0, (row or {}).get(col.key, col.default))
            entry.grid(row=i, column=1, sticky="ew", pady=3)
            self._entries[col.key] = entry
        body.columnconfigure(1, weight=1)
        buttons = ttk.Frame(body)
        buttons.grid(row=len(field.columns), column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right", padx=(6, 0))
        ttk.Button(buttons, text="OK", command=self._ok).pack(side="right")
        self.bind("<Return>", lambda _e: self._ok())
        self.bind("<Escape>", lambda _e: self.destroy())
        self.grab_set()
        first = next(iter(self._entries.values()), None)
        if first:
            first.focus_set()
        self.wait_window(self)

    def _ok(self):
        self.result = {k: e.get().strip() for k, e in self._entries.items()}
        self.destroy()


class TableEditor(ttk.Frame):
    def __init__(self, parent, field: Field, on_change):
        super().__init__(parent)
        self.field = field
        self.on_change = on_change
        self.rows: list[dict[str, str]] = []
        keys = [c.key for c in field.columns]
        self.tree = ttk.Treeview(self, columns=keys, show="headings", height=4, selectmode="browse")
        for col in field.columns:
            self.tree.heading(col.key, text=col.label)
            self.tree.column(col.key, width=min(col.width, 180), stretch=True)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(self, orient="horizontal", command=self.tree.xview)
        self.tree.configure(xscrollcommand=scroll.set)
        scroll.grid(row=1, column=0, sticky="ew")
        side = ttk.Frame(self)
        side.grid(row=0, column=1, sticky="n", padx=(6, 0))
        for text, cmd in (("Add", self.add), ("Edit", self.edit), ("Delete", self.delete)):
            ttk.Button(side, text=text, width=8, command=cmd).pack(pady=1)
        self.columnconfigure(0, weight=1)
        self.tree.bind("<Double-1>", lambda _e: self.edit())

    def set_rows(self, rows: list[dict[str, str]]):
        self.rows = [dict(r) for r in rows]
        self._redraw()

    def _redraw(self):
        self.tree.delete(*self.tree.get_children())
        for i, row in enumerate(self.rows):
            self.tree.insert("", "end", iid=str(i), values=[row.get(c.key, "") for c in self.field.columns])

    def _selected(self) -> int | None:
        sel = self.tree.selection()
        return int(sel[0]) if sel else None

    def add(self):
        dlg = RowDialog(self, self.field, None)
        if dlg.result:
            self.rows.append(dlg.result)
            self._redraw()
            self.on_change()

    def edit(self):
        idx = self._selected()
        if idx is None:
            return
        dlg = RowDialog(self, self.field, self.rows[idx])
        if dlg.result:
            self.rows[idx] = dlg.result
            self._redraw()
            self.on_change()

    def delete(self):
        idx = self._selected()
        if idx is None:
            return
        del self.rows[idx]
        self._redraw()
        self.on_change()


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Macro Generator - Cisco RoomOS")
        self.geometry(f"1380x{min(900, self.winfo_screenheight() - 100)}")
        self.minsize(1100, 650)

        self.mode = "template"                       # "template" | "sample"
        self.template: Template = TEMPLATES[0]
        self.widgets: dict[str, Any] = {}
        self.vars: dict[str, tk.Variable] = {}
        self.macro_name = tk.StringVar(value=self.template.default_macro_name)
        self.macro_name.trace_add("write", lambda *_: self.schedule_refresh())
        self._refresh_job: str | None = None
        self._poll_job: str | None = None
        self.current_js = ""
        self.push_queue: queue.Queue = queue.Queue()
        self.task_queue: queue.Queue = queue.Queue()

        self.session: SampleSession | None = None
        self.current_key: str | None = None
        self.file_labels: dict[str, str] = {}
        self.param_vars: dict[str, tk.Variable] = {}
        self.param_notes: dict[str, ttk.Label] = {}
        self.option_maps: dict[str, dict[str, str]] = {}
        self.file_vars: dict[str, tk.BooleanVar] = {}
        self.shown_samples: list[Sample] = []

        apply_theme(self)
        self._build_menu()
        self._build_header()
        self._build_layout()
        self._load_settings()
        self._enter_mode("template")                 # hides the sample-only widgets
        self.select_template(0)
        self._refilter()
        self._poll_job = self.after(150, self._poll_queues)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------ layout
    def _build_menu(self):
        menubar = tk.Menu(self)
        help_menu = tk.Menu(menubar, tearoff=False)
        help_menu.add_command(label="View License", command=self.show_license)
        help_menu.add_command(label=f"About {APP_NAME}", command=self.show_about)
        menubar.add_cascade(label="Help", menu=help_menu)
        self.config(menu=menubar)

    def _build_header(self):
        header = ttk.Frame(self, style="Header.TFrame", padding=(16, 8))
        header.pack(side="top", fill="x")
        title = ttk.Frame(header, style="Header.TFrame")
        title.pack(side="left")
        ttk.Label(title, text=APP_NAME, style="HeaderTitle.TLabel").pack(anchor="w")
        ttk.Label(title, text=TAGLINE, style="HeaderSub.TLabel").pack(anchor="w")
        credit = ttk.Frame(header, style="Header.TFrame")
        credit.pack(side="right")
        ttk.Label(credit, text=f"by {AUTHOR}", style="HeaderName.TLabel").pack(anchor="e")
        license_link = ttk.Label(credit, text=f"{LICENSE}  (v{VERSION})", style="HeaderLink.TLabel", cursor="hand2")
        license_link.pack(anchor="e")
        license_link.bind("<Button-1>", lambda _e: self.show_license())

    def show_about(self):
        messagebox.showinfo(f"About {APP_NAME}", f"{APP_NAME}\n\n{ABOUT_TEXT}", parent=self)

    def show_license(self):
        win = tk.Toplevel(self)
        win.title(f"{APP_NAME} - {LICENSE}")
        win.transient(self)
        text = tk.Text(win, width=80, height=24, wrap="word", padx=10, pady=10)
        text.insert("1.0", LICENSE_FILE.read_text(encoding="utf-8"))
        text.configure(state="disabled")
        text.pack(fill="both", expand=True)
        ttk.Button(win, text="Close", command=win.destroy).pack(pady=8)
        win.bind("<Escape>", lambda _e: win.destroy())

    def _build_layout(self):
        bar = ttk.Frame(self, padding=(8, 4, 8, 8))
        bar.pack(side="bottom", fill="x")   # packed first so a short window clips the form, never the toolbar

        panes = self.panes = ttk.PanedWindow(self, orient="horizontal")
        panes.pack(fill="both", expand=True, padx=6, pady=6)
        self.after(50, self._place_sashes)

        left = ttk.Frame(panes, padding=6)
        panes.add(left, weight=0)
        self.source_tabs = ttk.Notebook(left)
        self.source_tabs.pack(fill="x")

        gen_tab = ttk.Frame(self.source_tabs, padding=4)
        self.source_tabs.add(gen_tab, text="Generate")
        self.template_list = tk.Listbox(gen_tab, exportselection=False, height=len(TEMPLATES), width=34, activestyle="none")
        for t in TEMPLATES:
            self.template_list.insert("end", t.name)
        self.template_list.pack(fill="x")
        self.template_list.bind("<<ListboxSelect>>", self._on_template_pick)

        sample_tab = ttk.Frame(self.source_tabs, padding=4)
        self.source_tabs.add(sample_tab, text=f"Cisco samples ({len(load_catalog())})")
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._refilter())
        ttk.Entry(sample_tab, textvariable=self.search_var).pack(fill="x")
        self.tag_var = tk.StringVar(value=ALL_TAGS)
        tag_box = ttk.Combobox(sample_tab, textvariable=self.tag_var, values=[ALL_TAGS, *all_tags()], state="readonly")
        tag_box.pack(fill="x", pady=(4, 4))
        tag_box.bind("<<ComboboxSelected>>", lambda _e: self._refilter())
        list_frame = ttk.Frame(sample_tab)
        list_frame.pack(fill="x")
        self.sample_list = tk.Listbox(list_frame, exportselection=False, height=11, width=34, activestyle="none")
        sample_scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.sample_list.yview)
        self.sample_list.configure(yscrollcommand=sample_scroll.set)
        self.sample_list.pack(side="left", fill="x", expand=True)
        sample_scroll.pack(side="right", fill="y")
        self.sample_list.bind("<<ListboxSelect>>", self._on_sample_pick)
        self.count_label = ttk.Label(sample_tab, foreground="#777777")
        self.count_label.pack(anchor="w")
        self.source_tabs.bind("<<NotebookTabChanged>>", self._on_source_tab)

        self.desc_label = ttk.Label(left, wraplength=250, justify="left")
        self.desc_label.pack(anchor="w", pady=(8, 8))
        self.prereq_title = ttk.Label(left, text="Before you deploy", font=("Segoe UI", 10, "bold"))
        self.prereq_title.pack(anchor="w")
        self.prereq_label = ttk.Label(left, wraplength=250, justify="left", foreground="#555555")
        self.prereq_label.pack(anchor="w")

        middle = ttk.Frame(panes)
        panes.add(middle, weight=2)
        canvas = tk.Canvas(middle, highlightthickness=0)
        vscroll = ttk.Scrollbar(middle, orient="vertical", command=canvas.yview)
        self.form = ttk.Frame(canvas, padding=8)
        self.form.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        window = canvas.create_window((0, 0), window=self.form, anchor="nw")
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window, width=e.width))
        canvas.configure(yscrollcommand=vscroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        vscroll.pack(side="right", fill="y")
        canvas.bind("<Enter>", lambda _e: canvas.bind_all("<MouseWheel>", lambda ev: canvas.yview_scroll(int(-ev.delta / 120), "units")))
        canvas.bind("<Leave>", lambda _e: canvas.unbind_all("<MouseWheel>"))

        right = ttk.Frame(panes)
        panes.add(right, weight=2)
        self.tabs = ttk.Notebook(right)
        self.tabs.pack(fill="both", expand=True)

        self.preview_tab = ttk.Frame(self.tabs)
        self.tabs.add(self.preview_tab, text="Preview")
        self.file_row = ttk.Frame(self.preview_tab)
        self.file_var = tk.StringVar()
        self.file_combo = ttk.Combobox(self.file_row, textvariable=self.file_var, state="readonly", width=44)
        self.file_combo.pack(side="left")
        self.file_combo.bind("<<ComboboxSelected>>", self._on_file_pick)
        ttk.Button(self.file_row, text="Reset file", command=self.reset_file).pack(side="left", padx=6)
        self.edited_label = ttk.Label(self.file_row, foreground="#b36b00")
        self.edited_label.pack(side="left")
        self.status = ttk.Label(self.preview_tab, anchor="w", wraplength=520, justify="left")
        self.status.pack(fill="x", padx=6, pady=4)
        self.preview = tk.Text(self.preview_tab, wrap="none", font=("Consolas", 10), state="disabled", undo=True, padx=8, pady=6,
                               background=CODE_BG, foreground=CODE_FG, insertbackground=CODE_FG)
        pv_y = ttk.Scrollbar(self.preview_tab, orient="vertical", command=self.preview.yview)
        pv_x = ttk.Scrollbar(self.preview_tab, orient="horizontal", command=self.preview.xview)
        self.preview.configure(yscrollcommand=pv_y.set, xscrollcommand=pv_x.set)
        pv_x.pack(side="bottom", fill="x")
        pv_y.pack(side="right", fill="y")
        self.preview.pack(fill="both", expand=True)
        for sequence in ("<KeyRelease>", "<<Paste>>", "<<Cut>>", "<<Undo>>", "<<Redo>>"):
            self.preview.bind(sequence, self._on_edit)

        self.readme_tab = ttk.Frame(self.tabs)
        self.tabs.add(self.readme_tab, text="README")
        self.readme = tk.Text(self.readme_tab, wrap="word", font=("Segoe UI", 10), state="disabled", padx=8, pady=8)
        rd_y = ttk.Scrollbar(self.readme_tab, orient="vertical", command=self.readme.yview)
        self.readme.configure(yscrollcommand=rd_y.set)
        rd_y.pack(side="right", fill="y")
        self.readme.pack(fill="both", expand=True)

        self._build_deploy_tab()

        self.name_frame = ttk.Frame(bar)
        self.name_frame.pack(side="left")
        ttk.Label(self.name_frame, text="Macro name:").pack(side="left")
        ttk.Entry(self.name_frame, textvariable=self.macro_name, width=26).pack(side="left", padx=(4, 16))
        self.load_btn = ttk.Button(bar, text="Load profile...", command=self.load_profile)
        self.load_btn.pack(side="left", padx=3)
        ttk.Button(bar, text="Save profile...", command=self.save_profile).pack(side="left", padx=3)
        ttk.Button(bar, text="Copy JS", command=self.copy_js).pack(side="left", padx=3)
        self.save_btn = ttk.Button(bar, text="Save .js...", command=self.save_js, style="Accent.TButton")
        self.save_btn.pack(side="left", padx=3)
        ttk.Label(bar, text=f"{COPYRIGHT}  -  {LICENSE}", style="Muted.TLabel").pack(side="right")

    def _place_sashes(self):
        """Give the form (middle) the most room; the preview is for reading, not editing."""
        width = self.panes.winfo_width()
        if width < 200:
            self.after(50, self._place_sashes)
            return
        self.panes.sashpos(0, int(width * 0.25))
        self.panes.sashpos(1, int(width * 0.70))

    def _build_deploy_tab(self):
        self.deploy_tab = tab = ttk.Frame(self.tabs, padding=8)
        self.tabs.add(tab, text="Deploy to devices")
        ttk.Label(tab, text="Device IPs / hostnames (one per line)").pack(anchor="w")
        self.hosts_text = tk.Text(tab, height=5, width=40)
        self.hosts_text.pack(fill="x", pady=(2, 8))

        creds = ttk.Frame(tab)
        creds.pack(fill="x")
        ttk.Label(creds, text="Username").grid(row=0, column=0, sticky="w")
        ttk.Label(creds, text="Password").grid(row=1, column=0, sticky="w")
        self.user_var = tk.StringVar(value="admin")
        self.pass_var = tk.StringVar()
        ttk.Entry(creds, textvariable=self.user_var, width=26).grid(row=0, column=1, padx=6, pady=2, sticky="w")
        ttk.Entry(creds, textvariable=self.pass_var, show="*", width=26).grid(row=1, column=1, padx=6, pady=2, sticky="w")

        self.activate_var = tk.BooleanVar(value=True)
        self.restart_var = tk.BooleanVar(value=True)
        self.transpile_var = tk.BooleanVar(value=True)
        self.verify_var = tk.BooleanVar(value=False)
        for text, var in (
            ("Activate macros after saving (libraries are never activated)", self.activate_var),
            ("Restart macro runtime", self.restart_var),
            ("Transpile to ES5 (device default)", self.transpile_var),
            ("Verify device TLS certificate (devices ship self-signed)", self.verify_var),
        ):
            ttk.Checkbutton(tab, text=text, variable=var).pack(anchor="w")

        self.push_btn = ttk.Button(tab, text="Push macro", command=self.push, style="Accent.TButton")
        self.push_btn.pack(anchor="w", pady=8)
        ttk.Label(tab, text="The password is only kept in memory for this session.", foreground="#777777").pack(anchor="w")
        self.log = tk.Text(tab, height=12, state="disabled", font=("Consolas", 9), padx=6, pady=4, background=CODE_BG, foreground=CODE_FG)
        self.log.pack(fill="both", expand=True, pady=(8, 0))

    # ------------------------------------------------------------------ mode switching
    def _show_source_tab(self, index: int):
        self.source_tabs.select(index)

    def _enter_mode(self, mode: str):
        self.mode = mode
        sample = mode == "sample"
        if sample:
            self.file_row.pack(fill="x", padx=6, pady=(4, 0), before=self.status)
            try:
                self.tabs.add(self.readme_tab)
            except tk.TclError:
                pass
            self.name_frame.pack_forget()
            self.save_btn.configure(text="Export files...", command=self.export_files)
            self.push_btn.configure(text="Push to devices")
        else:
            self.file_row.pack_forget()
            self.tabs.hide(self.readme_tab)
            self.name_frame.pack(side="left", before=self.load_btn)
            self.save_btn.configure(text="Save .js...", command=self.save_js)
            self.push_btn.configure(text="Push macro")
        self.tabs.select(self.preview_tab)

    def _on_source_tab(self, _event):
        # <<NotebookTabChanged>> is queued, so it also arrives for tab changes made in code. By then the
        # mode already matches the tab, which makes this a no-op instead of a second (state-losing) selection.
        wanted = "template" if self.source_tabs.index(self.source_tabs.select()) == 0 else "sample"
        if wanted == self.mode:
            return
        if wanted == "template":
            sel = self.template_list.curselection()
            self.select_template(sel[0] if sel else TEMPLATES.index(self.template))
        else:
            sel = self.sample_list.curselection()
            if sel and sel[0] < len(self.shown_samples):
                self.select_sample(self.shown_samples[sel[0]].id)
            elif self.session:
                self.select_sample(self.session.sample.id)
            elif self.shown_samples:
                self.select_sample(self.shown_samples[0].id)

    # ------------------------------------------------------------------ generated templates
    def _on_template_pick(self, _event):
        sel = self.template_list.curselection()
        if sel:
            self.select_template(sel[0])

    def select_template(self, index: int, values: dict[str, Any] | None = None, macro_name: str | None = None):
        if self.mode != "template":
            self._enter_mode("template")
        self._show_source_tab(0)
        self.template = TEMPLATES[index]
        self.template_list.selection_clear(0, "end")
        self.template_list.selection_set(index)
        self.desc_label.configure(text=self.template.description)
        self.prereq_title.configure(text="Before you deploy")
        self.prereq_label.configure(text="\n\n".join("• " + p for p in self.template.prerequisites))
        self.macro_name.set(macro_name or self.template.default_macro_name)
        self._build_form()
        self.set_values(values or self.template.defaults())
        self.refresh()

    def _build_form(self):
        for child in self.form.winfo_children():
            child.destroy()
        self.widgets.clear()
        self.vars.clear()
        self.form.columnconfigure(1, weight=1)
        row = 0
        for f in self.template.fields:
            if f.kind == "bool":
                var = tk.BooleanVar()
                var.trace_add("write", lambda *_: self.schedule_refresh())
                ttk.Checkbutton(self.form, text=f.label, variable=var).grid(row=row, column=0, columnspan=2, sticky="w", pady=(6, 0))
                self.vars[f.key] = var
            else:
                ttk.Label(self.form, text=f.label).grid(row=row, column=0, sticky="nw", pady=(6, 0), padx=(0, 10))
                if f.kind in ("str", "int"):
                    var = tk.StringVar()
                    var.trace_add("write", lambda *_: self.schedule_refresh())
                    ttk.Entry(self.form, textvariable=var, width=14 if f.kind == "int" else 50).grid(
                        row=row, column=1, sticky="w" if f.kind == "int" else "ew", pady=(6, 0))
                    self.vars[f.key] = var
                elif f.kind == "choice":
                    var = tk.StringVar()
                    var.trace_add("write", lambda *_: self.schedule_refresh())
                    ttk.Combobox(self.form, textvariable=var, values=f.choices, state="readonly", width=28).grid(
                        row=row, column=1, sticky="w", pady=(6, 0))
                    self.vars[f.key] = var
                elif f.kind == "lines":
                    text = tk.Text(self.form, height=3, width=50)
                    text.bind("<KeyRelease>", lambda _e: self.schedule_refresh())
                    text.grid(row=row, column=1, sticky="ew", pady=(6, 0))
                    self.widgets[f.key] = text
                elif f.kind == "table":
                    ed = TableEditor(self.form, f, self.schedule_refresh)
                    ed.grid(row=row, column=1, sticky="ew", pady=(6, 0))
                    self.widgets[f.key] = ed
            row += 1
            if f.help:
                ttk.Label(self.form, text=f.help, foreground="#777777", wraplength=520, justify="left").grid(
                    row=row, column=0, columnspan=2, sticky="w")
                row += 1

    def set_values(self, values: dict[str, Any]):
        for f in self.template.fields:
            v = values.get(f.key, f.default)
            if f.kind == "table":
                self.widgets[f.key].set_rows(v or [])
            elif f.kind == "lines":
                w: tk.Text = self.widgets[f.key]
                w.delete("1.0", "end")
                w.insert("1.0", "\n".join(v) if isinstance(v, list) else (v or ""))
            elif f.kind == "bool":
                self.vars[f.key].set(bool(v))
            else:
                self.vars[f.key].set("" if v is None else str(v))

    def get_values(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for f in self.template.fields:
            if f.kind == "table":
                out[f.key] = [dict(r) for r in self.widgets[f.key].rows]
            elif f.kind == "lines":
                out[f.key] = self.widgets[f.key].get("1.0", "end").strip()
            else:
                out[f.key] = self.vars[f.key].get()
        return out

    # ------------------------------------------------------------------ Cisco samples
    def _refilter(self):
        tag = self.tag_var.get()
        self.shown_samples = search(self.search_var.get(), "" if tag == ALL_TAGS else tag)
        self.sample_list.delete(0, "end")
        for s in self.shown_samples:
            self.sample_list.insert("end", s.title)
        self.count_label.configure(text=f"{len(self.shown_samples)} of {len(load_catalog())}")
        if self.session:
            ids = [s.id for s in self.shown_samples]
            if self.session.sample.id in ids:
                i = ids.index(self.session.sample.id)
                self.sample_list.selection_set(i)
                self.sample_list.see(i)

    def _on_sample_pick(self, _event):
        sel = self.sample_list.curselection()
        if sel:
            self.select_sample(self.shown_samples[sel[0]].id)

    def select_sample(self, sample_id: str, profile: dict[str, Any] | None = None) -> list[str]:
        sample = get_sample(sample_id)
        if self.mode != "sample":
            self._enter_mode("sample")
        self._show_source_tab(1)
        self.session = SampleSession(sample)
        problems = self.session.apply_profile(profile) if profile else []

        ids = [s.id for s in self.shown_samples]
        if sample.id not in ids:                      # selected from a profile while a filter hides it
            self.search_var.set("")
            self.tag_var.set(ALL_TAGS)
            self._refilter()
            ids = [s.id for s in self.shown_samples]
        self.sample_list.selection_clear(0, "end")
        self.sample_list.selection_set(ids.index(sample.id))
        self.sample_list.see(ids.index(sample.id))

        extra = f"\n\nTags: {', '.join(sample.tags)}" if sample.tags else ""
        self.desc_label.configure(text=f"{sample.title}\n{sample.description}{extra}")
        notes = ["Cisco sample code, MIT licence - review it before putting it on a customer device."]
        if sample.caution:
            notes.insert(0, sample.caution)
        self.prereq_title.configure(text="Please note")
        self.prereq_label.configure(text="\n\n".join("• " + n for n in notes))

        self._set_readme(sample)
        self._build_sample_form()
        first = next((m.key for m in sample.macros if m.kind == "primary"), sample.macros[0].key)
        self._load_editor(first)
        self.refresh()
        return problems

    def _set_readme(self, sample: Sample):
        text = readme_to_text(read_data_file(sample, sample.readme)) if sample.readme else "(no README upstream)"
        self.readme.configure(state="normal")
        self.readme.delete("1.0", "end")
        self.readme.insert("1.0", text)
        self.readme.configure(state="disabled")

    def _build_sample_form(self):
        s = self.session
        assert s is not None
        for child in self.form.winfo_children():
            child.destroy()
        self.param_vars.clear()
        self.param_notes.clear()
        self.option_maps.clear()
        self.file_vars.clear()
        self.form.columnconfigure(1, weight=1)
        sample = s.sample
        row = 0

        head = ttk.Frame(self.form)
        head.grid(row=row, column=0, columnspan=2, sticky="ew")
        ttk.Label(head, text=sample.title, font=("Segoe UI", 12, "bold")).pack(side="left")
        ttk.Button(head, text="Open on GitHub", command=lambda: webbrowser.open(sample.upstream)).pack(side="right")
        row += 1

        missing = s.missing_remote()
        if missing:
            bar = ttk.Frame(self.form)
            bar.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(8, 0))
            ttk.Label(
                bar, foreground="#b00020", wraplength=480, justify="left",
                text=f"{len(missing)} file(s) this sample needs are hosted in other Cisco repos that declare no licence, "
                     "so they are not bundled. They are downloaded from a pinned commit and checked against a SHA-256.",
            ).pack(side="left", fill="x", expand=True)
            self.download_btn = ttk.Button(bar, text="Download", command=self.download_remote_files)
            self.download_btn.pack(side="right", padx=6)
            row += 1

        editable = [p for p in sample.params]
        if editable:
            ttk.Label(self.form, text="Settings", font=("Segoe UI", 10, "bold")).grid(row=row, column=0, columnspan=2, sticky="w", pady=(12, 0))
            row += 1
        for p in editable:
            ttk.Label(self.form, text=p.name + (" *" if p.required else "")).grid(row=row, column=0, sticky="nw", pady=(6, 0), padx=(0, 10))
            usable = s.param_editable(p) or (p.macro and f"macro:{p.macro}" in s.missing_remote())
            current = s.params.get(p.id, p.default)
            if p.type == "bool":
                var: tk.Variable = tk.BooleanVar(value=bool(current))
                widget = ttk.Checkbutton(self.form, variable=var)
                widget.grid(row=row, column=1, sticky="w", pady=(6, 0))
            elif p.type == "option":
                labels = {key: label for key, label in p.values.items()}
                self.option_maps[p.id] = {label: key for key, label in labels.items()}
                var = tk.StringVar(value=labels.get(option_key(current), option_key(current)))
                widget = ttk.Combobox(self.form, textvariable=var, values=list(labels.values()), state="readonly", width=40)
                widget.grid(row=row, column=1, sticky="w", pady=(6, 0))
            else:
                var = tk.StringVar(value="" if current is None else str(current))
                secret = SECRET_NAME.search(p.id) or SECRET_NAME.search(p.name)
                widget = ttk.Entry(self.form, textvariable=var, width=14 if p.type == "number" else 50, show="•" if secret else "")
                widget.grid(row=row, column=1, sticky="w" if p.type == "number" else "ew", pady=(6, 0))
            if not usable:
                widget.state(["disabled"])
            self.param_vars[p.id] = var
            var.trace_add("write", lambda *_a, pid=p.id: self._on_param(pid))
            row += 1
            note = ttk.Label(self.form, text=p.info, foreground="#777777", wraplength=480, justify="left")
            if not usable:
                note.configure(text=(p.info + "  " if p.info else "") + "(Not a simple value in the code - edit the macro directly.)",
                               foreground="#b36b00")
            note.grid(row=row, column=0, columnspan=2, sticky="w")
            self.param_notes[p.id] = note
            row += 1

        ttk.Label(self.form, text="Files", font=("Segoe UI", 10, "bold")).grid(row=row, column=0, columnspan=2, sticky="w", pady=(14, 0))
        row += 1
        for m in sample.macros:
            tag = {"primary": "macro", "library": "library - saved, not activated", "example": "example"}[m.kind]
            self._file_row(row, m.key, m.name, tag, selectable=True, pending=m.key in missing)
            row += 1
        for p in sample.panels:
            name = (p.file.rsplit("/", 1)[-1] if p.file else p.panel_id)
            tag = "UI panel" + (" - alternative / optional" if p.optional else "")
            self._file_row(row, p.key, name, tag, selectable=True, pending=p.key in missing)
            row += 1
        for rel in sample.extras:
            self._file_row(row, f"extra:{rel}", rel, "extra - not installed", selectable=False, pending=False)
            row += 1

        self.file_labels = {key: s.label(key) for key in s.keys()}

    def _file_row(self, row: int, key: str, name: str, tag: str, selectable: bool, pending: bool):
        frame = ttk.Frame(self.form)
        frame.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(2, 0))
        if selectable:
            var = tk.BooleanVar(value=bool(self.session.selected.get(key)))
            var.trace_add("write", lambda *_a, k=key, v=var: self._on_file_toggle(k, v))
            ttk.Checkbutton(frame, text=name, variable=var).pack(side="left")
            self.file_vars[key] = var
        else:
            ttk.Label(frame, text=name).pack(side="left", padx=(20, 0))
        ttk.Label(frame, text=("not downloaded - " if pending else "") + tag, foreground="#b00020" if pending else "#777777").pack(side="left", padx=8)
        ttk.Button(frame, text="View", width=6, command=lambda k=key: self._view_file(k)).pack(side="right")

    def _on_file_toggle(self, key: str, var: tk.BooleanVar):
        if self.session:
            self.session.selected[key] = var.get()
            self.refresh()

    def _on_param(self, param_id: str):
        s = self.session
        if not s:
            return
        p = next(x for x in s.sample.params if x.id == param_id)
        raw = self.param_vars[param_id].get()
        if p.type == "option":
            raw = self.option_maps[param_id].get(raw, raw)
        msg = s.set_param(param_id, raw)
        note = self.param_notes[param_id]
        if msg:
            note.configure(text=msg, foreground="#b00020")
        else:
            note.configure(text=p.info, foreground="#777777")
            if self.current_key == f"macro:{p.macro}":
                self._load_editor(self.current_key)
        self.refresh()

    def download_remote_files(self):
        s = self.session
        if not s:
            return
        self.download_btn.configure(state="disabled", text="Downloading...")
        sample_id = s.sample.id

        def work():
            s.load_remote(download_remote)

        def done(error: Exception | None):
            if self.session is not s:
                return
            if error:
                self.download_btn.configure(state="normal", text="Download")
                messagebox.showerror("Download failed", str(error))
                return
            self._build_sample_form()
            self._load_editor(self.current_key)
            self.refresh()

        self._run_bg(work, done)

    # ------------------------------------------------------------------ editor (sample files)
    def _load_editor(self, key: str | None):
        s = self.session
        if not s or not key:
            return
        self.current_key = key
        self.file_labels = {k: s.label(k) for k in s.keys()}
        self.file_combo.configure(values=list(self.file_labels.values()))
        self.file_var.set(self.file_labels[key])
        pending = key not in s.text
        self.preview.configure(state="normal")
        self.preview.delete("1.0", "end")
        self.preview.insert("1.0", "// Not downloaded yet - use the Download button on the left." if pending else s.get(key))
        self.preview.edit_reset()
        self.preview.configure(state="disabled" if pending else "normal")
        self._update_edited_label()

    def _view_file(self, key: str):
        self._load_editor(key)
        self.tabs.select(self.preview_tab)

    def _on_file_pick(self, _event):
        label = self.file_var.get()
        for key, text in self.file_labels.items():
            if text == label:
                self._load_editor(key)
                return

    def _on_edit(self, _event=None):
        s = self.session
        if self.mode != "sample" or not s or not self.current_key or self.current_key not in s.text:
            return
        s.set_text(self.current_key, self.preview.get("1.0", "end-1c"))
        self._update_edited_label()
        self.schedule_refresh()

    def _update_edited_label(self):
        s = self.session
        self.edited_label.configure(text="edited" if s and self.current_key and s.modified(self.current_key) else "")

    def reset_file(self):
        s = self.session
        if s and self.current_key and s.modified(self.current_key):
            if messagebox.askyesno("Reset file", "Discard your edits to this file and go back to the upstream version?"):
                s.reset(self.current_key)
                self._load_editor(self.current_key)
                self.refresh()

    # ------------------------------------------------------------------ preview / status
    def schedule_refresh(self):
        if self._refresh_job:
            self.after_cancel(self._refresh_job)
        self._refresh_job = self.after(REFRESH_DELAY_MS, self.refresh)

    def refresh(self):
        if self._refresh_job:
            self.after_cancel(self._refresh_job)
        self._refresh_job = None
        if self.mode == "sample":
            self._refresh_sample()
            return
        js, errors = generate(self.template, self.macro_name.get().strip(), self.get_values())
        self.current_js = js
        if errors:
            self.status.configure(text="Fix before saving / pushing:\n" + "\n".join("• " + e for e in errors), foreground="#b00020")
        else:
            self.status.configure(text=f"Ready - {len(js.splitlines())} lines.", foreground="#1b7a2f")
        self._set_preview(js or "// (nothing to preview until the problems above are fixed)")

    def _refresh_sample(self):
        b = self.session.bundle()
        if b.errors:
            text = "Fix before exporting / pushing:\n" + "\n".join("• " + e for e in b.errors)
            self.status.configure(text=text, foreground="#b00020")
        else:
            active = sum(1 for m in b.macros if m.activate)
            text = f"Ready - {len(b.macros)} macro(s) ({active} will be activated), {len(b.panels)} UI panel(s)."
            if b.warnings:
                text += "\n" + "\n".join("• " + w for w in b.warnings)
            self.status.configure(text=text, foreground="#b36b00" if b.warnings else "#1b7a2f")

    def _set_preview(self, text: str):
        self.preview.configure(state="normal")
        self.preview.delete("1.0", "end")
        self.preview.insert("1.0", text)
        self.preview.configure(state="disabled")

    # ------------------------------------------------------------------ file actions
    def _current_text(self) -> str:
        if self.mode == "sample":
            return self.preview.get("1.0", "end-1c")
        return self.current_js

    def _require_ready(self) -> bool:
        if self.mode == "sample":
            ok = self.session is not None and not self.session.bundle().errors
        else:
            ok = bool(self.current_js)
        if ok:
            return True
        self.tabs.select(self.preview_tab)
        messagebox.showwarning("Not ready", self.status.cget("text"))
        return False

    def copy_js(self):
        text = self._current_text()
        if text:
            self.clipboard_clear()
            self.clipboard_append(text)
            self.status.configure(text="Copied to clipboard.", foreground="#1b7a2f")

    def save_js(self):
        if not self._require_ready():
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".js", initialfile=f"{self.macro_name.get().strip()}.js", filetypes=[("JavaScript", "*.js")])
        if path:
            Path(path).write_text(self.current_js, encoding="utf-8")
            self.status.configure(text=f"Saved {path}", foreground="#1b7a2f")

    def export_files(self):
        if not self._require_ready():
            return
        s = self.session
        parent = filedialog.askdirectory(title="Choose a folder - a sub-folder for this sample is created inside it")
        if not parent:
            return
        target = Path(parent) / s.sample.id
        if target.exists() and any(target.iterdir()) and not messagebox.askyesno("Folder exists", f"{target} already has files. Overwrite the ones with the same names?"):
            return
        target.mkdir(parents=True, exist_ok=True)
        files = s.export_files(s.bundle())
        for name, text in files.items():
            (target / re.sub(r'[<>:"/\\|?*]', "_", name)).write_text(text, encoding="utf-8", newline="\n")
        self.status.configure(text=f"Exported {len(files)} file(s) to {target}", foreground="#1b7a2f")

    def save_profile(self):
        if self.mode == "sample":
            name = self.session.sample.id
            path = filedialog.asksaveasfilename(defaultextension=".json", initialfile=f"{name}.json", filetypes=[("Profile", "*.json")])
            if path:
                Path(path).write_text(json.dumps(self.session.to_profile(), indent=2, ensure_ascii=False), encoding="utf-8")
                self.status.configure(text=f"Profile saved to {path}", foreground="#1b7a2f")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".json", initialfile=f"{self.macro_name.get().strip()}.json", filetypes=[("Profile", "*.json")])
        if path:
            save_profile(path, self.template, self.macro_name.get().strip(), self.get_values())
            self.status.configure(text=f"Profile saved to {path}", foreground="#1b7a2f")

    def load_profile(self):
        path = filedialog.askopenfilename(filetypes=[("Profile", "*.json")])
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
            if str(data.get("template", "")).startswith(SAMPLE_PREFIX):
                problems = self.select_sample(data["template"][len(SAMPLE_PREFIX):], data)
                if problems:
                    messagebox.showwarning("Profile loaded with problems", "\n".join(problems))
                return
            template, macro_name, values = load_profile(path)
        except (OSError, ValueError, KeyError) as exc:
            messagebox.showerror("Could not load profile", str(exc))
            return
        self.select_template(TEMPLATES.index(template), values, macro_name)

    # ------------------------------------------------------------------ deploy
    def _log(self, line: str):
        self.log.configure(state="normal")
        self.log.insert("end", line + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def build_job(self) -> tuple[list[tuple[str, str, bool]], list[tuple[str, str]]]:
        """(macros, panels) ready for push_bundle, honouring the 'activate' checkbox."""
        activate = self.activate_var.get()
        if self.mode == "sample":
            b = self.session.bundle()
            return [(m.name, m.code, m.activate and activate) for m in b.macros], [(p.panel_id, p.xml) for p in b.panels]
        return [(self.macro_name.get().strip(), self.current_js, activate)], []

    def push(self):
        if not self._require_ready():
            return
        hosts = [h.strip() for h in self.hosts_text.get("1.0", "end").splitlines() if h.strip()]
        if not hosts:
            messagebox.showwarning("No devices", "Enter at least one device IP or hostname.")
            return
        if not self.pass_var.get():
            messagebox.showwarning("Password needed", "Enter the device admin password.")
            return
        macros, panels = self.build_job()
        summary = f"{len(macros)} macro(s)" + (f" and {len(panels)} UI panel(s)" if panels else "")
        names = ", ".join(n for n, _c, _a in macros)
        if not messagebox.askyesno(
            "Push to devices",
            f"Install {summary} on {len(hosts)} device(s)?\n\n{names}\n\n"
            "Existing macros / panels with the same names are overwritten.",
        ):
            return

        job = dict(
            macros=macros, panels=panels, user=self.user_var.get().strip(), password=self.pass_var.get(),
            restart=self.restart_var.get(), transpile=self.transpile_var.get(), verify=self.verify_var.get(),
        )
        self.push_btn.configure(state="disabled")
        self._log(f"--- pushing {summary} to {len(hosts)} device(s) ---")
        threading.Thread(target=self._push_worker, args=(hosts, job), daemon=True).start()

    def _push_worker(self, hosts: list[str], job: dict[str, Any]):
        for host in hosts:
            res = push_bundle(
                host, job["user"], job["password"], job["macros"], job["panels"],
                restart_runtime=job["restart"], transpile=job["transpile"], verify_tls=job["verify"],
            )
            self.push_queue.put(f"{host}: OK - {len(res.steps)} steps" if res.ok else f"{host}: FAILED - {res.error}")
        self.push_queue.put(None)

    def _run_bg(self, work, done):
        def runner():
            try:
                work()
                error = None
            except Exception as exc:   # surfaced to the user in `done`
                error = exc
            self.task_queue.put(lambda: done(error))
        threading.Thread(target=runner, daemon=True).start()

    def _poll_queues(self):
        try:
            while True:
                item = self.push_queue.get_nowait()
                if item is None:
                    self.push_btn.configure(state="normal")
                    self._log("--- done ---")
                else:
                    self._log(item)
        except queue.Empty:
            pass
        try:
            while True:
                self.task_queue.get_nowait()()
        except queue.Empty:
            pass
        self._poll_job = self.after(150, self._poll_queues)

    def destroy(self):
        for job in (self._refresh_job, self._poll_job):
            if job:
                self.after_cancel(job)
        self._refresh_job = self._poll_job = None
        super().destroy()

    # ------------------------------------------------------------------ settings
    def _load_settings(self):
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        self.user_var.set(data.get("user", "admin"))
        self.hosts_text.insert("1.0", "\n".join(data.get("hosts", [])))

    def _on_close(self):
        hosts = [h.strip() for h in self.hosts_text.get("1.0", "end").splitlines() if h.strip()]
        try:
            SETTINGS_FILE.write_text(json.dumps({"user": self.user_var.get().strip(), "hosts": hosts}, indent=2), encoding="utf-8")
        except OSError:
            pass
        self.destroy()


def enable_dpi_awareness():
    """Crisp text on scaled Windows displays; harmless elsewhere."""
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass


def run():
    enable_dpi_awareness()
    App().mainloop()

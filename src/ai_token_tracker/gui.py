"""Desktop window: `ai-tokens-gui` (or the AITokenTracker app).

The top card shows the latest (or selected) session. The list below has one
row per session; click the arrow to expand a session into its prompts, and a
prompt into its individual API calls.
"""

from __future__ import annotations

import threading
import tkinter as tk
import tkinter.font
from pathlib import Path
from tkinter import filedialog, ttk

from . import __version__
from .dashboard import Dashboard
from .core import Session, Usage, clip, default_roots, duration, fmt, load_sessions, local, short

AUTO_REFRESH_MS = 60_000
COLUMNS = (
    ("started", "Started", 115, "w"),
    ("tool", "AI", 110, "w"),
    ("project", "Project", 100, "w"),
    ("calls", "Calls", 50, "e"),
    ("in_write", "In + write", 90, "e"),
    ("cache_read", "Cache read", 100, "e"),
    ("output", "Output", 80, "e"),
    ("total", "Total", 95, "e"),
)


class App(ttk.Frame):
    def __init__(self, root: tk.Tk):
        super().__init__(root, padding=12)
        self.root = root
        self.roots: list[Path] | None = None  # None = default Claude Code folder
        self.sessions: list[Session] = []
        self.node_map: dict[str, object] = {}  # tree item id -> Session / Turn
        self.loading = False

        root.title("AI Token Tracker")
        root.geometry("1120x760")
        root.minsize(760, 420)
        self.grid(sticky="nsew")
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        style = ttk.Style(root)
        if "clam" in style.theme_names() and root.tk.call("tk", "windowingsystem") == "x11":
            style.theme_use("clam")
        style.configure("Title.TLabel", font=("TkDefaultFont", 12, "bold"))
        style.configure("Big.TLabel", font=("TkDefaultFont", 16, "bold"))
        style.configure("Muted.TLabel", foreground="#6b6b66")
        style.configure("Treeview", rowheight=24)

        notebook = ttk.Notebook(self)
        notebook.grid(row=0, column=0, sticky="nsew")
        self.dashboard = Dashboard(notebook)
        notebook.add(self.dashboard, text="  Dashboard  ")
        self.tab = ttk.Frame(notebook, padding=(0, 10, 0, 0))
        self.tab.columnconfigure(0, weight=1)
        self.tab.rowconfigure(2, weight=1)
        notebook.add(self.tab, text="  Sessions  ")

        self._build_card()
        self._build_toolbar()
        self._build_tree()
        self._build_status()

        self.refresh()
        self.after(AUTO_REFRESH_MS, self._auto_refresh)

    # ---- layout ---------------------------------------------------------- #

    def _build_card(self) -> None:
        card = ttk.LabelFrame(self.tab, text=" Latest session ", padding=10)
        card.grid(row=0, column=0, sticky="ew")
        card.columnconfigure(0, weight=1)
        self.card = card
        self.card_title = ttk.Label(card, text="Loading…", style="Title.TLabel")
        self.card_title.grid(row=0, column=0, sticky="w")
        self.card_meta = ttk.Label(card, text="", style="Muted.TLabel")
        self.card_meta.grid(row=1, column=0, sticky="w", pady=(2, 8))

        stats = ttk.Frame(card)
        stats.grid(row=2, column=0, sticky="w")
        self.stat_values: dict[str, ttk.Label] = {}
        for i, (key, label) in enumerate([
            ("total", "Total"), ("output", "Output"), ("input", "Input"),
            ("cache_write", "Cache write"), ("cache_read", "Cache read"),
        ]):
            box = ttk.Frame(stats, padding=(0, 0, 28, 0))
            box.grid(row=0, column=i, sticky="w")
            ttk.Label(box, text=label, style="Muted.TLabel").pack(anchor="w")
            value = ttk.Label(box, text="-", style="Big.TLabel")
            value.pack(anchor="w")
            self.stat_values[key] = value

    def _build_toolbar(self) -> None:
        bar = ttk.Frame(self.tab, padding=(0, 10, 0, 6))
        bar.grid(row=1, column=0, sticky="ew")
        bar.columnconfigure(1, weight=1)
        ttk.Label(bar, text="All sessions", style="Title.TLabel").grid(row=0, column=0, padx=(0, 12))
        self.search = tk.StringVar()
        self.search.trace_add("write", lambda *_: self._fill_tree())
        entry = ttk.Entry(bar, textvariable=self.search)
        entry.grid(row=0, column=1, sticky="ew")
        ttk.Label(bar, text="filter", style="Muted.TLabel").grid(row=0, column=2, padx=(6, 12))
        ttk.Button(bar, text="Expand all", command=lambda: self._set_open(True)).grid(row=0, column=3)
        ttk.Button(bar, text="Collapse all", command=lambda: self._set_open(False)).grid(row=0, column=4, padx=4)
        ttk.Button(bar, text="Claude folder…", command=self._choose_folder).grid(row=0, column=5)
        ttk.Button(bar, text="Refresh", command=self.refresh).grid(row=0, column=6, padx=(4, 0))

    def _build_tree(self) -> None:
        frame = ttk.Frame(self.tab)
        frame.grid(row=2, column=0, sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        tree = ttk.Treeview(frame, columns=[c[0] for c in COLUMNS], selectmode="browse")
        tree.heading("#0", text="Session / prompt / API call", anchor="w")
        tree.column("#0", width=320, minwidth=200, stretch=True)
        for key, title, width, anchor in COLUMNS:
            tree.heading(key, text=title, anchor=anchor)
            tree.column(key, width=width, minwidth=50, anchor=anchor, stretch=False)
        bold = tk.font.nametofont("TkDefaultFont").copy()
        bold.configure(weight="bold")
        tree.tag_configure("session", font=bold)
        tree.tag_configure("sub", foreground="#6b6b66")
        ysb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        xsb = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=ysb.set, xscrollcommand=xsb.set)
        tree.grid(row=0, column=0, sticky="nsew")
        ysb.grid(row=0, column=1, sticky="ns")
        xsb.grid(row=1, column=0, sticky="ew")
        tree.bind("<<TreeviewOpen>>", self._on_open)
        tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree = tree

    def _build_status(self) -> None:
        self.status = ttk.Label(self, text="", style="Muted.TLabel", padding=(0, 8, 0, 0))
        self.status.grid(row=1, column=0, sticky="ew")

    # ---- data ------------------------------------------------------------ #

    def refresh(self) -> None:
        if self.loading:
            return
        self.loading = True
        self.status.configure(text="Reading transcripts…")

        def work():
            try:
                result = load_sessions(self.roots)
                error = None
            except Exception as exc:  # show it instead of dying silently
                result, error = [], exc
            self.after(0, lambda: self._loaded(result, error))

        threading.Thread(target=work, daemon=True).start()

    def _loaded(self, sessions: list[Session], error: Exception | None) -> None:
        self.loading = False
        self.sessions = sessions
        self._fill_tree()
        self.dashboard.update_data(sessions)
        if sessions:
            self._show_card(sessions[0], latest=True)
        else:
            self.card.configure(text=" No sessions found ")
            self.card_title.configure(text="No AI sessions with token usage were found.")
            self.card_meta.configure(text="Looked for Claude Code, Codex CLI, Gemini CLI and the custom log "
                                          "(~/.ai-token-tracker/usage).  “Claude folder…” picks another Claude location.")
        grand = Usage()
        for s in sessions:
            grand.add(s.usage)
        text = (f"{len(sessions)} sessions · {fmt(grand.total)} tokens in total "
                f"({fmt(grand.output)} output) · auto-refresh every {AUTO_REFRESH_MS // 1000}s · v{__version__}")
        if error:
            text = f"Error while reading transcripts: {error}"
        self.status.configure(text=text)

    def _auto_refresh(self) -> None:
        self.refresh()
        self.after(AUTO_REFRESH_MS, self._auto_refresh)

    def _choose_folder(self) -> None:
        roots = self.roots or default_roots()
        start = str(roots[0]) if roots else str(Path.home())
        chosen = filedialog.askdirectory(title="Pick the Claude 'projects' folder", initialdir=start)
        if chosen:
            self.roots = [Path(chosen)]
            self.refresh()

    # ---- tree ------------------------------------------------------------ #

    def _fill_tree(self) -> None:
        open_ids = {(self.node_map[i].tool, self.node_map[i].session_id) for i in self.tree.get_children()
                    if self.tree.item(i, "open") and isinstance(self.node_map.get(i), Session)}
        selected = self.tree.selection()
        node = self.node_map.get(selected[0]) if selected else None
        selected_id = (node.tool, node.session_id) if isinstance(node, Session) else None

        self.tree.delete(*self.tree.get_children())
        self.node_map.clear()
        needle = self.search.get().strip().lower()
        for s in self.sessions:
            if needle and needle not in f"{s.title} {s.tool} {s.project} {s.session_id} {s.branch} {' '.join(s.models)}".lower():
                continue
            u = s.usage
            iid = self.tree.insert("", "end", text=clip(s.title, 90), tags=("session",), values=(
                s.start.astimezone().strftime("%d %b %H:%M") if s.start else "-", clip(s.tool, 16),
                clip(s.project, 20), len(s.calls),
                short(u.input + u.cache_write), short(u.cache_read), short(u.output), fmt(u.total),
            ))
            self.node_map[iid] = s
            self.tree.insert(iid, "end", text="…")  # placeholder so the arrow shows
            if (s.tool, s.session_id) in open_ids:
                self.tree.item(iid, open=True)
                self._expand(iid)
            if (s.tool, s.session_id) == selected_id:
                self.tree.selection_set(iid)

    def _expand(self, iid: str) -> None:
        node = self.node_map.get(iid)
        kids = self.tree.get_children(iid)
        if not (len(kids) == 1 and self.tree.item(kids[0], "text") == "…"):
            return  # already filled
        self.tree.delete(kids[0])
        if isinstance(node, Session):
            for n, t in enumerate(node.turns, 1):
                u = t.usage
                sub = t.prompt.startswith("[sub-agent]")
                label = f"{n}. {clip(t.prompt or '(before first prompt)', 120)}"
                tid = self.tree.insert(iid, "end", text=label, tags=("sub",) if sub else (), values=(
                    local(t.timestamp, with_date=False), "", "", len(t.calls),
                    fmt(u.input + u.cache_write), fmt(u.cache_read), fmt(u.output), fmt(u.total),
                ))
                self.node_map[tid] = t
                if t.calls:
                    self.tree.insert(tid, "end", text="…")
        else:  # a Turn: list its API calls
            for n, call in enumerate(node.calls, 1):
                u = call.usage
                self.tree.insert(iid, "end", text=f"call {n} · {call.model}" + (" (sub-agent)" if call.subagent else ""),
                                 tags=("sub",), values=(
                    local(call.timestamp, with_date=False), "", "", "",
                    fmt(u.input + u.cache_write), fmt(u.cache_read), fmt(u.output), fmt(u.total),
                ))

    def _on_open(self, _event) -> None:
        self._expand(self.tree.focus())

    def _set_open(self, is_open: bool) -> None:
        for iid in self.tree.get_children():
            if is_open:
                self._expand(iid)
            self.tree.item(iid, open=is_open)

    def _on_select(self, _event) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        iid = sel[0]
        while self.tree.parent(iid):  # prompts/calls -> their session
            iid = self.tree.parent(iid)
        s = self.node_map.get(iid)
        if isinstance(s, Session):
            self._show_card(s, latest=bool(self.sessions) and s is self.sessions[0])

    def _show_card(self, s: Session, latest: bool) -> None:
        self.card.configure(text=" Latest session " if latest else " Selected session ")
        self.card_title.configure(text=clip(s.title, 100))
        parts = [s.tool, s.project, s.branch, f"{local(s.start)} ({duration(s)})",
                 ", ".join(s.models), f"{len(s.calls)} API calls", s.session_id]
        self.card_meta.configure(text="  ·  ".join(p for p in parts if p))
        u = s.usage
        for key in ("total", "output", "input", "cache_write", "cache_read"):
            value = u.total if key == "total" else getattr(u, key)
            self.stat_values[key].configure(text=fmt(value))


def main() -> int:
    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

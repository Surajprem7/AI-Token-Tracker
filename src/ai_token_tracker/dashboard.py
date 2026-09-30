"""Dashboard tab: token totals over time, per engine (model), per AI app and per project."""

from __future__ import annotations

import tkinter as tk
from datetime import datetime, timedelta
from tkinter import ttk

from .core import BY_APP, BY_MODEL, BY_PROJECT, BY_TOOL, Session, Usage, breakdown, by_day, clip, fmt, short

CHART_DAYS = 14
FAMILY_COLORS = {
    "Opus": "#c2410c",
    "Sonnet": "#2563eb",
    "Haiku": "#16a34a",
    "Fable": "#7c3aed",
    "GPT": "#0f766e",
    "Gemini": "#db2777",
    "Llama": "#ca8a04",
    "Qwen": "#9333ea",
    "Mistral": "#ea580c",
    "Deepseek": "#1e40af",
    "Grok": "#334155",
}
OTHER_COLOR = "#94a3b8"


class Dashboard(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent, padding=(0, 10, 0, 0))
        self.columnconfigure((0, 1), weight=1, uniform="col")
        self.rowconfigure((2, 3), weight=1)
        self.days: dict[str, dict[str, Usage]] = {}

        # Tiles: today / 7 days / 30 days / all time
        tiles = ttk.Frame(self)
        tiles.grid(row=0, column=0, columnspan=2, sticky="ew")
        self.tiles: dict[str, tuple[ttk.Label, ttk.Label]] = {}
        for i, name in enumerate(("Today", "Last 7 days", "Last 30 days", "All time")):
            tiles.columnconfigure(i, weight=1, uniform="tile")
            box = ttk.LabelFrame(tiles, text=f" {name} ", padding=(10, 4, 10, 8))
            box.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 8, 0))
            big = ttk.Label(box, text="-", style="Big.TLabel")
            big.pack(anchor="w")
            small = ttk.Label(box, text="", style="Muted.TLabel")
            small.pack(anchor="w")
            self.tiles[name] = (big, small)

        # Daily chart, stacked by model family
        chart_box = ttk.LabelFrame(self, text=f" Tokens per day, last {CHART_DAYS} days (by engine) ", padding=6)
        chart_box.grid(row=1, column=0, columnspan=2, sticky="ew", pady=10)
        chart_box.columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(chart_box, height=150, highlightthickness=0, background="white")
        self.canvas.grid(row=0, column=0, sticky="ew")
        self.canvas.bind("<Configure>", lambda _e: self._draw_chart())

        # Breakdown tables
        self.tables: dict[str, ttk.Treeview] = {}
        for n, (title, label) in enumerate((("AI tool", "AI tool"), ("Engine (model)", "engine (model)"),
                                            ("App", "app"), ("Project", "project"))):
            row, col = 2 + n // 2, n % 2
            box = ttk.LabelFrame(self, text=f" By {label} ", padding=4)
            box.grid(row=row, column=col, sticky="nsew", padx=(0 if col == 0 else 8, 0), pady=(0, 6))
            box.columnconfigure(0, weight=1)
            box.rowconfigure(0, weight=1)
            tv = ttk.Treeview(box, columns=("name", "sessions", "output", "total", "share"), show="headings", height=4)
            tv.heading("name", text=title, anchor="w")
            tv.column("name", width=150, minwidth=100, stretch=True)
            for key, head, width in (("sessions", "Sess.", 55), ("output", "Output", 70), ("total", "Total", 75), ("share", "Share", 60)):
                tv.heading(key, text=head, anchor="e")
                tv.column(key, width=width, minwidth=40, anchor="e", stretch=False)
            sb = ttk.Scrollbar(box, orient="vertical", command=tv.yview)
            tv.configure(yscrollcommand=sb.set)
            tv.grid(row=0, column=0, sticky="nsew")
            sb.grid(row=0, column=1, sticky="ns")
            self.tables[title] = tv

    def update_data(self, sessions: list[Session]) -> None:
        self.days = by_day(sessions)
        today = datetime.now().date()

        def since(n_days: int | None) -> Usage:
            u = Usage()
            for day, fams in self.days.items():
                if n_days is None or (today - datetime.strptime(day, "%Y-%m-%d").date()).days < n_days:
                    for fu in fams.values():
                        u.add(fu)
            return u

        for name, n in (("Today", 1), ("Last 7 days", 7), ("Last 30 days", 30), ("All time", None)):
            u = since(n)
            big, small = self.tiles[name]
            big.configure(text=fmt(u.total))
            small.configure(text=f"output {short(u.output)} · new input {short(u.input + u.cache_write)}")

        grand = sum(s.usage.total for s in sessions) or 1
        for title, key in (("AI tool", BY_TOOL), ("Engine (model)", BY_MODEL), ("App", BY_APP), ("Project", BY_PROJECT)):
            tv = self.tables[title]
            tv.delete(*tv.get_children())
            for name, g in breakdown(sessions, key).items():
                tv.insert("", "end", values=(
                    clip(name, 40), len(g.session_ids), short(g.usage.output), short(g.usage.total),
                    f"{g.usage.total / grand * 100:.1f}%",
                ))
        self._draw_chart()

    def _draw_chart(self) -> None:
        c = self.canvas
        c.delete("all")
        w, h = c.winfo_width(), int(c["height"])
        if w < 50:
            return
        left, right, top, bottom = 56, 110, 10, 22
        today = datetime.now().date()
        dates = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(CHART_DAYS - 1, -1, -1)]
        fams = sorted({f for d in self.days.values() for f in d},
                      key=lambda f: list(FAMILY_COLORS).index(f) if f in FAMILY_COLORS else 99)
        totals = [sum(u.total for u in self.days.get(d, {}).values()) for d in dates]
        peak = max(totals) or 1
        plot_w, plot_h = w - left - right, h - top - bottom
        slot = plot_w / len(dates)

        for frac in (0, 0.5, 1):  # gridlines + labels
            y = top + plot_h * (1 - frac)
            c.create_line(left, y, left + plot_w, y, fill="#e4e3de")
            c.create_text(left - 6, y, text=short(int(peak * frac)), anchor="e", fill="#6b6b66")

        for i, day in enumerate(dates):
            x0 = left + i * slot + slot * 0.18
            x1 = left + (i + 1) * slot - slot * 0.18
            y = top + plot_h
            for fam in fams:
                u = self.days.get(day, {}).get(fam)
                if not u or not u.total:
                    continue
                seg = u.total / peak * plot_h
                c.create_rectangle(x0, y - seg, x1, y, width=0, fill=FAMILY_COLORS.get(fam, OTHER_COLOR))
                y -= seg
            if i % 2 == (len(dates) - 1) % 2:  # label every other day, always today
                c.create_text((x0 + x1) / 2, top + plot_h + 11, text=day[8:] + "/" + day[5:7], fill="#6b6b66")

        for n, fam in enumerate(fams or ["no data"]):  # legend
            y = top + 8 + n * 20
            c.create_rectangle(w - right + 14, y - 6, w - right + 26, y + 6, width=0,
                               fill=FAMILY_COLORS.get(fam, OTHER_COLOR))
            c.create_text(w - right + 32, y, text=fam, anchor="w")

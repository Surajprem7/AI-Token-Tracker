/* AI Token Tracker dashboard. Plain JS + inline SVG, no external libraries, works offline. */
"use strict";

const $ = (id) => document.getElementById(id);
const TOKEN = new URLSearchParams(location.search).get("t") || "";
const DAY = 86400000;
// Fixed colour per tool, so a tool keeps its colour whatever else is installed.
const KNOWN_TOOL_SLOTS = { "Codex CLI": 1, "Claude Code": 2, "Gemini CLI": 3, "Qwen Code": 4,
  "OpenCode": 5, "Cline": 6, "Roo Code": 7, "Kilo Code": 8 };
const SVG_NS = "http://www.w3.org/2000/svg";

const store = {
  get(key, fallback) { try { const v = localStorage.getItem("att." + key); return v === null ? fallback : JSON.parse(v); } catch { return fallback; } },
  set(key, value) { try { localStorage.setItem("att." + key, JSON.stringify(value)); } catch { /* storage unavailable */ } },
};

const state = {
  data: null,
  calls: [],            // flattened API calls
  toolColor: [], modelColor: [], modelSlot: [],
  period: store.get("period", "30"),
  hiddenTools: new Set(store.get("hiddenTools", [])),  // tool names switched off
  trendGroup: store.get("trendGroup", "tool"),
  trendMetric: store.get("trendMetric", "tokens"),
  breakdown: store.get("breakdown", "day"),
  sort: { key: null, dir: -1 },
  page: store.get("page", "overview"),
  search: "", sessionSort: "recent", shown: 50,
  theme: store.get("theme", "auto"),
  loadedAt: null,
};

/* ---------------------------------------------------------------- helpers */

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
const nf = new Intl.NumberFormat();
function fmt(n) { return nf.format(Math.round(n || 0)); }
function compact(n) {
  n = n || 0;
  const a = Math.abs(n);
  if (a >= 1e9) return (n / 1e9).toFixed(a >= 1e10 ? 0 : 1) + "B";
  if (a >= 1e6) return (n / 1e6).toFixed(a >= 1e7 ? 0 : 1) + "M";
  if (a >= 1e3) return (n / 1e3).toFixed(a >= 1e4 ? 0 : 1) + "k";
  return String(Math.round(n));
}
function money(v) {
  v = v || 0;
  if (v === 0) return "$0";
  if (v < 0.01) return "<$0.01";
  if (v < 1000) return "$" + v.toFixed(2);
  return "$" + nf.format(Math.round(v));
}
function metricFmt(v, metric) { return metric === "cost" ? money(v) : compact(v); }
function sod(ts) { const d = new Date(ts); d.setHours(0, 0, 0, 0); return d.getTime(); }
function addDays(ts, n) { const d = new Date(ts); d.setDate(d.getDate() + n); return d.getTime(); }
function dayKey(ts) { const d = new Date(ts); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; }
function dateLabel(ts, opts) { return new Date(ts).toLocaleDateString(undefined, opts || { month: "short", day: "numeric" }); }
function timeLabel(ts) { return ts ? new Date(ts).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" }) : "–"; }
function dateTime(ts) { return ts ? `${dateLabel(ts, { month: "short", day: "numeric", year: "numeric" })} ${timeLabel(ts)}` : "–"; }
function duration(ms) {
  if (!ms || ms < 0) return "";
  const m = Math.round(ms / 60000);
  return m < 60 ? `${m}m` : `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m`;
}
function el(tag, attrs, children) {
  const n = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs || {})) n.setAttribute(k, v);
  (children || []).forEach((c) => n.appendChild(c));
  return n;
}
function slotColor(slot) { return slot ? `var(--s${slot})` : "var(--other)"; }
function toast(msg) {
  const t = $("toast");
  t.textContent = msg; t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { t.hidden = true; }, 3500);
}

/* ---------------------------------------------------------------- tooltip */

const tip = $("tooltip");
function showTip(evt, html) {
  tip.innerHTML = html; tip.hidden = false;
  const pad = 14, r = tip.getBoundingClientRect();
  let x = evt.clientX + pad, y = evt.clientY + pad;
  if (x + r.width > innerWidth - 8) x = evt.clientX - r.width - pad;
  if (y + r.height > innerHeight - 8) y = evt.clientY - r.height - pad;
  tip.style.left = Math.max(8, x) + "px"; tip.style.top = Math.max(8, y) + "px";
}
function hideTip() { tip.hidden = true; }
function tipRows(title, rows, footer) {
  return `<div class="tt-title">${esc(title)}</div>` + rows.map((r) =>
    `<div class="tt-row">${r.color ? `<span class="dot" style="background:${r.color}"></span>` : ""}<span>${esc(r.label)}</span><span class="v">${esc(r.value)}</span></div>`
  ).join("") + (footer ? `<div class="tt-row muted" style="margin-top:4px">${esc(footer)}</div>` : "");
}

/* ---------------------------------------------------------------- data */

async function load(refresh) {
  const btn = $("refresh");
  btn.classList.add("spin");
  try {
    const res = await fetch("/api/data" + (refresh ? "?refresh=1" : ""), { headers: { "X-Token": TOKEN } });
    const body = await res.json();
    if (!res.ok) throw new Error(body.error || res.statusText);
    state.data = body;
    prepare();
    state.loadedAt = Date.now();
    render();
  } catch (err) {
    toast("Could not load usage: " + err.message);
  } finally {
    btn.classList.remove("spin");
    updateStamp();
  }
}

function prepare() {
  const d = state.data;
  const calls = [];
  d.sessions.forEach((s, si) => {
    s.turns.forEach((t, ti) => {
      t.c.forEach((c) => {
        const [ts, model, app, inp, out, cw, cr, cost, priced, sub] = c;
        calls.push({ ts, tool: s.tool, model, app, proj: s.project, si, ti, inp, out, cw, cr,
          total: inp + out + cw + cr, cost, priced, sub });
      });
    });
    let tot = 0, cost = 0, start = null, end = null;
    const models = new Set();
    s.turns.forEach((t) => t.c.forEach((c) => {
      tot += c[3] + c[4] + c[5] + c[6]; cost += c[7]; models.add(c[1]);
      if (c[0]) { start = start === null ? c[0] : Math.min(start, c[0]); end = end === null ? c[0] : Math.max(end, c[0]); }
    }));
    s._total = tot; s._cost = cost; s._start = start; s._end = end; s._models = [...models];
  });
  state.calls = calls;

  // Colours follow the entity and are fixed for the whole data set, so filters never repaint them.
  const byTool = new Map(), byModel = new Map();
  for (const c of calls) {
    byTool.set(c.tool, (byTool.get(c.tool) || 0) + c.total);
    byModel.set(c.model, (byModel.get(c.model) || 0) + c.total);
  }
  const used = new Set();
  state.toolColor = d.tools.map((name) => { const s = KNOWN_TOOL_SLOTS[name]; if (s) used.add(s); return s || 0; });
  const free = [1, 2, 3, 4, 5, 6, 7, 8].filter((s) => !used.has(s));
  [...byTool.entries()].sort((a, b) => b[1] - a[1]).forEach(([tool]) => {
    if (!state.toolColor[tool] && free.length) state.toolColor[tool] = free.shift();
  });
  state.modelSlot = d.models.map(() => 0);
  [...byModel.entries()].sort((a, b) => b[1] - a[1]).slice(0, 7).forEach(([m], i) => { state.modelSlot[m] = i + 1; });
}

function toolEnabled(toolIdx) { return !state.hiddenTools.has(state.data.tools[toolIdx]); }

function periodRange(period) {
  const now = Date.now();
  if (period === "today") return { start: sod(now), end: now + 1, days: 1, label: "Today" };
  if (period === "all") {
    let min = now;
    for (const c of state.calls) if (c.ts && c.ts < min) min = c.ts;
    return { start: sod(min), end: now + 1, days: Math.max(1, Math.round((sod(now) - sod(min)) / DAY) + 1), label: "All time", all: true };
  }
  const n = Number(period);
  return { start: addDays(sod(now), -(n - 1)), end: now + 1, days: n, label: `Last ${n} days` };
}

function filtered(range) {
  return state.calls.filter((c) => toolEnabled(c.tool) &&
    (range.all ? true : c.ts !== null && c.ts >= range.start && c.ts < range.end));
}

function sum(calls) {
  const u = { inp: 0, out: 0, cw: 0, cr: 0, total: 0, cost: 0, unpriced: 0, calls: calls.length };
  for (const c of calls) {
    u.inp += c.inp; u.out += c.out; u.cw += c.cw; u.cr += c.cr; u.total += c.total; u.cost += c.cost;
    if (!c.priced) u.unpriced += c.total;
  }
  return u;
}

function group(calls, keyFn) {
  const m = new Map();
  for (const c of calls) {
    const k = keyFn(c);
    let g = m.get(k);
    if (!g) { g = { key: k, inp: 0, out: 0, cw: 0, cr: 0, total: 0, cost: 0, calls: 0, sessions: new Set() }; m.set(k, g); }
    g.inp += c.inp; g.out += c.out; g.cw += c.cw; g.cr += c.cr; g.total += c.total; g.cost += c.cost;
    g.calls += 1; g.sessions.add(c.si);
  }
  return m;
}

/* ---------------------------------------------------------------- overview */

function render() {
  if (!state.data) return;
  renderChrome();
  if (state.page === "overview") renderOverview();
  if (state.page === "sessions") renderSessions();
  if (state.page === "sources") renderSources();
}

function renderChrome() {
  document.querySelectorAll(".tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.page === state.page)));
  document.querySelectorAll(".page").forEach((p) => { p.hidden = p.id !== "page-" + state.page; });
  document.querySelectorAll("#period button").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.period === state.period)));
  document.querySelector(".filters").hidden = state.page === "sources";

  const totals = group(state.calls, (c) => c.tool);
  const chips = $("toolChips");
  chips.innerHTML = "";
  state.data.tools.forEach((name, i) => {
    const b = document.createElement("button");
    b.className = "chip";
    b.setAttribute("aria-pressed", String(toolEnabled(i)));
    b.title = `${fmt(totals.get(i)?.total || 0)} tokens in total`;
    b.innerHTML = `<span class="dot" style="background:${slotColor(state.toolColor[i])}"></span>${esc(name)}`;
    b.onclick = () => {
      if (state.hiddenTools.has(name)) state.hiddenTools.delete(name); else state.hiddenTools.add(name);
      if (state.data.tools.every((t) => state.hiddenTools.has(t))) state.hiddenTools.delete(name);  // keep one on
      store.set("hiddenTools", [...state.hiddenTools]);
      render();
    };
    chips.appendChild(b);
  });
}

function renderOverview() {
  const range = periodRange(state.period);
  const calls = filtered(range);
  const u = sum(calls);

  $("heroLabel").textContent = `Tokens · ${range.label}`;
  $("heroTotal").textContent = fmt(u.total);
  $("heroCost").innerHTML = `${esc(money(u.cost))}<span class="est">estimated API cost</span>`;
  if (range.all) {
    $("heroDelta").textContent = "";
  } else {
    const len = range.days * DAY;
    const prev = sum(state.calls.filter((c) => toolEnabled(c.tool) && c.ts !== null && c.ts >= range.start - len && c.ts < range.start));
    const name = state.period === "today" ? "yesterday" : `the previous ${range.days} days`;
    if (!prev.total) $("heroDelta").textContent = `No usage in ${name}`;
    else {
      const pct = (u.total - prev.total) / prev.total * 100;
      $("heroDelta").textContent = `${pct >= 0 ? "▲" : "▼"} ${Math.abs(pct).toFixed(0)}% vs ${name} (${compact(prev.total)})`;
    }
  }

  // share by AI tool
  const byTool = [...group(calls, (c) => c.tool).values()].sort((a, b) => (state.toolColor[a.key] || 9) - (state.toolColor[b.key] || 9));
  $("shareBar").innerHTML = byTool.filter((g) => g.total).map((g) =>
    `<span style="width:${(g.total / (u.total || 1) * 100).toFixed(3)}%;background:${slotColor(state.toolColor[g.key])}"></span>`).join("");
  $("shareList").innerHTML = byTool.length ? byTool.sort((a, b) => b.total - a.total).map((g) => `
    <li><span class="dot" style="background:${slotColor(state.toolColor[g.key])}"></span>
      <span class="name">${esc(state.data.tools[g.key])}</span>
      <span class="pct">${(g.total / (u.total || 1) * 100).toFixed(1)}%</span>
      <span class="sub">${compact(g.total)} tokens · ${esc(money(g.cost))} · ${g.sessions.size} sessions</span></li>`).join("")
    : `<li class="muted">No usage in this period.</li>`;
  $("unpricedNote").textContent = u.unpriced
    ? `${compact(u.unpriced)} tokens came from models without a known price and aren't in the cost. You can add prices on the Sources page.`
    : "Costs are estimates at pay-as-you-go API list prices. Subscriptions are billed differently.";

  renderKpis(calls, range);
  renderTrend(calls, range);
  renderHours(calls);
  renderHeatmap();
  renderTopModels(calls, u);
  renderBreakdown(calls, u);
}

function streaks() {
  const days = new Set(state.calls.filter((c) => c.ts && toolEnabled(c.tool)).map((c) => sod(c.ts)));
  const sorted = [...days].sort((a, b) => a - b);
  let best = 0, run = 0, prev = null;
  for (const d of sorted) { run = prev !== null && Math.round((d - prev) / DAY) === 1 ? run + 1 : 1; best = Math.max(best, run); prev = d; }
  let cur = 0, day = sod(Date.now());
  if (!days.has(day)) day = addDays(day, -1);  // today not started yet still keeps the streak
  while (days.has(day)) { cur++; day = addDays(day, -1); }
  return { cur, best };
}

function renderKpis(calls, range) {
  const sessions = new Set(calls.map((c) => c.si));
  const prompts = new Set();
  for (const c of calls) {
    const t = state.data.sessions[c.si].turns[c.ti];
    if (t.p && !t.p.startsWith("[sub-agent]")) prompts.add(c.si + ":" + c.ti);
  }
  const active = new Set(calls.filter((c) => c.ts).map((c) => dayKey(c.ts)));
  const u = sum(calls);
  const s = streaks();
  const tiles = [
    ["Sessions", fmt(sessions.size), `${fmt(prompts.size)} prompts`],
    ["API calls", fmt(calls.length), calls.length ? `${compact(u.total / calls.length)} tokens per call` : ""],
    ["Output tokens", compact(u.out), `${compact(u.inp + u.cw)} new input`],
    ["Cache reads", compact(u.cr), u.total ? `${(u.cr / u.total * 100).toFixed(0)}% of all tokens` : ""],
    ["Active days", `${active.size}`, range.all ? `${compact(u.total / (active.size || 1))} per active day` : `of ${range.days} · ${compact(u.total / (active.size || 1))}/day`],
    ["Streak", `${s.cur} day${s.cur === 1 ? "" : "s"}`, `best ${s.best} day${s.best === 1 ? "" : "s"}`],
  ];
  $("kpis").innerHTML = tiles.map(([label, value, hint]) =>
    `<div class="kpi"><div class="label">${esc(label)}</div><div class="value">${esc(value)}</div><div class="hint">${esc(hint)}</div></div>`).join("");
}

/** Round axis: 4-5 ticks on a 1/2/2.5/5 x 10^n step. Returns { max, step }. */
function niceScale(v) {
  if (v <= 0) return { max: 1, step: 0.25 };
  const raw = v / 4, p = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * p).find((x) => x >= raw);
  return { max: Math.ceil(v / step) * step, step };
}

/** Rect with rounded top corners only (data end), square at the baseline. */
function barPath(x, y, w, h, r) {
  r = Math.min(r, w / 2, h);
  return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
}

function buckets(range) {
  const now = Date.now();
  if (state.period === "today") {
    const start = sod(now);
    return Array.from({ length: 24 }, (_, h) => ({ start: start + h * 3600000, end: start + (h + 1) * 3600000,
      label: `${String(h).padStart(2, "0")}:00`, short: String(h) }));
  }
  const out = [];
  const weekly = range.days > 120;
  let t = range.start;
  if (weekly) { const d = new Date(t); d.setDate(d.getDate() - ((d.getDay() + 6) % 7)); t = sod(d.getTime()); }
  while (t <= now) {
    const next = weekly ? addDays(t, 7) : addDays(t, 1);
    out.push({ start: t, end: next, label: weekly ? `Week of ${dateLabel(t)}` : dateLabel(t, { weekday: "short", month: "short", day: "numeric" }),
      short: dateLabel(t) });
    t = next;
  }
  return out;
}

function seriesOf(c) {
  if (state.trendGroup === "tool") return { key: "t" + c.tool, name: state.data.tools[c.tool], slot: state.toolColor[c.tool] };
  const slot = state.modelSlot[c.model];
  return slot ? { key: "m" + c.model, name: state.data.models[c.model], slot } : { key: "other", name: "Other models", slot: 0 };
}

function renderTrend(calls, range) {
  const metric = state.trendMetric;
  const bs = buckets(range);
  const series = new Map();
  const cells = bs.map(() => new Map());
  let bi = 0;
  const sortedCalls = calls.filter((c) => c.ts).sort((a, b) => a.ts - b.ts);
  for (const c of sortedCalls) {
    while (bi < bs.length - 1 && c.ts >= bs[bi].end) bi++;
    if (c.ts < bs[bi].start || c.ts >= bs[bi].end) continue;
    const s = seriesOf(c);
    if (!series.has(s.key)) series.set(s.key, s);
    const v = metric === "cost" ? c.cost : c.total;
    cells[bi].set(s.key, (cells[bi].get(s.key) || 0) + v);
  }
  const order = [...series.values()].sort((a, b) => (a.slot || 99) - (b.slot || 99));
  const totals = cells.map((m) => [...m.values()].reduce((a, b) => a + b, 0));
  const scale = niceScale(Math.max(0, ...totals)), max = scale.max;

  $("trendTitle").textContent = state.period === "today" ? "Usage by hour" : bs.length && range.days > 120 ? "Usage by week" : "Usage by day";
  const box = $("trend");
  box.innerHTML = "";
  box.style.minHeight = "";
  if (!sortedCalls.length) { box.innerHTML = `<div class="empty">No usage in this period.</div>`; $("trendLegend").innerHTML = ""; return; }

  const W = Math.max(300, box.clientWidth || 760), H = W < 500 ? 200 : 250, L = 44, R = 6, T = 10, B = 24;
  const pw = W - L - R, ph = H - T - B, slot = pw / bs.length;
  const bw = Math.max(2, Math.min(24, slot * 0.66));
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Stacked bar chart of usage over time" });
  for (let v = 0, i = 0; v <= max + 1e-9; v += scale.step, i++) {
    const y = T + ph - (v / max) * ph;
    svg.appendChild(el("line", { x1: L, x2: W - R, y1: y, y2: y, class: i === 0 ? "baseline" : "gridline" }));
    const t = el("text", { x: L - 8, y: y + 4, "text-anchor": "end" });
    t.textContent = metricFmt(v, metric);
    svg.appendChild(t);
  }
  const every = Math.max(1, Math.ceil(bs.length / Math.max(3, Math.floor(pw / 90))));
  bs.forEach((b, i) => {
    const x0 = L + i * slot + (slot - bw) / 2;
    const g = el("g", { class: "bar-group" });
    let y = T + ph;
    const present = order.filter((s) => cells[i].get(s.key));
    present.forEach((s, k) => {
      const v = cells[i].get(s.key);
      let h = (v / max) * ph;
      const top = k === present.length - 1;
      const gap = k > 0 && h > 3 ? 2 : 0;  // 2px surface gap between stacked segments
      h = Math.max(h - gap, 0.5);
      y -= gap;
      const node = top ? el("path", { d: barPath(x0, y - h, bw, h, 4) }) : el("rect", { x: x0, y: y - h, width: bw, height: h });
      node.setAttribute("fill", slotColor(s.slot));
      node.setAttribute("class", "seg");
      g.appendChild(node);
      y -= h;
    });
    const hit = el("rect", { x: L + i * slot, y: T, width: slot, height: ph, class: "hit" });
    hit.addEventListener("mousemove", (e) => {
      const rows = present.slice().reverse().map((s) => ({ color: slotColor(s.slot), label: s.name, value: metricFmt(cells[i].get(s.key), metric) }));
      showTip(e, tipRows(b.label, rows, rows.length ? `Total ${metric === "cost" ? money(totals[i]) : fmt(totals[i]) + " tokens"}` : "No usage"));
    });
    hit.addEventListener("mouseleave", hideTip);
    svg.appendChild(g);
    svg.appendChild(hit);
    if (i % every === (bs.length - 1) % every) {
      const t = el("text", { x: L + i * slot + slot / 2, y: H - 6, "text-anchor": "middle" });
      t.textContent = b.short;
      svg.appendChild(t);
    }
  });
  box.appendChild(svg);
  $("trendLegend").innerHTML = order.length > 1 ? order.map((s) =>
    `<span><span class="dot" style="background:${slotColor(s.slot)}"></span>${esc(s.name)}</span>`).join("") : "";
}

function renderHours(calls) {
  const hours = new Array(24).fill(0);
  for (const c of calls) if (c.ts) hours[new Date(c.ts).getHours()] += c.total;
  const box = $("hours");
  box.innerHTML = "";
  if (!hours.some(Boolean)) { box.innerHTML = `<div class="empty">No usage in this period.</div>`; return; }
  const W = Math.max(260, box.clientWidth || 360), H = 190, L = 4, R = 4, T = 8, B = 20, pw = W - L - R, ph = H - T - B, slot = pw / 24;
  const bw = Math.min(10, slot - 3), max = Math.max(...hours);
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Tokens by hour of day" });
  svg.appendChild(el("line", { x1: L, x2: W - R, y1: T + ph, y2: T + ph, class: "baseline" }));
  const peak = hours.indexOf(max);
  hours.forEach((v, h) => {
    const bh = v ? Math.max(2, (v / max) * ph) : 0;
    const x = L + h * slot + (slot - bw) / 2;
    if (bh) svg.appendChild(el("path", { d: barPath(x, T + ph - bh, bw, bh, 3), fill: "var(--s1)", class: "seg" }));
    const hit = el("rect", { x: L + h * slot, y: T, width: slot, height: ph, class: "hit" });
    hit.addEventListener("mousemove", (e) => showTip(e, tipRows(`${String(h).padStart(2, "0")}:00 – ${String(h).padStart(2, "0")}:59`,
      [{ label: "Tokens", value: fmt(v) }], h === peak ? "Your busiest hour" : "")));
    hit.addEventListener("mouseleave", hideTip);
    svg.appendChild(hit);
    if (h % 6 === 0) {
      const t = el("text", { x: L + h * slot + slot / 2, y: H - 4, "text-anchor": "middle" });
      t.textContent = `${String(h).padStart(2, "0")}:00`;
      svg.appendChild(t);
    }
  });
  box.appendChild(svg);
  const all = hours.reduce((a, b) => a + b, 0);
  const day = hours.slice(6, 18).reduce((a, b) => a + b, 0);
  const note = document.createElement("p");
  note.className = "note";
  note.textContent = `Busiest hour ${String(peak).padStart(2, "0")}:00 · ${(day / all * 100).toFixed(0)}% of tokens between 06:00 and 18:00 (local time)`;
  box.appendChild(note);
}

function renderHeatmap() {
  const perDay = new Map();
  for (const c of state.calls) {
    if (!c.ts || !toolEnabled(c.tool)) continue;
    const k = sod(c.ts);
    const d = perDay.get(k) || { total: 0, cost: 0 };
    d.total += c.total; d.cost += c.cost;
    perDay.set(k, d);
  }
  const today = sod(Date.now());
  const d0 = new Date(addDays(today, -364));
  const start = addDays(sod(d0.getTime()), -((d0.getDay() + 6) % 7));  // Monday of the first week
  const values = [...perDay.entries()].filter(([k]) => k >= start).map(([, v]) => v.total).sort((a, b) => a - b);
  const q = (p) => values.length ? values[Math.min(values.length - 1, Math.floor(p * values.length))] : 0;
  const cuts = [q(0.25), q(0.5), q(0.75)];
  const level = (v) => !v ? 0 : v <= cuts[0] ? 1 : v <= cuts[1] ? 2 : v <= cuts[2] ? 3 : 4;

  const cell = 12, gap = 3, L = 28, T = 16;
  const weeks = Math.ceil((Math.round((today - start) / DAY) + 1) / 7);
  const W = L + weeks * (cell + gap), H = T + 7 * (cell + gap) + 4;
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Daily activity over the past year", style: `min-width:${Math.min(W, 640)}px` });
  ["Mon", "", "Wed", "", "Fri", "", ""].forEach((name, r) => {
    if (!name) return;
    const t = el("text", { x: 0, y: T + r * (cell + gap) + cell - 2 });
    t.textContent = name; svg.appendChild(t);
  });
  let lastMonth = -1, lastLabelWeek = -9;
  for (let w = 0; w < weeks; w++) {
    for (let r = 0; r < 7; r++) {
      const day = addDays(start, w * 7 + r);
      if (day > today) continue;
      const m = new Date(day).getMonth();
      if (r === 0 && m !== lastMonth) {
        if (w < weeks - 2 && w - lastLabelWeek >= 3) {
          lastLabelWeek = w;
          const t = el("text", { x: L + w * (cell + gap), y: 10 });
          t.textContent = new Date(day).toLocaleDateString(undefined, { month: "short" });
          svg.appendChild(t);
        }
        lastMonth = m;
      }
      const v = perDay.get(day);
      const rect = el("rect", { x: L + w * (cell + gap), y: T + r * (cell + gap), width: cell, height: cell, rx: 3,
        fill: `var(--q${level(v ? v.total : 0)})` });
      rect.addEventListener("mousemove", (e) => showTip(e, tipRows(dateLabel(day, { weekday: "long", month: "short", day: "numeric", year: "numeric" }),
        v ? [{ label: "Tokens", value: fmt(v.total) }, { label: "Est. cost", value: money(v.cost) }] : [{ label: "No usage", value: "" }])));
      rect.addEventListener("mouseleave", hideTip);
      svg.appendChild(rect);
    }
  }
  const box = $("heatmap");
  box.innerHTML = "";
  const scroller = document.createElement("div");
  scroller.className = "heat-scroll";
  scroller.appendChild(svg);
  box.appendChild(scroller);
  const year = [...perDay.entries()].filter(([k]) => k >= start);
  const best = year.reduce((a, b) => (b[1].total > (a ? a[1].total : -1) ? b : a), null);
  const yearTotal = year.reduce((a, [, v]) => a + v.total, 0);
  const legend = document.createElement("div");
  legend.className = "heat-legend";
  legend.innerHTML = `<span class="heat-summary">${year.length} active days · ${esc(compact(yearTotal))} tokens`
    + (best ? ` · busiest ${esc(dateLabel(best[0], { month: "short", day: "numeric" }))} (${esc(compact(best[1].total))})` : "") + `</span>`
    + `Less ${[0, 1, 2, 3, 4].map((i) => `<i style="background:var(--q${i})"></i>`).join("")} More`;
  box.appendChild(legend);
  scroller.scrollLeft = scroller.scrollWidth;
}

function renderTopModels(calls, u) {
  const rows = [...group(calls, (c) => c.model).values()].sort((a, b) => b.total - a.total);
  $("topModelsNote").textContent = rows.length ? `${rows.length} model${rows.length === 1 ? "" : "s"}` : "";
  const top = rows[0]?.total || 1;
  $("topModels").innerHTML = rows.slice(0, 8).map((g) => {
    const color = slotColor(state.modelSlot[g.key]);
    return `<li><div class="row"><span class="dot" style="background:${color}"></span>
      <span class="name" title="${esc(state.data.models[g.key])}">${esc(state.data.models[g.key])}</span>
      <span class="val">${(g.total / (u.total || 1) * 100).toFixed(1)}% · ${esc(money(g.cost))}</span></div>
      <div class="track"><div class="fill" style="width:${(g.total / top * 100).toFixed(2)}%;background:${color}"></div></div></li>`;
  }).join("") || `<li class="muted">No usage in this period.</li>`;
}

const BREAKDOWN_COLS = [
  { key: "name", label: "Name" },
  { key: "sessions", label: "Sessions", opt: true },
  { key: "inp", label: "Input", opt: true },
  { key: "out", label: "Output", opt: true },
  { key: "cw", label: "Cache write", opt: true },
  { key: "cr", label: "Cache read", opt: true },
  { key: "total", label: "Total" },
  { key: "cost", label: "Est. cost" },
  { key: "share", label: "Share", opt: true },
];

function breakdownRows(calls, u) {
  const kind = state.breakdown;
  const d = state.data;
  const keyFn = { day: (c) => (c.ts ? dayKey(c.ts) : "unknown"), project: (c) => c.proj, model: (c) => c.model,
    tool: (c) => c.tool, app: (c) => c.app }[kind];
  const nameOf = { day: (k) => k, project: (k) => d.projects[k], model: (k) => d.models[k], tool: (k) => d.tools[k], app: (k) => d.apps[k] }[kind];
  return [...group(calls, keyFn).values()].map((g) => ({
    name: nameOf(g.key), sessions: g.sessions.size, inp: g.inp, out: g.out, cw: g.cw, cr: g.cr,
    total: g.total, cost: g.cost, share: g.total / (u.total || 1),
  }));
}

function renderBreakdown(calls, u) {
  const rows = breakdownRows(calls, u);
  const key = state.sort.key || (state.breakdown === "day" ? "name" : "total");
  const dir = state.sort.key ? state.sort.dir : -1;
  rows.sort((a, b) => (typeof a[key] === "string" ? a[key].localeCompare(b[key]) : a[key] - b[key]) * dir);
  const nameLabel = { day: "Date", project: "Project", model: "Model", tool: "AI tool", app: "App" }[state.breakdown];
  const head = BREAKDOWN_COLS.map((c) => `<th data-k="${c.key}" class="${c.opt ? "opt" : ""}" ${c.key === key ? `aria-sort="${dir < 0 ? "descending" : "ascending"}"` : ""}>${esc(c.key === "name" ? nameLabel : c.label)}</th>`).join("");
  const body = rows.map((r) => `<tr>${BREAKDOWN_COLS.map((c) => {
    const v = r[c.key];
    const text = c.key === "name" ? v : c.key === "cost" ? money(v) : c.key === "share" ? (v * 100).toFixed(1) + "%" : fmt(v);
    return `<td class="${c.opt ? "opt" : ""}" ${c.key === "name" ? `title="${esc(v)}"` : ""}>${esc(text)}</td>`;
  }).join("")}</tr>`).join("");
  $("breakdown").innerHTML = `<thead><tr>${head}</tr></thead><tbody>${body || `<tr><td class="muted">No usage in this period.</td></tr>`}</tbody>`;
  $("breakdown").querySelectorAll("th").forEach((th) => {
    th.onclick = () => {
      const k = th.dataset.k;
      state.sort = { key: k, dir: state.sort.key === k ? -state.sort.dir : (k === "name" ? 1 : -1) };
      renderBreakdown(calls, u);
    };
  });
  $("exportCsv").onclick = () => {
    const lines = [[nameLabel, ...BREAKDOWN_COLS.slice(1).map((c) => c.label)].join(",")];
    rows.forEach((r) => lines.push([`"${String(r.name).replace(/"/g, '""')}"`, r.sessions, r.inp, r.out, r.cw, r.cr, r.total, r.cost.toFixed(4), (r.share * 100).toFixed(2)].join(",")));
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([lines.join("\n")], { type: "text/csv" }));
    a.download = `ai-tokens-${state.breakdown}-${dayKey(Date.now())}.csv`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  };
}

/* ---------------------------------------------------------------- sessions */

function renderSessions() {
  const range = periodRange(state.period);
  const d = state.data;
  const q = state.search.trim().toLowerCase();
  let list = d.sessions.map((s, i) => ({ s, i })).filter(({ s }) => {
    if (!toolEnabled(s.tool)) return false;
    if (!range.all && !(s._end !== null && s._end >= range.start)) return false;
    if (!q) return true;
    const hay = [s.title, s.id, d.tools[s.tool], d.projects[s.project], s.branch, ...s._models.map((m) => d.models[m]),
      ...s.turns.map((t) => t.p)].join(" ").toLowerCase();
    return hay.includes(q);
  });
  const sorters = { recent: (a, b) => (b.s._end || 0) - (a.s._end || 0), tokens: (a, b) => b.s._total - a.s._total, cost: (a, b) => b.s._cost - a.s._cost };
  list.sort(sorters[state.sessionSort]);
  $("sessionCount").textContent = `${fmt(list.length)} session${list.length === 1 ? "" : "s"} · ${range.label.toLowerCase()}`;
  const box = $("sessionList");
  // Remember what's expanded, so a refresh doesn't fold everything the user opened.
  const openTurns = new Map();
  box.querySelectorAll("details.session[open]").forEach((det) => {
    openTurns.set(det.dataset.key, new Set([...det.querySelectorAll("details.turn[open]")].map((t) => t.dataset.idx)));
  });
  box.innerHTML = "";
  list.slice(0, state.shown).forEach(({ s }) => {
    const node = sessionNode(s);
    box.appendChild(node);
    const turns = openTurns.get(node.dataset.key);
    if (!turns) return;
    node._fill();
    node.open = true;
    node.querySelectorAll("details.turn").forEach((t) => {
      if (turns.has(t.dataset.idx)) { t._fill(); t.open = true; }
    });
  });
  if (!list.length) box.innerHTML = `<div class="card empty">No sessions match.</div>`;
  $("moreSessions").hidden = list.length <= state.shown;
}

const CARET = `<svg class="caret" viewBox="0 0 16 16"><path d="M6 4l4 4-4 4" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>`;

function sessionNode(s) {
  const d = state.data;
  const det = document.createElement("details");
  det.className = "session";
  det.dataset.key = `${s.tool}:${s.id}`;
  const meta = [d.tools[s.tool], d.projects[s.project], s.branch, dateTime(s._start), duration(s._end - s._start),
    s._models.map((m) => d.models[m]).join(", ")].filter(Boolean).join(" · ");
  det.innerHTML = `<summary>${CARET}
    <span class="s-title"><span class="dot" style="background:${slotColor(state.toolColor[s.tool])}"></span> ${esc(s.title)}</span>
    <span class="s-nums"><b>${fmt(s._total)}</b><span>${esc(money(s._cost))}</span></span>
    <span class="s-meta">${esc(meta)}</span></summary><div class="turns"></div>`;
  det._fill = () => {
    const box = det.querySelector(".turns");
    if (!box.childElementCount) s.turns.forEach((t, n) => box.appendChild(turnNode(t, n)));
  };
  det.addEventListener("toggle", () => { if (det.open) det._fill(); });
  return det;
}

function turnNode(t, n) {
  const d = state.data;
  let tot = 0, cost = 0;
  t.c.forEach((c) => { tot += c[3] + c[4] + c[5] + c[6]; cost += c[7]; });
  const det = document.createElement("details");
  det.className = "turn" + (t.p.startsWith("[sub-agent]") ? " sub" : "");
  det.dataset.idx = String(n);
  det.innerHTML = `<summary>${CARET}<span class="t-prompt" title="${esc(t.p)}">${n + 1}. ${esc(t.p || "(before the first prompt)")}</span>
    <span class="s-nums"><b>${fmt(tot)}</b><span>${esc(timeLabel(t.t))} · ${t.c.length} call${t.c.length === 1 ? "" : "s"} · ${esc(money(cost))}</span></span></summary>`;
  det._fill = () => {
    if (det.querySelector(".calls")) return;
    const rows = t.c.map((c, i) => `<tr><td>${i + 1}. ${esc(d.models[c[1]])}${c[9] ? " (sub-agent)" : ""}</td><td>${esc(timeLabel(c[0]))}</td>
      <td class="opt">${fmt(c[3])}</td><td>${fmt(c[4])}</td><td class="opt">${fmt(c[5])}</td><td class="opt">${fmt(c[6])}</td>
      <td>${fmt(c[3] + c[4] + c[5] + c[6])}</td><td>${c[8] ? esc(money(c[7])) : "no price"}</td></tr>`).join("");
    const wrap = document.createElement("div");
    wrap.className = "calls table-wrap";
    wrap.innerHTML = `<table class="data"><thead><tr><th>API call</th><th>Time</th><th class="opt">Input</th><th>Output</th>
      <th class="opt">Cache write</th><th class="opt">Cache read</th><th>Total</th><th>Est. cost</th></tr></thead><tbody>${rows}</tbody></table>`;
    det.appendChild(wrap);
  };
  det.addEventListener("toggle", () => { if (det.open) det._fill(); });
  return det;
}

/* ---------------------------------------------------------------- sources */

function renderSources() {
  const d = state.data;
  const colorOf = (name) => {
    const i = d.tools.indexOf(name.replace(/ \(.*\)$/, ""));  // "Roo Code (VS Code)" -> "Roo Code"
    return slotColor(i >= 0 ? state.toolColor[i] : KNOWN_TOOL_SLOTS[name] || 0);
  };
  const row = (s) => `
    <div class="source"><span class="dot" style="background:${colorOf(s.tool)}"></span>
      <b>${esc(s.tool)}</b>
      <span class="badge ${s.found ? "on" : ""}">${s.found ? `${fmt(s.files)} file${s.files === 1 ? "" : "s"}` : "not found"}</span>
      <span class="how">${esc(s.how)}</span>
      <span class="path">${s.paths.map(esc).join("<br>")}${s.also_checked ? `<br>(and ${s.also_checked} more places)` : ""}</span></div>`;
  const found = d.sources.filter((s) => s.found), missing = d.sources.filter((s) => !s.found);
  $("sourceList").innerHTML = (found.length ? found.map(row).join("") : `<p class="muted">No AI tool data found yet.</p>`)
    + (missing.length ? `<details class="more-sources"><summary>${missing.length} more supported AI tools (not on this computer)</summary>${missing.map(row).join("")}</details>` : "");
  const p = d.pricing;
  $("pricingInfo").innerHTML = `<dl class="kv">
    <dt>Price list</dt><dd>${p.source === "online" ? "Downloaded" : "Bundled with the app"}${p.updated ? `, ${esc(p.updated)}` : ""}</dd>
    <dt>Models priced</dt><dd>${fmt(p.models)}</dd>
    <dt>Your prices</dt><dd>${p.overrides ? `${p.overrides} override${p.overrides === 1 ? "" : "s"}` : "none"} · <code>~/.ai-token-tracker/prices.json</code></dd>
    <dt>Version</dt><dd>${esc(d.version)}</dd></dl>`;
  const unpriced = [...group(state.calls.filter((c) => !c.priced), (c) => c.model).keys()].map((m) => d.models[m]);
  $("pricesMsg").textContent = (unpriced.length ? `No price yet for: ${unpriced.slice(0, 6).join(", ")}${unpriced.length > 6 ? "…" : ""}. ` : "")
    + "Updating downloads LiteLLM's public price list. Nothing about your usage is sent.";
}

/* ---------------------------------------------------------------- Claude plan */

function resetsIn(iso) {
  const t = Date.parse(iso || "");
  if (!t) return "";
  const mins = Math.max(0, Math.round((t - Date.now()) / 60000));
  const text = mins < 60 ? `${mins} min` : mins < 48 * 60 ? `${Math.floor(mins / 60)} h ${mins % 60} min` : `${Math.round(mins / 1440)} days`;
  return `Resets in ${text} (${new Date(t).toLocaleString(undefined, { weekday: "short", hour: "2-digit", minute: "2-digit" })})`;
}

function planRowsHtml(windows) {
  return windows.map((w) => {
    const level = w.percent >= 90 ? "full" : w.percent >= 70 ? "warn" : "";
    return `<div class="plan-row ${level}"><div class="top"><span>${esc(w.label)}</span><span class="pct">${Math.round(w.percent)}% used</span></div>
      <div class="meter" role="meter" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.round(w.percent)}" aria-label="${esc(w.label)}">
        <span style="width:${w.percent.toFixed(1)}%"></span></div>
      <div class="when">${esc(resetsIn(w.resets_at))}</div></div>`;
  }).join("");
}

function safeStatusLink(url) {
  return /^https:\/\/(status\.claude\.com|status\.openai\.com|status\.cursor\.com|www\.githubstatus\.com)(\/|$)/.test(url || "") ? url : "#";
}

async function loadPlan(force) {
  let d;
  try {
    const res = await fetch("/api/limits" + (force ? "?force=1" : ""), { headers: { "X-Token": TOKEN } });
    d = await res.json();
  } catch {
    return;
  }
  state.limits = d;
  const incidents = d.incidents || [];
  const bar = $("statusBar");
  bar.hidden = !incidents.length;
  bar.className = "status-bar " + (incidents.some((i) => i.level !== "minor") ? "major" : "minor");
  bar.innerHTML = incidents.map((i) => `<span><b>${esc(i.name)}:</b> ${esc(i.description)}.
    <a href="${safeStatusLink(i.url)}" target="_blank" rel="noopener noreferrer">Status page</a></span>`).join("")
    + (incidents.length ? `<span class="muted small">Missing or late numbers may be caused by this, not by the tracker.</span>` : "");

  const card = $("planCard");
  const providers = d.providers || [];
  card.hidden = !providers.length;
  $("planName").textContent = providers.length ? `${providers.length} AI subscription${providers.length === 1 ? "" : "s"} logged in` : "";
  $("planRows").innerHTML = providers.map((p) => `
    <div class="plan-provider">
      <div class="plan-head"><b>${esc(p.name)}</b>${p.plan ? `<span class="muted small">${esc(p.plan)} plan</span>` : ""}</div>
      ${p.available ? planRowsHtml(p.windows) : `<p class="muted small">${esc(p.message || "Limits aren't available.")}</p>`}
    </div>`).join("");
  if (typeof renderWidget === "function") renderWidget();
}

/* ---------------------------------------------------------------- updates */

const updates = { status: null, installing: false, autoTried: false };

function safeLink(url) {
  return /^https:\/\/github\.com\//.test(url || "") ? url : "https://github.com/";
}

async function checkUpdates(force) {
  try {
    const res = await fetch("/api/update" + (force ? "?force=1" : ""), { headers: { "X-Token": TOKEN } });
    updates.status = await res.json();
  } catch {
    return;  // offline: try again later
  }
  renderUpdate();
  const u = updates.status;
  if (force && !u.available && !u.error) toast(`You have the latest version (${u.current}).`);
  if (force && u.error) toast(u.error);
  // Updates install automatically, once per session, as soon as a newer version is seen.
  if (u.available && u.can_install && !updates.installing && !updates.autoTried) {
    updates.autoTried = true;
    installUpdate();
  }
}

function renderUpdate() {
  const u = updates.status;
  if (!u) return;
  const bar = $("updateBar");
  if (updates.installing) {
    bar.hidden = false;
    bar.innerHTML = `<span class="grow">${esc(updates.installing)}</span>`;
  } else if (u.available) {
    const page = safeLink(u.latest.page);
    bar.hidden = false;
    bar.innerHTML = `<span class="grow"><b>AI Token Tracker ${esc(u.latest.version)}</b> is available. You have ${esc(u.current)}.</span>
      <a href="${esc(page)}" target="_blank" rel="noopener">What's new</a>
      ${u.can_install ? `<button class="btn" id="installUpdate">Update now</button>`
                      : `<a class="btn" href="${esc(page)}" target="_blank" rel="noopener">Download</a>`}`;
    const btn = $("installUpdate");
    if (btn) btn.onclick = () => installUpdate();
  } else {
    bar.hidden = true;
  }
  const how = { "windows-installer": "installs itself (Windows installer)", "mac-app": "installs itself (Mac app)",
    pip: "installs itself with pip", manual: "download new versions by hand (portable, Linux or source copy)" }[u.method] || "";
  $("updateInfo").innerHTML = `<dl class="kv">
    <dt>This version</dt><dd>${esc(u.current)}</dd>
    <dt>Latest</dt><dd>${u.latest ? esc(u.latest.version) : u.error ? "couldn't check (offline?)" : "not checked yet"}</dd>
    <dt>Updates</dt><dd>${esc(how)}</dd></dl>`;
}

async function installUpdate() {
  const u = updates.status;
  updates.installing = `Updating to ${u.latest.version}… The app will restart by itself.`;
  renderUpdate();
  try {
    const res = await fetch("/api/update/install", { method: "POST", headers: { "X-Token": TOKEN } });
    const body = await res.json();
    if (!res.ok) throw new Error(body.error || res.statusText);
    updates.installing = body.message;
  } catch (err) {
    updates.installing = false;
    toast("Update didn't install: " + err.message);
  }
  renderUpdate();
}

/* ---------------------------------------------------------------- wiring */

function applyTheme() {
  if (state.theme === "auto") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = state.theme;
  $("theme").title = `Theme: ${state.theme === "auto" ? "automatic" : state.theme}`;
}

function updateStamp() {
  $("updated").textContent = state.loadedAt ? `Updated ${timeLabel(state.loadedAt)}` : "";
}

function wireSegment(id, key, after) {
  document.querySelectorAll(`#${id} button`).forEach((b) => {
    b.setAttribute("aria-pressed", String(b.dataset.v === state[key]));
    b.onclick = () => {
      state[key] = b.dataset.v;
      store.set(key, state[key]);
      document.querySelectorAll(`#${id} button`).forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      if (after) after();
      render();
    };
  });
}

function init() {
  applyTheme();
  document.querySelectorAll(".tabs button").forEach((b) => {
    b.onclick = () => { state.page = b.dataset.page; store.set("page", state.page); render(); };
  });
  document.querySelectorAll("#period button").forEach((b) => {
    b.onclick = () => { state.period = b.dataset.period; store.set("period", state.period); state.shown = 50; render(); };
  });
  wireSegment("trendGroup", "trendGroup");
  wireSegment("trendMetric", "trendMetric");
  wireSegment("breakdownTab", "breakdown", () => { state.sort = { key: null, dir: -1 }; });
  $("refresh").onclick = () => { load(true); loadPlan(true); };
  $("theme").onclick = () => {
    state.theme = { auto: "light", light: "dark", dark: "auto" }[state.theme];
    store.set("theme", state.theme);
    applyTheme();
  };
  let searchTimer;
  $("sessionSearch").oninput = (e) => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => { state.search = e.target.value; state.shown = 50; renderSessions(); }, 150);
  };
  $("sessionSort").onchange = (e) => { state.sessionSort = e.target.value; renderSessions(); };
  $("moreSessions").onclick = () => { state.shown += 50; renderSessions(); };
  $("updatePrices").onclick = async () => {
    const btn = $("updatePrices");
    btn.disabled = true; btn.textContent = "Updating…";
    try {
      const res = await fetch("/api/update-prices", { method: "POST", headers: { "X-Token": TOKEN } });
      const body = await res.json();
      if (!res.ok) throw new Error(body.error || res.statusText);
      toast(`Prices updated: ${fmt(body.models)} models.`);
      await load(true);
    } catch (err) {
      toast(err.message);
    } finally {
      btn.disabled = false; btn.textContent = "Update prices online";
    }
  };
  document.addEventListener("scroll", hideTip, { passive: true });
  let resizeTimer, lastWidth = innerWidth;
  addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => { if (innerWidth !== lastWidth) { lastWidth = innerWidth; if (state.page === "overview") renderOverview(); } }, 150);
  });
  // The server re-reads logs only if a log file changed, so this is cheap; the ↻ button forces a full re-read.
  setInterval(() => { if (!document.hidden) load(false); }, 60000);
  setInterval(updateStamp, 30000);
  // Tells the local server the dashboard is still open (it quits when every tab is closed).
  setInterval(() => fetch("/api/ping", { headers: { "X-Token": TOKEN } }).catch(() => {}), 45000);
  $("checkNow").onclick = () => checkUpdates(true);
  load(false).then(() => { checkUpdates(false); loadPlan(false); });
  setInterval(() => { if (!document.hidden) loadPlan(false); }, 2 * 60 * 1000);
  setInterval(() => checkUpdates(false), 3 * 3600 * 1000);  // the server itself asks GitHub at most every 6 hours
}

init();

/* AI Token Tracker widget: today's tokens, the latest session and plan limits, in a small window. */
"use strict";

const $ = (id) => document.getElementById(id);
const TOKEN = new URLSearchParams(location.search).get("t") || "";
const SLOTS = { "Codex CLI": 1, "Claude Code": 2, "Gemini CLI": 3, "Qwen Code": 4, "OpenCode": 5, "Cline": 6, "Roo Code": 7, "Kilo Code": 8 };
const store = {
  get(key, fallback) { try { const v = localStorage.getItem("att." + key); return v === null ? fallback : JSON.parse(v); } catch { return fallback; } },
};

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
const nf = new Intl.NumberFormat();
function compact(n) {
  const a = Math.abs(n || 0);
  if (a >= 1e9) return (n / 1e9).toFixed(1) + "B";
  if (a >= 1e6) return (n / 1e6).toFixed(1) + "M";
  if (a >= 1e3) return (n / 1e3).toFixed(1) + "k";
  return String(Math.round(n || 0));
}
function money(v) {  // same currency choice as the dashboard
  const cur = store.get("currency", "USD"), rate = cur === "USD" ? 1 : store.get("rates", null)?.rates?.[cur];
  v = (v || 0) * (rate || 1);
  const code = rate ? cur : "USD";
  if (v === 0) return new Intl.NumberFormat(undefined, { style: "currency", currency: code, maximumFractionDigits: 0 }).format(0);
  if (v < 0.01) return "<" + new Intl.NumberFormat(undefined, { style: "currency", currency: code }).format(0.01);
  const digits = v >= 1000 || (v >= 100 && rate > 20) ? 0 : 2;
  return new Intl.NumberFormat(undefined, { style: "currency", currency: code, minimumFractionDigits: digits, maximumFractionDigits: digits }).format(v);
}
function ago(ms) {
  const m = Math.round((Date.now() - ms) / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m} min ago`;
  if (m < 48 * 60) return `${Math.round(m / 60)} h ago`;
  return `${Math.round(m / 1440)} days ago`;
}
function resets(iso) {
  const t = Date.parse(iso || "");
  if (!t) return "";
  const m = Math.max(0, Math.round((t - Date.now()) / 60000));
  return m < 60 ? `${m}m` : m < 24 * 60 ? `${Math.floor(m / 60)}h ${m % 60}m` : `${Math.floor(m / 1440)}d ${Math.floor((m % 1440) / 60)}h`;
}
const api = (path, opts = {}) => fetch(path, { ...opts, headers: { "X-Token": TOKEN, ...(opts.headers || {}) } });

async function refresh() {
  let d;
  try { d = await (await api("/api/summary")).json(); } catch { $("wUpdated").textContent = "Offline"; return; }
  const free = [1, 2, 3, 4, 5, 6, 7, 8].filter((s) => !d.today.tools.some((t) => SLOTS[t.tool] === s));
  const color = (tool) => { const s = SLOTS[tool] || free.shift(); return s ? `var(--s${s})` : "var(--other)"; };
  const colors = Object.fromEntries(d.today.tools.map((t) => [t.tool, color(t.tool)]));
  $("wTokens").textContent = nf.format(d.today.tokens);
  $("wCost").textContent = `${money(d.today.cost)} estimated`;
  $("wBar").innerHTML = d.today.tools.map((t) => `<span style="width:${(t.tokens / (d.today.tokens || 1) * 100).toFixed(2)}%;background:${colors[t.tool]}"></span>`).join("");
  $("wTools").innerHTML = d.today.tools.length ? d.today.tools.slice(0, 4).map((t) =>
    `<li><span class="dot" style="background:${colors[t.tool]}"></span><span class="name">${esc(t.tool)}</span><span>${compact(t.tokens)}</span></li>`).join("")
    : `<li class="muted small">No AI use yet today.</li>`;
  const l = d.latest;
  $("wLatest").hidden = !l;
  if (l) {
    $("wLatestTitle").textContent = l.title;
    $("wLatestTitle").title = l.title;
    $("wLatestSub").textContent = `${l.tool} · ${compact(l.tokens)} tokens · ${money(l.cost)} · ${ago(l.end)}`;
  }
  $("wUpdated").textContent = "Updated " + new Date().toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

async function refreshLimits() {
  let d;
  try { d = await (await api("/api/limits")).json(); } catch { return; }
  const rows = [];
  for (const p of d.providers || []) {
    for (const w of (p.windows || []).slice(0, 2)) rows.push({ name: p.name, ...w });
  }
  $("wLimits").hidden = !rows.length;
  const multi = (d.providers || []).filter((p) => (p.windows || []).length).length > 1;
  $("wLimitRows").innerHTML = rows.slice(0, 6).map((r) => {
    const level = r.percent >= 90 ? "full" : r.percent >= 70 ? "warn" : "";
    const until = resets(r.resets_at);
    return `<div class="usage-row ${level}"><div class="top"><span class="lbl">${multi ? esc(r.name) + " · " : ""}${esc(r.label)}:</span>
      <span class="pct">${Math.round(r.percent)}%</span>${until ? `<span class="reset">⏱ ${esc(until)}</span>` : ""}</div>
      <div class="meter"><span style="width:${r.percent.toFixed(1)}%"></span></div></div>`;
  }).join("") + (d.incidents || []).map((i) => `<p class="w-incident">${esc(i.name)}: ${esc(i.description)}</p>`).join("");
}

function applyTheme() {
  const t = store.get("theme", "auto");
  if (t === "auto") document.documentElement.removeAttribute("data-theme"); else document.documentElement.dataset.theme = t;
}

$("wOpen").onclick = async () => {
  const res = await api("/api/show", { method: "POST" }).then((r) => r.json()).catch(() => ({}));
  if (!res.native) window.open("/?t=" + encodeURIComponent(TOKEN), "_blank");  // browser mode
};
applyTheme();
refresh();
refreshLimits();
setInterval(refresh, 30000);
setInterval(refreshLimits, 2 * 60000);
setInterval(() => api("/api/ping").catch(() => {}), 45000);

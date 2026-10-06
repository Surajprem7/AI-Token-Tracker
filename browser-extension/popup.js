/* Popup: Claude plan usage, today's total and each chat's estimated tokens. */
"use strict";

const $ = (id) => document.getElementById(id);
const ext = globalThis.chrome;
const nf = new Intl.NumberFormat();
const SITE = { claude: "Claude.ai", chatgpt: "ChatGPT", gemini: "Gemini" };

function compact(n) {
  if (n >= 1e6) return (n / 1e6).toFixed(1) + "M";
  if (n >= 1e3) return (n / 1e3).toFixed(1) + "k";
  return String(n);
}
function until(iso) {
  const t = Date.parse(iso || "");
  if (!t) return "";
  const m = Math.max(0, Math.round((t - Date.now()) / 60000));
  return m < 60 ? `${m}m` : m < 1440 ? `${Math.floor(m / 60)}h ${m % 60}m` : `${Math.floor(m / 1440)}d ${Math.floor((m % 1440) / 60)}h`;
}
function ago(ms) {
  const m = Math.round((Date.now() - ms) / 60000);
  return m < 1 ? "just now" : m < 60 ? `${m} min ago` : m < 1440 ? `${Math.round(m / 60)} h ago` : `${Math.round(m / 1440)} d ago`;
}
function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; }
const ask = (msg) => new Promise((resolve) => ext.runtime.sendMessage(msg, (r) => resolve(r || {})));

async function render() {
  const { chats = {}, limits, connection } = await ext.storage.local.get(["chats", "limits", "connection"]);

  // Usage: Session (5h), Weekly...
  const rows = $("usageRows");
  rows.textContent = "";
  $("usageBox").hidden = !(limits && limits.windows && limits.windows.length);
  if (limits && limits.windows) {
    $("usageAge").textContent = limits.at ? `· Claude · ${ago(limits.at)}` : "· Claude";
    for (const w of limits.windows) {
      const row = el("div", "usage-row" + (w.percent >= 90 ? " full" : w.percent >= 70 ? " warn" : ""));
      const top = el("div", "top");
      top.append(el("span", "lbl", w.label + ":"), el("span", "pct", Math.round(w.percent) + "%"));
      if (w.resets_at) top.append(el("span", "reset", "⏱ " + until(w.resets_at)));
      const meter = el("div", "meter"), fill = el("span");
      fill.style.width = Math.max(0, Math.min(100, w.percent)) + "%";
      meter.append(fill);
      row.append(top, meter);
      rows.append(row);
    }
  }

  renderMessages(chats, await ext.storage.local.get(["caps", "windowHours"]));

  // Each chat's tokens, newest first; and today's total.
  const list = Object.values(chats).sort((a, b) => (b.seen || 0) - (a.seen || 0));
  const midnight = new Date(); midnight.setHours(0, 0, 0, 0);
  let today = 0;
  const ul = $("chats");
  ul.textContent = "";
  for (const c of list) {
    let total = 0;
    for (const t of c.turns || []) {
      total += (t.input || 0) + (t.output || 0);
      if ((t.t || c.seen) >= midnight.getTime()) today += (t.input || 0) + (t.output || 0);
    }
    if (ul.childElementCount >= 30) continue;
    const li = el("li");
    li.title = c.title || "";
    li.append(el("span", "t", c.title || "(untitled chat)"), el("span", "n", compact(total)),
      el("span", "s", `${SITE[c.site] || c.site} · ${(c.turns || []).length} replies · ${ago(c.seen)}`));
    ul.append(li);
  }
  if (!list.length) ul.append(el("li", "muted", "Open a chat on claude.ai, chatgpt.com or gemini.google.com."));
  $("todayTokens").textContent = nf.format(today);

  // Connection
  $("connectBox").hidden = !!connection;
  const st = $("status");
  if (!connection) { st.className = "status off"; st.textContent = "not connected"; return; }
  const ping = await ask({ type: "status" });
  st.className = "status " + (ping.ok ? "on" : "off");
  st.textContent = ping.ok ? "connected" : "tracker closed";
  st.title = ping.ok ? `AI Token Tracker ${ping.version}` : ping.error || "";
}

/** Messages sent per model in the last few hours (ChatGPT and Gemini don't show their limits),
    with your own limit and when the next message frees up. */
function messageCounts(chats, hours) {
  const since = Date.now() - hours * 3600000;
  const per = new Map();
  for (const c of Object.values(chats)) {
    if (c.site === "claude") continue;  // Claude shows real usage above
    for (const t of c.turns || []) {
      if (!t.t || t.t < since || t.sent === false) continue;
      const model = `${SITE[c.site]} · ${t.model || "default model"}`;
      const m = per.get(model) || { count: 0, oldest: Infinity };
      m.count++; m.oldest = Math.min(m.oldest, t.t);
      per.set(model, m);
    }
  }
  return per;
}

function renderMessages(chats, settings) {
  const hours = Number(settings.windowHours) || 3;
  const caps = settings.caps || {};
  const per = messageCounts(chats, hours);
  $("winH").textContent = hours;
  $("msgBox").hidden = !per.size && !Object.keys(caps).length;
  const rows = $("msgRows");
  rows.textContent = "";
  for (const [model, m] of [...per.entries()].sort((a, b) => b[1].count - a[1].count)) {
    const cap = Number(caps[model]) || 0;
    const pct = cap ? Math.min(100, (m.count / cap) * 100) : 0;
    const row = el("div", "usage-row" + (pct >= 90 ? " full" : pct >= 70 ? " warn" : ""));
    const top = el("div", "top");
    top.append(el("span", "lbl", model), el("span", "pct", cap ? `${m.count} / ${cap}` : `${m.count} sent`));
    top.append(el("span", "reset", "⏱ " + until(new Date(m.oldest + hours * 3600000).toISOString())));
    top.lastChild.title = "When your oldest message in this window stops counting";
    row.append(top);
    if (cap) { const meter = el("div", "meter"), fill = el("span"); fill.style.width = pct + "%"; meter.append(fill); row.append(meter); }
    rows.append(row);
  }
  // Limit settings: one box per model seen in the last week.
  const box = $("capInputs");
  box.textContent = "";
  const models = new Set([...messageCounts(chats, 24 * 7).keys(), ...Object.keys(caps)]);
  for (const model of models) {
    const label = el("label", "", model + " ");
    const input = el("input");
    input.type = "number"; input.min = "0"; input.dataset.model = model; input.value = caps[model] || "";
    label.append(input);
    box.append(label);
  }
  $("winInput").value = hours;
}

$("capsBtn").onclick = () => { $("capsBox").hidden = !$("capsBox").hidden; };
$("capsSave").onclick = async () => {
  const caps = {};
  document.querySelectorAll("#capInputs input").forEach((i) => { if (Number(i.value) > 0) caps[i.dataset.model] = Number(i.value); });
  const hours = Math.max(1, Math.min(168, Number($("winInput").value) || 3));
  await ext.storage.local.set({ caps, windowHours: hours });
  $("capsBox").hidden = true;
  render();
};

$("connect").onclick = async () => {
  $("connectMsg").textContent = "Connecting…";
  const r = await ask({ type: "connect", code: $("code").value });
  $("connectMsg").textContent = r.ok ? `Connected to AI Token Tracker ${r.version || ""}.` : r.error;
  render();
};
$("change").onclick = () => { $("connectBox").hidden = !$("connectBox").hidden; };
$("open").onclick = async () => {
  const r = await ask({ type: "open" });
  if (!r.ok) { $("connectBox").hidden = false; $("connectMsg").textContent = r.error || "Open the AI Token Tracker app first."; }
};
render();

/* A small usage box shown on the AI website itself (bottom-right corner):
   your plan usage (Session 5h / Weekly) and the tokens of the chat that's open.
   It lives in its own shadow DOM so the site's styles can't change it, and it can be
   folded to a small pill or hidden (turn it back on in the extension's popup). */
"use strict";
(function () {
  if (globalThis.__aittPanel) return;
  globalThis.__aittPanel = true;
  const AITT = globalThis.AITT || (globalThis.AITT = {});
  const state = { chat: null, limits: null, folded: false, enabled: true, today: 0 };

  const host = document.createElement("div");
  host.id = "ai-token-tracker-panel";
  const root = host.attachShadow({ mode: "closed" });
  root.innerHTML = `<style>
    :host { all: initial; }
    .box { position: fixed; right: 16px; bottom: 16px; z-index: 2147483000; width: 250px;
      font: 12.5px/1.4 system-ui, -apple-system, "Segoe UI", sans-serif; color: #1d1d1b;
      background: #fafaf8; border: 1px solid #e3e1dc; border-radius: 12px; box-shadow: 0 8px 28px rgba(0,0,0,.18); }
    @media (prefers-color-scheme: dark) { .box { color: #f2f2f0; background: #1f1f22; border-color: #34343a; } .bar { background: #34343a !important; } .muted { color: #a3a19a !important; } }
    .head { display: flex; align-items: center; gap: 6px; padding: 7px 10px; cursor: pointer; user-select: none; }
    .head b { font-size: 12px; } .head .sum { margin-left: auto; color: #2a78d6; font-weight: 600; }
    .head button { all: unset; cursor: pointer; padding: 0 3px; color: #8a887f; font-size: 14px; line-height: 1; }
    .body { padding: 0 10px 9px; display: grid; gap: 7px; }
    .row .top { display: flex; gap: 6px; align-items: baseline; }
    .row .lbl { min-width: 86px; } .row .pct { color: #2a78d6; font-weight: 600; } .row .reset { margin-left: auto; color: #8a887f; font-size: 11.5px; }
    .bar { height: 5px; border-radius: 3px; background: #ecebe7; overflow: hidden; margin-top: 3px; }
    .bar span { display: block; height: 100%; background: #2a78d6; }
    .warn .pct { color: #c98500; } .warn .bar span { background: #fab219; }
    .full .pct { color: #d03b3b; } .full .bar span { background: #d03b3b; }
    .chat { border-top: 1px solid rgba(128,128,128,.25); padding-top: 7px; }
    .chat .n { font-size: 16px; font-weight: 700; } .muted { color: #74726d; }
    .folded .body { display: none; } .folded { width: auto; }
    .box.inline { position: static; width: auto; margin: 6px 8px 10px; box-shadow: none; background: transparent;
      border: 1px solid rgba(128,128,128,.28); border-radius: 10px; color: inherit; }
    .box.inline .muted { color: inherit; opacity: .65; }
    .today { display: flex; justify-content: space-between; }
  </style><div class="box"><div class="head" title="AI Token Tracker (click to fold)"><b>AI Tokens</b><span class="sum"></span>
    <button class="hide" title="Hide (turn it back on in the extension)">×</button></div><div class="body"></div></div>`;
  const box = root.querySelector(".box"), body = root.querySelector(".body"), sum = root.querySelector(".sum");

  const nf = new Intl.NumberFormat();
  const compact = (n) => (n >= 1e6 ? (n / 1e6).toFixed(1) + "M" : n >= 1e3 ? (n / 1e3).toFixed(1) + "k" : String(n));
  function until(iso) {
    const t = Date.parse(iso || "");
    if (!t) return "";
    const m = Math.max(0, Math.round((t - Date.now()) / 60000));
    return m < 60 ? `${m}m` : m < 1440 ? `${Math.floor(m / 60)}h ${m % 60}m` : `${Math.floor(m / 1440)}d ${Math.floor((m % 1440) / 60)}h`;
  }
  function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; }

  /** claude.ai: put the box in the left sidebar (just above the account button at its bottom).
      Elsewhere, or if the sidebar can't be found (closed, or the site changed), a corner box. */
  function sidebar() {
    if (!location.hostname.endsWith("claude.ai")) return null;
    const nav = document.querySelector("nav");
    if (!nav || nav.getBoundingClientRect().width < 150) return null;
    return nav;
  }

  function place() {
    const nav = sidebar();
    box.classList.toggle("inline", !!nav);
    if (nav) {
      const anchor = nav.lastElementChild;  // the account / profile area at the bottom
      if (host.parentElement !== nav || host.nextElementSibling !== anchor) nav.insertBefore(host, anchor === host ? null : anchor);
    } else if (host.parentElement !== document.body) {
      (document.body || document.documentElement).append(host);
    }
  }

  function draw() {
    if (!state.enabled) { host.remove(); return; }
    place();
    box.classList.toggle("folded", state.folded);
    body.textContent = "";
    const windows = (state.limits && state.limits.windows) || [];
    for (const w of windows.slice(0, 4)) {
      const row = el("div", "row" + (w.percent >= 90 ? " full" : w.percent >= 70 ? " warn" : ""));
      const top = el("div", "top");
      top.append(el("span", "lbl", w.label + ":"), el("span", "pct", Math.round(w.percent) + "%"));
      if (w.resets_at) top.append(el("span", "reset", "⏱ " + until(w.resets_at)));
      const bar = el("div", "bar"), fill = el("span");
      fill.style.width = Math.max(0, Math.min(100, w.percent)) + "%";
      bar.append(fill);
      row.append(top, bar);
      body.append(row);
    }
    if (state.today) {
      const t = el("div", "today");
      t.append(el("span", "muted", "Today on AI websites"), el("b", "", compact(state.today) + " tokens"));
      body.append(t);
    }
    let total = 0;
    if (state.chat) {
      for (const t of state.chat.turns || []) total += (t.input || 0) + (t.output || 0);
      const chat = el("div", "chat");
      chat.append(el("div", "muted", "This chat (estimated)"), el("span", "n", nf.format(total)),
        el("span", "muted", ` tokens · ${(state.chat.turns || []).length} replies`));
      body.append(chat);
    } else {
      body.append(el("div", "muted chat", "Open a chat to see its tokens."));
    }
    const s = windows[0];
    sum.textContent = state.folded ? [s ? Math.round(s.percent) + "%" : "", state.chat ? compact(total) : ""].filter(Boolean).join(" · ") : "";
  }

  root.querySelector(".head").addEventListener("click", (e) => {
    if (e.target.closest(".hide")) return;
    state.folded = !state.folded;
    chrome.storage.local.set({ panelFolded: state.folded });
    draw();
  });
  root.querySelector(".hide").addEventListener("click", () => {
    state.enabled = false;
    chrome.storage.local.set({ panel: false });
    draw();
  });
  chrome.storage.onChanged.addListener((changes) => {
    if (changes.panel) { state.enabled = changes.panel.newValue !== false; draw(); }
  });

  // Plan usage: what the site reports (claude.ai), else what the desktop app knows.
  async function refreshUsage() {
    if (state.limits && state.limits.fromSite && Date.now() - state.limits.at < 5 * 60000) return;
    chrome.runtime.sendMessage({ type: "usage" }, (r) => {
      const lines = [];
      for (const p of (r && r.providers) || []) {
        for (const w of p.windows || []) lines.push({ ...w, label: (r.providers.length > 1 ? p.name + " " : "") + w.label });
      }
      if (lines.length && !(state.limits && state.limits.fromSite)) { state.limits = { windows: lines, at: Date.now() }; draw(); }
    });
  }

  function countToday() {
    chrome.storage.local.get("chats", ({ chats = {} }) => {
      const midnight = new Date(); midnight.setHours(0, 0, 0, 0);
      let n = 0;
      for (const c of Object.values(chats)) for (const t of c.turns || []) if ((t.t || c.seen) >= midnight.getTime()) n += (t.input || 0) + (t.output || 0);
      if (n !== state.today) { state.today = n; draw(); }
    });
  }
  chrome.storage.onChanged.addListener((changes) => { if (changes.chats) countToday(); });
  // The site redraws its sidebar now and then; put the box back if it was removed.
  setInterval(() => { if (state.enabled && !document.hidden && (!host.isConnected || (sidebar() && host.parentElement !== sidebar()))) draw(); }, 2000);

  AITT.panel = {
    chat(doc) { state.chat = doc; draw(); },
    clearChat() { state.chat = null; draw(); },
    limits(l) { state.limits = { ...l, fromSite: true, at: Date.now() }; draw(); },
  };

  chrome.storage.local.get(["panel", "panelFolded"], (v) => {
    state.enabled = v.panel !== false;
    state.folded = !!v.panelFolded;
    draw();
    refreshUsage();
    countToday();
  });
  setInterval(() => { if (!document.hidden) { refreshUsage(); draw(); } }, 60000);
})();

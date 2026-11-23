/* AI Token Tracker for Claude: the part that runs on claude.ai.

   It adds two things to the page:
     • a "Usage" section in the left sidebar: one bar per plan limit (Session 5h, Weekly, ...)
       with the percentage used and when it resets;
     • a thin line under the message box: the session bar, the open chat's length against the
       context window, whether the chat is still cached, roughly what the next message will
       use, and how many messages you have left.

   Limits come from claude.ai's own usage data (the same numbers as Settings → Usage).
   Chat length is counted from the chat's text with a tokenizer, or exactly with Anthropic's
   free token counter if you add an API key. "Messages left" is learned from your own use:
   after each message it notes how far the bars moved for how many tokens. */
"use strict";

(function () {
  if (window.__cutStarted) return;
  window.__cutStarted = true;

  const S = {
    orgId: null,
    usage: null,          // { limits: [...], fetchedAt, orgId }
    usageError: false,
    chat: null,           // { id, tokens, lastAt, model, title, estimated, exact }
    loadingChat: null,
    reloadChat: false,
    pending: null,        // snapshot taken when a message is sent
    learn: {},            // family -> { limitKey -> { pct, tok } }, plus growth
    settings: CUT.settings(),
    tokenCache: new Map(),
    exactCache: new Map(),
    wasCached: null,
  };
  const ROOT_UUID = "00000000-0000-4000-8000-000000000000";
  const IMAGE_TOKENS = 1600;     // rough size of one image in context
  const DOC_TOKENS = 1500;       // rough size of an uploaded file whose text we can't see
  const SUMMARY_TOKENS = 5000;   // rough size of a compaction summary whose text we can't see
  const t = CUT.t;

  /* ------------------------------------------------------------ data */

  async function api(path) {
    const res = await fetch("/api" + path, { credentials: "include", headers: { Accept: "application/json" } });
    if (!res.ok) throw new Error(res.status + " " + path);
    return res.json();
  }

  async function org() {
    const fromCookie = (document.cookie.match(/(?:^|;\s*)lastActiveOrg=([0-9a-f-]{36})/) || [])[1];
    if (fromCookie) return (S.orgId = fromCookie);
    if (S.orgId) return S.orgId;
    const orgs = await api("/organizations");
    const pick = (orgs || []).find((o) => (o.capabilities || []).includes("chat")) || (orgs || [])[0];
    return (S.orgId = pick ? pick.uuid : null);
  }

  /** merge=true: only replace the limits given (the reply stream reports just two of them). */
  function setLimits(limits, merge) {
    if (merge) {
      if (!limits.length) return;
      const list = S.usage ? S.usage.limits.map((l) => limits.find((n) => n.key === l.key) || l) : limits.slice();
      for (const n of limits) if (!list.some((l) => l.key === n.key)) list.push(n);
      limits = list;
    }
    S.usage = { limits, fetchedAt: Date.now(), orgId: S.orgId };
    S.usageError = false;
    const usage = S.usage;
    chrome.storage.local.get("usageByOrg").then(({ usageByOrg }) => {
      chrome.storage.local.set({ usage, orgId: usage.orgId, usageByOrg: { ...(usageByOrg || {}), [usage.orgId]: usage } });
    });
    render();
  }

  async function refreshUsage() {
    try {
      const id = await org();
      if (!id) throw new Error("no org");
      const limits = CUT.parseUsage(await api(`/organizations/${id}/usage`));
      // The free plan answers with no limits at all; keep what the reply stream told us then.
      if (!limits.length && S.usage && S.usage.limits.length && S.usage.orgId === id) return;
      setLimits(limits, false);
    } catch (e) {
      S.usageError = true;
      render();
    }
  }

  /** The model chosen in claude.ai's model picker, e.g. "Opus 5.5" (null if not found). */
  function pickerModel() {
    const el = document.querySelector('[data-testid="model-selector-dropdown"]');
    const text = el && el.textContent.trim();
    return text && CUT.modelKey(text) ? text : null;
  }
  const currentModel = () => (S.chat && S.chat.model) || pickerModel();

  const chatIdFromUrl = () => (location.pathname.match(/\/chat\/([0-9a-f-]{36})/) || [])[1] || null;

  function blockText(c) {
    if (!c) return "";
    if (typeof c === "string") return c;
    const parts = [];
    if (typeof c.text === "string") parts.push(c.text);
    if (typeof c.thinking === "string") parts.push(c.thinking);
    if (c.input) parts.push(typeof c.input === "string" ? c.input : JSON.stringify(c.input));
    if (c.content) {
      if (Array.isArray(c.content)) for (const x of c.content) parts.push(blockText(x));
      else parts.push(typeof c.content === "string" ? c.content : JSON.stringify(c.content));
    }
    return parts.join("\n");
  }

  function messageText(m) {
    let text = Array.isArray(m.content) ? m.content.map(blockText).join("\n") : (m.text || "");
    for (const a of m.attachments || []) if (a && a.extracted_content) text += "\n" + a.extracted_content;
    return text;
  }

  function messageTokens(m) {
    const key = m.uuid + "|" + (m.updated_at || m.created_at || "");
    if (S.tokenCache.has(key)) return S.tokenCache.get(key);
    const files = m.files_v2 || m.files || [];
    let extra = 0;
    for (const f of files) extra += f && f.file_kind === "image" ? IMAGE_TOKENS : DOC_TOKENS;
    const r = { text: messageText(m), extra, estimated: files.length > 0 };
    r.tokens = CUT_countTokens(r.text) + extra;
    S.tokenCache.set(key, r);
    if (S.tokenCache.size > 5000) S.tokenCache.clear();
    return r;
  }

  /** Exact count from Anthropic's token counter (through the background, which holds the key). */
  async function exactCount(chatId, leafKey, text, model) {
    const key = chatId + "|" + leafKey;
    if (S.exactCache.has(key)) return S.exactCache.get(key);
    try {
      const r = await chrome.runtime.sendMessage({ type: "count", text, model });
      if (r && r.ok) { S.exactCache.set(key, r.tokens); return r.tokens; }
    } catch (e) { /* fall back to the estimate */ }
    return null;
  }

  async function refreshChat() {
    const id = chatIdFromUrl();
    // Not a chat (new chat, settings, claude.ai/code): nothing to count. The popup keeps
    // showing the last chat you had open.
    if (!id) { S.chat = null; render(); return; }
    if (S.loadingChat === id) { S.reloadChat = true; return; }
    S.loadingChat = id;
    try {
      const o = await org();
      const conv = await api(`/organizations/${o}/chat_conversations/${id}?tree=True&rendering_mode=messages&render_all_tools=true`);
      const msgs = conv.chat_messages || [];
      const byId = new Map(msgs.map((m) => [m.uuid, m]));
      let leaf = byId.get(conv.current_leaf_message_uuid) || msgs[msgs.length - 1];
      const leafKey = leaf ? leaf.uuid + (leaf.updated_at || "") : "";
      const lastAt = leaf ? Date.parse(leaf.updated_at || leaf.created_at) || null : null;
      // The branch on screen, oldest first.
      const branch = [];
      for (let guard = 0; leaf && guard < 10000; guard++) {
        branch.push(leaf);
        if (!leaf.parent_message_uuid || leaf.parent_message_uuid === ROOT_UUID) break;
        leaf = byId.get(leaf.parent_message_uuid);
      }
      branch.reverse();
      // Compaction: when a chat gets too long, claude.ai replaces its start with a summary. It shows
      // as a message with a compaction_summary, or as an empty reply right after a reply. Only what
      // comes after the latest one is in the context, plus the summary itself.
      let from = 0, compactions = 0, summaryTokens = 0;
      branch.forEach((m, i) => {
        const summary = m.compaction_summary;
        const hasSummary = summary && (typeof summary === "string" ? summary.trim() : summary.length);
        const divider = m.sender === "assistant" && !(m.content || []).length && i > 0 && branch[i - 1].sender === "assistant";
        if (!hasSummary && !divider) return;
        compactions++;
        from = i + 1;
        summaryTokens = hasSummary ? CUT_countTokens(typeof summary === "string" ? summary : blockText({ content: summary })) : SUMMARY_TOKENS;
      });
      let tokens = summaryTokens, extra = 0, estimated = !!conv.project_uuid || (compactions > 0 && !summaryTokens);
      const texts = [];
      for (const m of branch.slice(from)) {
        const r = messageTokens(m);
        tokens += r.tokens;
        extra += r.extra;
        texts.push(r.text);
        estimated = estimated || r.estimated;
      }
      const model = conv.model || pickerModel() || (S.chat && S.chat.id === id ? S.chat.model : null);
      let exact = false;
      if (S.settings.apiKey && texts.length) {
        const n = await exactCount(id, leafKey, texts.join("\n\n"), model);
        if (n != null) { tokens = n + extra + summaryTokens; exact = true; }
      }
      if (chatIdFromUrl() === id) {
        S.chat = { id, tokens, lastAt, model, title: conv.name || "", estimated: estimated || !exact, exact,
          compactions, workspace: !!conv.workspace_upgraded };
        saveChat();
        render();
      }
    } catch (e) { /* a page we can't read (shared link, deleted chat): leave it */ }
    finally {
      S.loadingChat = null;
      if (S.reloadChat) { S.reloadChat = false; await refreshChat(); }
    }
  }

  /* ------------------------------------------------------------ estimates */

  const isCached = () => !!(S.chat && S.chat.lastAt && Date.now() - S.chat.lastAt < CUT.CACHE_MS);
  const growth = () => (S.learn.growth > 0 ? S.learn.growth : 1000);

  /** Rough limit-weighted tokens of the next message in the open chat (cache reads count ~10%). */
  function nextCost() {
    const L = S.chat ? S.chat.tokens : 0;
    return (isCached() ? 0.1 * L : L) + growth();
  }

  // The session limit runs down faster at peak times, so it's learned separately then.
  const learnKey = (key, peak) => (key === "session" && peak ? "session@peak" : key);

  function rate(family, key) {
    const r = S.learn[family] && S.learn[family][key];
    return r && r.pct >= 2 && r.tok >= 2000 ? r.pct / r.tok : null;
  }

  /** { nextPct, left } for the open chat; nulls while still learning. */
  function forecast() {
    const out = { nextPct: null, left: null };
    if (!S.usage) return out;
    const fam = CUT.family(currentModel()), cost = nextCost(), peak = CUT.isPeak();
    for (const l of S.usage.limits) {
      if (l.key === "extra") continue;
      const r = rate(fam, learnKey(l.key, peak));
      if (!r) continue;
      const per = r * cost;
      if (l.key === "session") out.nextPct = per;
      const n = Math.max(0, Math.floor((100 - l.pct) / per));
      out.left = out.left == null ? n : Math.min(out.left, n);
    }
    return out;
  }

  function learnFrom(p, model) {
    if (!S.usage || !S.chat || S.chat.id !== p.chatId) return;
    const fam = CUT.family(model || p.model);
    const grew = Math.max(0, S.chat.tokens - p.L0);
    S.learn.growth = S.learn.growth ? 0.8 * S.learn.growth + 0.2 * grew : grew;
    const cost = (p.cached ? 0.1 * p.L0 : p.L0) + grew;
    if (cost > 0) {
      const bucket = (S.learn[fam] = S.learn[fam] || {});
      for (const before of p.limits) {
        const after = S.usage.limits.find((l) => l.key === before.key);
        if (!after || before.key === "extra") continue;
        const sameWindow = before.resetsAt && after.resetsAt && Math.abs(after.resetsAt - before.resetsAt) < 15 * 60000;
        if (!sameWindow || after.pct < before.pct) continue;
        const k = learnKey(before.key, p.peak);
        const r = bucket[k] || { pct: 0, tok: 0 };
        bucket[k] = { pct: 0.85 * r.pct + (after.pct - before.pct), tok: 0.85 * r.tok + cost };
      }
    }
    chrome.storage.local.set({ learn: S.learn });
  }

  function saveChat() {
    S.wasCached = isCached();
    const f = S.chat ? forecast() : {};
    chrome.storage.local.set({
      chat: S.chat ? {
        title: S.chat.title, tokens: S.chat.tokens, estimated: S.chat.estimated,
        cachedUntil: S.chat.lastAt ? S.chat.lastAt + CUT.CACHE_MS : null,
        nextPct: f.nextPct, left: f.left, at: Date.now(),
      } : null,
    });
  }

  /* ------------------------------------------------------------ messages from the page */

  window.addEventListener("message", (ev) => {
    const d = ev.data;
    if (ev.source !== window || !d || d.__cut !== true) return;
    if (d.orgId && /^[0-9a-f-]{36}$/.test(d.orgId)) S.orgId = d.orgId;
    if (d.type === "send") {
      const same = S.chat && S.chat.id === d.chatId;
      S.pending = {
        chatId: d.chatId, model: same ? S.chat.model : pickerModel(), peak: CUT.isPeak(),
        L0: same ? S.chat.tokens : 0, cached: same ? isCached() : false,
        limits: S.usage ? S.usage.limits.map((l) => ({ ...l })) : [],
      };
    } else if (d.type === "done") {
      setLimits(CUT.parseStreamWindows(d.limit), true);
      if (d.model && S.chat && S.chat.id === d.chatId) S.chat.model = d.model;
      const p = S.pending && S.pending.chatId === d.chatId ? S.pending : null;
      S.pending = null;
      // Give claude.ai a moment to save the reply and update its usage numbers.
      setTimeout(async () => {
        await refreshChat();
        await refreshUsage();
        if (p && d.ok) learnFrom(p, d.model);
        saveChat();
        render();
      }, 2500);
    }
  });

  /* ------------------------------------------------------------ drawing */

  const el = (tag, cls, text) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  };

  /** A bar; `mark` ({ pct, label }) adds a thin line at another limit's level. */
  function bar(pct, extraCls, mark) {
    const track = el("div", "cut-track" + (extraCls ? " " + extraCls : ""));
    const fill = el("div", "cut-fill cut-" + CUT.color(pct));
    fill.style.width = Math.max(pct > 0 ? 2 : 0, Math.min(100, pct)) + "%";
    track.appendChild(fill);
    if (mark) {
      const m = el("div", "cut-mark");
      m.style.left = Math.min(100, Math.max(0, mark.pct)) + "%";
      track.appendChild(m);
      track.classList.add("cut-has-mark");
      track.title = t("tipMarker", { l: mark.label });
    }
    return track;
  }

  /** The weekly limit that applies to the model in use (all-models weekly or that model's own),
      whichever is higher: shown as a mark on the session bar. */
  function weeklyMark() {
    if (!S.usage) return null;
    const fam = CUT.family(currentModel());
    const fits = S.usage.limits.filter((l) => l.key === "weekly" || (fam !== "default" && l.key === "weekly_" + fam));
    if (!fits.length) return null;
    const top = fits.reduce((a, b) => (b.pct > a.pct ? b : a));
    return { pct: top.pct, label: t("marker", { l: CUT.label(top), p: Math.round(top.pct) }) };
  }

  function peakBadge() {
    if (!CUT.isPeak()) return null;
    const b = el("span", "cut-peak", t("peak"));
    b.title = t("tipPeak", CUT.peakLocal());
    return b;
  }

  const visibleLimits = () => (S.usage ? S.usage.limits.filter((l) => !S.settings.hidden.includes(l.key)) : []);

  /* --- sidebar --- */

  let sideBox = null;

  function findRecents(nav) {
    for (const n of nav.querySelectorAll("h2, h3, h4, div, span, p")) {
      if (n.childElementCount === 0 && /^(Recents|Recent)$/.test(n.textContent.trim())) return n;
    }
    return null;
  }

  /** Where claude.ai's own layouts want our section: { parent, before } or null. */
  function knownSidebarSpot() {
    // Newer layout: a scrolling area whose recents block is a direct child.
    const scroll = document.querySelector(".dframe-sidebar-body .dframe-nav-scroll");
    if (scroll) return { parent: scroll, before: scroll.querySelector(":scope > .dframe-recents-by-mode") };
    // Classic layout: the last block of the sidebar's scrolling column; ours goes at its top.
    const column = document.querySelector("nav .flex.flex-grow.flex-col.overflow-y-auto");
    const blocks = column ? column.querySelectorAll(".flex-1.relative") : [];
    const last = blocks[blocks.length - 1];
    const main = last && (last.querySelector(".px-2.mt-4") || last.querySelector(".px-2.pt-2"));
    if (main) return { parent: main, before: main.firstElementChild };
    return null;
  }

  function placeSidebar() {
    const nav = document.querySelector(".dframe-sidebar-body") || document.querySelector("nav");
    if (!S.settings.sidebar || !nav || nav.getBoundingClientRect().width < 150) {
      if (sideBox) sideBox.remove();
      return null;
    }
    if (!sideBox) sideBox = el("section", "cut-side");
    if (nav.contains(sideBox)) return sideBox;
    const spot = knownSidebarSpot();
    const recents = spot ? null : findRecents(nav);
    if (spot) {
      spot.parent.insertBefore(sideBox, spot.before && spot.before !== sideBox ? spot.before : null);
    } else if (recents) {
      // Climb while the heading is the first thing in its parent, to reach the whole
      // "Recents" block, and put ours just above it.
      let block = recents;
      while (block.parentElement && block.parentElement !== nav && block.parentElement.firstElementChild === block) {
        block = block.parentElement;
      }
      block.parentElement.insertBefore(sideBox, block);
    } else {
      const scroller = [...nav.querySelectorAll("div")].find((d) => /(auto|scroll)/.test(getComputedStyle(d).overflowY));
      (scroller || nav).appendChild(sideBox);
    }
    return sideBox;
  }

  function drawSidebar() {
    const box = placeSidebar();
    if (!box) return;
    box.replaceChildren();
    const head = el("button", "cut-side-head");
    head.type = "button";
    head.setAttribute("aria-expanded", String(!S.settings.collapsed));
    head.append(el("span", "cut-side-title", t("usage")), el("span", "cut-chevron" + (S.settings.collapsed ? "" : " cut-open"), "›"));
    if (S.usage) {
      const age = Math.round((Date.now() - S.usage.fetchedAt) / 60000);
      head.title = age < 1 ? t("updatedNow") : t("updatedMin", { n: age });
    }
    head.addEventListener("click", () => saveSetting("collapsed", !S.settings.collapsed));
    box.appendChild(head);
    if (S.settings.collapsed) {
      // Collapsed: one compact line with the session only.
      const s = S.usage && S.usage.limits.find((l) => l.key === "session");
      if (s) {
        const line = el("div", "cut-row-top cut-collapsed-line");
        line.append(el("span", "cut-pct cut-t-" + CUT.color(s.pct), Math.round(s.pct) + "%"), el("span", "cut-muted", "⏱ " + CUT.untilText(s.resetsAt)));
        const peak = peakBadge();
        if (peak) line.appendChild(peak);
        box.appendChild(line);
      }
      return;
    }
    const list = visibleLimits();
    if (!S.usage || !S.usage.limits.length) {
      box.appendChild(el("div", "cut-muted cut-small",
        S.usageError ? t("loadFailed") : S.usage ? t("noLimits") : t("loading")));
      return;
    }
    for (const l of list) {
      const row = el("div", "cut-row");
      const top = el("div", "cut-row-top");
      const left = el("span", "cut-row-left");
      left.append(el("span", "cut-label", CUT.label(l)), el("span", "cut-pct cut-t-" + CUT.color(l.pct), Math.round(l.pct) + "%"));
      const peak = l.key === "session" ? peakBadge() : null;
      if (peak) left.appendChild(peak);
      top.append(left, el("span", "cut-muted", l.note || (l.resetsAt ? "⏱ " + CUT.untilText(l.resetsAt) : "")));
      row.append(top, bar(l.pct, null, l.key === "session" ? weeklyMark() : null));
      row.title = `${CUT.label(l)}: ${l.note || Math.round(l.pct) + "%"}` +
        (l.resetsAt ? ` · ${t("resetsIn", { t: new Date(l.resetsAt).toLocaleString() })}` : "");
      box.appendChild(row);
    }
  }

  async function saveSetting(key, value) {
    S.settings = { ...S.settings, [key]: value };
    await chrome.storage.local.set({ settings: S.settings });
    render();
  }

  /* --- under the message box --- */

  let strip = null;

  /** The element our line goes right after. Works on chats and on claude.ai/code. */
  function findComposer() {
    // claude.ai's message box: a rounded box whose inner block holds the input. Our line goes
    // after that block, inside the box, so it stays with the box in every layout.
    const input = document.querySelector('[data-testid="chat-input"], [data-testid="code-prompt-input"]');
    const inner = input && input.closest(".rounded-composer > .relative.w-full.min-w-0, .bg-surface-3 > .relative.w-full.min-w-0");
    if (inner) return inner;
    // Otherwise: the last editor on the page (editing an earlier message adds another one above it).
    const editors = document.querySelectorAll('[data-testid="chat-input"], [data-testid="code-prompt-input"], div.ProseMirror[contenteditable="true"], fieldset textarea');
    const editor = editors[editors.length - 1];
    if (!editor) return null;
    const fs = editor.closest("fieldset");
    if (fs) return fs;
    // Otherwise the rounded box around the editor.
    let n = editor.parentElement;
    for (let i = 0; n && i < 8; i++, n = n.parentElement) {
      if (parseFloat(getComputedStyle(n).borderTopLeftRadius) >= 12 && n.getBoundingClientRect().width >= 280) return n;
    }
    return editor.parentElement;
  }

  function drawStrip() {
    const box = S.settings.composer ? findComposer() : null;
    if (!box) { if (strip) strip.remove(); return; }
    if (!strip) strip = el("div", "cut-strip");
    if (box.nextElementSibling !== strip) box.insertAdjacentElement("afterend", strip);
    strip.replaceChildren();

    const left = el("div", "cut-strip-left");
    const session = S.usage && S.usage.limits.find((l) => l.key === "session");
    if (session && !S.settings.hidden.includes("session")) {
      left.append(el("span", "cut-muted", CUT.t("session")), el("span", "cut-pct cut-t-" + CUT.color(session.pct), Math.round(session.pct) + "%"), bar(session.pct, "cut-track-mini", weeklyMark()));
      if (session.resetsAt) left.appendChild(el("span", "cut-muted", "⏱ " + CUT.untilText(session.resetsAt)));
      const peak = peakBadge();
      if (peak) left.appendChild(peak);
      left.title = t("tipSession");
    }
    strip.appendChild(left);

    const right = el("div", "cut-strip-right");
    if (S.chat) {
      const max = CUT.contextWindow(currentModel());
      const ctxPct = (S.chat.tokens / max) * 100;
      const len = el("span");
      len.append(el("span", "cut-muted", t("chat") + " "),
        el("b", ctxPct >= 80 ? "cut-t-" + CUT.color(ctxPct + 10) : null, (S.chat.exact ? "" : "≈") + CUT.fmtTokens(S.chat.tokens)),
        el("span", "cut-muted", " / " + CUT.fmtTokens(max) + " " + t("tokens")));
      len.title = [t("tipLength", { max: CUT.fmtTokens(max) }), S.chat.workspace ? t("tipWorkspace") : "",
        S.chat.exact ? t("tipExact") : S.chat.estimated ? t("tipEstimated") : "", t("tipSystem")].filter(Boolean).join("\n");
      right.appendChild(len);

      if (S.chat.compactions) {
        const c = el("span", "cut-muted", t("compactions", { n: S.chat.compactions }));
        c.title = t("tipCompactions", { n: S.chat.compactions });
        right.appendChild(c);
      }

      const cached = isCached();
      const c = el("span", cached ? "cut-t-green" : "cut-muted", cached ? t("cached", { t: CUT.untilText(S.chat.lastAt + CUT.CACHE_MS) }) : t("notCached"));
      c.title = cached ? t("tipCached") : t("tipNotCached");
      right.appendChild(c);

      const f = forecast();
      if (f.nextPct != null) {
        const next = el("span");
        next.append(el("span", "cut-muted", t("next")), el("b", "cut-t-" + (f.nextPct >= 5 ? "red" : "blue"), f.nextPct < 0.1 ? "<0.1%" : f.nextPct.toFixed(1) + "%"));
        next.title = t("tipNext");
        right.appendChild(next);
      }
      const msgs = el("span");
      if (f.left != null) {
        msgs.append(el("b", "cut-t-" + (f.left <= 5 ? "red" : "blue"), f.left >= 1000 ? "1000+" : "~" + f.left), el("span", "cut-muted", " " + t("msgsLeft")));
        msgs.title = t("tipLeft");
      } else {
        msgs.append(el("span", "cut-muted", t("msgsLeftLearning")));
        msgs.title = t("tipLearning");
      }
      right.appendChild(msgs);
    }
    strip.appendChild(right);
    strip.hidden = !left.childElementCount && !right.childElementCount;
  }

  function render() {
    drawSidebar();
    drawStrip();
  }

  /* ------------------------------------------------------------ start */

  async function start() {
    const saved = await chrome.storage.local.get(["usage", "learn", "settings", "orgId"]);
    S.settings = CUT.settings(saved.settings);
    CUT.setLang(S.settings.lang);
    S.learn = saved.learn || {};
    if (saved.orgId) S.orgId = saved.orgId;
    if (saved.usage && saved.usage.limits) S.usage = saved.usage;
    render();
    refreshUsage();
    refreshChat();

    chrome.storage.onChanged.addListener((ch, area) => {
      if (area !== "local") return;
      if (ch.settings) {
        const keyChanged = (ch.settings.newValue || {}).apiKey !== S.settings.apiKey;
        S.settings = CUT.settings(ch.settings.newValue);
        CUT.setLang(S.settings.lang);
        render();
        if (keyChanged) refreshChat();
      }
      if (ch.usage && ch.usage.newValue && (!S.usage || ch.usage.newValue.fetchedAt > S.usage.fetchedAt)) {
        S.usage = ch.usage.newValue;
        render();
      }
    });

    // claude.ai is a single-page app: watch for chat changes and for it redrawing our spots away.
    let lastChat = chatIdFromUrl();
    setInterval(() => {
      const id = chatIdFromUrl();
      if (id !== lastChat) { lastChat = id; S.chat = null; render(); refreshChat(); }
    }, 800);

    let queued = false;
    new MutationObserver(() => {
      if (queued) return;
      queued = true;
      setTimeout(() => {
        queued = false;
        const sideGone = S.settings.sidebar && !(sideBox && sideBox.isConnected);
        const stripGone = S.settings.composer && !(strip && strip.isConnected);
        if (sideGone || stripGone) render();
      }, 400);
    }).observe(document.body, { childList: true, subtree: true });

    setInterval(() => {                                   // countdowns
      render();
      if (S.chat && isCached() !== S.wasCached) saveChat();
    }, 10000);
    setInterval(() => { if (!document.hidden) refreshUsage(); }, 60000);
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden && (!S.usage || Date.now() - S.usage.fetchedAt > 20000)) { refreshUsage(); refreshChat(); }
    });
  }

  start();
})();

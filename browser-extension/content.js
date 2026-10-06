/* Reads the open chat on claude.ai, chatgpt.com or gemini.google.com and estimates its tokens.

   The websites don't show token counts. For every reply we estimate:
     input  = everything the AI had to read: the whole chat so far, plus attached files
     output = the reply itself (including visible thinking and tool calls)
   using about 4 characters per token. These are estimates, marked as such in the tracker.

   It only reads the chat you have open, using the site's own data (the same requests the
   page makes, with your existing login). Results go to this extension's background page,
   which passes them to the AI Token Tracker app on your computer. Nothing goes anywhere else. */
"use strict";

const est = (t) => AITT.estimateTokens(t);
const POLL_MS = 15000;

/* ------------------------------------------------------------ helpers */

function send(msg) {
  try { chrome.runtime.sendMessage(msg); } catch { /* extension reloaded: ignore */ }
}

async function getJSON(url, headers) {
  const res = await fetch(url, { credentials: "include", headers: { Accept: "application/json", ...(headers || {}) } });
  if (!res.ok) throw new Error(`${res.status}`);
  return res.json();
}

/** Turns [{role: "user"|"assistant"|"tool", text, files, id, t, model}] into per-reply estimates. */
function estimateTurns(messages) {
  const turns = [];
  let context = 0, prompt = "";
  for (const m of messages) {
    const tokens = est(m.text) + est(m.files || "");
    if (m.role === "assistant") {
      turns.push({ id: m.id, t: m.t, prompt: prompt.slice(0, 400), model: m.model || "", input: context, output: est(m.text) });
      prompt = "";
    } else if (m.role === "user") {
      prompt = m.text || prompt;
    }
    context += tokens;
  }
  return turns;
}

/* ------------------------------------------------------------ claude.ai */

const claude = {
  site: "claude",
  chatId() { return (location.pathname.match(/^\/chat\/([0-9a-f-]{36})/) || [])[1]; },
  async org() {
    const fromCookie = (document.cookie.match(/(?:^|;\s*)lastActiveOrg=([0-9a-f-]{36})/) || [])[1];
    if (fromCookie) return fromCookie;
    const orgs = await getJSON("/api/organizations");
    const chat = (orgs || []).find((o) => (o.capabilities || []).includes("chat")) || (orgs || [])[0];
    return chat && chat.uuid;
  },
  async limits() {
    const org = await this.org();
    if (!org) return null;
    const u = await getJSON(`/api/organizations/${org}/usage`);
    const row = (key, label) => u && u[key] && typeof u[key].utilization === "number"
      ? { label, percent: Math.max(0, Math.min(100, u[key].utilization)), resets_at: u[key].resets_at || null } : null;
    const windows = [row("five_hour", "Session (5h)"), row("seven_day", "Weekly"),
      row("seven_day_opus", "Weekly (Opus)"), row("seven_day_sonnet", "Weekly (Sonnet)")].filter(Boolean);
    return windows.length ? { provider: "claude", windows } : null;
  },
  text(content) {
    const parts = [];
    for (const c of content || []) {
      if (!c) continue;
      if (typeof c.text === "string") parts.push(c.text);
      if (typeof c.thinking === "string") parts.push(c.thinking);
      if (c.input) parts.push(JSON.stringify(c.input));
      if (c.content) parts.push(typeof c.content === "string" ? c.content : JSON.stringify(c.content));
    }
    return parts.join("\n");
  },
  async read(id) {
    const org = await this.org();
    if (!org) return null;
    const c = await getJSON(`/api/organizations/${org}/chat_conversations/${id}?tree=True&rendering_mode=messages&render_all_tools=true`);
    let msgs = c.chat_messages || [];
    // With branches (edited prompts, retries), follow only the branch on screen.
    if (c.current_leaf_message_uuid) {
      const byId = new Map(msgs.map((m) => [m.uuid, m]));
      const branch = [];
      for (let m = byId.get(c.current_leaf_message_uuid); m; m = byId.get(m.parent_message_uuid)) branch.push(m);
      if (branch.length) msgs = branch.reverse();
    }
    const messages = msgs.map((m) => ({
      id: m.uuid, t: Date.parse(m.created_at) || null, model: c.model || "",
      role: m.sender === "human" ? "user" : "assistant",
      text: this.text(m.content) || m.text || "",
      files: (m.attachments || []).map((a) => a.extracted_content || "").join("\n"),
    }));
    return { title: c.name || "", model: c.model || "", turns: estimateTurns(messages) };
  },
};

/* ------------------------------------------------------------ chatgpt.com */

const chatgpt = {
  site: "chatgpt",
  token: null,
  chatId() { return (location.pathname.match(/\/c\/([0-9a-zA-Z-]{8,})/) || [])[1]; },
  async auth() {
    if (!this.token) this.token = (await getJSON("/api/auth/session")).accessToken;
    return this.token;
  },
  async limits() { return null; },  // ChatGPT's website doesn't publish its limits
  async read(id) {
    let c;
    try {
      c = await getJSON(`/backend-api/conversation/${id}`, { Authorization: `Bearer ${await this.auth()}` });
    } catch (e) {
      this.token = null;  // login may have changed; try again next time
      throw e;
    }
    const map = c.mapping || {};
    const branch = [];
    for (let n = map[c.current_node]; n; n = map[n.parent]) if (n.message) branch.push(n.message);
    branch.reverse();
    const messages = branch.filter((m) => m.author && m.author.role !== "system").map((m) => {
      const parts = (m.content && (m.content.parts || [m.content.text])) || [];
      const text = parts.map((p) => (typeof p === "string" ? p : p && p.text ? p.text : "")).join("\n");
      return { id: m.id, t: m.create_time ? Math.round(m.create_time * 1000) : null,
        role: m.author.role === "user" ? "user" : m.author.role === "assistant" ? "assistant" : "tool",
        model: (m.metadata && m.metadata.model_slug) || "", text };
    });
    // Several assistant messages in a row (tool use, browsing) are one answer to the user.
    return { title: c.title || "", model: (messages.find((m) => m.model) || {}).model || "", turns: estimateTurns(messages) };
  },
};

/* ------------------------------------------------------------ gemini.google.com */

const gemini = {
  site: "gemini",
  chatId() { return (location.pathname.match(/\/app\/([0-9a-zA-Z_-]{6,})/) || [])[1]; },
  async limits() { return null; },
  async read(id) {
    // Gemini has no simple data request to reuse, so read the chat on screen.
    const nodes = [...document.querySelectorAll("user-query, model-response")];
    if (!nodes.length) return null;
    const seen = await chrome.storage.local.get("geminiSeen");
    const firstSeen = (seen.geminiSeen && seen.geminiSeen[id]) || {};
    const messages = nodes.map((n, i) => {
      const key = String(i);
      firstSeen[key] = firstSeen[key] || Date.now();
      return { id: `${id}-${i}`, t: firstSeen[key], role: n.tagName.toLowerCase() === "user-query" ? "user" : "assistant",
        model: "", text: n.innerText || "" };
    });
    await chrome.storage.local.set({ geminiSeen: { ...(seen.geminiSeen || {}), [id]: firstSeen } });
    const title = (document.title || "").replace(/\s*[-|]\s*Gemini.*$/i, "").trim();
    return { title: title === "Gemini" ? "" : title, model: "gemini (website)", turns: estimateTurns(messages) };
  },
};

/* ------------------------------------------------------------ main loop */

const site = location.hostname.endsWith("claude.ai") ? claude
  : location.hostname.includes("gemini.google.com") ? gemini : chatgpt;
let lastSent = "", pending = "", lastLimits = 0;

async function tick() {
  if (document.hidden) return;
  try {
    if (Date.now() - lastLimits > 120000) {
      lastLimits = Date.now();
      const limits = await site.limits();
      if (limits) send({ type: "limits", limits });
    }
    const id = site.chatId();
    if (!id) return;
    const chat = await site.read(id);
    if (!chat || !chat.turns.length) return;
    const doc = { site: site.site, id, url: location.href, ...chat };
    const sig = JSON.stringify(doc.turns.map((t) => [t.id, t.input, t.output]));
    // Send once a reply has stopped growing (same on two checks in a row).
    if (sig !== lastSent && sig === pending) {
      lastSent = sig;
      send({ type: "chat", doc });
    }
    pending = sig;
  } catch {
    /* signed out, offline or the site changed: try again on the next check */
  }
}

tick();
setInterval(tick, POLL_MS);
let lastPath = location.pathname;
setInterval(() => { if (location.pathname !== lastPath) { lastPath = location.pathname; pending = ""; setTimeout(tick, 2000); } }, 1000);
document.addEventListener("visibilitychange", () => { if (!document.hidden) tick(); });

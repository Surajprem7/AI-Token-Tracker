/* Keeps the latest estimates and passes them to the AI Token Tracker app on this computer.

   Connection: the tracker's Sources page shows a code like "47690-abc...". Pasting it in this
   extension's popup tells us the app's local address (127.0.0.1:<port>) and its key.
   If the app isn't running, chats wait here and are sent when it is. */
"use strict";

const MAX_CHATS = 200;
const ext = globalThis.chrome;

async function get(keys) { return ext.storage.local.get(keys); }
async function set(obj) { return ext.storage.local.set(obj); }

function parseCode(code) {
  const m = String(code || "").trim().match(/^(\d{2,5})-([A-Za-z0-9_-]{16,})$/);
  return m ? { port: Number(m[1]), key: m[2] } : null;
}

async function tracker(path, body) {
  const { connection } = await get("connection");
  if (!connection) throw new Error("not connected");
  const res = await fetch(`http://127.0.0.1:${connection.port}${path}`, {
    method: body === undefined ? "GET" : "POST",
    headers: { "X-AITT-Key": connection.key, "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (res.status === 403) throw new Error("The connection code is wrong. Copy it again from the tracker's Sources page.");
  if (!res.ok) throw new Error(`The tracker replied ${res.status}`);
  return res.json();
}

/** A copy installed from the desktop app carries connection.json (the app's address and key):
    connect with it automatically, and re-read it when the app's address changed. */
async function connectFromBundle(force) {
  const { connection } = await get("connection");
  if (connection && !force) return;
  try {
    const res = await fetch(ext.runtime.getURL("connection.json"), { cache: "no-store" });
    if (!res.ok) return;
    const c = await res.json();
    if (c && c.port && c.key && (!connection || connection.key !== c.key || connection.port !== c.port)) {
      await set({ connection: { port: Number(c.port), key: String(c.key) } });
    }
  } catch { /* store or zip install: no bundled file */ }
}

/** Send every chat the tracker hasn't got yet. */
async function sync() {
  await connectFromBundle(false);
  const { chats = {}, unsent = [] } = await get(["chats", "unsent"]);
  const left = [];
  for (const key of unsent) {
    const chat = chats[key];
    if (!chat) continue;
    try {
      await tracker("/api/web/chat", chat);
    } catch (e) {
      left.push(key);
      await set({ lastError: String(e.message || e), lastErrorAt: Date.now() });
      await connectFromBundle(true);  // the app may be on a new address now
    }
  }
  const { limits } = await get("limits");
  if (limits && limits.unsent) {
    try {
      await tracker("/api/web/limits", limits);
      limits.unsent = false;
      await set({ limits });
    } catch { /* next time */ }
  }
  await set({ unsent: left, ...(left.length < unsent.length ? { lastSync: Date.now(), lastError: "" } : {}) });
}

ext.runtime.onMessage.addListener((msg, _sender, reply) => {
  (async () => {
    if (msg.type === "chat" && msg.doc && msg.doc.site && msg.doc.id) {
      const { chats = {}, unsent = [] } = await get(["chats", "unsent"]);
      const key = `${msg.doc.site}:${msg.doc.id}`;
      chats[key] = { ...msg.doc, seen: Date.now() };
      const keys = Object.keys(chats).sort((a, b) => (chats[b].seen || 0) - (chats[a].seen || 0));
      for (const old of keys.slice(MAX_CHATS)) delete chats[old];
      await set({ chats, unsent: [...new Set([...unsent, key])].filter((k) => chats[k]) });
      await sync();
    } else if (msg.type === "limits" && msg.limits) {
      await set({ limits: { ...msg.limits, at: Date.now(), unsent: true } });
      await sync();
    } else if (msg.type === "connect") {
      const conn = parseCode(msg.code);
      if (!conn) return reply({ ok: false, error: "That isn't a connection code. It looks like 47690-AbC…" });
      await set({ connection: conn });
      try {
        const info = await tracker("/api/web/ping");
        const { chats = {} } = await get("chats");
        await set({ unsent: Object.keys(chats) });  // send everything collected so far
        await sync();
        return reply({ ok: true, version: info.version });
      } catch (e) {
        return reply({ ok: false, error: String(e.message || e).includes("fetch")
          ? "Saved. The tracker app isn't open right now; chats will be sent when it is." : String(e.message || e) });
      }
    } else if (msg.type === "status") {
      await connectFromBundle(false);
      try { return reply({ ok: true, ...(await tracker("/api/web/ping")) }); } catch (e) { return reply({ ok: false, error: String(e.message || e) }); }
    } else if (msg.type === "open") {
      try { await tracker("/api/web/show", {}); return reply({ ok: true }); } catch (e) { return reply({ ok: false, error: String(e.message || e) }); }
    }
    reply({ ok: true });
  })();
  return true;  // the reply comes later
});

ext.runtime.onInstalled.addListener(() => connectFromBundle(false).then(sync));
ext.runtime.onStartup.addListener(() => connectFromBundle(false));
ext.alarms.create("sync", { periodInMinutes: 1 });
ext.alarms.onAlarm.addListener((a) => { if (a.name === "sync") sync(); });

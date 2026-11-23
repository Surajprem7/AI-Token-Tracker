/* Runs inside the claude.ai page itself (not the extension's isolated world) so it can see the
   page's own requests. It only watches message sends: when you send a message it tells the
   extension "a message is going out", and when Claude's reply has finished streaming it says
   "done", passing along the usage windows and the model that the reply stream reports.
   Nothing is changed and nothing leaves the page. */
(function () {
  "use strict";
  if (window.__cutHooked) return;
  window.__cutHooked = true;

  const COMPLETION = /\/api\/organizations\/([0-9a-f-]+)\/chat_conversations\/([0-9a-f-]+)\/(retry_)?completion(?:\?|$)/;
  const post = (msg) => window.postMessage({ __cut: true, ...msg }, location.origin);
  const realFetch = window.fetch;

  function urlOf(input) {
    try {
      if (typeof input === "string") return new URL(input, location.href).href;
      if (input instanceof URL) return input.href;
      if (input && typeof input.url === "string") return input.url;
    } catch (e) { /* ignore */ }
    return "";
  }

  function parseEvents(text, out) {
    for (const line of text.split("\n")) {
      if (!line.startsWith("data:")) continue;
      let evt;
      try { evt = JSON.parse(line.slice(5).trim()); } catch (e) { continue; }
      if (evt && evt.type === "message_limit" && evt.message_limit) out.limit = evt.message_limit;
      if (evt && evt.type === "message_start" && evt.message && evt.message.model) out.model = evt.message.model;
    }
  }

  async function watch(response, orgId, chatId) {
    const out = { limit: null, model: null };
    try {
      const type = response.headers.get("content-type") || "";
      if (!response.ok && !type.includes("event-stream")) {
        // A refused message (limit reached) carries the same usage info inside the error.
        const body = await response.json().catch(() => null);
        const inner = body && body.error && body.error.message;
        try { out.limit = typeof inner === "string" ? JSON.parse(inner) : inner || null; } catch (e) { /* ignore */ }
      } else if (response.body) {
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const cut = buffer.lastIndexOf("\n\n");
          if (cut >= 0) {
            parseEvents(buffer.slice(0, cut), out);
            buffer = buffer.slice(cut + 2);
          }
        }
        parseEvents(buffer, out);
      }
    } catch (e) { /* stream aborted: still report what we have */ }
    post({ type: "done", orgId, chatId, ok: response.ok, limit: out.limit, model: out.model });
  }

  window.fetch = async function (input, init) {
    const url = urlOf(input);
    const m = COMPLETION.exec(url);
    const method = ((init && init.method) || (input && input.method) || "GET").toUpperCase();
    if (!m || method !== "POST") return realFetch.apply(this, arguments);

    post({ type: "send", orgId: m[1], chatId: m[2] });
    const response = await realFetch.apply(this, arguments);
    try { watch(response.clone(), m[1], m[2]); } catch (e) { /* ignore */ }
    return response;
  };
})();

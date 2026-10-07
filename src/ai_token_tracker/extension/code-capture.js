/* claude.ai/code (Claude Code in the cloud): the page receives Claude's replies, and each reply
   carries its exact token usage, the same numbers Claude Code saves on a computer
   ({input_tokens, output_tokens, cache_read_input_tokens, cache_creation_input_tokens}).
   This small script runs in the page itself, looks at the data the page already downloads
   (fetch, streams and WebSockets), copies only those usage numbers, the reply id, model and time,
   and hands them to the extension. It changes nothing and sends nothing anywhere itself. */
(function () {
  "use strict";
  if (window.__aittCodeCapture) return;
  window.__aittCodeCapture = true;

  const onCodePage = () => location.pathname.startsWith("/code");
  const MAX_TEXT = 30 * 1024 * 1024;

  function report(items) {
    if (items.length) window.postMessage({ __aitt: "code-usage", items }, location.origin);
  }

  /** Every {id, usage:{input_tokens, output_tokens, ...}} object inside some JSON, with the nearest time. */
  function scan(value) {
    const found = [];
    let budget = 50000;
    (function walk(node, ts, depth) {
      if (!node || typeof node !== "object" || depth > 12 || --budget < 0) return;
      if (Array.isArray(node)) { for (const v of node) walk(v, ts, depth + 1); return; }
      const t = node.timestamp || node.created_at || node.createdAt || ts;
      const u = node.usage;
      if (u && typeof u === "object" && typeof u.input_tokens === "number" && typeof u.output_tokens === "number"
          && typeof node.id === "string") {
        found.push({ id: node.id, model: typeof node.model === "string" ? node.model : "", t: typeof t === "string" || typeof t === "number" ? t : null,
          usage: { input: u.input_tokens || 0, output: u.output_tokens || 0,
                   cache_read: u.cache_read_input_tokens || 0, cache_write: u.cache_creation_input_tokens || 0 } });
      }
      for (const k in node) if (k !== "usage") walk(node[k], t, depth + 1);
    })(value, null, 0);
    return found;
  }

  function scanText(text) {
    if (!text || text.length > MAX_TEXT || text.indexOf("input_tokens") < 0) return;
    const items = [];
    try { items.push(...scan(JSON.parse(text))); } catch {
      for (let line of text.split("\n")) {  // event streams and JSON lines
        line = line.trim();
        if (line.startsWith("data:")) line = line.slice(5).trim();
        if (!line.startsWith("{") && !line.startsWith("[")) continue;
        if (line.indexOf("input_tokens") < 0) continue;
        try { items.push(...scan(JSON.parse(line))); } catch { /* partial line */ }
      }
    }
    report(items);
  }

  // fetch: read a copy of each response; streams are read as they arrive.
  const realFetch = window.fetch;
  window.fetch = async function (...args) {
    const res = await realFetch.apply(this, args);
    try {
      if (onCodePage() && res.body) {
        const type = res.headers.get("content-type") || "";
        if (/json|event-stream|ndjson|text\/plain/.test(type)) {
          const reader = res.clone().body.getReader();
          const dec = new TextDecoder();
          let buf = "";
          (async () => {
            for (;;) {
              const { done, value } = await reader.read();
              if (done) break;
              buf += dec.decode(value, { stream: true });
              if (/event-stream|ndjson/.test(type)) {  // handle complete lines as they come
                const cut = buf.lastIndexOf("\n");
                if (cut >= 0) { scanText(buf.slice(0, cut)); buf = buf.slice(cut + 1); }
              } else if (buf.length > MAX_TEXT) { reader.cancel(); return; }
            }
            scanText(buf);
          })().catch(() => {});
        }
      }
    } catch { /* never get in the page's way */ }
    return res;
  };

  // WebSockets and EventSource: look at each incoming message.
  const RealWS = window.WebSocket;
  if (RealWS) {
    window.WebSocket = new Proxy(RealWS, {
      construct(target, args) {
        const ws = new target(...args);
        ws.addEventListener("message", (e) => { if (onCodePage() && typeof e.data === "string") scanText(e.data); });
        return ws;
      },
    });
  }
  const RealES = window.EventSource;
  if (RealES) {
    window.EventSource = new Proxy(RealES, {
      construct(target, args) {
        const es = new target(...args);
        const listen = es.addEventListener.bind(es);
        listen("message", (e) => { if (onCodePage()) scanText(e.data); });
        es.addEventListener = function (type, fn, opts) {  // named events too
          if (type !== "message") listen(type, (e) => { if (onCodePage()) scanText(e.data); });
          return listen(type, fn, opts);
        };
        return es;
      },
    });
  }
})();

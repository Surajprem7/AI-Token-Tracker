"""A stand-in for claude.ai, for testing the extension: a page shaped like claude.ai
(sidebar with "Recents", message box in a fieldset) and the API answers it reads."""
import json, ssl, sys, time, threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

ORG = "11111111-2222-4333-8444-555555555555"
CHAT = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
NOW = time.time()
state = {"session": 34, "weekly": 18, "opus": 62, "sent": 0}

LONG = ("This is a fairly long assistant reply about planning a project, with details. " * 400)

def iso(ts): return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))

def usage():
    return {"limits": [
        {"kind": "session", "percent": state["session"], "resets_at": iso(NOW + 2 * 3600 + 17 * 60)},
        {"kind": "weekly_all", "percent": state["weekly"], "resets_at": iso(NOW + 3 * 86400 + 5 * 3600)},
        {"kind": "weekly_scoped", "percent": state["opus"], "resets_at": iso(NOW + 3 * 86400 + 5 * 3600),
         "scope": {"model": {"display_name": "Opus"}}},
    ]}

def conversation():
    msgs, parent = [], "00000000-0000-4000-8000-000000000000"
    n = 6 + 2 * state["sent"]
    for i in range(n):
        uid = f"m{i:04d}-0000-4000-8000-000000000000"
        human = i % 2 == 0
        msgs.append({"uuid": uid, "parent_message_uuid": parent, "sender": "human" if human else "assistant",
                     "created_at": iso(time.time() - (120 if i == n - 1 else 3600)),
                     "updated_at": iso(time.time() - (120 if i == n - 1 else 3600)),
                     "content": [{"type": "text", "text": "Can you help me plan the launch?" if human else LONG}]})
        parent = uid
    return {"uuid": CHAT, "name": "Planning the product launch", "model": "claude-opus-4-5",
            "current_leaf_message_uuid": parent, "chat_messages": msgs}

PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Claude</title>
<style>
 body{margin:0;font:14px/1.5 system-ui,sans-serif;background:#faf9f5;color:#3d3929;display:flex;height:100vh}
 nav{width:280px;background:#f5f4ef;border-right:1px solid #e5e3da;padding:12px;box-sizing:border-box;display:flex;flex-direction:column}
 nav a{display:block;padding:6px 8px;border-radius:8px;color:inherit;text-decoration:none}
 nav a:hover{background:#ebe9e1}
 .list{overflow-y:auto;flex:1}
 h3{font-size:12px;font-weight:500;opacity:.6;margin:14px 8px 4px}
 main{flex:1;display:flex;flex-direction:column;align-items:center}
 .msgs{flex:1;width:100%;max-width:720px;padding:24px;box-sizing:border-box;overflow:auto}
 .bubble{background:#f0eee6;padding:10px 14px;border-radius:12px;margin:10px 0}
 .wrap{width:100%;max-width:720px;padding:0 24px 20px;box-sizing:border-box}
 fieldset{border:1px solid #dedbd0;border-radius:18px;background:#fff;padding:14px;margin:0;box-shadow:0 2px 8px rgba(0,0,0,.05)}
 .ProseMirror{min-height:44px;outline:none}
 button.send{float:right;background:#c96442;color:#fff;border:0;border-radius:8px;padding:6px 12px;cursor:pointer}
</style></head><body>
<nav><a href="/new"><b>+ New chat</b></a><a href="#">Chats</a><a href="#">Projects</a>
 <div class="list">
  <div><h3>Starred</h3><a href="#">Weekly report</a></div>
  <div><h3>Recents</h3><a href="/chat/CHAT">Planning the product launch</a><a href="#">Python script help</a><a href="#">Trip to Goa</a></div>
 </div></nav>
<main><div class="msgs"><div class="bubble">Can you help me plan the launch?</div><div class="bubble">Sure! Here is a plan…</div></div>
<div class="wrap"><fieldset><div class="ProseMirror" contenteditable="true" data-testid="chat-input"></div>
<button class="send" id="send">Send</button><div style="clear:both"></div></fieldset></div></main>
<script>
document.cookie = "lastActiveOrg=ORG; path=/";
document.getElementById('send').onclick = async () => {
  const r = await fetch('/api/organizations/ORG/chat_conversations/CHAT/completion', {method:'POST', body:'{}'});
  await r.text();
  if (!location.pathname.startsWith('/chat/')) history.pushState({}, '', '/chat/CHAT');
};
</script></body></html>""".replace("CHAT", CHAT).replace("ORG", ORG)

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _json(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code); self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        p = self.path.split("?")[0]
        if p == "/api/organizations": return self._json([{"uuid": ORG, "capabilities": ["chat", "claude_max"]}])
        if p == f"/api/organizations/{ORG}/usage": return self._json(usage())
        if p == f"/api/organizations/{ORG}/chat_conversations/{CHAT}": return self._json(conversation())
        if p.startswith("/api/"): return self._json({"error": "nope"}, 404)
        b = PAGE.encode()
        self.send_response(200); self.send_header("content-type", "text/html"); self.end_headers(); self.wfile.write(b)
    def do_POST(self):
        if self.path.endswith("/completion"):
            self.rfile.read(int(self.headers.get("content-length") or 0))
            state["sent"] += 1
            state["session"] += 3; state["weekly"] += 1; state["opus"] += 2
            self.send_response(200); self.send_header("content-type", "text/event-stream"); self.end_headers()
            ev = [{"type": "message_start", "message": {"model": "claude-opus-4-5"}},
                  {"type": "content_block_delta", "delta": {"text": "ok"}},
                  {"type": "message_limit", "message_limit": {"type": "within_limit", "windows": {
                      "5h": {"status": "within_limit", "utilization": state["session"] / 100, "resets_at": int(NOW + 2 * 3600 + 17 * 60)},
                      "7d": {"status": "within_limit", "utilization": state["weekly"] / 100, "resets_at": int(NOW + 3 * 86400 + 5 * 3600)}}}},
                  {"type": "message_stop"}]
            for e in ev:
                self.wfile.write(f"event: x\ndata: {json.dumps(e)}\n\n".encode()); self.wfile.flush(); time.sleep(0.05)
            return
        self._json({}, 404)

srv = ThreadingHTTPServer(("127.0.0.1", 8443), H)
ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); import os; here = os.path.dirname(os.path.abspath(__file__))
ctx.load_cert_chain(os.path.join(here, "cert.pem"), os.path.join(here, "key.pem"))
srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
print("mock up", flush=True)
srv.serve_forever()

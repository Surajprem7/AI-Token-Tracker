/* Runs on the AI Token Tracker app's own "Connect" page (http://127.0.0.1:<port>/connect?...).
   The page carries the connection code in a tag; we save it, so nobody has to copy and paste. */
"use strict";
(function () {
  const tag = document.querySelector('meta[name="aitt-connect"]');
  const status = document.getElementById("aitt-status");
  if (!tag || !tag.content || !status) return;  // any other local page: do nothing
  chrome.runtime.sendMessage({ type: "connect", code: tag.content }, (r) => {
    r = r || {};
    status.className = r.ok ? "ok" : "";
    status.textContent = r.ok ? "Connected! You can close this tab. Your chats on claude.ai, ChatGPT and Gemini will now appear in AI Token Tracker."
      : (r.error || "Couldn't connect.");
    const help = document.getElementById("aitt-help");
    if (help && r.ok) help.hidden = true;
  });
})();

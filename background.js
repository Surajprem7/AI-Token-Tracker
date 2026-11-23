/* Background worker:
   • refreshes your usage every few minutes, even when no claude.ai tab is open;
   • shows the session % on the toolbar icon;
   • tells you when a limit you'd used up has reset;
   • counts tokens exactly with Anthropic's free token counter, if you added an API key. */
"use strict";
if (typeof importScripts === "function") importScripts("i18n.js", "shared.js");   // Chrome; Firefox loads shared.js itself

const REFRESH_MIN = 5;
const RELEASES_API = "https://api.github.com/repos/Surajprem7/AI-Token-Tracker/releases/latest";
const COUNT_MODELS = ["claude-sonnet-4-5", "claude-haiku-4-5"];       // fallbacks for the token counter

async function getSettings() {
  const { settings } = await chrome.storage.local.get("settings");
  const s = CUT.settings(settings);
  CUT.setLang(s.lang);
  return s;
}

async function fetchJSON(url) {
  const res = await fetch(url, { credentials: "include", headers: { Accept: "application/json" } });
  if (!res.ok) throw new Error(String(res.status));
  return res.json();
}

const chatOrgs = (orgs) => (orgs || []).filter((o) => o && o.uuid && (!o.capabilities || o.capabilities.includes("chat")));

/** Every organisation (account) you can chat in: personal, team, ... */
async function listOrgs() {
  const orgs = chatOrgs(await fetchJSON("https://claude.ai/api/organizations"));
  await chrome.storage.local.set({ orgs: orgs.map((o) => ({ uuid: o.uuid, name: o.name || "" })) });
  return orgs;
}

/** Refreshes the usage of every account; the one open on claude.ai is also stored as `usage`. */
async function refresh() {
  await getSettings();
  try {
    const orgs = await listOrgs();
    if (!orgs.length) return { ok: false, error: CUT.t("notSignedIn") };
    let { orgId, usageByOrg } = await chrome.storage.local.get(["orgId", "usageByOrg"]);
    if (!orgId || !orgs.some((o) => o.uuid === orgId)) {
      orgId = orgs[0].uuid;                      // the page tells us the real one when claude.ai is open
      await chrome.storage.local.set({ orgId });
    }
    usageByOrg = usageByOrg || {};
    let any = false;
    for (const o of orgs) {
      try {
        const limits = CUT.parseUsage(await fetchJSON(`https://claude.ai/api/organizations/${o.uuid}/usage`));
        if (limits.length) { usageByOrg[o.uuid] = { limits, fetchedAt: Date.now(), orgId: o.uuid }; any = true; }
      } catch (e) { /* this account has no usage data: skip it */ }
    }
    const patch = { usageByOrg };
    if (usageByOrg[orgId]) patch.usage = usageByOrg[orgId];
    await chrome.storage.local.set(patch);
    return any ? { ok: true } : { ok: true, empty: true };   // free plan: the page fills it from the chat stream
  } catch (e) {
    return { ok: false, error: /^(401|403)$/.test(e.message) ? CUT.t("notSignedIn") : CUT.t("unreachable") };
  }
}

/* ------------------------------------------------------------ toolbar badge */

async function updateBadge() {
  const s = await getSettings();
  const { usage } = await chrome.storage.local.get("usage");
  const session = usage && usage.limits && usage.limits.find((l) => l.key === "session");
  if (!s.badge || !session || (session.resetsAt && session.resetsAt < Date.now())) {
    await chrome.action.setBadgeText({ text: "" });
    return;
  }
  const pct = Math.round(session.pct);
  await chrome.action.setBadgeText({ text: pct + "%" });
  await chrome.action.setBadgeBackgroundColor({ color: pct >= CUT.WARN_AT ? "#ef4444" : pct >= 70 ? "#f59e0b" : "#3b82f6" });
  if (chrome.action.setBadgeTextColor) await chrome.action.setBadgeTextColor({ color: "#ffffff" });
}

/* ------------------------------------------------------------ reset notifications */

// For every limit at or above the threshold, set an alarm for its reset time.
// Alarm names carry the limit and its reset time, so setting one again just replaces it.
async function scheduleResets() {
  const s = await getSettings();
  const { usage } = await chrome.storage.local.get("usage");
  const all = await chrome.alarms.getAll();
  const wanted = new Set();
  if (s.notify && usage && usage.limits) {
    for (const l of usage.limits) {
      if (!l.resetsAt || l.resetsAt <= Date.now() || l.pct < s.notifyAt) continue;
      const name = `reset|${l.key}|${l.resetsAt}`;
      wanted.add(name);
      if (!all.some((a) => a.name === name)) chrome.alarms.create(name, { when: l.resetsAt + 30000 });
    }
  }
  // Drop alarms for limits that are no longer high (e.g. threshold raised). Never drop one whose
  // reset time has come: the fresh usage then already shows the new window, but we still owe the notice.
  for (const a of all) {
    if (!a.name.startsWith("reset|") || wanted.has(a.name)) continue;
    if (Number(a.name.split("|")[2]) > Date.now() + 60000) chrome.alarms.clear(a.name);
  }
}

async function notifyReset(alarmName) {
  const s = await getSettings();
  if (!s.notify || !chrome.notifications) return;
  const key = alarmName.split("|")[1];
  const { usage } = await chrome.storage.local.get("usage");
  const l = (usage && usage.limits && usage.limits.find((x) => x.key === key)) || { key };
  chrome.notifications.create(alarmName, {
    type: "basic",
    iconUrl: "icons/icon128.png",
    title: CUT.t("notifTitle"),
    message: CUT.t("notifBody", { l: CUT.label(l) }),
  });
  refresh().then(updateBadge);
}

/* ------------------------------------------------------------ exact token count */

async function countTokens(text, model) {
  const s = await getSettings();
  if (!CUT.validKey(s.apiKey)) return { ok: false };
  const models = [...new Set([/^claude-/.test(model || "") ? model : null, ...COUNT_MODELS].filter(Boolean))];
  for (const m of models) {
    try {
      const res = await fetch("https://api.anthropic.com/v1/messages/count_tokens", {
        method: "POST",
        headers: {
          "content-type": "application/json",
          "x-api-key": s.apiKey,
          "anthropic-version": "2023-06-01",
          "anthropic-dangerous-direct-browser-access": "true",
        },
        body: JSON.stringify({ model: m, messages: [{ role: "user", content: text || " " }] }),
      });
      if (res.ok) {
        const j = await res.json();
        if (typeof j.input_tokens === "number") return { ok: true, tokens: j.input_tokens };
      }
      if (res.status === 401 || res.status === 403) return { ok: false, error: "key" };
    } catch (e) { return { ok: false }; }
  }
  return { ok: false };
}

/* ------------------------------------------------------------ updates */

// Store installs (Chrome Web Store, Edge Add-ons, Firefox Add-ons) are updated by the browser itself.
// A copy loaded by hand from a GitHub zip ("development" install) checks GitHub once a day instead,
// and the popup offers the new version.
const newer = (a, b) => {
  const x = String(a).split(".").map(Number), y = String(b).split(".").map(Number);
  for (let i = 0; i < Math.max(x.length, y.length); i++) {
    if ((x[i] || 0) !== (y[i] || 0)) return (x[i] || 0) > (y[i] || 0);
  }
  return false;
};

async function checkForUpdate() {
  try {
    const self = chrome.management && chrome.management.getSelf ? await chrome.management.getSelf() : null;
    if (self && self.installType !== "development") { await chrome.storage.local.set({ update: null }); return; }
    const res = await fetch(RELEASES_API, { headers: { Accept: "application/vnd.github+json" } });
    if (!res.ok) return;
    const rel = await res.json();
    const latest = String(rel.tag_name || "").replace(/^v/, "");
    const mine = chrome.runtime.getManifest().version;
    const zip = (rel.assets || []).find((a) => /-chrome\.zip$/.test(a.name));
    await chrome.storage.local.set({
      update: latest && newer(latest, mine) ? { version: latest, page: rel.html_url, zip: zip && zip.browser_download_url } : null,
    });
  } catch (e) { /* offline: try again tomorrow */ }
}

/* ------------------------------------------------------------ wiring */

function setup() {
  chrome.alarms.create("refresh", { periodInMinutes: REFRESH_MIN });
  chrome.alarms.create("update-check", { periodInMinutes: 24 * 60 });
  refresh().then(updateBadge).then(scheduleResets);
  checkForUpdate();
}

chrome.runtime.onInstalled.addListener(setup);
chrome.runtime.onStartup.addListener(setup);
chrome.alarms.onAlarm.addListener((a) => {
  if (a.name === "refresh") refresh().then(updateBadge);
  else if (a.name === "update-check") checkForUpdate();
  else if (a.name.startsWith("reset|")) notifyReset(a.name);
});
chrome.storage.onChanged.addListener((ch, area) => {
  if (area !== "local" || !(ch.usage || ch.settings)) return;
  updateBadge();
  scheduleResets();
});
chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  if (!msg) return false;
  if (msg.type === "refresh") { refresh().then(async (r) => { await updateBadge(); reply(r); }); return true; }
  if (msg.type === "count") { countTokens(msg.text, msg.model).then(reply); return true; }
  if (msg.type === "checkUpdate") { checkForUpdate().then(() => reply({ ok: true })); return true; }
  return false;
});
if (chrome.notifications) {
  chrome.notifications.onClicked.addListener((id) => {
    chrome.tabs.create({ url: "https://claude.ai/new" });
    chrome.notifications.clear(id);
  });
}

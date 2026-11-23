"use strict";

const $ = (id) => document.getElementById(id);
const t = CUT.t;
let lastError = "";
let settings = CUT.settings();

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

function translatePage() {
  document.documentElement.lang = settings.lang === "auto" ? navigator.language : settings.lang;
  for (const n of document.querySelectorAll("[data-i18n]")) n.textContent = t(n.dataset.i18n);
  for (const n of document.querySelectorAll("[data-i18n-title]")) n.title = t(n.dataset.i18nTitle);
}

function drawLimits(usage) {
  const box = $("limits");
  box.replaceChildren();
  $("updated").textContent = "";
  const list = usage && usage.limits ? usage.limits.filter((l) => !settings.hidden.includes(l.key)) : [];
  if (!usage || !usage.limits || !usage.limits.length) {
    box.appendChild(el("p", "muted", lastError ? `${lastError}. ${t("signIn")}` : t("noData")));
    return;
  }
  for (const l of list) {
    const row = el("div", "limit");
    const top = el("div", "top");
    const name = el("span", null, CUT.label(l));
    name.appendChild(el("span", "pct " + CUT.color(l.pct), Math.round(l.pct) + "%"));
    top.append(name, el("span", "muted", l.note || (l.resetsAt ? t("resetsIn", { t: CUT.untilText(l.resetsAt) }) : "")));
    const track = el("div", "track");
    const fill = el("div", "fill " + CUT.color(l.pct));
    fill.style.width = Math.min(100, l.pct) + "%";
    track.appendChild(fill);
    row.append(top, track);
    if (l.resetsAt) row.title = new Date(l.resetsAt).toLocaleString();
    box.appendChild(row);
  }
  const mins = Math.round((Date.now() - usage.fetchedAt) / 60000);
  $("updated").textContent = (mins < 1 ? t("updatedNow") : t("updatedMin", { n: mins })) + (lastError ? ` · ${lastError}` : "");
}

function drawChat(chat) {
  // The last chat you had open, if it was in the last 30 minutes.
  if (!chat || Date.now() - chat.at > 30 * 60000) { $("chatBox").hidden = true; return; }
  $("chatBox").hidden = false;
  $("chatTitle").textContent = chat.title || t("untitled");
  $("chatTokens").textContent = `${chat.estimated ? "≈" : ""}${CUT.fmtTokens(chat.tokens)} / ${CUT.fmtTokens(chat.max || CUT.CONTEXT)}`;
  const cached = chat.cachedUntil && chat.cachedUntil > Date.now();
  $("chatCache").textContent = cached ? t("tLeft", { t: CUT.untilText(chat.cachedUntil) }) : t("expired");
  $("chatCache").className = cached ? "green" : "";
  $("chatNext").textContent = chat.nextPct == null ? t("learning") : t("ofSession", { p: chat.nextPct < 0.1 ? "<0.1" : chat.nextPct.toFixed(1) });
  $("chatLeft").textContent = chat.left == null ? t("learning") : chat.left >= 1000 ? "1000+" : "~" + chat.left;
}

function drawSettings(usage) {
  for (const box of document.querySelectorAll("input[data-key]")) box.checked = !!settings[box.dataset.key];
  $("notifyAt").value = String(settings.notifyAt);
  $("lang").replaceChildren(...Object.entries(CUT.LANGS).map(([code, name]) => {
    const o = el("option", null, name || t("sAuto"));
    o.value = code;
    o.selected = settings.lang === code;
    return o;
  }));
  if (document.activeElement !== $("apiKey")) $("apiKey").value = settings.apiKey || "";
  // One switch per limit claude.ai reports for this account.
  const bars = $("bars");
  bars.replaceChildren();
  for (const l of (usage && usage.limits) || []) {
    const lab = el("label");
    const box = el("input");
    box.type = "checkbox";
    box.checked = !settings.hidden.includes(l.key);
    box.addEventListener("change", () => {
      const hidden = settings.hidden.filter((k) => k !== l.key);
      if (!box.checked) hidden.push(l.key);
      save({ hidden });
    });
    lab.append(box, " " + CUT.label(l));
    bars.appendChild(lab);
  }
}

async function load() {
  const r = await chrome.storage.local.get(["usage", "usageByOrg", "orgs", "orgId", "chat", "settings", "update"]);
  settings = CUT.settings(r.settings);
  CUT.setLang(settings.lang);
  translatePage();
  // A newer version on GitHub (only for copies installed from a zip; store copies update themselves).
  const up = r.update && r.update.version && r.update.version !== chrome.runtime.getManifest().version ? r.update : null;
  $("update").hidden = !up;
  if (up) { $("update").textContent = t("updateAvail", { v: up.version }); $("update").href = up.page; }
  // Several accounts (personal, team…): pick which one to look at. Default: the one open on claude.ai.
  const orgs = r.orgs || [];
  const view = orgs.some((o) => o.uuid === settings.viewOrg) ? settings.viewOrg : r.orgId;
  $("orgRow").hidden = orgs.length < 2;
  $("org").replaceChildren(...orgs.map((o, i) => {
    const opt = el("option", null, o.name || `${t("account")} ${i + 1}`);
    opt.value = o.uuid;
    opt.selected = o.uuid === view;
    return opt;
  }));
  const usage = (view && r.usageByOrg && r.usageByOrg[view]) || r.usage;
  drawLimits(usage);
  drawChat(view === r.orgId ? r.chat : null);
  drawSettings(usage);
}

async function save(patch) {
  settings = { ...settings, ...patch };
  await chrome.storage.local.set({ settings });
}

async function refresh() {
  $("refresh").classList.add("spin");
  try {
    const r = await chrome.runtime.sendMessage({ type: "refresh" });
    lastError = r && !r.ok ? r.error : "";
  } catch (e) { lastError = t("unreachable"); }
  $("refresh").classList.remove("spin");
  await load();
}

document.addEventListener("change", (e) => {
  const key = e.target && e.target.dataset && e.target.dataset.key;
  if (key) save({ [key]: e.target.checked });
});
$("notifyAt").addEventListener("change", (e) => save({ notifyAt: Number(e.target.value) }));
$("lang").addEventListener("change", (e) => save({ lang: e.target.value }));
$("org").addEventListener("change", (e) => save({ viewOrg: e.target.value }));
$("saveKey").addEventListener("click", async () => {
  const key = $("apiKey").value.trim();
  if (key && !CUT.validKey(key)) { $("keyMsg").textContent = t("keyBad"); return; }
  // Ask for access to Anthropic's API only when a key is actually added.
  if (key && chrome.permissions) {
    // Firefox also asks the user to allow sending the chat's text (its built-in data consent).
    const isFirefox = typeof browser !== "undefined" && /Firefox\//.test(navigator.userAgent);
    const want = isFirefox
      ? { origins: ["https://api.anthropic.com/*"], data_collection: ["websiteContent"] }
      : { origins: ["https://api.anthropic.com/*"] };
    const ok = await chrome.permissions.request(want).catch(() => false);
    if (!ok) return;
  }
  await save({ apiKey: key });
  $("keyMsg").textContent = t("saved");
});

$("refresh").addEventListener("click", refresh);
$("ver").textContent = "v" + chrome.runtime.getManifest().version;
chrome.storage.onChanged.addListener((ch, area) => {
  if (area === "local" && (ch.usage || ch.usageByOrg || ch.chat || ch.settings || ch.orgs || ch.update)) load();
});
load().then(refresh);

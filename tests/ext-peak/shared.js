/* Helpers shared by the page script, the popup and the background worker. */
(function () {
  "use strict";
  const CUT = (globalThis.CUT = globalThis.CUT || {});

  CUT.WARN_AT = 90;                    // bars turn red from this percentage
  CUT.CACHE_MS = 5 * 60 * 1000;        // claude.ai keeps a chat in the prompt cache for about 5 minutes
  CUT.CONTEXT = 200000;                // context window when the model isn't known
  CUT.DEFAULT_SETTINGS = {
    sidebar: true, composer: true, badge: true, collapsed: false,
    hidden: [],                        // limit keys switched off
    notify: true, notifyAt: 90,        // reset notification for limits that got at least this high
    lang: "auto", apiKey: "", viewOrg: null,
  };
  CUT.settings = (s) => ({ ...CUT.DEFAULT_SETTINGS, ...(s || {}) });

  /* ---------------------------------------------------------- translations (strings in i18n.js) */

  let lang = "en";
  CUT.setLang = (l) => {
    const STR = CUT.STR || {};
    const nav = ((globalThis.navigator && navigator.language) || "en").toLowerCase();
    const want = l && l !== "auto" ? l : nav.slice(0, 2);
    lang = STR[want] ? want : "en";
  };
  CUT.t = (key, vars) => {
    const STR = CUT.STR || { en: {} };
    let s = (STR[lang] && STR[lang][key]) || STR.en[key] || key;
    if (vars) for (const k in vars) s = s.split("{" + k + "}").join(String(vars[k]));
    return s;
  };
  CUT.setLang("auto");

  /* ---------------------------------------------------------- peak hours */

  // Anthropic: on weekdays 5–11 AM Pacific the 5-hour session limit runs down faster.
  const PEAK_START = 5, PEAK_END = 11;
  function pacific(date) {
    const parts = new Intl.DateTimeFormat("en-US", { timeZone: "America/Los_Angeles", weekday: "short", hour: "numeric", hourCycle: "h23" })
      .formatToParts(date);
    const get = (t) => (parts.find((p) => p.type === t) || {}).value;
    return { day: get("weekday"), hour: Number(get("hour")) };
  }
  CUT.isPeak = (date = new Date()) => { return true;
    const p = pacific(date);
    return p.day !== "Sat" && p.day !== "Sun" && p.hour >= PEAK_START && p.hour < PEAK_END;
  };
  /** Today's peak window in the user's own clock, e.g. { start: "6:30 PM", end: "12:30 AM" }. */
  CUT.peakLocal = (date = new Date()) => {
    // Pacific is UTC-8 or UTC-7: find which one applies today by checking the Pacific hour of noon UTC.
    const noon = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate(), 12));
    const offset = 12 - pacific(noon).hour;               // 7 or 8
    const at = (h) => new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate(), h + offset));
    const fmt = (d) => d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    return { start: fmt(at(PEAK_START)), end: fmt(at(PEAK_END)) };
  };

  /* ---------------------------------------------------------- context window */

  // claude.ai chat context windows on paid plans, from Claude's help centre
  // (support.claude.com article 8606394, read 2026-11-23). Anything not listed: 200K.
  const CONTEXT = {
    "fable 5.2": 1e6, "fable 5.1": 1e6, "fable 5": 5e5,
    "mythos 5.2": 1e6, "mythos 5.1": 1e6, "mythos 5": 5e5,
    "opus 5.5": 1e6, "opus 5": 1e6, "opus 4.8": 5e5, "opus 4.7": 5e5, "opus 4.6": 5e5,
    "sonnet 5.5": 1e6, "sonnet 5": 1e6, "sonnet 4.6": 5e5,
    "haiku 5.5": 1e6,
  };
  /** "claude-opus-5-5", "claude-opus-4-5-20251101" or "Opus 5.5" -> "opus 5.5" / "opus 4.5". */
  CUT.modelKey = (model) => {
    const m = /(opus|sonnet|haiku|fable|mythos)[\s-]*(\d{1,2})(?:[.-](\d{1,2})(?!\d))?/i.exec(model || "");
    if (!m) return null;
    return m[1].toLowerCase() + " " + m[2] + (m[3] ? "." + m[3] : "");
  };
  CUT.contextWindow = (model) => CONTEXT[CUT.modelKey(model)] || CUT.CONTEXT;

  /* ---------------------------------------------------------- usage data */

  CUT.label = (l) => {
    if (l.key === "session") return CUT.t("session");
    if (l.key === "weekly") return CUT.t("weekly");
    if (l.key === "extra") return CUT.t("extra");
    if (l.model) return CUT.t("weeklyOf", { m: l.model });
    return l.key;
  };

  const time = (v) => (v ? (typeof v === "number" ? (v < 1e12 ? v * 1000 : v) : Date.parse(v)) || null : null);

  /** Turns claude.ai's /usage answer into a list of { key, model?, pct, resetsAt, note? }.
      Understands both the newer `limits` array and the older five_hour / seven_day fields. */
  CUT.parseUsage = function (u) {
    const out = [];
    if (!u || typeof u !== "object") return out;
    const add = (key, pct, resetsAt, model) => {
      if (typeof pct !== "number" || isNaN(pct) || out.some((l) => l.key === key)) return;
      const l = { key, pct: Math.max(0, Math.min(100, pct)), resetsAt: time(resetsAt) };
      if (model) l.model = model;
      out.push(l);
    };

    if (Array.isArray(u.limits)) {
      for (const l of u.limits) {
        if (!l) continue;
        if (l.kind === "session") add("session", l.percent, l.resets_at);
        else if (l.kind === "weekly_all") add("weekly", l.percent, l.resets_at);
        else if (l.kind === "weekly_scoped") {
          const name = (l.scope && l.scope.model && l.scope.model.display_name) || "Model";
          add("weekly_" + name.toLowerCase().replace(/[^a-z0-9]+/g, "_"), l.percent, l.resets_at, name);
        }
      }
    }
    const old = (field, key, model) => u[field] && add(key, u[field].utilization, u[field].resets_at, model);
    old("five_hour", "session");
    old("seven_day", "weekly");
    old("seven_day_opus", "weekly_opus", "Opus");
    old("seven_day_sonnet", "weekly_sonnet", "Sonnet");

    // Extra usage (pay-as-you-go credits), in cents.
    const spend = u.spend && u.spend.enabled ? u.spend : null;
    const extra = !spend && u.extra_usage && u.extra_usage.is_enabled ? u.extra_usage : null;
    const used = spend ? spend.used && spend.used.amount_minor : extra && extra.used_credits;
    const cap = spend ? (spend.limit && spend.limit.amount_minor) || (spend.cap && spend.cap.money && spend.cap.money.amount_minor) : extra && extra.monthly_limit;
    if ((spend || extra) && cap > 0) {
      out.push({ key: "extra", pct: Math.min(100, ((used || 0) / cap) * 100), resetsAt: null,
        note: `$${((used || 0) / 100).toFixed(2)} / $${(cap / 100).toFixed(2)}` });
    }
    return out;
  };

  /** The reply stream's `windows` ({ "5h": {utilization 0-1, resets_at seconds}, "7d": ... }). */
  CUT.parseStreamWindows = function (limit) {
    const w = limit && limit.windows;
    const out = [];
    if (!w) return out;
    const one = (name, key) => {
      const x = w[name];
      if (!x || typeof x.utilization !== "number") return;
      out.push({ key, pct: x.status === "exceeded_limit" ? 100 : Math.round(x.utilization * 100), resetsAt: time(x.resets_at) });
    };
    one("5h", "session");
    one("7d", "weekly");
    return out;
  };

  CUT.family = (model) => {
    const m = /(opus|sonnet|haiku|fable|mythos)/i.exec(model || "");
    return m ? m[1].toLowerCase() : "default";
  };

  CUT.untilText = function (ts, now = Date.now()) {
    if (!ts) return "";
    const diff = ts - now;
    if (diff <= 0) return CUT.t("resetting");
    const mins = Math.ceil(diff / 60000);
    const d = Math.floor(mins / 1440), h = Math.floor((mins % 1440) / 60), m = mins % 60;
    if (d) return `${d}d ${h}h`;
    if (h) return `${h}h ${m}m`;
    return `${m}m`;
  };

  CUT.fmtTokens = function (n) {
    if (n == null) return "–";
    const short = (x, digits) => x.toFixed(digits).replace(/\.0$/, "");
    if (n >= 1e6) return short(n / 1e6, n >= 1e7 ? 0 : 1) + "M";
    if (n >= 1e3) return short(n / 1e3, n >= 1e5 ? 0 : 1) + "k";
    return String(Math.round(n));
  };

  CUT.color = (pct) => (pct >= CUT.WARN_AT ? "red" : pct >= 70 ? "amber" : "blue");
  CUT.validKey = (k) => /^sk-ant-[A-Za-z0-9_-]{20,}$/.test(k || "");
})();

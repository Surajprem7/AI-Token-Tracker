/* Helpers shared by the page script, the popup and the background worker. */
(function () {
  "use strict";
  const CUT = (globalThis.CUT = globalThis.CUT || {});

  CUT.WARN_AT = 90;                    // bars turn red from this percentage
  CUT.CACHE_MS = 5 * 60 * 1000;        // claude.ai keeps a chat in the prompt cache for about 5 minutes
  CUT.CONTEXT = 200000;                // context window of a regular claude.ai chat
  CUT.DEFAULT_SETTINGS = {
    sidebar: true, composer: true, badge: true, collapsed: false,
    hidden: [],                        // limit keys switched off
    notify: true, notifyAt: 90,        // reset notification for limits that got at least this high
    lang: "auto", apiKey: "",
  };
  CUT.settings = (s) => ({ ...CUT.DEFAULT_SETTINGS, ...(s || {}) });

  /* ---------------------------------------------------------- translations */

  const STR = {
    en: {
      usage: "Usage", session: "Session (5h)", weekly: "Weekly", extra: "Extra usage", weeklyOf: "Weekly · {m}",
      loading: "Loading your usage…", noLimits: "claude.ai reports no limits yet. Send a message to see them.",
      loadFailed: "Couldn't load usage. Reload the page or check you're signed in.",
      updatedNow: "Updated just now", updatedMin: "Updated {n} min ago", resetting: "resetting…", resetsIn: "resets in {t}",
      chat: "Chat", tokens: "tokens", cached: "Cached {t}", notCached: "Not cached", next: "Next ≈", msgsLeft: "msgs left",
      learning: "learning…", msgsLeftLearning: "msgs left: learning…",
      tipSession: "How much of your 5-hour session limit is used, and when it resets",
      tipLength: "Length of this chat (what Claude re-reads with every message) out of the {max}-token context window.",
      tipEstimated: "Images, files or project knowledge are estimated.", tipExact: "Counted exactly with your Anthropic API key.",
      tipSystem: "Claude's own system prompt isn't included.",
      tipCached: "Claude still has this chat in its cache: a message sent now uses much less of your limit.",
      tipNotCached: "The cache has expired: the next message re-reads the whole chat at full cost.",
      tipNext: "Rough share of your session limit the next message in this chat will use",
      tipLeft: "About how many more messages like the next one fit in your limits, learned from your recent messages",
      tipLearning: "After a few messages with this model, the tracker learns how much each one uses and shows how many you have left",
      popupTitle: "Claude usage", refresh: "Refresh now", thisChat: "This chat", untitled: "Untitled chat",
      length: "Length", cache: "Cache", nextMsg: "Next message", left: "Messages left", tLeft: "{t} left", expired: "expired",
      ofSession: "{p}% of session", noData: "No usage yet. Open claude.ai once and it will appear here.",
      signIn: "Open claude.ai and make sure you're signed in.", settings: "Settings",
      sSidebar: "Show usage in claude.ai's sidebar", sComposer: "Show the line under the message box",
      sBadge: "Show session % on the toolbar icon", sBars: "Bars to show", sNotify: "Notify me when a limit resets",
      sNotifyAt: "only if it reached", sLang: "Language", sAuto: "Automatic", sKey: "Anthropic API key (optional, exact token counts)",
      sKeyHint: "Used only to count tokens (free). The chat text is sent to Anthropic's token counter.",
      save: "Save", saved: "Saved", keyBad: "That doesn't look like an Anthropic key (sk-ant-…)",
      usagePage: "claude.ai usage page", notifTitle: "Claude limit reset", notifBody: "Your {l} limit has reset.",
      notSignedIn: "Not signed in to claude.ai", unreachable: "Couldn't reach claude.ai",
    },
    hi: {
      usage: "उपयोग", session: "सत्र (5 घं)", weekly: "साप्ताहिक", extra: "अतिरिक्त उपयोग", weeklyOf: "साप्ताहिक · {m}",
      loading: "आपका उपयोग लोड हो रहा है…", noLimits: "claude.ai ने अभी कोई सीमा नहीं बताई। देखने के लिए एक संदेश भेजें।",
      loadFailed: "उपयोग लोड नहीं हुआ। पेज रीलोड करें या देखें कि आप साइन इन हैं।",
      updatedNow: "अभी अपडेट हुआ", updatedMin: "{n} मिनट पहले अपडेट हुआ", resetting: "रीसेट हो रहा है…", resetsIn: "{t} में रीसेट",
      chat: "चैट", tokens: "टोकन", cached: "कैश {t}", notCached: "कैश नहीं", next: "अगला ≈", msgsLeft: "संदेश बाकी",
      learning: "सीख रहा है…", msgsLeftLearning: "बाकी संदेश: सीख रहा है…",
      tipSession: "आपकी 5 घंटे की सत्र सीमा का कितना उपयोग हुआ, और यह कब रीसेट होगी",
      tipLength: "इस चैट की लंबाई (जिसे Claude हर संदेश पर दोबारा पढ़ता है), {max} टोकन की संदर्भ सीमा में से।",
      tipEstimated: "चित्र, फ़ाइलें या प्रोजेक्ट ज्ञान अनुमानित हैं।", tipExact: "आपकी Anthropic API कुंजी से सटीक गिना गया।",
      tipSystem: "Claude का अपना सिस्टम प्रॉम्प्ट शामिल नहीं है।",
      tipCached: "यह चैट अभी Claude के कैश में है: अभी भेजा गया संदेश आपकी सीमा का बहुत कम उपयोग करेगा।",
      tipNotCached: "कैश समाप्त हो गया: अगला संदेश पूरी चैट को पूरी कीमत पर दोबारा पढ़ेगा।",
      tipNext: "इस चैट का अगला संदेश आपकी सत्र सीमा का लगभग कितना हिस्सा लेगा",
      tipLeft: "आपके हाल के संदेशों से सीखा गया: ऐसे और कितने संदेश आपकी सीमा में आएँगे",
      tipLearning: "इस मॉडल के साथ कुछ संदेशों के बाद ट्रैकर सीख लेता है कि हर संदेश कितना लेता है",
      popupTitle: "Claude उपयोग", refresh: "अभी रीफ़्रेश करें", thisChat: "यह चैट", untitled: "बिना नाम की चैट",
      length: "लंबाई", cache: "कैश", nextMsg: "अगला संदेश", left: "बाकी संदेश", tLeft: "{t} बाकी", expired: "समाप्त",
      ofSession: "सत्र का {p}%", noData: "अभी कोई उपयोग नहीं। एक बार claude.ai खोलें, फिर यहाँ दिखेगा।",
      signIn: "claude.ai खोलें और देखें कि आप साइन इन हैं।", settings: "सेटिंग्स",
      sSidebar: "claude.ai के साइडबार में उपयोग दिखाएँ", sComposer: "संदेश बॉक्स के नीचे लाइन दिखाएँ",
      sBadge: "टूलबार आइकन पर सत्र % दिखाएँ", sBars: "दिखाने वाले बार", sNotify: "सीमा रीसेट होने पर सूचना दें",
      sNotifyAt: "केवल अगर यह पहुँची हो", sLang: "भाषा", sAuto: "स्वचालित", sKey: "Anthropic API कुंजी (वैकल्पिक, सटीक टोकन गिनती)",
      sKeyHint: "केवल टोकन गिनने के लिए (मुफ़्त)। चैट का टेक्स्ट Anthropic के टोकन काउंटर को भेजा जाता है।",
      save: "सहेजें", saved: "सहेजा गया", keyBad: "यह Anthropic कुंजी जैसी नहीं लगती (sk-ant-…)",
      usagePage: "claude.ai उपयोग पेज", notifTitle: "Claude सीमा रीसेट", notifBody: "आपकी {l} सीमा रीसेट हो गई है।",
      notSignedIn: "claude.ai में साइन इन नहीं है", unreachable: "claude.ai तक नहीं पहुँच सके",
    },
    es: {
      usage: "Uso", session: "Sesión (5 h)", weekly: "Semanal", extra: "Uso adicional", weeklyOf: "Semanal · {m}",
      loading: "Cargando tu uso…", noLimits: "claude.ai aún no muestra límites. Envía un mensaje para verlos.",
      loadFailed: "No se pudo cargar el uso. Recarga la página o comprueba que has iniciado sesión.",
      updatedNow: "Actualizado ahora", updatedMin: "Actualizado hace {n} min", resetting: "reiniciando…", resetsIn: "se reinicia en {t}",
      chat: "Chat", tokens: "tokens", cached: "En caché {t}", notCached: "Sin caché", next: "Siguiente ≈", msgsLeft: "mensajes restantes",
      learning: "aprendiendo…", msgsLeftLearning: "mensajes restantes: aprendiendo…",
      tipSession: "Cuánto has usado de tu límite de sesión de 5 horas y cuándo se reinicia",
      tipLength: "Longitud de este chat (lo que Claude relee en cada mensaje) sobre una ventana de contexto de {max} tokens.",
      tipEstimated: "Las imágenes, archivos o conocimiento del proyecto son estimados.", tipExact: "Contado con exactitud con tu clave de API de Anthropic.",
      tipSystem: "No incluye el prompt de sistema de Claude.",
      tipCached: "Claude aún tiene este chat en caché: un mensaje ahora usa mucho menos de tu límite.",
      tipNotCached: "La caché ha caducado: el próximo mensaje relee todo el chat a coste completo.",
      tipNext: "Parte aproximada de tu límite de sesión que usará el próximo mensaje",
      tipLeft: "Cuántos mensajes como el próximo caben aún en tus límites, aprendido de tus mensajes recientes",
      tipLearning: "Tras unos mensajes con este modelo, el tracker aprende cuánto usa cada uno",
      popupTitle: "Uso de Claude", refresh: "Actualizar ahora", thisChat: "Este chat", untitled: "Chat sin título",
      length: "Longitud", cache: "Caché", nextMsg: "Próximo mensaje", left: "Mensajes restantes", tLeft: "quedan {t}", expired: "caducada",
      ofSession: "{p}% de la sesión", noData: "Aún no hay uso. Abre claude.ai una vez y aparecerá aquí.",
      signIn: "Abre claude.ai y comprueba que has iniciado sesión.", settings: "Ajustes",
      sSidebar: "Mostrar el uso en la barra lateral de claude.ai", sComposer: "Mostrar la línea bajo el cuadro de mensaje",
      sBadge: "Mostrar el % de sesión en el icono", sBars: "Barras visibles", sNotify: "Avisarme cuando se reinicie un límite",
      sNotifyAt: "solo si llegó a", sLang: "Idioma", sAuto: "Automático", sKey: "Clave de API de Anthropic (opcional, tokens exactos)",
      sKeyHint: "Solo para contar tokens (gratis). El texto del chat se envía al contador de tokens de Anthropic.",
      save: "Guardar", saved: "Guardado", keyBad: "No parece una clave de Anthropic (sk-ant-…)",
      usagePage: "página de uso de claude.ai", notifTitle: "Límite de Claude reiniciado", notifBody: "Tu límite {l} se ha reiniciado.",
      notSignedIn: "No has iniciado sesión en claude.ai", unreachable: "No se pudo conectar con claude.ai",
    },
  };
  CUT.LANGS = { auto: null, en: "English", hi: "हिन्दी", es: "Español" };
  let lang = "en";
  CUT.setLang = (l) => {
    const want = l && l !== "auto" ? l : (globalThis.navigator && navigator.language || "en").slice(0, 2).toLowerCase();
    lang = STR[want] ? want : "en";
  };
  CUT.t = (key, vars) => {
    let s = (STR[lang] && STR[lang][key]) || STR.en[key] || key;
    if (vars) for (const k in vars) s = s.split("{" + k + "}").join(String(vars[k]));
    return s;
  };
  CUT.setLang("auto");

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
    if (n >= 1e6) return (n / 1e6).toFixed(n >= 1e7 ? 0 : 1) + "M";
    if (n >= 1e3) return (n / 1e3).toFixed(n >= 1e5 ? 0 : 1) + "k";
    return String(Math.round(n));
  };

  CUT.color = (pct) => (pct >= CUT.WARN_AT ? "red" : pct >= 70 ? "amber" : "blue");
  CUT.validKey = (k) => /^sk-ant-[A-Za-z0-9_-]{20,}$/.test(k || "");
})();

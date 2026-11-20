"use strict";
// aiklips app shell, shared by home, settings and the editor:
//  - the native menu bar's commands (window.aiklipsMenu, called from editor/window.py)
//  - the first-run welcome tour and "what's new" after an update (state in ~/.aiklips/ui.json via /api/app)
//  - icons, stacked toasts, UI preferences (calm motion)
(function () {
  const AK = (window.AK = window.AK || {});

  // ---------- tiny DOM helper ----------
  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else if (k === "class") el.className = v;
      else if (k === "html") el.innerHTML = v;
      else if (k === "style" && typeof v === "object") for (const [sk, sv] of Object.entries(v)) sk.startsWith("--") ? el.style.setProperty(sk, sv) : (el.style[sk] = sv);
      else el.setAttribute(k, v);
    }
    for (const c of kids.flat(Infinity)) if (c != null && c !== false) el.append(c.nodeType ? c : String(c));
    return el;
  }
  AK.h = h;

  // ---------- icons (lucide-style strokes) ----------
  const P = {
    home: '<path d="M3 10.5 12 3l9 7.5"/><path d="M5 9.5V20a1 1 0 0 0 1 1h4v-6h4v6h4a1 1 0 0 0 1-1V9.5"/>',
    folder: '<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/>',
    film: '<rect width="18" height="18" x="3" y="3" rx="2"/><path d="M7 3v18M17 3v18M3 7.5h4M3 12h18M3 16.5h4M17 7.5h4M17 16.5h4"/>',
    spark: '<path d="M12 3v18M3 12h18M5.6 5.6l12.8 12.8M18.4 5.6 5.6 18.4"/>',
    wand: '<path d="m21.64 3.64-1.28-1.28a1.21 1.21 0 0 0-1.72 0L2.36 18.64a1.21 1.21 0 0 0 0 1.72l1.28 1.28a1.2 1.2 0 0 0 1.72 0L21.64 5.36a1.2 1.2 0 0 0 0-1.72"/><path d="m14 7 3 3M5 6v4M19 14v4M10 2v2M7 8H3M21 16h-4M11 3H9"/>',
    layers: '<path d="m12.83 2.18 8.58 3.9a1 1 0 0 1 0 1.83l-8.58 3.9a2 2 0 0 1-1.66 0L2.6 7.91a1 1 0 0 1 0-1.83l8.58-3.9a2 2 0 0 1 1.66 0Z"/><path d="m2 12 9.17 4.17a2 2 0 0 0 1.66 0L22 12"/><path d="m2 17 9.17 4.17a2 2 0 0 0 1.66 0L22 17"/>',
    gear: '<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/>',
    plus: '<path d="M5 12h14M12 5v14"/>',
    upload: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m17 8-5-5-5 5"/><path d="M12 3v12"/>',
    download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 10 5 5 5-5"/><path d="M12 15V3"/>',
    search: '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
    grid: '<rect width="7" height="7" x="3" y="3" rx="1.5"/><rect width="7" height="7" x="14" y="3" rx="1.5"/><rect width="7" height="7" x="3" y="14" rx="1.5"/><rect width="7" height="7" x="14" y="14" rx="1.5"/>',
    list: '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
    more: '<circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/><circle cx="5" cy="12" r="1"/>',
    play: '<path d="M7 4.5v15a1 1 0 0 0 1.5.86l12.5-7.5a1 1 0 0 0 0-1.72L8.5 3.64A1 1 0 0 0 7 4.5z"/>',
    check: '<path d="M20 6 9 17l-5-5"/>',
    x: '<path d="M18 6 6 18M6 6l12 12"/>',
    arrow: '<path d="M5 12h14M13 6l6 6-6 6"/>',
    back: '<path d="m15 18-6-6 6-6"/>',
    chat: '<path d="M7.9 20A9 9 0 1 0 4 16.1L2 22Z"/>',
    sliders: '<path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M2 14h4M10 8h4M18 16h4"/>',
    timeline: '<rect width="18" height="5" x="3" y="4" rx="1.5"/><rect width="12" height="5" x="3" y="15" rx="1.5"/><path d="M17 13v10"/>',
    monitor: '<rect width="20" height="14" x="2" y="3" rx="2"/><path d="M8 21h8M12 17v4"/>',
    key: '<circle cx="7.5" cy="15.5" r="5.5"/><path d="m21 2-9.6 9.6M15.5 7.5l3 3L22 7l-3-3"/>',
    brush: '<path d="m9.06 11.9 8.07-8.06a2.85 2.85 0 1 1 4.03 4.03l-8.06 8.08"/><path d="M7.07 14.94c-1.66 0-3 1.35-3 3.02 0 1.33-2.5 1.52-2 2.02 1.08 1.1 2.49 2.02 4 2.02 2.2 0 4-1.8 4-4.04a3.01 3.01 0 0 0-3-3.02z"/>',
    refresh: '<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/>',
    help: '<circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3M12 17h.01"/>',
    gift: '<rect x="3" y="8" width="18" height="4" rx="1"/><path d="M12 8v13M19 12v7a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2v-7M7.5 8a2.5 2.5 0 0 1 0-5A4.8 8 0 0 1 12 8a4.8 8 0 0 1 4.5-5 2.5 2.5 0 0 1 0 5"/>',
    login: '<path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4"/><path d="m10 17 5-5-5-5M15 12H3"/>',
    plug: '<path d="M12 22v-5M9 8V2M15 8V2M18 8v5a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V8z"/>',
    clock: '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
    trash: '<path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>',
    copy: '<rect width="14" height="14" x="8" y="8" rx="2"/><path d="M4 16a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2"/>',
    edit: '<path d="M12 20h9"/><path d="M16.38 3.62a1 1 0 0 1 3 3L7.37 18.64a2 2 0 0 1-.86.5l-2.87.84a.5.5 0 0 1-.62-.62l.84-2.87a2 2 0 0 1 .5-.86z"/>',
    info: '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/>',
    eye: '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
    external: '<path d="M15 3h6v6M10 14 21 3M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
    github: '<path d="M15 22v-4a4.8 4.8 0 0 0-1-3.5c3 0 6-2 6-5.5.08-1.25-.27-2.48-1-3.5.28-1.15.28-2.35 0-3.5 0 0-1 0-3 1.5-2.64-.5-5.36-.5-8 0C6 2 5 2 5 2c-.3 1.15-.3 2.35 0 3.5A5.4 5.4 0 0 0 4 9c0 3.5 3 5.5 6 5.5-.39.49-.68 1.05-.85 1.65S8.93 17.38 9 18v4"/><path d="M9 18c-4.51 2-5-2-7-2"/>',
  };
  AK.ICONS = P;
  AK.icon = (name, size = 16, cls = "") => h("span", { class: `ic ${cls}`, style: { width: size + "px", height: size + "px", display: "inline-flex", flex: "none" },
    html: `<svg viewBox="0 0 24 24" width="${size}" height="${size}" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">${P[name] || ""}</svg>` });
  AK.logo = (size = 28, cls = "ak-mark") => h("img", { src: "/static/logo.svg", alt: "", class: cls, width: size, height: size });

  // ---------- toasts ----------
  AK.toast = (msg, err = false, ms) => {
    let box = document.getElementById("ak-toasts");
    if (!box) document.body.append((box = h("div", { id: "ak-toasts", role: "status", "aria-live": "polite" })));
    const t = h("div", { class: "ak-toast" + (err ? " err" : "") }, h("i"), h("span", {}, msg));
    box.append(t);
    while (box.children.length > 3) box.firstChild.remove();
    setTimeout(() => { t.classList.add("out"); setTimeout(() => t.remove(), 240); }, ms || (err ? 5000 : 2400));
  };

  // ---------- app info + prefs ----------
  AK.app = null;
  AK.load = async () => {
    try { AK.app = await (await fetch("/api/app", { cache: "no-store" })).json(); } catch { AK.app = null; }
    applyPrefs();
    return AK.app;
  };
  AK.seen = (body) => fetch("/api/app/seen", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) }).catch(() => {});
  AK.savePrefs = async (prefs) => {
    if (AK.app) AK.app.prefs = { ...(AK.app.prefs || {}), ...prefs };
    applyPrefs();
    await AK.seen({ prefs, version: false });
  };
  function applyPrefs() { document.documentElement.classList.toggle("ak-calm", !!(AK.app && AK.app.prefs && AK.app.prefs.calm)); }

  // ---------- modal shell ----------
  function modal(content, { onClose, closable = true } = {}) {
    const veil = h("div", { class: "ak-veil", role: "dialog", "aria-modal": "true" });
    const sheet = h("div", { class: "ak-sheet" });
    const close = () => {
      veil.classList.add("out"); document.removeEventListener("keydown", esc, true);
      setTimeout(() => veil.remove(), 220); onClose && onClose();
    };
    const esc = (e) => { if (e.key === "Escape" && closable) { e.stopPropagation(); close(); } };
    if (closable) sheet.append(h("button", { class: "ak-x", title: "Close", "aria-label": "Close", onclick: close }, AK.icon("x", 16)));
    sheet.append(content);
    veil.append(sheet);
    veil.addEventListener("mousedown", (e) => { if (e.target === veil && closable) close(); });
    document.addEventListener("keydown", esc, true);
    document.body.append(veil);
    return { veil, sheet, close };
  }
  AK.modal = modal;

  // ---------- confirm / prompt: in our dark sheet, not the webview's white "127.0.0.1 says" box ----------
  function ask(message, { input = null, ok = "OK", cancel = "Cancel", danger = false, title = "" } = {}) {
    return new Promise((resolve) => {
      let done = false, field = null;
      const finish = (v) => { if (done) return; done = true; resolve(v); m.close(); };
      if (input !== null) field = h("input", { class: "ak-ask-in", value: input, onkeydown: (e) => { if (e.key === "Enter") { e.preventDefault(); finish(field.value); } } });
      const body = h("div", { class: "ak-ask" },
        title ? h("h3", {}, title) : "",
        h("p", {}, ...String(message).split("\n").flatMap((ln, i) => (i ? [h("br"), ln] : [ln]))),
        field || "",
        h("div", { class: "ak-ask-btns" },
          h("button", { class: "ghost", onclick: () => finish(input !== null ? null : false) }, cancel),
          h("button", { class: danger ? "primary danger" : "primary", onclick: () => finish(input !== null ? field.value : true) }, ok)));
      const m = modal(body, { onClose: () => { if (!done) { done = true; resolve(input !== null ? null : false); } } });
      m.sheet.classList.add("ak-sheet-sm");
      setTimeout(() => (field || body.querySelector(".primary")).focus(), 30);
      if (field) field.select();
    });
  }
  AK.confirm = (message, opts = {}) => ask(message, opts);
  AK.prompt = (message, value = "", opts = {}) => ask(message, { ...opts, input: value ?? "" });

  const card = (ic, title, body, cls = "", d = 0) => h("div", { class: `ob-card ${cls}`, style: { "--d": d + "ms" } },
    h("span", { class: "ic-b" }, AK.icon(ic, 19)), h("div", { class: "t" }, h("b", {}, title), h("span", {}, body)));

  const choice = (ic, title, body, onclick, cls = "", d = 0) => h("button", { class: `ob-card ob-choice ${cls}`, style: { "--d": d + "ms" }, onclick },
    h("span", { class: "ic-b" }, AK.icon(ic, 19)), h("span", { class: "t" }, h("b", {}, title), h("span", {}, body)), h("span", { class: "go" }, AK.icon("arrow", 16)));

  // ---------- what's new ----------
  AK.whatsNew = (groups, { all = false } = {}) => {
    if (!groups || !groups.length) return;
    const v = (AK.app && AK.app.version) || "";
    const body = h("div", { class: "wn" },
      h("div", { class: "wn-head" }, AK.logo(46, ""), h("div", {},
        h("h2", { html: all ? "What's <em>new</em>" : "aiklips just got <em>better</em>" }),
        h("span", {}, all ? `You're on version ${v}.` : `Updated to version ${v}. Here's what changed.`))));
    let d = 0;
    for (const g of groups) {
      body.append(h("div", { class: "wn-ver" }, `Version ${g.version}`),
        h("div", { class: "wn-list" }, g.items.map((it) => card(it.icon, it.title, it.body, "", (d += 60)))));
    }
    const m = modal(body);
    body.append(h("div", { class: "wn-foot" }, h("button", { class: "primary", onclick: () => m.close() }, "Got it")));
  };

  // ---------- first-run welcome tour ----------
  const FORMATS = [
    { id: "reels", label: "9:16", sub: "Reels · TikTok", w: 18, h: 32 },
    { id: "portrait45", label: "4:5", sub: "Feed post", w: 24, h: 30 },
    { id: "square", label: "1:1", sub: "Square", w: 28, h: 28 },
    { id: "landscape", label: "16:9", sub: "YouTube", w: 36, h: 20 },
  ];
  AK.FORMATS = FORMATS;
  const TOUR = [
    ["prev", "monitor", "Preview", "Plays your edit live. Click it to select text and layers."],
    ["chat", "chat", "AI editor", "Type what you want: “cut the pauses”, “add captions”."],
    ["insp", "sliders", "Inspector", "Fine-tune whatever is selected: colour, motion, sound."],
    ["tl", "timeline", "Timeline", "Drag, trim and split clips by hand. Undo is always there."],
  ];

  AK.welcome = ({ onDone } = {}) => {
    let step = 0, ai = null, poll = null;
    const prefs = (AK.app && AK.app.prefs) || {};
    let fmt = prefs.format || "reels";
    const art = h("div", { class: "ob-art" });
    const bodyEl = h("div", { class: "ob-body" });
    const dots = h("div", { class: "ob-dots" });
    const root = h("div", { class: "ob" }, art, bodyEl);
    const finish = (action) => {
      clearInterval(poll);
      if (AK.app) { AK.app.onboarded = true; AK.app.prefs = { ...(AK.app.prefs || {}), format: fmt }; }
      m.close();
      onDone && onDone(action, fmt);
      AK.seen({ onboarded: true, prefs: { format: fmt } });
    };
    const m = modal(root, { onClose: () => { clearInterval(poll); AK.seen({ onboarded: true }); } });
    const STEPS = 5;

    async function aiStatus() {
      try { ai = await (await fetch("/api/ai/status", { cache: "no-store" })).json(); } catch { ai = null; }
      if (step === 1) paint(false);
    }
    async function act(url, body) {
      try {
        const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
        const j = await r.json();
        if (j && j.opened === false && j.command) AK.toast(`Run this in a terminal: ${j.command}`, false, 8000);
        else AK.toast("A terminal window opened. Finish there and come back.");
      } catch { AK.toast("Couldn't start that. Try Settings → Providers & keys.", true); }
      clearInterval(poll); poll = setInterval(aiStatus, 3000);
    }
    const gotoProviders = () => { clearInterval(poll); AK.seen({ onboarded: true, prefs: { format: fmt } }); location.href = "/settings#providers"; };

    function artFor(i) {
      art.replaceChildren();
      if (i === 3) {
        const mock = h("div", { class: "mock" }, h("i", { "data-k": "insp" }, h("b", {}, "Inspector")), h("i", { "data-k": "prev" }, h("b", {}, "Preview")),
          h("i", { "data-k": "chat" }, h("b", {}, "AI")), h("i", { class: "tl", "data-k": "tl" }, h("b", {}, "Timeline")));
        art.append(h("div", { class: "ob-scene" }, mock));
        let k = 0;
        const cycle = () => { mock.querySelectorAll("i").forEach((x) => x.classList.toggle("hi", x.dataset.k === TOUR[k % 4][0])); k++; };
        cycle(); clearInterval(art._t); art._t = setInterval(() => { if (!art.isConnected) return clearInterval(art._t); cycle(); }, 1400);
      } else {
        clearInterval(art._t);
        art.append(h("div", { class: "ob-glow" }), AK.logo(104, "ob-logo"));
      }
      art.append(dots);
    }

    function paint(animate = true) {
      dots.replaceChildren(...Array.from({ length: STEPS }, (_, i) => h("i", { class: i === step ? "on" : "" })));
      let s;
      const back = step > 0 ? h("button", { class: "ob-skip", onclick: () => go(step - 1) }, "Back") : h("button", { class: "ob-skip", onclick: () => finish("skip") }, "Skip tour");
      const next = (label = "Continue") => h("button", { class: "primary", onclick: () => go(step + 1) }, label, " ", AK.icon("arrow", 15));
      if (step === 0) {
        const says = ["Cut the pauses", "Add captions that pop", "Swap the green screen", "Make it 30 seconds"];
        s = [h("div", { class: "ob-kicker" }, "Welcome to aiklips"),
          h("h2", { html: "Edit video by <em>saying</em> what you want" }),
          h("p", { class: "lead" }, "A real editor with a timeline you can drag, plus an AI you can talk to. Everything renders on your computer; your footage never leaves it."),
          h("div", { class: "ob-say" }, says.map((t, i) => h("span", { style: { "--d": 200 + i * 90 + "ms" } }, `“${t}”`))),
          h("div", { class: "ob-foot" }, back, h("span", { class: "sp" }), next("Get started"))];
      } else if (step === 1) {
        if (!ai) aiStatus();
        const st = ai && ai.step;
        let main;
        if (!ai) main = h("div", { class: "ob-wait" }, h("span", { class: "ak-spin" }), "Checking your AI connection…");
        else if (st === "ready") main = card("check", "Connected", `The AI editor will use ${ai.model_label}.`, "ok");
        else if (st === "install") main = h("div", { style: { display: "grid", gap: "8px" } },
          card("chat", "Use your Claude subscription", "Installs Claude Code (free), then you sign in once."),
          h("div", { style: { display: "flex", gap: "8px", flexWrap: "wrap" } },
            h("button", { class: "primary", onclick: () => act("/api/ai/install") }, AK.icon("download", 15), " Install Claude Code"),
            h("button", { onclick: gotoProviders }, AK.icon("key", 15), " Use an API key instead")),
          poll ? h("div", { class: "ob-wait" }, h("span", { class: "ak-spin" }), "Waiting for the install to finish…") : null);
        else if (st === "login") main = h("div", { style: { display: "grid", gap: "8px" } },
          card("login", "Sign in to Claude", "Opens a sign-in window. Your Claude plan covers the AI's work."),
          h("div", { style: { display: "flex", gap: "8px", flexWrap: "wrap" } },
            h("button", { class: "primary", onclick: () => act("/api/ai/login", { method: "claudeai" }) }, AK.icon("login", 15), " Sign in with Claude"),
            h("button", { onclick: gotoProviders }, AK.icon("key", 15), " Use an API key instead")),
          poll ? h("div", { class: "ob-wait" }, h("span", { class: "ak-spin" }), "Waiting for you to sign in…") : null);
        else main = h("div", { style: { display: "grid", gap: "8px" } },
          card("key", "Add your API key", `${ai.model_label} needs a key before it can work.`),
          h("div", {}, h("button", { class: "primary", onclick: gotoProviders }, AK.icon("key", 15), " Add a key")));
        if (st === "ready") clearInterval(poll);
        s = [h("div", { class: "ob-kicker" }, "Step 1 · Your AI"),
          h("h2", { html: "Connect the <em>brain</em>" }),
          h("p", { class: "lead" }, "aiklips works with Claude, DeepSeek, OpenRouter or any Anthropic-compatible API. You can edit by hand without one, too."),
          main,
          h("div", { class: "ob-foot" }, back, h("span", { class: "sp" }), st === "ready" ? next() : h("button", { onclick: () => go(2) }, "Skip for now"))];
      } else if (step === 2) {
        const opts = h("div", { class: "ob-opts" }, FORMATS.map((f) => h("button", { class: "ob-opt" + (f.id === fmt ? " on" : ""), type: "button",
          onclick: () => { fmt = f.id; paint(false); } }, h("div", { class: "shape", style: { width: f.w + "px", height: f.h + "px" } }), h("b", {}, f.label), h("small", {}, f.sub))));
        s = [h("div", { class: "ob-kicker" }, "Step 2 · Your format"),
          h("h2", { html: "Where will it <em>live?</em>" }),
          h("p", { class: "lead" }, "New projects start in this shape. You can pick another for any project, and change it later."),
          opts, h("div", { class: "ob-foot" }, back, h("span", { class: "sp" }), next())];
      } else if (step === 3) {
        s = [h("div", { class: "ob-kicker" }, "Step 3 · The editor"),
          h("h2", { html: "Four panes, <em>one idea</em>" }),
          h("p", { class: "lead" }, "Talk or drag, whichever is faster. Every change is a normal edit you can undo."),
          h("div", { class: "ob-tour" }, TOUR.map(([, ic, t, d], i) => card(ic, t, d, "", 80 + i * 70))),
          h("div", { class: "ob-foot" }, back, h("span", { class: "sp" }), next())];
      } else {
        s = [h("div", { class: "ob-kicker" }, "All set"),
          h("h2", { html: "Let's make your <em>first edit</em>" }),
          h("p", { class: "lead" }, "Pick how you want to start. You can drop videos on the home screen any time."),
          h("div", { class: "ob-choices" },
            choice("plus", "New project", "Name it and pick the format, then bring in footage.", () => finish("new"), "hot", 60),
            choice("upload", "Import a video", "Choose a clip and aiklips builds the project around it.", () => finish("import"), "", 120),
            choice("home", "Look around first", "Go to the home screen. Replay this tour any time from Help.", () => finish("explore"), "", 180)),
          h("div", { class: "ob-foot" }, back)];
      }
      const box = h("div", { class: "ob-step", style: animate ? {} : { animation: "none" } }, s);
      bodyEl.replaceChildren(box);
    }
    function go(i) {
      const was = step; step = Math.max(0, Math.min(STEPS - 1, i));
      if (was !== step && (was === 3 || step === 3 || was === 0)) artFor(step);
      else dots.replaceChildren(...Array.from({ length: STEPS }, (_, k) => h("i", { class: k === step ? "on" : "" })));
      paint();
    }
    artFor(0); paint();
  };

  // ---------- native menu bar ----------
  // Pages handle what they can by listening for "aiklips:menu" and calling preventDefault(); the rest navigates.
  window.aiklipsMenu = (cmd) => {
    const ev = new CustomEvent("aiklips:menu", { detail: cmd, cancelable: true });
    if (!window.dispatchEvent(ev)) return;
    const go = {
      home: "/", settings: "/settings", updates: "/settings#updates",
      new: "/?do=new", open: "/?do=open", welcome: "/?do=welcome", whatsnew: "/?do=whatsnew",
    }[cmd];
    if (cmd === "welcome") return AK.load().then(() => AK.welcome({ onDone: (a) => { if (a !== "explore") location.href = "/?do=new"; } }));
    if (cmd === "whatsnew") return AK.load().then(() => AK.whatsNew(AK.app && AK.app.all_notes, { all: true }));
    if (go) location.href = go;
  };
})();

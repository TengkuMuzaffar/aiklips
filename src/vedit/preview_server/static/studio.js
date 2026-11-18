// aiklips Design studio: scenes of animated elements on a canvas (Canva-style).
// Everything is drawn live in the browser: shapes as SVG (static/shapes.js), text / images as the export's own sprites
// (/api/sprite), video layers in <video>, motion from static/animate.js (a port of ops/animate.py). Every change is
// an editor action (POST /api/op), so it is undoable, shared with the CLI and visible to the AI.
"use strict";
// AK.h sets `value` as an attribute, which a <textarea> ignores: set the property too
const h = (tag, attrs, ...kids) => {
  const el = AK.h(tag, attrs, ...kids);
  if (attrs && attrs.value != null && (tag === "textarea" || tag === "input")) el.value = attrs.value;
  return el;
};
const withQ = (el, q) => { el.dataset.q = q; return el; };
// Element.append(null) would print "null": drop empty children
const put = (el, ...kids) => el.append(...kids.flat(Infinity).filter((k) => k != null && k !== false));
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const r1 = (v) => Math.round(v * 10) / 10;
const r3 = (v) => Math.round(v * 1000) / 1000;
const toast = (m, err) => AK.toast(m, err);

// ---- icons (24px stroke) -------------------------------------------------------------------------------------
const IC = {
  elements: '<path d="M4 4h7v7H4z"/><circle cx="17.5" cy="7.5" r="3.5"/><path d="M7.5 14l4 6.5h-8z"/><path d="M14 14h7v7h-7z" opacity=".55"/>',
  text: '<path d="M5 6V4h14v2M12 4v16M9 20h6"/>',
  media: '<rect x="3" y="5" width="18" height="14" rx="2.5"/><circle cx="9" cy="10" r="1.7"/><path d="M21 16l-5-5-8 8"/>',
  story: '<rect x="3" y="4" width="7.5" height="7" rx="1.5"/><rect x="13.5" y="4" width="7.5" height="7" rx="1.5"/><rect x="3" y="14" width="7.5" height="6" rx="1.5"/><path d="M14 17h7"/>',
  ai: '<path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z"/><path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z"/>',
  home: '<path d="M4 11l8-7 8 7v9h-5v-6H9v6H4z"/>',
  undo: '<path d="M9 14L4 9l5-5"/><path d="M4 9h10a6 6 0 010 12h-3"/>',
  redo: '<path d="M15 14l5-5-5-5"/><path d="M20 9H10a6 6 0 000 12h3"/>',
  play: '<path d="M7 4.5v15l13-7.5z" fill="currentColor"/>',
  pause: '<path d="M7 4h4v16H7zM14 4h4v16h-4z" fill="currentColor" stroke="none"/>',
  trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
  copy: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a1 1 0 00-1-1H5a1 1 0 00-1 1v10a1 1 0 001 1h3"/>',
  motion: '<circle cx="15" cy="12" r="5"/><path d="M3 9h5M2 12h5M3 15h5"/>',
  layers: '<path d="M12 3l9 5-9 5-9-5z"/><path d="M3 13l9 5 9-5"/>',
  props: '<path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0"/><circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="18" cy="18" r="2"/>',
  clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
  opacity: '<rect x="4" y="4" width="16" height="16" rx="2"/><path d="M4 9h5V4M9 14h5V9M14 20v-6h6M4 20l5-6" stroke-width="1.4"/>',
  spacing: '<path d="M7 5h10M12 5v11M4 20h16M4 20l2-2M4 20l2 2M20 20l-2-2M20 20l-2 2"/>',
  "align-left": '<path d="M4 6h16M4 10h10M4 14h16M4 18h10"/>',
  "align-center": '<path d="M4 6h16M7 10h10M4 14h16M7 18h10"/>',
  "align-right": '<path d="M4 6h16M10 10h10M4 14h16M10 18h10"/>',
  list: '<circle cx="5" cy="7" r="1.2" fill="currentColor"/><circle cx="5" cy="12" r="1.2" fill="currentColor"/><circle cx="5" cy="17" r="1.2" fill="currentColor"/><path d="M9 7h11M9 12h11M9 17h11"/>',
  "list-num": '<path d="M4 5.5h1.5V9M4 9h3M4 14.5c0-1 2.8-1 2.8.5 0 1-2.8 2-2.8 3.5h3M10 7h10M10 12h10M10 17h10"/>',
  border: '<rect x="4" y="4" width="16" height="16" rx="2" stroke-dasharray="3 2.5"/>',
  corner: '<path d="M4 20V12a8 8 0 018-8h8"/>',
  "star-pts": '<path d="M12 3l2.4 5.4 5.6.6-4.2 3.8 1.2 5.7L12 15.6 7 18.5l1.2-5.7L4 9l5.6-.6z"/>',
  flip: '<path d="M12 3v18M8 7L3 12l5 5zM16 7l5 5-5 5z"/>',
  sound: '<path d="M4 9v6h4l5 4V5L8 9zM16.5 8.5a5 5 0 010 7M19 6a8.5 8.5 0 010 12"/>',
  mute: '<path d="M4 9v6h4l5 4V5L8 9zM17 9l5 6M22 9l-5 6"/>',
  more: '<circle cx="5" cy="12" r="1.4" fill="currentColor"/><circle cx="12" cy="12" r="1.4" fill="currentColor"/><circle cx="19" cy="12" r="1.4" fill="currentColor"/>',
  move: '<path d="M12 3v18M3 12h18M12 3l-3 3M12 3l3 3M12 21l-3-3M12 21l3-3M3 12l3-3M3 12l3 3M21 12l-3-3M21 12l-3 3"/>',
  rotate: '<path d="M20 12a8 8 0 11-2.4-5.7M20 4v5h-5"/>',
};
function ic(name, size = 18) {
  const s = h("span", { class: "ic", style: { width: size + "px", height: size + "px", display: "inline-flex" } });
  s.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${IC[name]}</svg>`;
  return s;
}

// ---- state ---------------------------------------------------------------------------------------------------
let ST = null;                 // /api/state
const P = () => ST.project;
let T = 0, PLAYING = false, LAST_TS = 0;
let SEL = [];                  // selected layer ids (SEL[0] = primary)
let VIEW = "elements";         // drawer view
let ZOOM = null;               // null = fit, else screen px per canvas px
let REC = false;               // record motion
const OVR = new Map();         // layer id -> live drag values {x, y, w, h, rotation, sxm, sym}
let PREVIEW = null;            // {id, presets, t0}: hover-preview of an animation preset on the canvas
let BOX = new Map();           // layer id -> last drawn box {cx, cy, w, h, rot} (screen px, relative to #stagewrap)
let K = 0.3;                   // screen px per canvas px
let CVX = 0, CVY = 0;          // canvas offset inside #stagewrap

async function api(url, body, method = "POST") {
  try {
    const r = await fetch(url, { method, headers: { "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body) });
    if (!r.ok) {
      let msg = r.statusText;
      try { msg = (await r.json()).detail || msg; } catch {}
      toast(msg, true);
      return null;
    }
    return await r.json();
  } catch (e) { toast(String(e), true); return null; }
}
let SAVING = 0;
function setSaving(d) { SAVING += d; $("#saved").textContent = SAVING > 0 ? "Saving…" : "Saved"; }
async function op(name, args) {
  setSaving(1);
  const r = await api("/api/op", { op: name, args });
  setSaving(-1);
  await refresh();
  return r;
}
async function batch(ops) {
  ops = ops.filter(Boolean);
  if (!ops.length) return null;
  if (ops.length === 1) return op(ops[0].op, ops[0].args);
  return op("batch", { ops });
}
async function refresh() {
  const r = await fetch("/api/state", { cache: "no-store" });
  if (r.status === 404 || r.redirected) { location.href = "/"; return; }
  if (!r.ok) return;
  ST = await r.json();
  if (ST.project.kind !== "design") { location.href = "/editor"; return; }
  SEL = SEL.filter((id) => P().layers.some((l) => l.id === id));
  T = clamp(T, 0, Math.max(0, dur() - 0.001));
  CONTENT_GEN++;
  renderAll();
}
const dur = () => (ST ? ST.duration : 0);
const layerById = (id) => P().layers.find((l) => l.id === id);
const bgIds = () => new Set(P().scenes.map((s) => s.bg_layer).filter(Boolean));
function sceneStarts() { let t = 0; return P().scenes.map((s) => { const st = t; t += s.dur; return r3(st); }); }
function sceneAt(t) {
  const st = sceneStarts(), sc = P().scenes;
  for (let i = 0; i < sc.length; i++) if (t >= st[i] && t < st[i] + sc[i].dur) return { sc: sc[i], i, start: st[i], end: r3(st[i] + sc[i].dur) };
  const i = sc.length - 1;
  return i >= 0 ? { sc: sc[i], i, start: st[i], end: r3(st[i] + sc[i].dur) } : null;
}
const cur = () => sceneAt(T);
const fmt = (s) => `${Math.floor(s / 60)}:${(s % 60).toFixed(1).padStart(4, "0")}`;
const typing = () => { const a = document.activeElement; return a && (a.tagName === "INPUT" || a.tagName === "TEXTAREA" || a.tagName === "SELECT" || a.isContentEditable); };

// ---- sprites (text / image), as in the footage editor --------------------------------------------------------
const SPRITES = new Map(), SPR_WAIT = new Map();
let SPR_SCALE = 0.5, CONTENT_GEN = 0;
function contentKey(l) {
  return JSON.stringify({ type: l.type, text: l.text, style: l.style, image: l.image, width: l.width, radius: l.radius, bw: l.border_width,
    bc: l.border_color, mask: l.mask, chroma: l.chroma, cs: l.chroma_similarity });
}
function spriteFetch(l, opts) {
  const key = `${contentKey(l)}|${SPR_SCALE}|${opts.reveal ?? ""}`;
  if (SPRITES.has(key) || SPR_WAIT.has(key)) return key;
  const layer = { ...l, keyframes: [], presets: [], sx: 1, sy: 1 };
  const pr = fetch("/api/sprite", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ layer, scale: SPR_SCALE, ...opts }) })
    .then((r) => (r.ok ? r.blob() : null))
    .then((b) => new Promise((res) => {
      if (!b) return res(null);
      const img = new Image();
      img.onload = () => res({ url: img.src, w: img.naturalWidth, h: img.naturalHeight, scale: SPR_SCALE });
      img.onerror = () => res(null);
      img.src = URL.createObjectURL(b);
    }))
    .then((sp) => { SPR_WAIT.delete(key); if (sp) SPRITES.set(key, sp); })
    .catch(() => SPR_WAIT.delete(key));
  SPR_WAIT.set(key, pr);
  return key;
}
function spriteFor(l, pr) {
  if (l.type === "image") return spriteFetch(l, {});
  const total = (l.text || "").split(/\s+/).filter(Boolean).reduce((a, w) => a + w.length, 0);
  const k = VAnim.pyRound(total * pr.reveal);
  return spriteFetch(l, k >= total ? {} : { reveal: k });
}
const srcKey = (id) => { const s = P().sources[id]; let x = 2166136261, t = `${ST.project_dir}|${id}|${s?.path}|${s?.duration}`; for (let i = 0; i < t.length; i++) { x ^= t.charCodeAt(i); x = Math.imul(x, 16777619); } return (x >>> 0).toString(36); };
const isGap = (s) => /\/\.gap-[0-9a-f]+\.mp4$/i.test(s.path) || /[\\/]\.gap-/.test(s.path);
const IMG_EXT = /\.(png|jpe?g|webp|gif|bmp)$/i;

// ---- stage ---------------------------------------------------------------------------------------------------
const LV = new Map(); // layer id -> {el, kind, key, img, vid}
const BLEND = { normal: "normal", screen: "screen", multiply: "multiply", overlay: "overlay", add: "plus-lighter", lighten: "lighten", darken: "darken" };
function layout() {
  const wrap = $("#stagewrap"), cv = P().canvas;
  const W = wrap.clientWidth, H = wrap.clientHeight, pad = 48;
  const fit = Math.min((W - pad * 2) / cv.width, (H - pad * 2) / cv.height);
  K = ZOOM ?? Math.max(0.05, fit);
  const w = cv.width * K, hh = cv.height * K;
  CVX = Math.round((W - w) / 2); CVY = Math.round((H - hh) / 2);
  Object.assign($("#cv").style, { left: CVX + "px", top: CVY + "px", width: w + "px", height: hh + "px", background: cv.background });
  $("#z-val").textContent = Math.round(K * 100) + "%";
  SPR_SCALE = clamp(Math.ceil(K * (window.devicePixelRatio || 1) * 4) / 4, 0.25, 1);
}
function makeEl(l) {
  if (l.type === "video") {
    const vid = h("video", { playsinline: true, preload: "auto", muted: true });
    const bord = h("div", { class: "vl-border" });
    return { el: h("div", { class: "L" }, vid, bord), kind: "video", vid, bord, src: null };
  }
  if (l.type === "shape") return { el: h("div", { class: "L" }), kind: "shape", key: null };
  const img = h("img", { alt: "", draggable: false });
  return { el: h("div", { class: "L" }, img), kind: "img", img, shown: null };
}
// base (unscaled) size in canvas px of a layer, from its type
function baseSize(l, pr, v) {
  if (l.type === "shape") {
    const dh = { line: 16, arrow: 90 }[l.shape] || 400;
    return [pr.w ?? l.width ?? 400, pr.h ?? l.height ?? dh];
  }
  if (l.type === "video") {
    const src = P().sources[l.source], cv = P().canvas;
    const W = l.width || Math.round(cv.width * 0.45), aspect = (src?.height || 1080) / (src?.width || 1920);
    if (l.fit === "cover") return [cv.width, cv.height];
    if (l.fit === "contain") { const bw = aspect <= cv.height / cv.width ? cv.width : cv.height / aspect; return [bw, bw * aspect]; }
    return [W, l.mask === "circle" ? W : W * aspect];
  }
  const sp = v && v.shown && SPRITES.get(v.shown);
  return sp ? [sp.w / sp.scale, sp.h / sp.scale] : null;
}
function propsFor(l, t) {
  const end = l.end == null ? dur() : Math.min(l.end, dur());
  const ldur = Math.max(0, end - l.start);
  if (PREVIEW && PREVIEW.id === l.id) {
    const loopLen = 1.8, pt = ((performance.now() - PREVIEW.t0) / 1000) % loopLen;
    return VAnim.propsAt({ ...l, presets: PREVIEW.presets, keyframes: [] }, pt, loopLen, P().canvas);
  }
  return VAnim.propsAt(l, t - l.start, ldur, P().canvas);
}
function visibleAt(l, t) {
  const end = l.end == null ? dur() : Math.min(l.end, dur());
  return !l.hidden && l.type !== "adjust" && l.type !== "captions" && t >= l.start && t < end && end - l.start > 0.01;
}
function drawStage() {
  if (!ST) return;
  const stage = $("#cv"), seen = new Set(), bgs = bgIds();
  BOX = new Map();
  P().layers.forEach((l, z) => {
    seen.add(l.id);
    let v = LV.get(l.id);
    const want = l.type === "video" ? "video" : l.type === "shape" ? "shape" : "img";
    if (v && v.kind !== want) { v.el.remove(); v = null; }
    if (!v) { v = makeEl(l); LV.set(l.id, v); }
    if (stage.children[z] !== v.el) stage.insertBefore(v.el, stage.children[z] || null);
    v.el.classList.toggle("bgl", bgs.has(l.id));
    const st = v.el.style;
    const show = visibleAt(l, T) || (PREVIEW && PREVIEW.id === l.id);
    if (!show) { st.display = "none"; if (v.vid && !v.vid.paused) v.vid.pause(); return; }
    const pr = propsFor(l, T);
    const o = OVR.get(l.id);
    if (o) {
      if (o.x != null) pr.x = o.x; if (o.y != null) pr.y = o.y; if (o.rotation != null) pr.rotation = o.rotation;
      if (o.w != null) pr.w = o.w; if (o.h != null) pr.h = o.h;
      if (o.sxm) pr.sx *= o.sxm; if (o.sym) pr.sy *= o.sym;
    }
    st.mixBlendMode = BLEND[l.blend] || "normal";
    if (l.type === "shape") {
      const [w, hh] = baseSize(l, pr);
      const key = `${CONTENT_GEN}|${Math.round(w * K)}|${Math.round(hh * K)}|${pr.fill}|${K}`;
      if (v.key !== key) { v.el.innerHTML = VShapes.svg(l, w * K, hh * K, pr.fill, K); v.key = key; }
      place(l, v, st, pr, w, hh);
    } else if (l.type === "video") {
      drawVideo(l, v, pr);
    } else {
      const key = spriteFor(l, pr);
      if (SPRITES.has(key)) v.shown = key;
      const sp = v.shown && SPRITES.get(v.shown);
      if (!sp) { st.display = "none"; return; }
      if (v.img.src !== sp.url) v.img.src = sp.url;
      place(l, v, st, pr, sp.w / sp.scale, sp.h / sp.scale);
    }
  });
  for (const [id, v] of LV) if (!seen.has(id)) { if (v.vid) v.vid.pause(); v.el.remove(); LV.delete(id); }
  drawOverlay();
}
function place(l, v, st, pr, w, hh) {
  const s = pr.scale, W = w * K, H = hh * K;
  if (pr.opacity <= 0.001 || s * pr.sx <= 0.001 || s * pr.sy <= 0.001) { st.display = "none"; return; }
  st.display = "block"; st.width = W + "px"; st.height = H + "px"; st.opacity = pr.opacity;
  st.transform = `translate(${pr.x * K - W / 2}px, ${pr.y * K - H / 2}px) rotate(${pr.rotation}deg) scale(${s * pr.sx}, ${s * pr.sy})`;
  st.clipPath = l.type !== "text" && pr.reveal < 0.999 ? `inset(0 ${(1 - pr.reveal) * 100}% 0 0)` : "";
  BOX.set(l.id, { cx: CVX + pr.x * K, cy: CVY + pr.y * K, w: W * s * pr.sx, h: H * s * pr.sy, rot: pr.rotation, base: [w, hh], pr });
}
function drawVideo(l, v, pr) {
  const src = P().sources[l.source];
  if (!src) return;
  const url = `/api/source/${encodeURIComponent(l.source)}/media?k=${srcKey(l.source)}`;
  if (v.src !== url) { v.src = url; v.vid.src = url; }
  const local = T - l.start, first = src.duration - l.src_in;
  const want = local < first ? l.src_in + local : l.loop ? (local - first) % src.duration : null;
  if (want == null) { v.el.style.display = "none"; return; }
  v.vid.muted = !(l.volume > 0); v.vid.volume = clamp(l.volume || 0, 0, 1);
  if (PLAYING) { if (v.vid.paused) v.vid.play().catch(() => {}); if (Math.abs(v.vid.currentTime - want) > 0.25) v.vid.currentTime = want; }
  else { if (!v.vid.paused) v.vid.pause(); if (Math.abs(v.vid.currentTime - want) > 0.03 && !v.vid.seeking) v.vid.currentTime = want; }
  v.vid.style.transform = `scale(${l.flip_h ? -1 : 1}, ${l.flip_v ? -1 : 1})`;
  const [w, hh] = baseSize(l, pr);
  const s = pr.scale;
  v.el.style.borderRadius = l.mask === "circle" ? "50%" : `${(l.radius || 0) * K}px`;
  v.el.style.overflow = "hidden";
  v.bord.style.border = l.border_width > 0 ? `${l.border_width * K}px solid ${l.border_color}` : "none";
  v.bord.style.borderRadius = "inherit";
  place(l, v, v.el.style, pr, w, hh);
  void s;
}

// ---- selection overlay: box, 8 handles, rotate knob, guides, motion path -------------------------------------
const HANDLES = [[-1, -1], [0, -1], [1, -1], [1, 0], [1, 1], [0, 1], [-1, 1], [-1, 0]];
let GUIDES = [], MARQ = null, DIMS = null;
function drawOverlay() {
  const ov = $("#over");
  ov.innerHTML = "";
  if (document.body.classList.contains("present")) return;
  SEL.forEach((id, i) => {
    const b = BOX.get(id);
    if (!b) return;
    const box = h("div", { class: "selbox" + (i ? " sub" : "") });
    Object.assign(box.style, { width: b.w + "px", height: b.h + "px", transform: `translate(${b.cx - b.w / 2}px, ${b.cy - b.h / 2}px) rotate(${b.rot}deg)` });
    if (i === 0 && SEL.length === 1 && !PLAYING) {
      for (const [hx, hy] of HANDLES) {
        const edge = hx === 0 || hy === 0;
        const d = h("div", { class: "hd" + (edge ? (hx === 0 ? " n" : " e") : ""), "data-h": `${hx},${hy}` });
        Object.assign(d.style, { left: `${(hx + 1) * 50}%`, top: `${(hy + 1) * 50}%`, cursor: cursorFor(hx, hy, b.rot) });
        box.append(d);
      }
      const rot = h("div", { class: "hd rot", "data-h": "rot", title: "Rotate (Shift: 15° steps)" }, ic("rotate", 14));
      Object.assign(rot.style, { left: "calc(50% - 16px)", top: `calc(100% + 28px)` });
      const mv = h("div", { class: "hd rot mv", "data-h": "move", title: "Move" }, ic("move", 14));
      Object.assign(mv.style, { left: "calc(50% + 16px)", top: `calc(100% + 28px)`, cursor: "move" });
      box.append(rot, mv);
      const l = layerById(id);
      if (l && l.keyframes.some((k) => k.x != null || k.y != null)) ov.append(motionPath(l));
    }
    ov.append(box);
  });
  // the floating mini bar above the selection (Canva): duplicate, delete, more
  const boxes = SEL.map((id) => BOX.get(id)).filter(Boolean);
  if (boxes.length && !PLAYING && !OVR.size) {
    let top = Infinity, x0 = Infinity, x1 = -Infinity;
    for (const b of boxes) {
      const a = (b.rot * Math.PI) / 180, hw = (Math.abs(b.w * Math.cos(a)) + Math.abs(b.h * Math.sin(a))) / 2, hh = (Math.abs(b.w * Math.sin(a)) + Math.abs(b.h * Math.cos(a))) / 2;
      top = Math.min(top, b.cy - hh); x0 = Math.min(x0, b.cx - hw); x1 = Math.max(x1, b.cx + hw);
    }
    const more = h("button", { title: "More", onclick: (ev) => menu(ev.currentTarget, [
      ["Properties", () => openView("properties")], ["Animate", () => openView("animate")], ["Effects", () => openView("effects")],
      ["Bring to front", () => batch(SEL.map((id) => ({ op: "layer_move", args: { layer: id, to: "front" } })))],
      ["Send to back", () => batch(SEL.map((id) => ({ op: "layer_move", args: { layer: id, to: "back" } })))],
    ]) }, ic("more", 16));
    const mini = h("div", { class: "mini", style: { left: (x0 + x1) / 2 + "px", top: Math.max(8, top - 14) + "px" } },
      h("button", { title: "Duplicate (⌘D)", onclick: duplicateSel }, ic("copy", 16)),
      h("button", { title: "Delete (⌫)", onclick: deleteSel }, ic("trash", 16)), more);
    ov.append(mini);
  }
  if (BG_SEL && !SEL.length) {  // the selected background: an outline around the whole canvas
    const cv = P().canvas;
    ov.append(h("div", { class: "selbox bgsel", style: { width: cv.width * K + "px", height: cv.height * K + "px", transform: `translate(${CVX}px, ${CVY}px)` } }));
  }
  for (const g of GUIDES) ov.append(h("div", { class: "guide", style: g }));
  if (MARQ) ov.append(h("div", { class: "marquee", style: { left: Math.min(MARQ.x0, MARQ.x1) + "px", top: Math.min(MARQ.y0, MARQ.y1) + "px", width: Math.abs(MARQ.x1 - MARQ.x0) + "px", height: Math.abs(MARQ.y1 - MARQ.y0) + "px" } }));
  if (DIMS) ov.append(h("div", { class: "dims", style: { left: DIMS.x + "px", top: DIMS.y + "px" } }, DIMS.text));
}
function cursorFor(hx, hy, rot) {
  const a = ((Math.atan2(hy, hx) * 180) / Math.PI + rot + 360) % 180;
  return ["ew-resize", "nwse-resize", "ns-resize", "nesw-resize"][Math.round(a / 45) % 4];
}
function motionPath(l) {
  const cv = P().canvas, pts = [];
  const end = l.end == null ? dur() : l.end, ld = end - l.start;
  for (let i = 0; i <= 40; i++) {
    const t = (ld * i) / 40, pr = VAnim.propsAt({ ...l, presets: [] }, t, ld, cv);
    pts.push(`${CVX + pr.x * K},${CVY + pr.y * K}`);
  }
  const dots = l.keyframes.filter((k) => k.x != null || k.y != null).map((k) => {
    const pr = VAnim.propsAt({ ...l, presets: [] }, k.t, ld, cv);
    return `<rect x="${CVX + pr.x * K - 4}" y="${CVY + pr.y * K - 4}" width="8" height="8" transform="rotate(45 ${CVX + pr.x * K} ${CVY + pr.y * K})" fill="#c5f467"/>`;
  }).join("");
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("class", "path");
  s.innerHTML = `<polyline points="${pts.join(" ")}" fill="none" stroke="#c5f467" stroke-width="1.5" stroke-dasharray="4 4" opacity=".8"/>${dots}`;
  return s;
}

// hit test: topmost visible, non-background layer whose rotated box holds the point (stage px)
function hitAt(x, y) {
  const bgs = bgIds(), ls = P().layers;
  for (let i = ls.length - 1; i >= 0; i--) {
    const l = ls[i], b = BOX.get(l.id);
    if (!b || bgs.has(l.id) || !visibleAt(l, T)) continue;
    const a = (-b.rot * Math.PI) / 180, dx = x - b.cx, dy = y - b.cy;
    const lx = dx * Math.cos(a) - dy * Math.sin(a), ly = dx * Math.sin(a) + dy * Math.cos(a);
    if (Math.abs(lx) <= b.w / 2 + 3 && Math.abs(ly) <= b.h / 2 + 3) return l;
  }
  return null;
}
const FOLLOWS = ["animate", "position", "effects", "properties"]; // drawer views that show the selection
function select(ids, keepView) {
  SEL = ids;
  BG_SEL = null;
  if (ids.length) SCENE_SEL = null;
  if (!keepView && !SEL.length && FOLLOWS.includes(VIEW) && VIEW !== "properties") VIEW = "properties";
  renderCtx(); renderTimeline();
  if (FOLLOWS.includes(VIEW)) renderDrawer();
  $("#sel-hint").textContent = SEL.length > 1 ? `${SEL.length} selected` : "";
}

// ---- pointer: move, resize, rotate, marquee ------------------------------------------------------------------
const hasKey = (l, ...props) => l.keyframes.some((k) => props.some((p) => k[p] != null));
const keyed = (l, ...props) => REC || hasKey(l, ...props);
function wrapXY(e) { const r = $("#stagewrap").getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; }

$("#stagewrap").addEventListener("pointerdown", (e) => {
  if (e.button !== 0 || !ST || document.body.classList.contains("present")) return;
  if (e.target.closest("#zoom, .mini")) return;
  const [x, y] = wrapXY(e);
  const hd = e.target.closest(".hd");
  if (hd && hd.dataset.h === "move") return startMove(e, x, y);
  if (hd && SEL.length === 1) return startHandle(e, hd.dataset.h, x, y);
  const l = hitAt(x, y);
  if (!l) {
    if (!e.shiftKey) { SCENE_SEL = null; select([]); }
    MARQ = { x0: x, y0: y, x1: x, y1: y, add: e.shiftKey ? [...SEL] : [] };
    capture(e, (ev) => { const [mx, my] = wrapXY(ev); MARQ.x1 = mx; MARQ.y1 = my; drawOverlay(); }, () => {
      const x0 = Math.min(MARQ.x0, MARQ.x1), x1 = Math.max(MARQ.x0, MARQ.x1), y0 = Math.min(MARQ.y0, MARQ.y1), y1 = Math.max(MARQ.y0, MARQ.y1);
      const ids = x1 - x0 < 4 && y1 - y0 < 4 ? [] : [...BOX.entries()].filter(([id, b]) => !bgIds().has(id) && b.cx > x0 && b.cx < x1 && b.cy > y0 && b.cy < y1).map(([id]) => id);
      const add = MARQ.add, click = x1 - x0 < 4 && y1 - y0 < 4; MARQ = null;
      const cv = P().canvas, onCanvas = x >= CVX && x <= CVX + cv.width * K && y >= CVY && y <= CVY + cv.height * K;
      // a plain click on an empty part of the canvas selects the scene's background (like Canva's page background)
      if (click && !add.length && onCanvas && cur()) { selectBackground(cur().sc.id); return; }
      select([...new Set([...add, ...ids])]); drawOverlay();
    });
    return;
  }
  if (e.shiftKey) { select(SEL.includes(l.id) ? SEL.filter((i) => i !== l.id) : [...SEL, l.id]); return; }
  if (!SEL.includes(l.id)) select([l.id]);
  startMove(e, x, y);
});
function capture(e, move, up) {
  const el = $("#stagewrap");
  el.setPointerCapture(e.pointerId);
  const mv = (ev) => move(ev);
  const fin = (ev) => { el.removeEventListener("pointermove", mv); el.removeEventListener("pointerup", fin); el.removeEventListener("pointercancel", fin); up(ev); };
  el.addEventListener("pointermove", mv); el.addEventListener("pointerup", fin); el.addEventListener("pointercancel", fin);
}
function startMove(e, x0, y0) {
  const cv = P().canvas, start = new Map();
  for (const id of SEL) { const b = BOX.get(id); if (b) start.set(id, { x: b.pr.x, y: b.pr.y, b }); }
  let moved = false;
  capture(e, (ev) => {
    const [x, y] = wrapXY(ev);
    let dx = (x - x0) / K, dy = (y - y0) / K;
    if (!moved && Math.hypot(x - x0, y - y0) < 3) return;
    moved = true;
    // snap the primary layer's centre / edges to the canvas centre / edges and other layers' centres
    GUIDES = [];
    const p0 = start.get(SEL[0]);
    if (p0 && !ev.altKey) {
      const tol = 6 / K, hw = p0.b.w / 2 / K, hh = p0.b.h / 2 / K;
      const xs = [0, cv.width / 2, cv.width], ys = [0, cv.height / 2, cv.height];
      for (const [id, b] of BOX) if (!SEL.includes(id) && !bgIds().has(id)) { xs.push((b.cx - CVX) / K); ys.push((b.cy - CVY) / K); }
      const nx = p0.x + dx, ny = p0.y + dy;
      let bx = null, by = null;
      for (const c of xs) for (const off of [0, -hw, hw]) if (Math.abs(nx + off - c) < tol && (bx == null || Math.abs(nx + off - c) < Math.abs(bx.d))) bx = { d: nx + off - c, c };
      for (const c of ys) for (const off of [0, -hh, hh]) if (Math.abs(ny + off - c) < tol && (by == null || Math.abs(ny + off - c) < Math.abs(by.d))) by = { d: ny + off - c, c };
      if (bx) { dx -= bx.d; GUIDES.push({ left: CVX + bx.c * K + "px", top: CVY + "px", width: "1px", height: cv.height * K + "px" }); }
      if (by) { dy -= by.d; GUIDES.push({ left: CVX + "px", top: CVY + by.c * K + "px", width: cv.width * K + "px", height: "1px" }); }
    }
    for (const [id, s] of start) OVR.set(id, { ...(OVR.get(id) || {}), x: s.x + dx, y: s.y + dy });
    const p = OVR.get(SEL[0]);
    if (p) DIMS = { x: CVX + p.x * K, y: CVY + p.y * K + (p0 ? p0.b.h / 2 : 0) + 10, text: `x ${Math.round(p.x)}  y ${Math.round(p.y)}` };
  }, () => {
    GUIDES = []; DIMS = null;
    if (!moved) { for (const id of start.keys()) OVR.delete(id); return; }
    const ops = [];
    for (const id of start.keys()) {
      const o = OVR.get(id), l = layerById(id);
      if (!o || !l) continue;
      if (keyed(l, "x", "y")) ops.push(kf(l, "x", r1(o.x)), kf(l, "y", r1(o.y)));
      else ops.push({ op: "layer_set", args: { layer: id, x: r1(o.x), y: r1(o.y) } });
    }
    batch(ops).finally(() => { for (const id of start.keys()) OVR.delete(id); });
  });
}
const kf = (l, prop, value) => ({ op: "keyframe_set", args: { layer: l.id, t: r3(clamp(T, l.start, (l.end ?? dur()) - 0.001)), prop, value } });

function startHandle(e, which, x0, y0) {
  const id = SEL[0], l = layerById(id), b = BOX.get(id);
  if (!l || !b) return;
  const rad = (b.rot * Math.PI) / 180, cos = Math.cos(rad), sin = Math.sin(rad);
  if (which === "rot") {
    capture(e, (ev) => {
      const [x, y] = wrapXY(ev);
      let a = (Math.atan2(y - b.cy, x - b.cx) * 180) / Math.PI - 90;
      if (ev.shiftKey) a = Math.round(a / 15) * 15;
      else for (const snap of [0, 90, 180, -90, -180]) if (Math.abs(a - snap) < 3) a = snap;
      a = ((a + 540) % 360) - 180;
      OVR.set(id, { rotation: a });
      DIMS = { x: b.cx, y: b.cy + b.h / 2 + 40, text: `${Math.round(a)}°` };
    }, () => {
      DIMS = null;
      const o = OVR.get(id);
      if (!o) return;
      batch([keyed(l, "rotation") ? kf(l, "rotation", r1(o.rotation)) : { op: "layer_set", args: { layer: id, rotation: r1(o.rotation) } }])
        .finally(() => OVR.delete(id));
    });
    return;
  }
  const [hx, hy] = which.split(",").map(Number);
  const corner = hx !== 0 && hy !== 0;
  const W0 = b.w, H0 = b.h, pr = b.pr;
  let res = null;
  capture(e, (ev) => {
    const [x, y] = wrapXY(ev);
    const dx = x - x0, dy = y - y0;
    const lx = dx * cos + dy * sin, ly = -dx * sin + dy * cos; // pointer delta in the layer's own frame
    let W = W0, H = H0;
    const prop = l.type === "shape" ? ev.shiftKey : !ev.shiftKey; // shapes stretch freely; Shift locks the ratio (and the reverse for media / text)
    if (corner) {
      if (prop) { const f = Math.max(0.05, 1 + ((hx * lx) / W0 + (hy * ly) / H0) / 2); W = W0 * f; H = H0 * f; }
      else { W = Math.max(4, W0 + hx * lx); H = Math.max(4, H0 + hy * ly); }
    } else if (hx) W = Math.max(4, W0 + hx * lx);
    else H = Math.max(4, H0 + hy * ly);
    // keep the opposite side still: the centre moves by half the growth, in the layer's direction
    const gx = (hx * (W - W0)) / 2, gy = (hy * (H - H0)) / 2;
    const cx = pr.x + (gx * cos - gy * sin) / K, cy = pr.y + (gx * sin + gy * cos) / K;
    const fw = W / W0, fh = H / H0;
    const o = { x: cx, y: cy };
    if (l.type === "shape") { o.w = b.base[0] * fw; o.h = b.base[1] * fh; }
    else { o.sxm = fw; o.sym = fh; }
    OVR.set(id, o);
    res = { fw, fh, o, uniform: Math.abs(fw - fh) < 1e-6 };
    DIMS = { x: b.cx, y: b.cy + H / 2 + 12, text: `${Math.round(W / K)} × ${Math.round(H / K)}` };
  }, () => {
    DIMS = null;
    if (!res) { OVR.delete(id); return; }
    const { fw, fh, o, uniform } = res, ops = [];
    const pos = keyed(l, "x", "y") ? [kf(l, "x", r1(o.x)), kf(l, "y", r1(o.y))] : null;
    const set = { layer: id }; if (!pos) { set.x = r1(o.x); set.y = r1(o.y); }
    if (l.type === "shape") {
      if (keyed(l, "w", "h")) ops.push(kf(l, "w", Math.round(o.w)), kf(l, "h", Math.round(o.h)));
      else { set.width = Math.round(o.w); set.height = Math.round(o.h); }
    } else if (l.type === "text" && uniform) {
      set.style = { size: Math.max(8, Math.round(l.style.size * fw)), max_width: Math.max(40, Math.round(l.style.max_width * fw)) };
    } else if (l.type === "text" && fh === 1 && hx) {
      set.style = { max_width: Math.max(40, Math.round(l.style.max_width * fw)) };
    } else if ((l.type === "image" || l.type === "video") && uniform && l.width) {
      set.width = Math.max(8, Math.round(l.width * fw));
    } else if (keyed(l, "sx", "sy")) {
      ops.push(kf(l, "sx", r3(b.pr.sx * fw)), kf(l, "sy", r3(b.pr.sy * fh)));
    } else { set.sx = r3((l.sx ?? 1) * fw); set.sy = r3((l.sy ?? 1) * fh); }
    if (Object.keys(set).length > 1) ops.unshift({ op: "layer_set", args: set });
    if (pos) ops.push(...pos);
    batch(ops).finally(() => OVR.delete(id));
  });
}

// double-click text: edit it in place
$("#stagewrap").addEventListener("dblclick", (e) => {
  const [x, y] = wrapXY(e), l = hitAt(x, y);
  if (!l || l.type !== "text") return;
  const b = BOX.get(l.id);
  const ta = h("textarea", { id: "inline-edit", value: l.text || "" });
  Object.assign(ta.style, { left: b.cx - Math.max(160, b.w / 2) + "px", top: b.cy - Math.max(30, b.h / 2) + "px", width: Math.max(320, b.w) + "px", height: Math.max(60, b.h) + "px" });
  $("#stagewrap").append(ta); ta.focus(); ta.select();
  const done = (save) => { if (!ta.isConnected) return; const v = ta.value.trim(); ta.remove(); if (save && v && v !== l.text) op("layer_set", { layer: l.id, text: v }); };
  ta.addEventListener("keydown", (ev) => { ev.stopPropagation(); if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); done(true); } if (ev.key === "Escape") done(false); });
  ta.addEventListener("blur", () => done(true));
});

// drop from the drawer onto the canvas
$("#stagewrap").addEventListener("dragover", (e) => { if (e.dataTransfer.types.includes("text/x-ak")) e.preventDefault(); });
$("#stagewrap").addEventListener("drop", (e) => {
  const raw = e.dataTransfer.getData("text/x-ak");
  if (!raw) return;
  e.preventDefault();
  const [x, y] = wrapXY(e);
  const at = { x: r1(clamp((x - CVX) / K, 0, P().canvas.width)), y: r1(clamp((y - CVY) / K, 0, P().canvas.height)) };
  addItem(JSON.parse(raw), at);
});

// ---- adding things to the current scene ----------------------------------------------------------------------
function sceneSpan() { const c = cur(); return c ? { start: c.start, end: c.end, scene: c.sc.id } : { start: 0, end: null }; }
async function addItem(it, at = {}) {
  const sp = sceneSpan();
  const before = new Set(P().layers.map((l) => l.id));
  if (it.kind === "shape") {
    // what you see on the tile is what you get (the CLI defaults are callout boxes for footage)
    const pv = SHAPE_PREVIEW[it.shape] || {}, look = { fill: pv.fill, border_width: 0 };
    for (const k of ["radius", "sides", "points", "inner"]) if (pv[k] != null) look[k] = pv[k];
    if (it.shape === "rect") look.radius = 24;
    await op("layer_add_shape", { shape: it.shape, ...sp, ...at, ...look, ...(it.extra || {}) });
  }
  else if (it.kind === "text") await op("layer_add_text", { text: it.text, ...sp, ...at, style: it.style, look: it.look });
  else if (it.kind === "source") await op("layer_add_media", { source: it.source, ...sp, ...at });
  else if (it.kind === "overlay") await op("overlay_add", { key: it.key, start: sp.start });
  else if (it.kind === "template") await op("template_apply", { key: it.key, start: sp.start });
  const added = P().layers.filter((l) => !before.has(l.id)).map((l) => l.id);
  if (added.length) select(added.slice(-1));
}

// ---- floating toolbar (Canva-style): only the selected element's own controls ---------------------------------
// Root (nothing selected): no toolbar; the project and the current scene live in the Properties panel.
const setL = (l, args) => op("layer_set", { layer: l.id, ...args });
const setStyle = (l, style) => op("layer_set", { layer: l.id, style });
const tb = (content, attrs = {}) => h("button", { class: "tb" + (attrs.on ? " on" : ""), title: attrs.title, onclick: attrs.onclick, "aria-pressed": attrs.on ? "true" : null }, content);
const sep = () => h("span", { class: "tb-sep" });
const word = (label, view, title) => tb(label, { on: VIEW === view && !$("#drawer").classList.contains("closed"), title, onclick: () => openView(view) });
function openView(view) { VIEW = view; openDrawer(); renderDrawer(); renderCtx(); }
function spanOf(l) { return [l.start, l.end ?? dur()]; }

// popovers: a small panel under a toolbar button (closes on outside click / Escape)
let POP = null;
function closePop() { if (POP) { POP.el.remove(); document.removeEventListener("pointerdown", POP.off, true); POP = null; } }
function popover(anchor, build, key) {
  if (POP && POP.key === key) { closePop(); return; }
  closePop();
  const el = h("div", { class: "pop" });
  build(el);
  document.body.append(el);
  const r = anchor.getBoundingClientRect(), w = el.offsetWidth;
  Object.assign(el.style, { left: clamp(r.left + r.width / 2 - w / 2, 8, innerWidth - w - 8) + "px", top: r.bottom + 8 + "px" });
  const off = (e) => { if (!el.contains(e.target) && !anchor.contains(e.target)) closePop(); };
  setTimeout(() => document.addEventListener("pointerdown", off, true), 0);
  POP = { el, off, key };
}
function rangeRow(label, value, min, max, step, onchange, fmtv = (v) => v) {
  const out = h("span", { class: "v" }, String(fmtv(value)));
  return h("div", { class: "prow" }, h("span", { class: "pl" }, label),
    h("input", { type: "range", min, max, step, value, oninput: (e) => { out.textContent = fmtv(+e.target.value); }, onchange: (e) => onchange(+e.target.value) }), out);
}
function numRow(label, value, onchange, attrs = {}) {
  return h("div", { class: "prow" }, h("span", { class: "pl" }, label), h("input", { type: "number", value, ...attrs, onchange: (e) => onchange(e.target.value) }), attrs.unit ? h("span", { class: "v" }, attrs.unit) : null);
}
// every colour already used in the design first (consistency), then a default palette, then any colour
const DEFAULT_COLORS = ["#FFFFFF", "#0B0F0D", "#C5F467", "#FFE600", "#FFC23B", "#FF7A59", "#FF3B6B", "#7C5CFF", "#22D3EE", "#34D399", "#0B1F3A", "#F5F1E8"];
function docColors() {
  const s = new Set();
  const add = (c) => { if (c && /^#[0-9a-f]{6}/i.test(c)) s.add(c.slice(0, 7).toUpperCase()); };
  for (const l of P().layers) { add(l.fill); (l.fill_gradient || []).forEach(add); if (l.type === "text") { add(l.style.color); add(l.style.bg_color); } }
  P().scenes.forEach((sc) => (sc.background || "").split(",").forEach(add));
  add(P().canvas.background);
  return [...s].slice(0, 12);
}
function colorPicker(el, cur, onpick, title = "Colour") {
  const dots = (cols) => h("div", { class: "swatches" }, cols.map((c) => h("button", { class: "sw" + ((cur || "").toUpperCase().startsWith(c) ? " on" : ""), title: c, style: { background: c }, onclick: () => onpick(c) })));
  const dc = docColors();
  put(el, h("div", { class: "ph" }, title),
    dc.length ? [h("div", { class: "pl2" }, "In this design"), dots(dc)] : null,
    h("div", { class: "pl2" }, "Default colours"), dots(DEFAULT_COLORS),
    h("label", { class: "prow" }, h("span", { class: "pl" }, "Custom"), h("input", { type: "color", value: (cur || "#ffffff").slice(0, 7), onchange: (e) => onpick(e.target.value) }), h("span", { class: "v" }, (cur || "").toUpperCase())));
}
function durationPop(btn, l) {
  popover(btn, (el) => {
    const [s, e] = spanOf(l);
    put(el, h("div", { class: "ph" }, "Timing"),
      numRow("Starts", r3(s), (v) => setL(l, { start: +v }), { step: 0.1, min: 0, unit: "s" }),
      numRow("Ends", r3(e), (v) => setL(l, { end: +v }), { step: 0.1, unit: "s" }),
      numRow("Duration", r3(e - s), (v) => setL(l, { end: r3(s + Math.max(0.1, +v)) }), { step: 0.1, min: 0.1, unit: "s" }),
      h("div", { class: "hint" }, l.scene ? `In ${(P().scenes.find((x) => x.id === l.scene) || {}).name || l.scene}` : "Spans more than one scene"));
  }, "dur");
}
function renderCtx() {
  const box = $("#ctx");
  box.innerHTML = "";
  closePop();
  if (ST && !SEL.length && bgScene()) {
    box.classList.remove("idle");
    const pill = h("div", { class: "pill" });
    box.append(pill);
    return renderBgCtx(pill, bgScene());
  }
  if (!ST || !SEL.length) { box.classList.add("idle"); return; }
  box.classList.remove("idle");
  const pill = h("div", { class: "pill" });
  box.append(pill);
  const ls = SEL.map(layerById).filter(Boolean);
  const l = ls.length === 1 ? ls[0] : null;
  if (!l) {
    put(pill, h("span", { class: "tb-label" }, `${ls.length} elements`), sep(),
      tb(ic("opacity", 18), { title: "Transparency", onclick: (e) => popover(e.currentTarget, (el) => put(el, h("div", { class: "ph" }, "Transparency"),
        rangeRow("Opacity", Math.round(ls[0].opacity * 100), 0, 100, 1, (v) => batch(ls.map((x) => ({ op: "layer_set", args: { layer: x.id, opacity: v / 100 } }))), (v) => v + "%")), "op") }),
      sep(), word("Animate", "animate"), word("Position", "position"), word("Properties", "properties"));
    return;
  }
  const [s, e] = spanOf(l);
  const clock = tb([ic("clock", 18), h("span", {}, `${(e - s).toFixed(1)}s`)], { title: "Timing: when it starts and ends" });
  clock.onclick = () => durationPop(clock, l);
  const items = [clock];
  if (l.type === "text") {
    const st = l.style;
    const fontSel = h("select", { class: "tb-font", title: "Font", onchange: (ev) => setStyle(l, { font: ev.target.value }) },
      h("option", { value: "" }, "Default font"), (TYPO?.fonts || []).map((f) => h("option", { value: f.key, selected: st.font === f.key }, f.family || f.key)));
    const size = h("input", { class: "tb-num", type: "number", min: 8, max: 600, value: st.size, title: "Font size", onchange: (ev) => setStyle(l, { size: clamp(+ev.target.value, 8, 600) }) });
    const step = (d) => setStyle(l, { size: clamp(st.size + d, 8, 600) });
    const colorBtn = tb(h("span", { class: "tb-A" }, "A", h("i", { style: { background: st.gradient ? `linear-gradient(90deg, ${st.gradient.join(",")})` : st.color } })), { title: "Text colour" });
    colorBtn.onclick = () => popover(colorBtn, (el) => colorPicker(el, st.color, (c) => setStyle(l, { color: c, gradient: "" }), "Text colour"), "tc");
    const alignNext = { left: "center", center: "right", right: "left" }[st.align] || "center";
    const listNext = { none: "bullet", bullet: "number", number: "none" }[st.list_style || "none"];
    const spacing = tb(ic("spacing", 18), { title: "Spacing: letters and lines" });
    spacing.onclick = () => popover(spacing, (el) => put(el, h("div", { class: "ph" }, "Spacing"),
      rangeRow("Letters", st.letter_spacing, -10, 60, 1, (v) => setStyle(l, { letter_spacing: v })),
      rangeRow("Lines", st.line_spacing, 0.8, 2.5, 0.05, (v) => setStyle(l, { line_spacing: v }), (v) => (+v).toFixed(2))), "sp");
    items.push(sep(), fontSel,
      h("span", { class: "tb-size" }, h("button", { class: "tb", title: "Smaller", onclick: () => step(-4) }, "−"), size, h("button", { class: "tb", title: "Bigger", onclick: () => step(4) }, "+")),
      colorBtn,
      tb(h("b", { class: "tb-glyph" }, "B"), { on: st.bold, title: "Bold", onclick: () => setStyle(l, { bold: !st.bold }) }),
      tb(h("i", { class: "tb-glyph" }, "I"), { on: st.italic, title: "Italic", onclick: () => setStyle(l, { italic: !st.italic }) }),
      tb(h("u", { class: "tb-glyph" }, "U"), { on: st.underline, title: "Underline", onclick: () => setStyle(l, { underline: !st.underline }) }),
      tb(h("s", { class: "tb-glyph" }, "S"), { on: st.strike, title: "Strikethrough", onclick: () => setStyle(l, { strike: !st.strike }) }),
      tb(h("span", { class: "tb-glyph" }, "aA"), { on: st.uppercase, title: "Uppercase", onclick: () => setStyle(l, { uppercase: !st.uppercase }) }),
      tb(ic("align-" + (st.align || "center"), 18), { title: `Alignment: ${st.align} (click for ${alignNext})`, onclick: () => setStyle(l, { align: alignNext }) }),
      tb(ic(st.list_style === "number" ? "list-num" : "list", 18), { on: st.list_style && st.list_style !== "none", title: `List: ${st.list_style || "none"} (click for ${listNext})`, onclick: () => setStyle(l, { list_style: listNext }) }),
      spacing);
  } else if (l.type === "shape") {
    const g = l.fill_gradient && l.fill_gradient.length >= 2;
    const fill = tb(h("span", { class: "sw-dot", style: { background: g ? `linear-gradient(135deg, ${l.fill_gradient.join(",")})` : l.fill || "transparent", borderStyle: l.fill || g ? "solid" : "dashed" } }), { title: "Fill colour" });
    fill.onclick = () => popover(fill, (el) => {
      colorPicker(el, g ? l.fill_gradient[0] : l.fill, (c) => (g ? setL(l, { fill_gradient: [c, ...l.fill_gradient.slice(1)].join(",") }) : setL(l, { fill: c })), "Fill");
      put(el, h("div", { class: "prow" }, h("span", { class: "pl" }, "Gradient"),
        h("button", { class: "chip" + (g ? " on" : ""), onclick: () => setL(l, { fill_gradient: g ? "" : `${l.fill || "#FF3B6B"},#7C5CFF` }) }, g ? "On" : "Off"),
        g ? h("input", { type: "color", title: "End colour", value: l.fill_gradient[l.fill_gradient.length - 1].slice(0, 7), onchange: (ev) => setL(l, { fill_gradient: [...l.fill_gradient.slice(0, -1), ev.target.value].join(",") }) }) : null),
        g ? rangeRow("Angle", Math.round(l.gradient_angle ?? 90), 0, 345, 15, (v) => setL(l, { gradient_angle: v }), (v) => v + "°") : null,
        h("div", { class: "prow" }, h("span", { class: "pl" }, "No fill"), h("button", { class: "chip" + (l.fill ? "" : " on"), onclick: () => setL(l, { fill: l.fill ? "none" : "#FFFFFF", fill_gradient: "", border_width: l.fill ? Math.max(l.border_width, 8) : l.border_width }) }, l.fill ? "Off" : "On")));
    }, "fill");
    const border = tb(ic("border", 18), { on: l.border_width > 0, title: "Border" });
    border.onclick = () => popover(border, (el) => put(el, h("div", { class: "ph" }, "Border"),
      rangeRow("Weight", l.border_width, 0, 80, 1, (v) => setL(l, { border_width: v }), (v) => v + "px"),
      h("div", { class: "pl2" }, "Colour"), h("div", { class: "swatches" }, [...new Set([...docColors(), ...DEFAULT_COLORS])].slice(0, 16).map((c) =>
        h("button", { class: "sw" + ((l.border_color || "").toUpperCase().startsWith(c) ? " on" : ""), style: { background: c }, onclick: () => setL(l, { border_color: c, border_width: Math.max(l.border_width, 6) }) })))), "bd");
    items.push(sep(), fill, border);
    if (l.shape === "rect" || l.shape === "bubble") {
      const corner = tb(ic("corner", 18), { on: l.radius > 0, title: "Corner rounding" });
      corner.onclick = () => popover(corner, (el) => put(el, h("div", { class: "ph" }, "Corner rounding"), rangeRow("Radius", l.radius, 0, 300, 1, (v) => setL(l, { radius: v }), (v) => v + "px")), "cr");
      items.push(corner);
    }
    if (["polygon", "star", "ring"].includes(l.shape)) {
      const shapeOpt = tb(ic("star-pts", 18), { title: "Shape options" });
      shapeOpt.onclick = () => popover(shapeOpt, (el) => put(el, h("div", { class: "ph" }, SHAPE_LABELS[l.shape]),
        l.shape === "polygon" ? rangeRow("Sides", l.sides, 3, 12, 1, (v) => setL(l, { sides: v })) : null,
        l.shape === "star" ? rangeRow("Points", l.points, 3, 16, 1, (v) => setL(l, { points: v })) : null,
        l.shape !== "polygon" ? rangeRow(l.shape === "ring" ? "Hole" : "Depth", Math.round(l.inner * 100), 5, 95, 1, (v) => setL(l, { inner: v / 100 }), (v) => v + "%") : null), "so");
      items.push(shapeOpt);
    }
    items.push(h("select", { class: "tb-kind", title: "Change the shape", onchange: (ev) => setL(l, { shape: ev.target.value }) },
      Object.keys(SHAPE_LABELS).map((k) => h("option", { value: k, selected: l.shape === k }, SHAPE_LABELS[k]))));
  } else if (l.type === "image" || l.type === "video") {
    const corner = tb(ic("corner", 18), { on: l.radius > 0 || l.mask === "circle", title: "Corners and crop shape" });
    corner.onclick = () => popover(corner, (el) => put(el, h("div", { class: "ph" }, "Corners"),
      rangeRow("Radius", l.radius, 0, 400, 1, (v) => setL(l, { radius: v }), (v) => v + "px"),
      h("div", { class: "prow" }, h("span", { class: "pl" }, "Circle"), h("button", { class: "chip" + (l.mask === "circle" ? " on" : ""), onclick: () => setL(l, { mask: l.mask === "circle" ? "rect" : "circle" }) }, l.mask === "circle" ? "On" : "Off"))), "cr");
    const border = tb(ic("border", 18), { on: l.border_width > 0, title: "Border" });
    border.onclick = () => popover(border, (el) => { put(el, h("div", { class: "ph" }, "Border"), rangeRow("Weight", l.border_width, 0, 80, 1, (v) => setL(l, { border_width: v }), (v) => v + "px")); colorPicker(el, l.border_color, (c) => setL(l, { border_color: c, border_width: Math.max(l.border_width, 6) }), "Border colour"); }, "bd");
    const flip = tb(ic("flip", 18), { on: l.flip_h || l.flip_v, title: "Flip" });
    flip.onclick = () => popover(flip, (el) => put(el, h("div", { class: "ph" }, "Flip"),
      h("button", { class: "pbtn", onclick: () => setL(l, { flip_h: !l.flip_h }) }, "Flip horizontal"), h("button", { class: "pbtn", onclick: () => setL(l, { flip_v: !l.flip_v }) }, "Flip vertical")), "fl");
    items.push(sep(), corner, border, flip);
    if (l.type === "video") {
      const vol = tb(ic(l.volume > 0 ? "sound" : "mute", 18), { title: "Sound" });
      vol.onclick = () => popover(vol, (el) => put(el, h("div", { class: "ph" }, "Sound"), rangeRow("Volume", Math.round(l.volume * 100), 0, 100, 1, (v) => setL(l, { volume: v / 100 }), (v) => v + "%")), "vol");
      items.push(vol);
    }
  }
  const tr = tb(ic("opacity", 18), { on: l.opacity < 1, title: "Transparency" });
  tr.onclick = () => popover(tr, (el) => put(el, h("div", { class: "ph" }, "Transparency"), rangeRow("Opacity", Math.round(l.opacity * 100), 0, 100, 1, (v) => setL(l, { opacity: v / 100 }), (v) => v + "%")), "op");
  items.push(tr, sep(), word("Effects", "effects", "Shadow, outline, glow, blend"), word("Animate", "animate", "Enter, during and exit motion, keyframes"),
    word("Position", "position", "Arrange, align, size"));
  put(pill, items);
}
async function duplicateSel() {
  if (!SEL.length) return;
  const before = new Set(P().layers.map((l) => l.id));
  await batch(SEL.map((id) => ({ op: "layer_dup", args: { layer: id } })));
  select(P().layers.filter((l) => !before.has(l.id)).map((l) => l.id));
}
async function deleteSel() {
  if (!SEL.length) return;
  const ids = SEL.filter((id) => !bgIds().has(id));
  select([]);
  await batch(ids.map((id) => ({ op: "layer_remove", args: { layer: id } })));
}

// ---- Properties panel: the project (nothing selected), the selected scene, or the selected element(s) -----------
let SCENE_SEL = null; // a scene block clicked in the timeline
let BG_SEL = null;    // a scene whose background is selected (empty canvas clicked)
function selectBackground(sceneId) {
  select([], true);
  BG_SEL = sceneId; SCENE_SEL = null;
  renderCtx(); drawOverlay();
  if (FOLLOWS.includes(VIEW)) { VIEW = "properties"; renderDrawer(); }
}
const bgScene = () => (BG_SEL ? P().scenes.find((s) => s.id === BG_SEL) : null);
const setBg = (sc, v) => op("scene_set", { scene: sc.id, background: v });
function bgSwatch(v, cur) {
  const cols = v.split(",");
  return h("button", { class: "sw" + ((cur || "").toUpperCase() === v.toUpperCase() ? " on" : ""), title: cols.join(" → "),
    style: { background: cols.length > 1 ? `linear-gradient(${cols.join(",")})` : cols[0] }, onclick: () => setBg(bgScene() || cur_sc(), v) });
}
const cur_sc = () => cur()?.sc;
// toolbar while the background is selected: its colour, gradient, the scene's length and entrance
function renderBgCtx(pill, sc) {
  const cols = (sc.background || "").split(",").filter(Boolean);
  const shown = cols.length > 1 ? `linear-gradient(135deg, ${cols.join(",")})` : cols[0] || P().canvas.background;
  const i = P().scenes.findIndex((s) => s.id === sc.id);
  const len = tb([ic("clock", 18), h("span", {}, `${sc.dur.toFixed(1)}s`)], { title: "Scene length" });
  len.onclick = () => popover(len, (el) => put(el, h("div", { class: "ph" }, "Scene length"),
    rangeRow("Length", sc.dur, 0.5, 30, 0.5, (v) => op("scene_set", { scene: sc.id, dur: v }), (v) => (+v).toFixed(1) + "s")), "len");
  const color = tb(h("span", { class: "sw-dot", style: { background: shown } }), { title: "Background colour" });
  color.onclick = () => openView("properties");
  const ent = h("select", { class: "tb-kind", title: "How this scene's elements enter", onchange: (e) => op("scene_set", { scene: sc.id, transition: e.target.value }) },
    TRANS.map(([v, lb]) => h("option", { value: v, selected: sc.transition === v }, v === "cut" ? "No entrance" : lb + " in")));
  put(pill, h("span", { class: "tb-label" }, `Background · ${sc.name || `Scene ${i + 1}`}`), sep(), len, color,
    tb(h("span", { class: "tb-glyph" }, "◐"), { on: cols.length > 1, title: "Gradient", onclick: () => setBg(sc, cols.length > 1 ? cols[0] : `${cols[0] || P().canvas.background},#7C5CFF`) }),
    sc.background ? tb("Reset", { title: "Back to the canvas colour", onclick: () => setBg(sc, "") }) : null,
    sep(), ent, sep(), word("Properties", "properties"));
}
function drawBackgroundProps(body, sc) {
  const i = P().scenes.findIndex((s) => s.id === sc.id);
  const cur = sc.background || "";
  const cols = cur.split(",").filter(Boolean);
  put(body, h("div", { class: "phead" }, h("span", { class: "pt" }, "Background"), h("b", {}, sc.name || `Scene ${i + 1}`)),
    sect("Solid colours", h("div", { class: "swatches bgs" }, BACKGROUNDS.filter((b) => !b.includes(",")).map((b) => bgSwatch(b, cur)))),
    sect("Gradients", h("div", { class: "swatches bgs" }, BACKGROUNDS.filter((b) => b.includes(",")).map((b) => bgSwatch(b, cur)))),
    docColors().length ? sect("In this design", h("div", { class: "swatches bgs" }, docColors().map((c) => bgSwatch(c, cur)))) : null,
    sect("Custom",
      h("div", { class: "field" }, h("span", {}, cols.length > 1 ? "Top" : "Colour"),
        cinput(cols[0] || P().canvas.background, (v) => setBg(sc, cols.length > 1 ? [v, ...cols.slice(1)].join(",") : v))),
      cols.length > 1 ? h("div", { class: "field" }, h("span", {}, "Bottom"), cinput(cols[cols.length - 1], (v) => setBg(sc, [...cols.slice(0, -1), v].join(",")))) : null,
      h("div", { class: "field" }, h("span", {}, "Gradient"), toggle(cols.length > 1, () => setBg(sc, cols.length > 1 ? cols[0] : `${cols[0] || P().canvas.background},#7C5CFF`), cols.length > 1 ? "On" : "Off"))),
    sect("Apply",
      h("div", { class: "row2" },
        h("button", { disabled: !cur || P().scenes.length < 2, title: "Give every scene this background",
          onclick: () => batch(P().scenes.filter((s) => s.id !== sc.id).map((s) => ({ op: "scene_set", args: { scene: s.id, background: cur } }))) }, "Apply to all scenes"),
        h("button", { disabled: !cur, onclick: () => setBg(sc, "") }, "Reset to canvas colour")),
      h("div", { class: "hint" }, `Canvas colour (behind every scene): `, h("input", { type: "color", value: P().canvas.background.slice(0, 7), style: { width: "22px", height: "22px", verticalAlign: "middle" }, onchange: (e) => op("canvas_set", { background: e.target.value }) })),
      h("div", { class: "row2" }, h("button", { class: "ghost", onclick: () => { BG_SEL = null; SCENE_SEL = sc.id; renderCtx(); drawOverlay(); renderDrawer(); } }, "Scene settings…"))));
}
const sect = (title, ...rows) => h("section", { class: "psec" }, h("h3", {}, title), ...rows.flat().filter(Boolean));
const fld = (label, input) => h("label", { class: "field" }, h("span", {}, label), input);
const ninput = (value, onchange, attrs = {}) => h("input", { type: "number", value, ...attrs, onchange: (e) => onchange(e.target.value) });
const cinput = (value, onchange) => h("input", { type: "color", value: (value || "#ffffff").slice(0, 7), onchange: (e) => onchange(e.target.value) });
const toggle = (on, onclick, label) => h("button", { class: "chip" + (on ? " on" : ""), onclick }, label);
const select2 = (opts, value, onchange) => h("select", { onchange: (e) => onchange(e.target.value) }, opts.map(([v, lb]) => h("option", { value: v, selected: v === value }, lb)));
const TRANS = [["cut", "None"], ["fade", "Fade"], ["slide", "Slide"], ["zoom", "Zoom"], ["pop", "Pop"], ["wipe", "Wipe"]];
const BLENDS = [["normal", "Normal"], ["screen", "Screen"], ["multiply", "Multiply"], ["overlay", "Overlay"], ["add", "Add"], ["lighten", "Lighten"], ["darken", "Darken"]];
function drawProperties(body) {
  const ls = SEL.map(layerById).filter(Boolean);
  if (!ls.length && bgScene()) return drawBackgroundProps(body, bgScene());
  if (ls.length > 1) return drawMultiProps(body, ls);
  if (ls.length === 1) return drawLayerProps(body, ls[0]);
  const sc = SCENE_SEL ? P().scenes.find((s) => s.id === SCENE_SEL) : null;
  if (sc) return drawSceneProps(body, sc, true);
  drawProjectProps(body);
  const c = cur();
  if (c) drawSceneProps(body, c.sc, false);
}
function drawProjectProps(body) {
  const p = P(), cv = p.canvas;
  const fmtNow = Object.entries({ reels: [1080, 1920], square: [1080, 1080], portrait45: [1080, 1350], landscape: [1920, 1080] }).find(([, [w, hh]]) => w === cv.width && hh === cv.height)?.[0];
  put(body, sect("Project",
    fld("Name", h("input", { value: p.name, onchange: (e) => e.target.value.trim() && op("rename", { name: e.target.value.trim() }) })),
    h("div", { class: "field" }, h("span", {}, "Format"), h("div", { class: "seg" }, [["reels", "9:16"], ["portrait45", "4:5"], ["square", "1:1"], ["landscape", "16:9"]].map(([k, lb]) =>
      h("button", { class: fmtNow === k ? "on" : "", title: `${k} format`, onclick: () => fmtNow !== k && op("canvas_set", { preset: k }) }, lb)))),
    h("div", { class: "field" }, h("span", {}, "Size"), h("span", { class: "ro" }, `${cv.width} × ${cv.height} px`)),
    fld("Canvas colour", cinput(cv.background, (v) => op("canvas_set", { background: v }))),
    fld("Frame rate", select2([["24", "24 fps"], ["25", "25 fps"], ["30", "30 fps"], ["60", "60 fps"]], String(Math.round(cv.fps)), (v) => op("canvas_set", { fps: v }))),
    h("div", { class: "field" }, h("span", {}, "Length"), h("span", { class: "ro" }, `${fmt(dur())} · ${p.scenes.length} scene${p.scenes.length === 1 ? "" : "s"} · ${p.layers.filter((l) => !bgIds().has(l.id)).length} elements`)),
    h("div", { class: "field" }, h("span", {}, "Music"), h("span", { class: "ro" }, p.music.length ? p.music.map((m) => (p.sources[m.source]?.path || m.source).split(/[\\/]/).pop()).join(", ") : "None"),
      h("button", { class: "ghost", onclick: () => openView("media") }, p.music.length ? "Edit" : "Add"))));
}
function drawSceneProps(body, sc, picked) {
  const i = P().scenes.findIndex((s) => s.id === sc.id), st = sceneStarts()[i];
  const cols = (sc.background || "").split(",").filter(Boolean);
  put(body, sect(picked ? `Scene · ${sc.name || `Scene ${i + 1}`}` : `Current scene · ${sc.name || `Scene ${i + 1}`}`,
    fld("Name", h("input", { value: sc.name, placeholder: `Scene ${i + 1}`, onchange: (e) => op("scene_set", { scene: sc.id, name: e.target.value }) })),
    h("div", { class: "field" }, h("span", {}, "Time"), h("span", { class: "ro" }, `${fmt(st)} – ${fmt(st + sc.dur)}`)),
    fld("Length", ninput(sc.dur, (v) => op("scene_set", { scene: sc.id, dur: v }), { step: 0.5, min: 0.5, max: 60 })),
    h("div", { class: "field" }, h("span", {}, "Background"),
      cinput(cols[0] || P().canvas.background, (v) => op("scene_set", { scene: sc.id, background: cols.length > 1 ? [v, ...cols.slice(1)].join(",") : v })),
      cols.length > 1 ? cinput(cols[cols.length - 1], (v) => op("scene_set", { scene: sc.id, background: [...cols.slice(0, -1), v].join(",") })) : null,
      toggle(cols.length > 1, () => op("scene_set", { scene: sc.id, background: cols.length > 1 ? cols[0] : `${cols[0] || P().canvas.background},#7C5CFF` }), "Gradient"),
      sc.background ? h("button", { class: "ghost", title: "Use the canvas colour", onclick: () => op("scene_set", { scene: sc.id, background: "" }) }, "Clear") : null),
    fld("Entrance", select2(TRANS, sc.transition, (v) => op("scene_set", { scene: sc.id, transition: v }))),
    h("label", { class: "field col" }, h("span", {}, "Notes"), h("textarea", { value: sc.notes, placeholder: "What happens in this scene, what is said…", onchange: (e) => op("scene_set", { scene: sc.id, notes: e.target.value }) })),
    h("div", { class: "row2" },
      h("button", { onclick: () => op("scene_dup", { scene: sc.id }) }, ic("copy", 14), " Duplicate"),
      h("button", { disabled: P().scenes.length < 2, onclick: () => confirm(`Delete ${sc.name || sc.id} and its elements?`) && op("scene_remove", { scene: sc.id }).then(() => { SCENE_SEL = null; }) }, ic("trash", 14), " Delete"),
      sc.notes ? h("button", { class: "ghost", disabled: AIBUSY.scene, onclick: () => designScene(sc.id) }, "✦ Design from notes") : null)));
}
const TYPE_LABEL = { text: "Text", shape: "Shape", image: "Image", video: "Video", adjust: "Adjustment", captions: "Captions" };
function drawLayerProps(body, l) {
  const [s, e] = spanOf(l), b = BOX.get(l.id), pr = b ? b.pr : { x: 0, y: 0 };
  const name = l.type === "shape" ? SHAPE_LABELS[l.shape] : l.type === "text" ? (l.text || "").slice(0, 28) : TYPE_LABEL[l.type];
  put(body, h("div", { class: "phead" }, h("span", { class: "pt" }, TYPE_LABEL[l.type] || l.type), h("b", {}, name), h("span", { class: "hint" }, l.id)));
  if (l.type === "text") put(body, sect("Text",
    h("textarea", { class: "big", value: l.text || "", onchange: (ev) => setL(l, { text: ev.target.value }) }),
    fld("Font", h("select", { onchange: (ev) => setStyle(l, { font: ev.target.value }) }, h("option", { value: "" }, "Default font"),
      (TYPO?.fonts || []).map((f) => h("option", { value: f.key, selected: l.style.font === f.key }, f.family || f.key)))),
    fld("Size", ninput(l.style.size, (v) => setStyle(l, { size: v }), { min: 8, max: 600 })),
    fld("Colour", cinput(l.style.color, (v) => setStyle(l, { color: v }))),
    h("div", { class: "field" }, h("span", {}, "Style"), h("div", { class: "seg" },
      [["bold", "B"], ["italic", "I"], ["underline", "U"], ["strike", "S"], ["uppercase", "aA"]].map(([k, lb]) => h("button", { class: l.style[k] ? "on" : "", title: k, onclick: () => setStyle(l, { [k]: !l.style[k] }) }, lb)))),
    fld("Align", select2([["left", "Left"], ["center", "Centre"], ["right", "Right"]], l.style.align, (v) => setStyle(l, { align: v }))),
    fld("List", select2([["none", "None"], ["bullet", "Bullets"], ["number", "Numbers"]], l.style.list_style || "none", (v) => setStyle(l, { list_style: v }))),
    fld("Letter spacing", ninput(l.style.letter_spacing, (v) => setStyle(l, { letter_spacing: v }), { step: 1 })),
    fld("Line spacing", ninput(l.style.line_spacing, (v) => setStyle(l, { line_spacing: v }), { step: 0.05, min: 0.6, max: 3 })),
    fld("Box width", ninput(l.style.max_width, (v) => setStyle(l, { max_width: v }), { step: 10, min: 40 }))));
  if (l.type === "shape") {
    const g = l.fill_gradient && l.fill_gradient.length >= 2;
    put(body, sect("Shape",
      fld("Kind", select2(Object.entries(SHAPE_LABELS), l.shape, (v) => setL(l, { shape: v }))),
      h("div", { class: "field" }, h("span", {}, "Fill"), g ? null : cinput(l.fill || "#ffffff", (v) => setL(l, { fill: v })),
        g ? l.fill_gradient.map((c, i) => cinput(c, (v) => { const a = [...l.fill_gradient]; a[i] = v; setL(l, { fill_gradient: a.join(",") }); })) : null,
        toggle(g, () => setL(l, { fill_gradient: g ? "" : `${l.fill || "#FF3B6B"},#7C5CFF` }), "Gradient"),
        toggle(!l.fill && !g, () => setL(l, { fill: l.fill ? "none" : "#FFFFFF", fill_gradient: "" }), "None")),
      g ? fld("Gradient angle", ninput(Math.round(l.gradient_angle ?? 90), (v) => setL(l, { gradient_angle: v }), { step: 15 })) : null,
      h("div", { class: "field" }, h("span", {}, "Border"), ninput(l.border_width, (v) => setL(l, { border_width: v }), { min: 0, max: 200 }), cinput(l.border_color, (v) => setL(l, { border_color: v }))),
      l.shape === "rect" || l.shape === "bubble" ? fld("Corners", ninput(l.radius, (v) => setL(l, { radius: v }), { min: 0, max: 500 })) : null,
      l.shape === "polygon" ? fld("Sides", ninput(l.sides, (v) => setL(l, { sides: v }), { min: 3, max: 24 })) : null,
      l.shape === "star" ? fld("Points", ninput(l.points, (v) => setL(l, { points: v }), { min: 3, max: 24 })) : null,
      l.shape === "star" || l.shape === "ring" ? fld(l.shape === "ring" ? "Hole" : "Depth", ninput(l.inner, (v) => setL(l, { inner: v }), { min: 0.05, max: 0.95, step: 0.05 })) : null));
  }
  if (l.type === "image" || l.type === "video") put(body, sect(TYPE_LABEL[l.type],
    fld("Width", ninput(l.width ?? "", (v) => setL(l, { width: v }), { min: 8 })),
    fld("Corners", ninput(l.radius, (v) => setL(l, { radius: v }), { min: 0, max: 500 })),
    h("div", { class: "field" }, h("span", {}, "Crop shape"), h("div", { class: "seg" }, [["rect", "Rectangle"], ["circle", "Circle"]].map(([k, lb]) => h("button", { class: l.mask === k ? "on" : "", onclick: () => setL(l, { mask: k }) }, lb)))),
    h("div", { class: "field" }, h("span", {}, "Border"), ninput(l.border_width, (v) => setL(l, { border_width: v }), { min: 0, max: 200 }), cinput(l.border_color, (v) => setL(l, { border_color: v }))),
    h("div", { class: "field" }, h("span", {}, "Flip"), toggle(l.flip_h, () => setL(l, { flip_h: !l.flip_h }), "Horizontal"), toggle(l.flip_v, () => setL(l, { flip_v: !l.flip_v }), "Vertical")),
    l.type === "video" ? fld("Fit", select2([["free", "Free"], ["cover", "Fill the canvas"], ["contain", "Fit the canvas"]], l.fit, (v) => setL(l, { fit: v }))) : null,
    l.type === "video" ? fld("Volume", h("input", { type: "range", min: 0, max: 1, step: 0.05, value: l.volume, onchange: (ev) => setL(l, { volume: ev.target.value }) })) : null));
  put(body, sect("Position & size",
    h("div", { class: "grid2" }, fld("X", ninput(Math.round(pr.x), (v) => setL(l, { x: +v }))), fld("Y", ninput(Math.round(pr.y), (v) => setL(l, { y: +v }))),
      l.type === "shape" ? [fld("W", ninput(l.width ?? 400, (v) => setL(l, { width: v }), { min: 1 })), fld("H", ninput(l.height ?? 400, (v) => setL(l, { height: v }), { min: 1 }))]
        : [fld("Stretch W", ninput(l.sx ?? 1, (v) => setL(l, { sx: v }), { step: 0.05, min: 0.05 })), fld("Stretch H", ninput(l.sy ?? 1, (v) => setL(l, { sy: v }), { step: 0.05, min: 0.05 }))],
      fld("Rotate", ninput(l.rotation, (v) => setL(l, { rotation: v }))), fld("Scale", ninput(l.scale, (v) => setL(l, { scale: v }), { step: 0.05, min: 0.05 }))),
    fld("Opacity", h("input", { type: "range", min: 0, max: 1, step: 0.01, value: l.opacity, onchange: (ev) => setL(l, { opacity: ev.target.value }) })),
    fld("Blend", select2(BLENDS, l.blend, (v) => setL(l, { blend: v })))));
  put(body, sect("Timing",
    h("div", { class: "grid2" }, fld("Start", ninput(r3(s), (v) => setL(l, { start: +v }), { step: 0.1, min: 0 })), fld("End", ninput(r3(e), (v) => setL(l, { end: +v }), { step: 0.1 }))),
    h("div", { class: "field" }, h("span", {}, "Scene"), h("span", { class: "ro" }, l.scene ? (P().scenes.find((x) => x.id === l.scene)?.name || l.scene) : "All scenes"))));
  const pres = l.presets.map((p) => h("span", { class: "chip on" }, PRESET_LABEL(p.name)));
  put(body, sect("Animation",
    h("div", { class: "chips" }, pres.length ? pres : h("span", { class: "hint" }, "No presets")),
    h("div", { class: "hint" }, l.keyframes.length ? `${l.keyframes.length} keyframe${l.keyframes.length === 1 ? "" : "s"}` : "No keyframes"),
    h("button", { onclick: () => openView("animate") }, ic("motion", 14), " Edit animation")));
  if (l.why) put(body, sect("Why it's here", h("div", { class: "hint" }, l.why)));
}
function drawMultiProps(body, ls) {
  put(body, h("div", { class: "phead" }, h("span", { class: "pt" }, "Selection"), h("b", {}, `${ls.length} elements`)),
    sect("Together",
      fld("Opacity", h("input", { type: "range", min: 0, max: 1, step: 0.01, value: ls[0].opacity, onchange: (e) => batch(ls.map((x) => ({ op: "layer_set", args: { layer: x.id, opacity: e.target.value } }))) })),
      fld("Blend", select2(BLENDS, ls[0].blend, (v) => batch(ls.map((x) => ({ op: "layer_set", args: { layer: x.id, blend: v } }))))),
      h("div", { class: "row2" }, h("button", { onclick: () => openView("animate") }, "Animate all"), h("button", { onclick: () => openView("position") }, "Align"), h("button", { onclick: duplicateSel }, "Duplicate"), h("button", { onclick: deleteSel }, "Delete"))),
    sect("Elements", ls.map((l) => h("button", { class: "textbtn", onclick: () => select([l.id]) }, `${TYPE_LABEL[l.type]} · ${l.type === "shape" ? SHAPE_LABELS[l.shape] : (l.text || l.id).slice(0, 30)}`))));
}

// ---- Effects panel (text: shadow, outline, glow, box, gradient; any: blend) ---------------------------------------
function drawEffects(body) {
  const l = SEL.length === 1 ? layerById(SEL[0]) : null;
  if (!l) { body.append(h("div", { class: "hint" }, "Select one element to add effects.")); return; }
  if (l.type === "text") {
    const st = l.style, grad = st.gradient && st.gradient.length >= 2;
    const card = (name, on, onToggle, ...rows) => h("div", { class: "fx" + (on ? " on" : "") }, h("div", { class: "fx-h" }, h("b", {}, name), toggle(on, onToggle, on ? "On" : "Off")), on ? rows : null);
    put(body,
      card("Shadow", st.shadow, () => setStyle(l, { shadow: !st.shadow })),
      card("Outline", st.stroke_width > 0, () => setStyle(l, { stroke_width: st.stroke_width > 0 ? 0 : 6 }),
        fld("Weight", ninput(st.stroke_width, (v) => setStyle(l, { stroke_width: v }), { min: 0, max: 40 })), fld("Colour", cinput(st.stroke_color, (v) => setStyle(l, { stroke_color: v })))),
      card("Glow", !!st.glow_color, () => setStyle(l, { glow_color: st.glow_color ? "" : "#FF2BD6" }),
        fld("Colour", cinput(st.glow_color, (v) => setStyle(l, { glow_color: v }))), fld("Size", ninput(st.glow_size, (v) => setStyle(l, { glow_size: v }), { min: 2, max: 80 }))),
      card("Background box", !!st.bg_color, () => setStyle(l, { bg_color: st.bg_color ? "" : "#000000B0" }),
        fld("Colour", cinput(st.bg_color, (v) => setStyle(l, { bg_color: v }))), fld("Padding", ninput(st.bg_padding, (v) => setStyle(l, { bg_padding: v }), { min: 0, max: 120 })),
        fld("Rounding", ninput(st.bg_radius, (v) => setStyle(l, { bg_radius: v }), { min: 0, max: 200 }))),
      card("Gradient fill", grad, () => setStyle(l, { gradient: grad ? "" : `${st.color},#FF6A00` }),
        grad ? fld("Top", cinput(st.gradient[0], (v) => setStyle(l, { gradient: [v, ...st.gradient.slice(1)].join(",") }))) : null,
        grad ? fld("Bottom", cinput(st.gradient[st.gradient.length - 1], (v) => setStyle(l, { gradient: [...st.gradient.slice(0, -1), v].join(",") }))) : null));
  }
  put(body, sect("Blend with what's below", h("div", { class: "blends" }, BLENDS.map(([k, lb]) => h("button", { class: "chip" + (l.blend === k ? " on" : ""), onclick: () => setL(l, { blend: k }) }, lb)))),
    h("div", { class: "hint" }, "Screen and Add brighten (light leaks, glows); Multiply darkens (paper, shadows)."));
}

// ---- drawer --------------------------------------------------------------------------------------------------
const RAIL = [["elements", "Elements", "elements"], ["text", "Text", "text"], ["media", "Media", "media"], ["storyboard", "Storyboard", "story"], ["ai", "AI", "ai"], ["properties", "Properties", "props"]];
const SHAPE_LABELS = { rect: "Rectangle", ellipse: "Circle", triangle: "Triangle", diamond: "Diamond", polygon: "Hexagon", star: "Star", ring: "Ring", heart: "Heart", bubble: "Speech bubble", blob: "Blob", line: "Line", arrow: "Arrow" };
const SHAPE_PREVIEW = {
  rect: { width: 600, height: 380, radius: 40, fill: "#e9f0eb" }, ellipse: { width: 400, height: 400, fill: "#e9f0eb" },
  triangle: { width: 400, height: 360, fill: "#FFE600" }, diamond: { width: 400, height: 400, fill: "#7C5CFF" },
  polygon: { width: 400, height: 400, fill: "#22D3EE", sides: 6 }, star: { width: 400, height: 400, fill: "#FFC23B", points: 5, inner: 0.48 },
  ring: { width: 400, height: 400, fill: "#FF3B6B", inner: 0.68 }, heart: { width: 400, height: 370, fill: "#FF3B6B" },
  bubble: { width: 560, height: 400, fill: "#e9f0eb", radius: 70 }, blob: { width: 420, height: 400, fill: "#34D399" },
  line: { width: 600, height: 24, fill: "#e9f0eb" }, arrow: { width: 560, height: 160, fill: "#FFE600" },
};
const BACKGROUNDS = ["#101014", "#0B1F3A", "#14281D", "#2B0F2E", "#3A0D12", "#F5F1E8", "#FFE600", "#FF3B6B", "#22D3EE", "#7C5CFF",
  "#0B1F3A,#7C5CFF", "#FF3B6B,#FFC23B", "#101014,#2B0F2E", "#22D3EE,#34D399", "#F5F1E8,#FFC23B", "#14281D,#C5F467"];
let TYPO = null, LIB = null, TEMPLATES = null;
function renderRail() {
  const r = $("#rail"); r.innerHTML = "";
  for (const [k, label, icon] of RAIL) r.append(h("button", { class: VIEW === k ? "on" : "", onclick: () => { if (VIEW === k && !$("#drawer").classList.contains("closed")) return toggleDrawer(); VIEW = k; openDrawer(); renderDrawer(); renderRail(); } }, ic(icon, 22), label));
}
function openDrawer() { $("#drawer").classList.remove("closed"); $("#dr-toggle").textContent = "‹"; setTimeout(layoutAll, 240); }
function toggleDrawer() { const d = $("#drawer"); d.classList.toggle("closed"); $("#dr-toggle").textContent = d.classList.contains("closed") ? "›" : "‹"; setTimeout(layoutAll, 240); }
$("#dr-toggle").onclick = toggleDrawer;
function renderDrawer() {
  renderRail();
  const body = $("#dr-body"), title = $("#dr-title");
  const keepScroll = body.scrollTop;
  body.innerHTML = "";
  const views = { elements: ["Elements", drawElements], text: ["Text", drawText], media: ["Media", drawMedia], storyboard: ["Storyboard", drawStoryboard],
    ai: ["AI assistant", drawAI], animate: ["Animate", drawAnimate], position: ["Position", drawPosition],
    properties: ["Properties", drawProperties], effects: ["Effects", drawEffects] };
  const [t, fn] = views[VIEW] || views.elements;
  title.textContent = t;
  fn(body);
  body.scrollTop = keepScroll;
}
function tile(it, label, inner) {
  return h("div", { class: "tile", title: label, draggable: true,
    ondragstart: (e) => e.dataTransfer.setData("text/x-ak", JSON.stringify(it)),
    onclick: () => addItem(it) }, inner, h("span", { class: "lb" }, label));
}
function shapeSvg(kind, box = 64) {
  const l = { shape: kind, ...SHAPE_PREVIEW[kind] };
  const k = box / Math.max(l.width, l.height);
  const s = h("span", { style: { display: "inline-flex", width: l.width * k + "px", height: l.height * k + "px" } });
  s.innerHTML = VShapes.svg(l, l.width * k, l.height * k, undefined, k);
  return s;
}
function drawElements(body) {
  const q = h("input", { class: "search", placeholder: "Search shapes, lines, backgrounds…", oninput: () => filter() });
  body.append(q);
  const sections = [];
  const sec = (name, tiles, cls = "tiles") => { const g = h("div", { class: cls }, tiles); const s = h("div", {}, h("h3", {}, name), g); sections.push([s, tiles]); body.append(s); };
  sec("Shapes", Object.keys(SHAPE_LABELS).filter((k) => k !== "line" && k !== "arrow").map((k) =>
    withQ(tile({ kind: "shape", shape: k }, SHAPE_LABELS[k], shapeSvg(k)), SHAPE_LABELS[k].toLowerCase() + " " + k)));
  sec("Lines & arrows", ["line", "arrow"].map((k) => withQ(tile({ kind: "shape", shape: k }, SHAPE_LABELS[k], shapeSvg(k, 72)), k)));
  const c = cur();
  void c;
  const tex = h("div", { class: "tiles wide" }, h("div", { class: "hint" }, "Loading…"));
  body.append(h("h3", {}, "Textures & stickers"), tex);
  const tpl = h("div", {});
  body.append(h("h3", {}, "My templates"), tpl);
  const fill = () => {
    tex.innerHTML = "";
    for (const it of (LIB || []).slice(0, 24)) tex.append(withQ(tile({ kind: "overlay", key: it.key }, it.label, h("img", { src: `/api/overlays/${it.key}/thumb.jpg`, loading: "lazy", alt: "" })), (it.label + " " + it.key).toLowerCase()));
    if (!(LIB || []).length) tex.append(h("div", { class: "hint" }, "No textures available"));
    tpl.innerHTML = "";
    if (!(TEMPLATES || []).length) tpl.append(h("div", { class: "hint" }, "Select elements and choose Position → Save as template to reuse them in any design."));
    else tpl.append(h("div", { class: "tiles wide" }, TEMPLATES.map((t) => tile({ kind: "template", key: t.key }, t.name, h("div", { style: { padding: "10px", fontSize: "12px" } }, t.name)))));
  };
  if (LIB) fill(); else Promise.all([fetch("/api/overlays").then((r) => r.json()), fetch("/api/templates").then((r) => r.json())])
    .then(([o, t]) => { LIB = (o.library || []).filter((x) => x.kind !== "shape" && !String(x.key).startsWith("shape-")); TEMPLATES = t.templates || []; if (VIEW === "elements") fill(); }).catch(() => {});
  function filter() {
    const s = q.value.trim().toLowerCase();
    for (const t of $$(".tile", body)) t.style.display = !s || (t.dataset.q || "").includes(s) ? "" : "none";
  }
}
function drawText(body) {
  const add = (text, size, extra = {}) => addItem({ kind: "text", text, style: { size, ...extra } });
  body.append(
    h("button", { class: "textbtn", style: { fontSize: "22px", fontWeight: 700 }, onclick: () => add("Add a heading", 120) }, "Add a heading"),
    h("button", { class: "textbtn", style: { fontSize: "16px", fontWeight: 600 }, onclick: () => add("Add a subheading", 72) }, "Add a subheading"),
    h("button", { class: "textbtn", style: { fontSize: "13px" }, onclick: () => add("Add a little bit of body text", 44, { stroke_width: 0 }) }, "Add a little bit of body text"),
    h("h3", {}, "Text looks"));
  const g = h("div", { class: "looks" }, h("div", { class: "hint" }, "Loading…"));
  body.append(g, h("div", { class: "hint", style: { marginTop: "10px" } }, "Tip: double-click text on the canvas to edit it."));
  const fill = () => {
    g.innerHTML = "";
    for (const lk of TYPO.looks || []) {
      const name = lk.name || lk.key;
      g.append(h("div", { class: "look", title: lk.about || name, draggable: true,
        ondragstart: (e) => e.dataTransfer.setData("text/x-ak", JSON.stringify({ kind: "text", text: "Your text", look: name })),
        onclick: () => addItem({ kind: "text", text: "Your text", look: name }) },
        h("span", { style: lookStyle(lk) }, name.replace(/[_-]/g, " "))));
    }
  };
  if (TYPO) fill(); else loadTypo().then(() => VIEW === "text" && fill());
}
function lookStyle(lk) {
  const s = lk.style || {};
  return { color: s.color || "#fff", fontWeight: 700, textTransform: s.uppercase ? "uppercase" : "none", background: s.bg_color || "transparent",
    padding: s.bg_color ? "3px 8px" : 0, borderRadius: "6px", textShadow: s.glow_color ? `0 0 10px ${s.glow_color}` : s.stroke_width ? "0 0 2px #000, 0 0 2px #000" : "none",
    letterSpacing: (s.letter_spacing || 0) / 10 + "px" };
}
async function loadTypo() { try { TYPO = await (await fetch("/api/typography")).json(); } catch { TYPO = { fonts: [], looks: [] }; } renderCtx(); }
function drawMedia(body) {
  const drop = h("div", { class: "drop", onclick: () => $("#file-in").click() }, h("b", {}, "Upload media"), h("div", { class: "hint" }, "Images, videos, music · or drop files here"));
  drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("over"); upload([...e.dataTransfer.files]); });
  body.append(drop);
  const srcs = Object.entries(P().sources).filter(([, s]) => !isGap(s));
  const vis = srcs.filter(([, s]) => s.has_video || IMG_EXT.test(s.path)), aud = srcs.filter(([, s]) => !s.has_video && !IMG_EXT.test(s.path) && s.has_audio);
  body.append(h("h3", {}, "In this design"));
  if (!vis.length) body.append(h("div", { class: "hint" }, "Nothing uploaded yet."));
  else body.append(h("div", { class: "tiles wide" }, vis.map(([id, s]) => tile({ kind: "source", source: id }, s.path.split(/[\\/]/).pop(),
    h("img", { src: `/api/source/${encodeURIComponent(id)}/thumb.jpg?k=${srcKey(id)}`, alt: "", loading: "lazy" })))));
  body.append(h("h3", {}, "Music"));
  if (P().music.length) for (const m of P().music) {
    const s = P().sources[m.source];
    body.append(h("div", { class: "card" }, h("div", { class: "hd2" }, h("b", { style: { flex: 1 } }, s ? s.path.split(/[\\/]/).pop() : m.source),
      h("button", { class: "ghost icon", title: "Remove music", onclick: () => op("music_remove", { music: m.id }) }, ic("trash", 15))),
      h("div", { class: "field" }, h("span", {}, "Volume"), h("input", { type: "range", min: 0, max: 1, step: 0.05, value: m.volume, onchange: (e) => op("music_set", { music: m.id, volume: e.target.value }) }))));
  }
  for (const [id, s] of aud) if (!P().music.some((m) => m.source === id)) body.append(h("button", { class: "textbtn", onclick: () => op("add_music", { source: id, volume: 0.6 }) }, "♪ Use " + s.path.split(/[\\/]/).pop() + " as music"));
  if (!aud.length && !P().music.length) body.append(h("div", { class: "hint" }, "Upload an audio file to add music."));
}
$("#file-in").onchange = (e) => { upload([...e.target.files]); e.target.value = ""; };
async function upload(files) {
  for (const f of files) {
    const fd = new FormData();
    fd.append("file", f); fd.append("append", "false");
    toast(`Uploading ${f.name}…`);
    const r = await fetch("/api/import", { method: "POST", body: fd });
    if (!r.ok) { toast(`${f.name}: ${(await r.json().catch(() => ({}))).detail || r.statusText}`, true); continue; }
    const res = await r.json();
    await refresh();
    const s = P().sources[res.source];
    if (s && (s.has_video || IMG_EXT.test(s.path))) await addItem({ kind: "source", source: res.source });
    else if (s && s.has_audio && !P().music.length) await op("add_music", { source: res.source, volume: 0.6 });
  }
  if (VIEW === "media") renderDrawer();
}
document.addEventListener("dragover", (e) => { if (e.dataTransfer.types.includes("Files")) e.preventDefault(); });
document.addEventListener("drop", (e) => { if (e.dataTransfer.files.length && !e.defaultPrevented) { e.preventDefault(); upload([...e.dataTransfer.files]); } });

// ---- storyboard ----------------------------------------------------------------------------------------------
let SB = { brief: "", style: "", busy: false, proposal: null };
function drawStoryboard(body) {
  const ta = h("textarea", { placeholder: "What is the video about? Paste a script, or a few lines: product, audience, message, call to action.", value: SB.brief, oninput: (e) => { SB.brief = e.target.value; } });
  const len = h("input", { type: "number", min: 5, max: 120, value: SB.length || 20, style: { width: "64px" }, onchange: (e) => { SB.length = +e.target.value; } });
  const style = h("select", { onchange: (e) => { SB.style = e.target.value; } }, h("option", { value: "" }, "Any style"),
    (SB.styles || []).map((s) => h("option", { value: s.id, selected: SB.style === s.id, title: s.about }, s.id.replace(/-/g, " "))));
  body.append(h("div", { class: "ai-box" }, h("b", {}, "Write the storyboard with AI"),
    h("div", { class: "hint" }, `Turns your brief into scenes with timing, notes and colours. Uses ${MODEL_LABEL()}.`),
    ta, h("div", { class: "row2" }, h("span", { class: "hint" }, "Length"), len, h("span", { class: "hint" }, "s"), style, h("div", { style: { flex: 1 } }),
      h("button", { class: "primary", disabled: SB.busy, onclick: writeStoryboard }, SB.busy ? "Writing…" : "Write scenes"))));
  if (!SB.styles) fetch("/api/design/options").then((r) => r.json()).then((o) => { SB.styles = o.styles || []; if (VIEW === "storyboard") renderDrawer(); }).catch(() => { SB.styles = []; });
  if (SB.proposal) {
    const pr = SB.proposal;
    body.append(h("div", { class: "proposal" }, h("b", {}, `${pr.scenes.length} scenes · ${pr.scenes.reduce((a, s) => a + s.dur, 0).toFixed(1)} s`),
      pr.note ? h("div", { class: "hint" }, pr.note) : null,
      h("ol", {}, pr.scenes.map((s) => h("li", {}, h("b", {}, s.name), ` · ${s.dur}s · ${s.transition} — ${s.notes}`))),
      h("div", { class: "row2" }, h("button", { class: "primary", onclick: () => applyStoryboard("replace") }, "Replace my scenes"),
        h("button", { onclick: () => applyStoryboard("append") }, "Add after"), h("button", { class: "ghost", onclick: () => { SB.proposal = null; renderDrawer(); } }, "Discard"))));
  }
  body.append(h("h3", {}, `Scenes · ${fmt(dur())}`));
  const c = cur();
  P().scenes.forEach((sc, i) => {
    const st = sceneStarts()[i];
    body.append(h("div", { class: "card" + (c && c.sc.id === sc.id ? " cur" : ""), onclick: (e) => { if (e.target === e.currentTarget) seek(st); } },
      h("div", { class: "hd2" },
        h("span", { class: "sw", style: { width: "18px", height: "18px", flex: "none", borderRadius: "5px", background: bgCss(sc) } }),
        h("input", { value: sc.name, placeholder: `Scene ${i + 1}`, onfocus: () => seek(st), onchange: (e) => op("scene_set", { scene: sc.id, name: e.target.value }) }),
        h("button", { class: "ghost icon", title: "Move up", disabled: i === 0, onclick: () => op("scene_move", { scene: sc.id, index: i - 1 }) }, "↑"),
        h("button", { class: "ghost icon", title: "Move down", disabled: i === P().scenes.length - 1, onclick: () => op("scene_move", { scene: sc.id, index: i + 1 }) }, "↓"),
        h("button", { class: "ghost icon", title: "Duplicate scene", onclick: () => op("scene_dup", { scene: sc.id }) }, ic("copy", 14)),
        h("button", { class: "ghost icon", title: "Delete scene", disabled: P().scenes.length < 2, onclick: () => confirm(`Delete ${sc.name || sc.id} and its elements?`) && op("scene_remove", { scene: sc.id }) }, ic("trash", 14))),
      h("div", { class: "row2" }, h("span", { class: "hint" }, `${fmt(st)} ·`),
        h("input", { type: "number", step: 0.5, min: 0.5, max: 60, value: sc.dur, onchange: (e) => op("scene_set", { scene: sc.id, dur: e.target.value }) }), h("span", { class: "hint" }, "s"),
        h("select", { onchange: (e) => op("scene_set", { scene: sc.id, transition: e.target.value }) },
          ["cut", "fade", "slide", "zoom", "pop", "wipe"].map((t) => h("option", { value: t, selected: sc.transition === t }, t === "cut" ? "No entrance" : t[0].toUpperCase() + t.slice(1) + " in")))),
      h("textarea", { placeholder: "Notes: what happens, what is said…", value: sc.notes, onchange: (e) => op("scene_set", { scene: sc.id, notes: e.target.value }) }),
      sc.notes ? h("button", { class: "ghost", style: { marginTop: "6px" }, disabled: AIBUSY.scene, onclick: () => designScene(sc.id) }, AIBUSY.scene === sc.id ? "Designing…" : "✦ Design this scene from its notes") : null));
  });
  body.append(h("button", { class: "textbtn", style: { textAlign: "center" }, onclick: () => op("scene_add", { dur: 4, after: c?.sc.id }) }, "+ Add scene"));
}
function bgCss(sc) { const c = (sc.background || P().canvas.background).split(","); return c.length > 1 ? `linear-gradient(${c.join(",")})` : c[0]; }
async function writeStoryboard() {
  if (!SB.brief.trim()) return toast("Write a brief or paste a script first", true);
  SB.busy = true; renderDrawer();
  const r = await api("/api/design/storyboard", { brief: SB.brief, length: SB.length || 20, style: SB.style || null, model: CUR_MODEL });
  SB.busy = false;
  if (r) SB.proposal = r;
  renderDrawer();
}
async function applyStoryboard(mode) {
  const r = await api("/api/design/storyboard/apply", { scenes: SB.proposal.scenes, mode });
  if (r) { SB.proposal = null; await refresh(); seek(mode === "append" ? r.start || 0 : 0); toast(`${r.added} scenes added`); }
  renderDrawer();
}
const AIBUSY = { scene: null, animate: false };
async function designScene(id) {
  AIBUSY.scene = id; renderDrawer();
  const r = await api("/api/design/scene", { scene: id, model: CUR_MODEL });
  AIBUSY.scene = null;
  if (r) { await refresh(); toast(`${r.added} elements added to the scene${r.skipped ? ` (${r.skipped} skipped)` : ""} · ⌘Z to undo`); }
  renderDrawer();
}
async function animateWithAI(intent) {
  if (!SEL.length) return toast("Select the elements to animate first", true);
  AIBUSY.animate = true; renderDrawer();
  const r = await api("/api/design/animate", { layers: SEL, intent, model: CUR_MODEL });
  AIBUSY.animate = false;
  if (r) { await refresh(); toast(`Animated ${r.changed} element${r.changed === 1 ? "" : "s"} · ⌘Z to undo`); }
  renderDrawer();
}

// ---- animate panel -------------------------------------------------------------------------------------------
let ANIM_TAB = "in";
const PHASE_OF = (n) => (VAnim.PRESETS[n] || [])[0];
const PRESET_LABEL = (n) => n.replace(/_(in|out)$/, "").replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
const KF_PROPS = { shape: ["x", "y", "w", "h", "rotation", "scale", "opacity", "fill"], default: ["x", "y", "scale", "sx", "sy", "rotation", "opacity"] };
const PROP_LABEL = { x: "X", y: "Y", w: "Width", h: "Height", sx: "Stretch W", sy: "Stretch H", rotation: "Rotation", scale: "Scale", opacity: "Opacity", fill: "Colour" };
let DEMO_RAF = null;
function drawAnimate(body) {
  const ls = SEL.map(layerById).filter(Boolean);
  if (!ls.length) { body.append(h("div", { class: "hint" }, "Select an element on the canvas to animate it.")); return; }
  const l = ls[0];
  body.append(h("div", { class: "tabs" }, [["in", "Enter"], ["loop", "During"], ["out", "Exit"]].map(([k, lb]) =>
    h("button", { class: ANIM_TAB === k ? "on" : "", onclick: () => { ANIM_TAB = k; renderDrawer(); } }, lb))));
  const active = l.presets.find((p) => PHASE_OF(p.name) === ANIM_TAB);
  const names = Object.keys(VAnim.PRESETS).filter((n) => PHASE_OF(n) === ANIM_TAB && n !== "typewriter" || (n === "typewriter" && ANIM_TAB === "in" && l.type === "text"));
  const grid = h("div", { class: "presets" });
  grid.append(h("div", { class: "pre" + (active ? "" : " on"), onclick: () => active && batch(ls.flatMap((x) => x.presets.filter((p) => PHASE_OF(p.name) === ANIM_TAB).map((p) => ({ op: "preset_remove", args: { layer: x.id, name: p.name } })))) },
    h("div", { class: "demo" }, h("i", { style: { opacity: 0.35 } })), "None"));
  for (const n of names) {
    const demo = h("i");
    const el = h("div", { class: "pre" + (active?.name === n ? " on" : ""), title: VAnim.PRESETS[n] ? "" : n,
      onmouseenter: () => { startDemo(demo, n); PREVIEW = { id: l.id, presets: [{ name: n, params: {} }], t0: performance.now() }; },
      onmouseleave: () => { stopDemo(demo); PREVIEW = null; },
      onclick: () => { PREVIEW = null; batch(ls.map((x) => ({ op: "preset_add", args: { layer: x.id, name: n, replace_phase: true } }))); } },
      h("div", { class: "demo" }, demo), PRESET_LABEL(n));
    grid.append(el);
  }
  body.append(grid);
  if (active && ANIM_TAB !== "loop") {
    const d = active.duration ?? VAnim.presetDuration(l, active);
    body.append(h("div", { class: "field" }, h("span", {}, "Duration"),
      h("input", { type: "range", min: 0.1, max: 3, step: 0.05, value: d, onchange: (e) => op("preset_add", { layer: l.id, name: active.name, duration: e.target.value, params: active.params, replace_phase: true }) }),
      h("span", { class: "hint" }, `${(+d).toFixed(2)}s`)));
  }
  if (ls.length > 1) { body.append(h("div", { class: "hint", style: { marginTop: "10px" } }, `Applies to all ${ls.length} selected elements.`)); }
  // keyframes
  body.append(h("h3", {}, "Keyframes at the playhead"));
  const local = T - l.start, end = l.end ?? dur();
  const pr = VAnim.propsAt({ ...l, presets: [] }, local, end - l.start, P().canvas);
  const here = l.keyframes.find((k) => Math.abs(k.t - local) <= 0.02);
  if (T < l.start || T >= end) body.append(h("div", { class: "hint" }, "Move the playhead inside this element's time to key it."));
  else for (const prop of KF_PROPS[l.type === "shape" ? "shape" : "default"]) {
    const on = here && here[prop] != null;
    const val = prop === "fill" ? pr.fill || "—" : pr[prop] == null ? "—" : prop === "opacity" || prop === "scale" || prop === "sx" || prop === "sy" ? (+pr[prop]).toFixed(2) : Math.round(pr[prop]);
    const anyKf = l.keyframes.some((k) => k[prop] != null);
    body.append(h("div", { class: "kfl" }, h("span", {}, PROP_LABEL[prop], anyKf ? h("span", { class: "hint" }, " · animated") : null), h("span", { class: "v" }, String(val)),
      h("button", { class: "dia" + (on ? " on" : ""), title: on ? "Remove this keyframe" : "Add a keyframe with the current value",
        onclick: () => {
          if (on) return op("keyframe_remove", { layer: l.id, t: T, prop });
          let v = pr[prop];
          if (v == null) v = prop === "w" ? l.width || 400 : prop === "h" ? l.height || 400 : prop === "fill" ? l.fill || "#FFFFFF" : 1;
          op("keyframe_set", { layer: l.id, t: T, prop, value: typeof v === "number" ? r3(v) : v });
        } }, h("i"))));
  }
  if (here) body.append(h("div", { class: "field" }, h("span", {}, "Easing"),
    h("select", { onchange: (e) => op("keyframe_set", { layer: l.id, t: T, prop: "ease", value: e.target.value }) },
      Object.keys(VAnim.EASINGS).map((n) => h("option", { value: n, selected: here.ease === n }, n.replace(/_/g, " "))))));
  const kts = [...new Set(l.keyframes.map((k) => r3(l.start + k.t)))].sort((a, b) => a - b);
  body.append(h("div", { class: "row2", style: { display: "flex", gap: "6px", marginTop: "10px", flexWrap: "wrap" } },
    h("button", { disabled: !kts.some((t) => t < T - 0.02), onclick: () => seek([...kts].reverse().find((t) => t < T - 0.02)) }, "‹ Prev key"),
    h("button", { disabled: !kts.some((t) => t > T + 0.02), onclick: () => seek(kts.find((t) => t > T + 0.02)) }, "Next key ›"),
    h("button", { class: REC ? "on" : "", onclick: toggleRec, title: "While on, moving / stretching / rotating writes keyframes at the playhead" }, REC ? "● Recording" : "● Record motion"),
    l.keyframes.length || l.presets.length ? h("button", { class: "ghost", onclick: () => batch(ls.flatMap((x) => [{ op: "keyframes_clear", args: { layer: x.id } }, ...x.presets.map((p) => ({ op: "preset_remove", args: { layer: x.id, name: p.name } }))])) }, "Clear animation") : null));
  body.append(h("div", { class: "hint", style: { marginTop: "8px" } }, "Hover a preset to preview it on the canvas. With Record on, drag, stretch or rotate the element at different playhead times to build a motion path."));
  // AI
  body.append(h("h3", {}, "Animate with AI"));
  const intent = h("input", { placeholder: "e.g. bouncy and playful · slow cinematic reveal", style: { width: "100%" } });
  body.append(intent, h("div", { style: { marginTop: "6px", display: "flex", gap: "6px" } },
    h("button", { class: "primary", disabled: AIBUSY.animate, onclick: () => animateWithAI(intent.value) }, AIBUSY.animate ? "Animating…" : "✦ Animate selection")));
}
function startDemo(el, name) {
  stopDemo(el);
  const l = { id: "demo", type: "shape", x: 0, y: 0, scale: 1, rotation: 0, opacity: 1, sx: 1, sy: 1, width: 22, height: 22, presets: [{ name, params: {} }], keyframes: [] };
  const t0 = performance.now(), cv = { width: 200, height: 200 }, phase = PHASE_OF(name);
  const tick = () => {
    const t = ((performance.now() - t0) / 1000) % 1.6;
    const pr = VAnim.propsAt(l, phase === "out" ? t + 0.6 : t, phase === "out" ? 2 : 1.6, cv);
    const kx = clamp(pr.x / 6, -18, 18), ky = clamp(pr.y / 6, -14, 14);
    el.style.transform = `translate(${kx}px, ${ky}px) rotate(${pr.rotation}deg) scale(${pr.scale * pr.sx}, ${pr.scale * pr.sy})`;
    el.style.opacity = pr.opacity;
    el.style.clipPath = pr.reveal < 0.999 ? `inset(0 ${(1 - pr.reveal) * 100}% 0 0)` : "";
    if (pr.fill && name === "color_cycle") el.style.background = pr.fill;
    el._raf = requestAnimationFrame(tick);
  };
  tick();
}
function stopDemo(el) { cancelAnimationFrame(el._raf); el.style.transform = ""; el.style.opacity = ""; el.style.clipPath = ""; el.style.background = ""; }

// ---- position panel ------------------------------------------------------------------------------------------
function drawPosition(body) {
  const ls = SEL.map(layerById).filter(Boolean);
  if (!ls.length) { body.append(h("div", { class: "hint" }, "Select an element first.")); return; }
  const l = ls[0], cv = P().canvas;
  body.append(h("h3", {}, "Arrange"), h("div", { style: { display: "grid", gridTemplateColumns: "1fr 1fr", gap: "6px" } },
    [["front", "Bring to front"], ["up", "Forward"], ["down", "Backward"], ["back", "Send to back"]].map(([to, lb]) =>
      h("button", { onclick: () => batch(ls.map((x) => ({ op: "layer_move", args: { layer: x.id, to } }))) }, lb))));
  const b = BOX.get(l.id);
  const half = b ? [b.w / 2 / K, b.h / 2 / K] : [0, 0];
  const al = (ax, ay) => batch(ls.map((x) => {
    const bb = BOX.get(x.id), hw = bb ? bb.w / 2 / K : 0, hh = bb ? bb.h / 2 / K : 0, args = { layer: x.id };
    if (ax != null) args.x = r1(ax === 0 ? hw : ax === 1 ? cv.width / 2 : cv.width - hw);
    if (ay != null) args.y = r1(ay === 0 ? hh : ay === 1 ? cv.height / 2 : cv.height - hh);
    return { op: "layer_set", args };
  }));
  void half;
  body.append(h("h3", {}, "Align to page"), h("div", { style: { display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "6px" } },
    h("button", { onclick: () => al(0, null) }, "Left"), h("button", { onclick: () => al(1, null) }, "Centre"), h("button", { onclick: () => al(2, null) }, "Right"),
    h("button", { onclick: () => al(null, 0) }, "Top"), h("button", { onclick: () => al(null, 1) }, "Middle"), h("button", { onclick: () => al(null, 2) }, "Bottom")));
  if (ls.length === 1) {
    const pr = b ? b.pr : { x: 0, y: 0 };
    const f = (label, value, fn, attrs = {}) => h("div", { class: "field" }, h("span", {}, label), h("input", { type: "number", value, ...attrs, onchange: (e) => fn(e.target.value) }));
    body.append(h("h3", {}, "Size & position"),
      f("X", Math.round(pr.x), (v) => setL(l, { x: +v })), f("Y", Math.round(pr.y), (v) => setL(l, { y: +v })));
    if (l.type === "shape") body.append(f("Width", l.width ?? 400, (v) => setL(l, { width: v })), f("Height", l.height ?? 400, (v) => setL(l, { height: v })));
    else body.append(f("Stretch W", l.sx ?? 1, (v) => setL(l, { sx: v }), { step: 0.05 }), f("Stretch H", l.sy ?? 1, (v) => setL(l, { sy: v }), { step: 0.05 }));
    body.append(f("Scale", l.scale, (v) => setL(l, { scale: v }), { step: 0.05 }), f("Rotation", l.rotation, (v) => setL(l, { rotation: v })),
      f("Starts", r3(l.start), (v) => setL(l, { start: v }), { step: 0.1 }), f("Ends", r3(l.end ?? dur()), (v) => setL(l, { end: v }), { step: 0.1 }));
  }
  body.append(h("h3", {}, "Reuse"), h("button", { onclick: async () => {
    const name = prompt("Template name (reusable in any design):");
    if (!name) return;
    const r = await api("/api/templates", { name, layers: SEL });
    if (r) { TEMPLATES = null; LIB = null; toast(`Saved template “${name}”`); }
  } }, "Save selection as template"));
}

// ---- AI panel: chat with the connected model + quick actions -------------------------------------------------
let ws = null, CHATLOG = [], AIBUSY_CHAT = false, MODELS = null, CUR_MODEL = null;
const MODEL_LABEL = () => { const m = MODELS?.models?.find((x) => x.key === CUR_MODEL); return m ? m.label : "your connected AI model"; };
function connectChat() {
  ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/chat`);
  ws.onmessage = (m) => {
    const ev = JSON.parse(m.data);
    if (ev.models) { MODELS = ev.models; if (ev.model) CUR_MODEL = ev.model; }
    if (ev.type === "models") { if (VIEW === "ai") renderDrawer(); return; }
    if (ev.type === "history") { CHATLOG = ev.log || []; AIBUSY_CHAT = !!ev.busy; if (VIEW === "ai") renderDrawer(); return; }
    if (ev.type === "status") { AIBUSY_CHAT = !!ev.busy; if (VIEW === "ai") renderDrawer(); return; }
    if (["status_step", "auditor", "mode"].includes(ev.type)) return;
    if (ev.type === "tool_result") { const t = CHATLOG.find((x) => x.type === "tool" && x.id === ev.id); if (t) t.ok = ev.ok; }
    else if (ev.type === "permission_done") CHATLOG = CHATLOG.filter((x) => x.id !== ev.id);
    else CHATLOG.push(ev);
    if (ev.type === "tool_result" || ev.type === "done") refresh();
    if (VIEW === "ai") renderChatMsgs();
  };
  ws.onclose = () => setTimeout(connectChat, 1500);
}
function md(t) {
  const esc = (s) => s.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[c]);
  return esc(t).split(/\n{2,}/).map((p) => `<p>${p.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\n/g, "<br>")}</p>`).join("");
}
function renderChatMsgs() {
  const box = $("#chat .msgs");
  if (!box) return;
  box.innerHTML = "";
  for (const ev of CHATLOG) {
    let el = null;
    if (ev.type === "user") el = h("div", { class: "m-user" }, ev.text);
    else if (ev.type === "assistant") { el = h("div", { class: "m-ai" }); el.innerHTML = md(ev.text || ""); }
    else if (ev.type === "tool") el = h("div", { class: "m-tool", title: ev.summary }, h("span", { class: ev.ok === undefined ? "" : ev.ok ? "ok" : "bad" }, ev.ok === undefined ? "▸ " : ev.ok ? "✓ " : "✗ "), ev.summary);
    else if (ev.type === "error") el = h("div", { class: "m-err" }, ev.text);
    else if (ev.type === "done") el = h("div", { class: "m-meta" }, ev.ok ? `done · ${ev.seconds}s` : `stopped ${ev.error || ""}`);
    else if (ev.type === "notice") el = h("div", { class: "m-note" }, ev.text);
    else if (ev.type === "audit") el = h("div", { class: "m-note" }, `Audit: ${ev.score ?? ""} ${ev.summary || ""}`);
    else if (ev.type === "permission" || ev.type === "plan_offer") {
      const answer = (a) => { ws.send(JSON.stringify(ev.type === "plan_offer" ? { type: "plan_answer", id: ev.id, answer: a, context: chatContext() } : { type: "permission", id: ev.id, allow: a })); CHATLOG = CHATLOG.filter((x) => x.id !== ev.id); renderChatMsgs(); };
      el = h("div", { class: "m-perm" }, h("div", {}, ev.type === "plan_offer" ? "Plan ready" : `Allow ${ev.tool || "this edit"}?`),
        ev.detail ? h("code", {}, ev.detail) : ev.text ? h("div", { class: "m-ai" }, ev.text) : null,
        h("div", { style: { display: "flex", gap: "6px" } }, h("button", { class: "primary", onclick: () => answer(ev.type === "plan_offer" ? "go" : true) }, ev.type === "plan_offer" ? "Do it" : "Allow"),
          h("button", { onclick: () => answer(ev.type === "plan_offer" ? "no" : false) }, ev.type === "plan_offer" ? "Not now" : "Deny")));
    }
    if (el) box.append(el);
  }
  if (AIBUSY_CHAT) box.append(h("div", { class: "m-meta" }, "Working…"));
  box.scrollTop = box.scrollHeight;
}
function chatContext() {
  const l = SEL.length ? layerById(SEL[0]) : null, c = cur();
  return { playhead: T, selection: l ? { kind: "layer", id: l.id } : null, scene: c?.sc.id,
    label: [l ? `${l.type} ${l.id}` : null, c ? `${c.sc.name || c.sc.id}` : null, `@ ${fmt(T)}`].filter(Boolean).join(" · ") };
}
function sendChat(text) {
  if (!ws || ws.readyState !== 1) return toast("The AI isn't connected yet", true);
  if (AIBUSY_CHAT) { ws.send(JSON.stringify({ type: "interrupt" })); return; }
  if (!text.trim()) return;
  ws.send(JSON.stringify({ type: "ask", text, context: chatContext() }));
}
function drawAI(body) {
  body.style.display = "flex"; body.style.flexDirection = "column";
  const ta = h("textarea", { placeholder: SEL.length ? "Ask about the selected element… e.g. make it bounce in and glow" : "Describe the motion video you want, or ask for a change…", rows: 3,
    onkeydown: (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); sendChat(ta.value); ta.value = ""; } } });
  const pick = h("select", { title: "AI model (any connected provider)", onchange: (e) => {
    if (e.target.value === "__manage") { location.href = "/settings#providers"; return; }
    CUR_MODEL = e.target.value; ws?.send(JSON.stringify({ type: "model", model: CUR_MODEL })); } });
  if (MODELS) {
    for (const pv of MODELS.providers || []) {
      const ms = (MODELS.models || []).filter((m) => m.provider === pv.id);
      if (ms.length) pick.append(h("optgroup", { label: pv.name + (pv.ready ? "" : " · needs key") }, ms.map((m) => h("option", { value: m.key, selected: m.key === CUR_MODEL, disabled: !pv.ready }, m.label))));
    }
    pick.append(h("option", { value: "__manage" }, "Connect another provider…"));
  } else pick.append(h("option", {}, "Connecting…"));
  const c = cur();
  const chips = [
    ["✦ Storyboard from a brief", () => { VIEW = "storyboard"; renderDrawer(); }],
    c && c.sc.notes ? ["✦ Design this scene", () => designScene(c.sc.id)] : ["✦ Fill this scene", () => sendChat(`Design ${c?.sc.id || "the current scene"}: add animated shapes and a title that fit the rest of the video.`)],
    SEL.length ? ["✦ Animate selection", () => { VIEW = "animate"; renderDrawer(); }] : null,
    ["Check my design", runCheck],
  ].filter(Boolean);
  body.append(
    h("div", { class: "chips" }, chips.map(([lb, fn]) => h("button", { class: "chip", onclick: fn }, lb))),
    h("div", { id: "chat", style: { flex: 1, minHeight: 0 } }, h("div", { class: "msgs" }),
      h("div", { class: "composer" }, ta, h("div", { class: "row2" }, pick, h("div", { style: { flex: 1 } }),
        h("button", { class: "ghost", title: "New chat", onclick: () => ws?.send(JSON.stringify({ type: "reset" })) }, "New"),
        h("button", { class: "primary", onclick: () => { sendChat(ta.value); ta.value = ""; } }, AIBUSY_CHAT ? "Stop" : "Send")))));
  renderChatMsgs();
  if (!CHATLOG.length) $("#chat .msgs").append(h("div", { class: "hint" }, "The assistant edits this design with the same tools you use: scenes, shapes, text, animation. Every change is one undo away."));
}

// ---- timeline: scenes strip + element bars ----------------------------------------------------------------------
let PPS = 60; // px per second
function renderTimeline() {
  if (!ST) return;
  const scroll = $("#tl-scroll"), inner = $("#tl-inner"), D = Math.max(dur(), 0.5);
  PPS = Math.max(18, (scroll.clientWidth - 100) / D);
  inner.style.width = D * PPS + 100 + "px";
  const strip = $("#scenes"); strip.innerHTML = "";
  const c = cur(), starts = sceneStarts();
  P().scenes.forEach((sc, i) => {
    const el = h("div", { class: "scn" + (c && c.sc.id === sc.id ? " cur" : "") + (SCENE_SEL === sc.id ? " picked" : ""), style: { width: sc.dur * PPS - (i ? 4 : 0) + "px" }, title: sc.notes || sc.name },
      h("div", { class: "bgc", style: { background: bgCss(sc) } }),
      h("div", { class: "nm" }, sc.name || `Scene ${i + 1}`), h("div", { class: "du" }, `${sc.dur.toFixed(1)}s`),
      sc.transition !== "cut" ? h("div", { class: "tr" }, sc.transition) : null,
      h("button", { class: "more", title: "Scene options", onclick: (e) => { e.stopPropagation(); sceneMenu(e, sc, i); } }, "⋯"),
      h("div", { class: "grip", title: "Drag to change the length" }));
    el.addEventListener("pointerdown", (e) => sceneDrag(e, el, sc, i, starts[i]));
    strip.append(el);
  });
  strip.append(h("button", { id: "add-scene", title: "Add a scene", onclick: () => op("scene_add", { dur: 4, after: c?.sc.id }).then(() => { const n = sceneAt(c ? c.end : 0); if (n) seek(n.start); }) }, "+"));
  // rows: elements of the current scene, then whole-video elements; top of the stack first
  const rows = $("#rows"); rows.innerHTML = "";
  const bgs = bgIds();
  const ls = P().layers.filter((l) => !bgs.has(l.id) && l.type !== "captions" && (l.scene == null || l.scene === c?.sc.id)).reverse();
  for (const l of ls) {
    const end = l.end ?? dur();
    const bar = h("div", { class: "bar" + (SEL.includes(l.id) ? " on" : ""), style: { left: l.start * PPS + "px", width: Math.max(8, (end - l.start) * PPS) + "px" } },
      h("div", { class: "ed l" }), barLabel(l), l.scene == null ? h("span", { class: "tag" }, "· all scenes") : null, h("div", { class: "ed r" }));
    for (const k of l.keyframes) bar.append(h("div", { class: "kf", style: { left: k.t * PPS + "px" }, title: `Keyframe ${r3(l.start + k.t)}s`, onpointerdown: (e) => { e.stopPropagation(); seek(l.start + k.t); select([l.id]); } }));
    bar.addEventListener("pointerdown", (e) => barDrag(e, bar, l));
    rows.append(h("div", { class: "row" }, bar));
  }
  if (!ls.length) rows.append(h("div", { class: "hint", style: { padding: "4px 2px" } }, "This scene is empty: add elements from the left panel."));
  drawPlayhead();
}
function barLabel(l) {
  const t = l.type === "text" ? `T  ${l.text || ""}` : l.type === "shape" ? `◆  ${SHAPE_LABELS[l.shape] || l.shape}` : l.type === "image" ? `▣  ${(l.image || "").split(/[\\/]/).pop()}` : l.type === "video" ? "▶  video" : l.type;
  return h("span", {}, t);
}
function drawPlayhead() { $("#playhead").style.left = 16 + T * PPS + "px"; $("#time").innerHTML = `<b>${fmt(T)}</b> / ${fmt(dur())}`; }
function sceneMenu(e, sc, i) {
  menu(e.currentTarget, [
    ["Duplicate scene", () => op("scene_dup", { scene: sc.id })],
    ["Add scene after", () => op("scene_add", { dur: 4, after: sc.id })],
    i > 0 ? ["Move left", () => op("scene_move", { scene: sc.id, index: i - 1 })] : null,
    i < P().scenes.length - 1 ? ["Move right", () => op("scene_move", { scene: sc.id, index: i + 1 })] : null,
    ["Edit in storyboard", () => { VIEW = "storyboard"; openDrawer(); renderDrawer(); }],
    P().scenes.length > 1 ? ["Delete scene", () => confirm(`Delete ${sc.name || sc.id} and its elements?`) && op("scene_remove", { scene: sc.id })] : null,
  ]);
}
function sceneDrag(e, el, sc, i, st) {
  if (e.button !== 0 || e.target.closest(".more")) return;
  const grip = e.target.classList.contains("grip"), x0 = e.clientX;
  el.setPointerCapture(e.pointerId);
  let moved = false, newDur = sc.dur, target = i;
  el.onpointermove = (ev) => {
    const dx = ev.clientX - x0;
    if (!moved && Math.abs(dx) < 4) return;
    moved = true;
    if (grip) {
      el.classList.add("resizing");
      newDur = clamp(Math.round((sc.dur + dx / PPS) * 10) / 10, 0.5, 60);
      el.style.width = newDur * PPS - (i ? 4 : 0) + "px";
      $(".du", el).textContent = `${newDur.toFixed(1)}s`;
    } else {
      el.classList.add("dragging");
      el.style.transform = `translateX(${dx}px)`;
      const mid = st + sc.dur / 2 + dx / PPS, ss = sceneStarts();
      target = P().scenes.reduce((n, s, j) => (j !== i && mid > ss[j] + s.dur / 2 ? n + 1 : n), 0);
    }
  };
  el.onpointerup = () => {
    el.onpointermove = el.onpointerup = null;
    if (!moved) { seek(st); SCENE_SEL = sc.id; select([]); openView("properties"); return; }
    if (grip && newDur !== sc.dur) op("scene_set", { scene: sc.id, dur: newDur });
    else if (!grip && target !== i) op("scene_move", { scene: sc.id, index: target });
    else renderTimeline();
  };
}
function barDrag(e, bar, l) {
  if (e.button !== 0) return;
  e.stopPropagation();
  if (!SEL.includes(l.id)) select([l.id]);
  const side = e.target.classList.contains("ed") ? (e.target.classList.contains("l") ? "l" : "r") : "m";
  const x0 = e.clientX, s0 = l.start, e0 = l.end ?? dur();
  bar.setPointerCapture(e.pointerId);
  let ns = s0, ne = e0, moved = false;
  const c = sceneAt(s0 + 0.001);
  bar.onpointermove = (ev) => {
    const d = (ev.clientX - x0) / PPS;
    if (!moved && Math.abs(ev.clientX - x0) < 3) return;
    moved = true;
    const lo = 0, hi = dur();
    if (side === "l") ns = clamp(r3(s0 + d), lo, e0 - 0.1);
    else if (side === "r") ne = clamp(r3(e0 + d), s0 + 0.1, hi);
    else { const len = e0 - s0; ns = clamp(r3(s0 + d), lo, hi - len); ne = r3(ns + len); }
    // snap to scene edges and the playhead
    for (const t of [...sceneStarts(), dur(), T]) {
      if (side !== "r" && Math.abs(ns - t) * PPS < 6) { if (side === "m") ne = r3(ne + t - ns); ns = t; }
      if (side === "r" && Math.abs(ne - t) * PPS < 6) ne = t;
    }
    bar.style.left = ns * PPS + "px"; bar.style.width = Math.max(8, (ne - ns) * PPS) + "px";
  };
  bar.onpointerup = () => {
    bar.onpointermove = bar.onpointerup = null;
    if (!moved) { seek(clamp(T, s0, e0 - 0.01)); if (VIEW !== "animate") openView("properties"); return; }
    op("layer_set", { layer: l.id, start: ns, end: ne });
    void c;
  };
}
$("#tl-scroll").addEventListener("pointerdown", (e) => {
  if (e.target.closest(".scn, .bar, button")) return;
  const r = $("#tl-inner").getBoundingClientRect();
  const at = (ev) => seek(clamp((ev.clientX - r.left - 16) / PPS, 0, dur() - 0.001));
  at(e);
  const mv = (ev) => at(ev);
  const up = () => { window.removeEventListener("pointermove", mv); window.removeEventListener("pointerup", up); };
  window.addEventListener("pointermove", mv); window.addEventListener("pointerup", up);
});
function seek(t) {
  if (t == null) return;
  const before = cur()?.sc.id;
  T = clamp(t, 0, Math.max(0, dur() - 0.001));
  syncMusic(true);
  if (cur()?.sc.id !== before) {
    if (BG_SEL) BG_SEL = null;  // the selected background was the scene we left
    renderTimeline(); renderCtx(); drawOverlay(); if (VIEW === "storyboard" || VIEW === "properties") renderDrawer();
  }
  else drawPlayhead();
  if (VIEW === "animate") renderDrawerSoon();
}
let DR_T = null;
function renderDrawerSoon() { clearTimeout(DR_T); DR_T = setTimeout(renderDrawer, 120); }

// ---- playback + music --------------------------------------------------------------------------------------------
const AUD = new Map();
function syncMusic(force) {
  for (const m of P()?.music || []) {
    let a = AUD.get(m.id);
    if (!a) { a = new Audio(`/api/source/${encodeURIComponent(m.source)}/media?k=${srcKey(m.source)}`); a.loop = m.loop; AUD.set(m.id, a); }
    const src = P().sources[m.source], end = m.end ?? dur();
    a.volume = clamp(m.volume, 0, 1);
    let want = T >= m.start && T < end ? m.in + (T - m.start) : null;
    if (want != null && src && want >= src.duration) want = m.loop ? want % src.duration : null;
    if (!PLAYING || want == null) { if (!a.paused) a.pause(); if (want != null && force) a.currentTime = want; continue; }
    if (a.paused) { a.currentTime = want; a.play().catch(() => {}); }
    else if (Math.abs(a.currentTime - want) > 0.3) a.currentTime = want;
  }
}
function setPlaying(on) {
  PLAYING = on && dur() > 0;
  if (PLAYING && T >= dur() - 0.05) T = 0;
  $("#b-play").innerHTML = ""; $("#b-play").append(ic(PLAYING ? "pause" : "play", 16));
  LAST_TS = performance.now();
  syncMusic(true);
  if (!PLAYING) { renderTimeline(); renderCtx(); }
}
function frame(ts) {
  if (PLAYING) {
    const before = cur()?.sc.id;
    T += (ts - LAST_TS) / 1000;
    if (T >= dur()) { T = dur() - 0.001; if (document.body.classList.contains("present")) T = 0; else setPlaying(false); }
    if (cur()?.sc.id !== before) { renderTimeline(); renderCtx(); }
    drawPlayhead();
    syncMusic(false);
  }
  LAST_TS = ts;
  drawStage();
  requestAnimationFrame(frame);
}
$("#b-play").onclick = () => setPlaying(!PLAYING);
function toggleRec() { REC = !REC; $("#b-rec").classList.toggle("on", REC); $("#tl-hint").textContent = REC ? "Recording motion: changes at the playhead become keyframes" : ""; if (VIEW === "animate") renderDrawer(); }
$("#b-rec").onclick = toggleRec;

// ---- top bar -----------------------------------------------------------------------------------------------------
function menu(anchor, items) {
  $$(".menu").forEach((m) => m.remove());
  const r = anchor.getBoundingClientRect();
  const m = h("div", { class: "menu", style: { left: Math.min(r.left, innerWidth - 230) + "px", top: r.bottom + 6 + "px" } },
    items.filter(Boolean).map(([lb, fn, k]) => h("button", { onclick: () => { m.remove(); fn(); } }, lb, k ? h("span", { class: "k" }, k) : null)));
  document.body.append(m);
  setTimeout(() => document.addEventListener("pointerdown", function off(e) { if (!m.contains(e.target)) { m.remove(); document.removeEventListener("pointerdown", off); } }), 0);
}
$("#b-home").append(ic("home", 18)); $("#b-home").onclick = () => { location.href = "/"; };
$("#b-undo").append(ic("undo", 18)); $("#b-redo").append(ic("redo", 18));
$("#b-undo").onclick = () => api("/api/undo").then(refresh);
$("#b-redo").onclick = () => api("/api/redo").then(refresh);
$("#b-file").onclick = (e) => menu(e.currentTarget, [
  ["Rename", () => { $("#title").focus(); document.execCommand("selectAll"); }],
  ["Save a version…", async () => { const n = prompt("Name this version (you can jump back to it any time):"); if (n) { await api("/api/version", { name: n }); toast(`Saved version “${n}”`); } }],
  ["Export video…", openExport, "E"],
  ["Back to all projects", () => { location.href = "/"; }],
]);
$("#b-resize").onclick = (e) => menu(e.currentTarget, [["9:16 · Reels / TikTok", "reels"], ["1:1 · Square", "square"], ["4:5 · Portrait", "portrait45"], ["16:9 · Landscape", "landscape"]]
  .map(([lb, k]) => [lb, () => op("canvas_set", { preset: k })]));
$("#title").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); e.target.blur(); } e.stopPropagation(); });
$("#title").addEventListener("blur", (e) => { const v = e.target.textContent.trim(); if (v && v !== P().name) op("rename", { name: v }); else e.target.textContent = P().name; });
$("#b-preview").onclick = () => { document.body.classList.add("present"); T = 0; setPlaying(true); setTimeout(layoutAll, 30); };
$("#present-x").onclick = exitPresent;
function exitPresent() { document.body.classList.remove("present"); setPlaying(false); setTimeout(layoutAll, 30); }
$("#b-check").onclick = runCheck;
async function runCheck() {
  toast("Checking the design…");
  const r = await api("/api/audit", {});
  if (!r) return;
  const sev = { "must-fix": "#f87171", "should-fix": "#fbbf24", nice: "#93a69b" };
  AK.modal(h("div", { style: { maxWidth: "520px" } }, h("h2", { style: { margin: "0 0 6px" } }, `Score ${r.score ?? "—"}`),
    h("div", { class: "hint" }, r.summary || (r.passed ? "Looks good." : "A few things to look at:")),
    h("div", { style: { marginTop: "12px", maxHeight: "50vh", overflow: "auto" } }, (r.findings || []).map((f) =>
      h("div", { class: "card" }, h("b", { style: { color: sev[f.severity] || "inherit" } }, f.severity), ` · ${f.category || ""}`,
        f.at != null ? h("button", { class: "ghost", onclick: () => seek(f.at) }, ` @${f.at}s`) : null, h("div", {}, f.message)))),
    h("div", { style: { marginTop: "12px", display: "flex", gap: "6px" } }, h("button", { class: "primary", onclick: () => { $$(".ak-veil").forEach((v) => v.remove()); VIEW = "ai"; openDrawer(); renderDrawer(); sendChat("Fix the must-fix findings of the audit, then re-run it."); } }, "Ask AI to fix"))));
}
$("#b-export").onclick = openExport;
async function openExport() {
  const d = $("#dlg-export");
  d.innerHTML = "";
  const st = h("div", { class: "hint" }, `${P().canvas.width}×${P().canvas.height} · ${fmt(dur())} · MP4`);
  const bar = h("div", { class: "prog" }, h("i"));
  const go = h("button", { class: "primary" }, "Export MP4");
  d.append(h("h2", { style: { margin: "0 0 4px" } }, "Export"), st, bar, h("div", { style: { display: "flex", gap: "6px", justifyContent: "flex-end" } }, h("button", { onclick: () => d.close() }, "Close"), go));
  d.showModal();
  go.onclick = async () => {
    go.disabled = true;
    const r = await api("/api/exports", {});
    if (!r) { go.disabled = false; return; }
    const poll = async () => {
      const j = await (await fetch(`/api/exports/${r.id}`)).json();
      $("i", bar).style.width = Math.round((j.progress || 0) * 100) + "%";
      st.textContent = j.status === "running" ? `${j.stage || "Rendering"}… ${Math.round((j.progress || 0) * 100)}%` : j.status === "done" ? `Saved ${j.result.file}` : j.error || j.status;
      if (j.status === "running") return setTimeout(poll, 600);
      go.disabled = false;
      if (j.status === "done") {
        go.textContent = "Show in folder";
        go.onclick = () => api("/api/exports/reveal", { path: j.result.output });
      }
    };
    poll();
  };
}

// ---- zoom, keyboard, resize --------------------------------------------------------------------------------------
$("#z-in").onclick = () => { ZOOM = Math.min(4, K * 1.25); layoutAll(); };
$("#z-out").onclick = () => { ZOOM = Math.max(0.05, K / 1.25); layoutAll(); };
$("#z-fit").onclick = () => { ZOOM = null; layoutAll(); };
$("#stagewrap").addEventListener("wheel", (e) => { if (!e.ctrlKey && !e.metaKey) return; e.preventDefault(); ZOOM = clamp(K * (e.deltaY < 0 ? 1.1 : 1 / 1.1), 0.05, 4); layoutAll(); }, { passive: false });
document.addEventListener("keydown", (e) => {
  if (document.body.classList.contains("present")) { if (e.key === "Escape") exitPresent(); if (e.key === " ") { e.preventDefault(); setPlaying(!PLAYING); } return; }
  if (e.key === "Escape" && (POP || $(".menu"))) { closePop(); document.querySelectorAll(".menu").forEach((m) => m.remove()); return; }
  if (typing()) return;
  const mod = e.metaKey || e.ctrlKey;
  if (mod && e.key.toLowerCase() === "z") { e.preventDefault(); (e.shiftKey ? $("#b-redo") : $("#b-undo")).click(); return; }
  if (mod && e.key.toLowerCase() === "d") { e.preventDefault(); duplicateSel(); return; }
  if (mod && e.key.toLowerCase() === "a") { e.preventDefault(); const c = cur(); select(P().layers.filter((l) => !bgIds().has(l.id) && visibleAt(l, T) && (!c || l.scene === c.sc.id || l.scene == null)).map((l) => l.id)); return; }
  if (e.key === " ") { e.preventDefault(); setPlaying(!PLAYING); return; }
  if (e.key === "Escape") { select([]); return; }
  if ((e.key === "Delete" || e.key === "Backspace") && !SEL.length && bgScene()) { e.preventDefault(); if (bgScene().background) setBg(bgScene(), ""); return; }
  if (e.key === "Delete" || e.key === "Backspace") { e.preventDefault(); deleteSel(); return; }
  if (e.key === "p" || e.key === "P") { $("#b-preview").click(); return; }
  if (e.key === "e" || e.key === "E") { openExport(); return; }
  if (e.key === "r" || e.key === "R") { toggleRec(); return; }
  if (e.key === "ArrowLeft" && !SEL.length) { seek(T - (e.shiftKey ? 1 : 1 / P().canvas.fps)); return; }
  if (e.key === "ArrowRight" && !SEL.length) { seek(T + (e.shiftKey ? 1 : 1 / P().canvas.fps)); return; }
  const nudge = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[e.key];
  if (nudge && SEL.length) {
    e.preventDefault();
    const s = e.shiftKey ? 10 : 1;
    batch(SEL.map((id) => {
      const l = layerById(id), b = BOX.get(id);
      if (!l || !b) return null;
      const x = r1(b.pr.x + nudge[0] * s), y = r1(b.pr.y + nudge[1] * s);
      return keyed(l, "x", "y") ? null : { op: "layer_set", args: { layer: id, x, y } };
    }));
  }
});
function layoutAll() { if (!ST) return; layout(); renderTimeline(); CONTENT_GEN++; }
new ResizeObserver(() => layoutAll()).observe($("#stagewrap"));

// ---- live updates from the CLI / AI ------------------------------------------------------------------------------
function listen() {
  const es = new EventSource("/api/events");
  es.onmessage = (m) => {
    const ev = JSON.parse(m.data);
    if (ev.closed) { es.close(); location.href = "/"; return; }
    if (ST && ev.rev !== ST.rev && !SAVING && !OVR.size) refresh();
  };
  es.onerror = () => { es.close(); setTimeout(listen, 2000); };
}

function renderAll() {
  $("#title").textContent = P().name;
  if (document.activeElement !== $("#title")) $("#title").textContent = P().name;
  layout();
  renderCtx();
  renderTimeline();
  if (VIEW !== "ai" || !$("#chat")) renderDrawer();
  const empty = !P().layers.some((l) => !bgIds().has(l.id));
  $("#cv-empty").innerHTML = "";
  if (empty) $("#cv-empty").append(h("b", {}, "Start with a shape"), h("div", {}, "Drag one from Elements, write a storyboard, or ask the AI."));
}

// ---- boot ----------------------------------------------------------------------------------------------------------
(async function boot() {
  await AK.load?.();
  $("#b-play").append(ic("play", 16));
  renderRail();
  await refresh();
  if (!ST) return;
  loadTypo();
  connectChat();
  listen();
  requestAnimationFrame((ts) => { LAST_TS = ts; frame(ts); });
})();

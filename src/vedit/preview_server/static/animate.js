// Port of src/vedit/ops/animate.py for the editor's live preview: a layer's transform at a moment.
// Keep in sync with the Python file; tests/test_live_preview.py checks both give the same numbers.
(function (root) {
  "use strict";
  const clamp01 = (p) => Math.max(0, Math.min(1, p));

  function bounceOut(p) {
    const n1 = 7.5625, d1 = 2.75;
    if (p < 1 / d1) return n1 * p * p;
    if (p < 2 / d1) { p -= 1.5 / d1; return n1 * p * p + 0.75; }
    if (p < 2.5 / d1) { p -= 2.25 / d1; return n1 * p * p + 0.9375; }
    p -= 2.625 / d1; return n1 * p * p + 0.984375;
  }
  function elasticOut(p) {
    if (p === 0 || p === 1) return p;
    return 2 ** (-10 * p) * Math.sin((p * 10 - 0.75) * (2 * Math.PI) / 3) + 1;
  }
  function backOut(p, s = 1.70158) { p -= 1; return p * p * ((s + 1) * p + s) + 1; }

  const EASINGS = {
    linear: (p) => p,
    ease_in: (p) => p * p * p,
    ease_out: (p) => 1 - (1 - p) ** 3,
    ease_in_out: (p) => (p < 0.5 ? 4 * p ** 3 : 1 - (-2 * p + 2) ** 3 / 2),
    back_out: backOut,
    back_in: (p) => p * p * (2.70158 * p - 1.70158),
    bounce_out: bounceOut,
    elastic_out: elasticOut,
    step: (p) => (p < 1 ? 0 : 1),
  };
  function ease(name, p) { return (EASINGS[name] || EASINGS.linear)(clamp01(p)); }

  function parsePos(v, total) {
    if (typeof v === "string") {
      const s = v.trim();
      if (s.endsWith("%")) return (parseFloat(s.slice(0, -1)) / 100) * total;
      return parseFloat(s);
    }
    return Number(v);
  }

  const num = (v, d) => (v == null || v === "" || isNaN(Number(v)) ? d : Number(v));

  // fn(props, p, t, params)
  const fade = (pr, p) => { pr.opacity *= ease("ease_out", p); };
  const pop = (pr, p) => { pr.scale *= Math.max(0, ease("back_out", p)); pr.opacity *= Math.min(1, p * 3); };
  const slide = (pr, p, t, k) => {
    const d = num(k.distance, 220), dir = k.direction || "up", off = (1 - ease("ease_out", p)) * d;
    if (dir === "up") pr.y += off; else if (dir === "down") pr.y -= off;
    else if (dir === "left") pr.x += off; else if (dir === "right") pr.x -= off;
    pr.opacity *= ease("ease_out", p);
  };
  function hex4(v) {
    let h = String(v).trim().replace(/^#/, "");
    if (h.length === 3 || h.length === 4) h = h.split("").map((c) => c + c).join("");
    if (h.length === 6) h += "FF";
    return [0, 2, 4, 6].map((i) => parseInt(h.slice(i, i + 2), 16));
  }
  // colour between a and b, same rounding as animate.py mix_hex
  function mixHex(a, b, p) {
    const ca = hex4(a), cb = hex4(b);
    const v = ca.map((x, i) => Math.floor(x + (cb[i] - x) * p + 0.5));
    const alpha = ca[3] !== 255 || cb[3] !== 255;
    return "#" + (alpha ? v : v.slice(0, 3)).map((n) => Math.max(0, Math.min(255, n)).toString(16).toUpperCase().padStart(2, "0")).join("");
  }
  const wipe = (pr, p) => { pr.reveal *= ease("ease_in_out", p); };
  const PRESETS = {
    fade_in: ["in", 0.4, fade], fade_out: ["out", 0.4, fade],
    pop_in: ["in", 0.35, pop], pop_out: ["out", 0.25, pop],
    slide_in: ["in", 0.45, slide], slide_out: ["out", 0.4, slide],
    zoom_in: ["in", 0.5, (pr, p, t, k) => { const s = num(k.from, 1.8); pr.scale *= s + (1 - s) * ease("ease_out", p); pr.opacity *= ease("ease_out", p); }],
    punch: ["in", 0.25, (pr, p, t, k) => { pr.scale *= 1 + num(k.amount, 0.25) * (1 - ease("ease_out", p)); }],
    spin_in: ["in", 0.5, (pr, p, t, k) => { pr.rotation += num(k.degrees, -180) * (1 - ease("ease_out", p)); pr.scale *= ease("back_out", p); }],
    drop_in: ["in", 0.7, (pr, p, t, k) => { pr.y -= (1 - ease("bounce_out", p)) * num(k.distance, 400); pr.opacity *= Math.min(1, p * 4); }],
    typewriter: ["in", 0.0, (pr, p) => { pr.reveal *= p; }],
    shake: ["loop", 0, (pr, p, t, k) => { const a = num(k.amplitude, 10), f = num(k.frequency, 18); pr.x += Math.sin(t * f * 2.1) * a; pr.y += Math.cos(t * f * 1.7) * a * 0.6; }],
    bob: ["loop", 0, (pr, p, t, k) => { pr.y += Math.sin((2 * Math.PI * t) / num(k.period, 2.0)) * num(k.amplitude, 14); }],
    pulse: ["loop", 0, (pr, p, t, k) => { pr.scale *= 1 + num(k.amount, 0.06) * (0.5 + 0.5 * Math.sin((2 * Math.PI * t) / num(k.period, 0.8))); }],
    wiggle: ["loop", 0, (pr, p, t, k) => { pr.rotation += Math.sin((2 * Math.PI * t) / num(k.period, 0.6)) * num(k.degrees, 6); }],
    spin: ["loop", 0, (pr, p, t, k) => { pr.rotation += (360 * t) / num(k.period, 3.0); }],
    float: ["loop", 0, (pr, p, t, k) => { const per = num(k.period, 3.0); pr.y += Math.sin((2 * Math.PI * t) / per) * num(k.amplitude, 18); pr.rotation += Math.sin((2 * Math.PI * t) / (per * 1.3)) * num(k.degrees, 2.5); }],
    sway: ["loop", 0, (pr, p, t, k) => { pr.rotation += Math.sin((2 * Math.PI * t) / num(k.period, 2.4)) * num(k.degrees, 8); }],
    orbit: ["loop", 0, (pr, p, t, k) => { const r = num(k.radius, 24), per = num(k.period, 4.0); pr.x += Math.cos((2 * Math.PI * t) / per) * r; pr.y += Math.sin((2 * Math.PI * t) / per) * r; }],
    drift: ["loop", 0, (pr, p, t, k) => { pr.x += num(k.speed_x, 30) * t; pr.y += num(k.speed_y, 0) * t; }],
    push: ["loop", 0, (pr, p, t, k) => { pr.scale *= 1 + num(k.rate, 0.03) * t; }],
    stretch_in: ["in", 0.45, (pr, p, t, k) => { const v = Math.max(0, ease("back_out", p)); if ((k.axis || "x") === "y") pr.sy *= v; else pr.sx *= v; pr.opacity *= Math.min(1, p * 3); }],
    grow_in: ["in", 0.5, (pr, p, t, k) => { const v = ease("ease_out", p); if ((k.axis || "y") === "x") pr.sx *= v; else pr.sy *= v; }],
    rise_in: ["in", 0.5, (pr, p, t, k) => { pr.y += (1 - ease("ease_out", p)) * num(k.distance, 80); pr.scale *= 0.94 + 0.06 * ease("ease_out", p); pr.opacity *= ease("ease_out", p); }],
    wipe_in: ["in", 0.5, wipe], wipe_out: ["out", 0.4, wipe],
    shrink_out: ["out", 0.3, (pr, p) => { pr.scale *= ease("ease_in_out", p); pr.opacity *= Math.min(1, p * 2); }],
    breathe: ["loop", 0, (pr, p, t, k) => { pr.scale *= 1 + num(k.amount, 0.04) * Math.sin((2 * Math.PI * t) / num(k.period, 3.0)); }],
    squash_stretch: ["loop", 0, (pr, p, t, k) => { const a = num(k.amount, 0.08), v = Math.sin((2 * Math.PI * t) / num(k.period, 0.9)); pr.sx *= 1 + a * v; pr.sy *= 1 - a * v; }],
    color_cycle: ["loop", 0, (pr, p, t, k) => {
      const cols = k.colors && k.colors.length ? k.colors : ["#FF3B6B", "#FFC23B", "#22D3EE"], per = num(k.period, 3.0);
      if (cols.length < 2 || per <= 0) return;
      const x = ((t / per) % 1) * cols.length, i = Math.floor(x) % cols.length;
      pr.fill = mixHex(cols[i], cols[(i + 1) % cols.length], x - Math.floor(x));
    }],
  };

  function presetDuration(layer, preset) {
    const P = PRESETS[preset.name];
    if (preset.duration != null) return Math.max(0.01, preset.duration);
    if (preset.name === "typewriter") return Math.max(0.2, 0.045 * (layer.text || "").length);
    return P[1];
  }

  function interp(kfs, prop, t, total) {
    const pts = kfs.filter((k) => k[prop] != null).map((k) => [k.t, k[prop], k.ease || "ease_in_out"]);
    if (!pts.length) return null;
    pts.sort((a, b) => a[0] - b[0]); // stable, like Python's sort
    const conv = prop === "x" || prop === "y" ? (v) => parsePos(v, total) : Number;
    if (t <= pts[0][0]) return conv(pts[0][1]);
    for (let i = 0; i + 1 < pts.length; i++) {
      const [t0, v0, e0] = pts[i], [t1, v1] = pts[i + 1];
      if (t0 <= t && t <= t1) {
        const p = t1 > t0 ? (t - t0) / (t1 - t0) : 1;
        const a = conv(v0), b = conv(v1);
        return a + (b - a) * ease(e0, p);
      }
    }
    return conv(pts[pts.length - 1][1]);
  }

  function interpColor(kfs, prop, t) {
    const pts = kfs.filter((k) => k[prop] != null).map((k) => [k.t, k[prop], k.ease || "ease_in_out"]);
    if (!pts.length) return null;
    pts.sort((a, b) => a[0] - b[0]);
    if (t <= pts[0][0]) return mixHex(pts[0][1], pts[0][1], 0);
    for (let i = 0; i + 1 < pts.length; i++) {
      const [t0, v0, e0] = pts[i], [t1, v1] = pts[i + 1];
      if (t0 <= t && t <= t1) return mixHex(v0, v1, ease(e0, t1 > t0 ? (t - t0) / (t1 - t0) : 1));
    }
    const last = pts[pts.length - 1][1];
    return mixHex(last, last, 0);
  }

  const PROPS = ["x", "y", "scale", "rotation", "opacity", "reveal", "w", "h", "sx", "sy"];
  function propsAt(layer, t, duration, canvas) {
    const pr = {
      x: parsePos(layer.x, canvas.width), y: parsePos(layer.y, canvas.height),
      scale: Number(layer.scale), rotation: Number(layer.rotation), opacity: Number(layer.opacity), reveal: 1,
      w: layer.width == null ? null : Number(layer.width), h: layer.height == null ? null : Number(layer.height),
      sx: layer.sx == null ? 1 : Number(layer.sx), sy: layer.sy == null ? 1 : Number(layer.sy),
      fill: layer.fill === undefined ? null : layer.fill,
    };
    for (const prop of PROPS) {
      const v = interp(layer.keyframes || [], prop, t, prop === "x" ? canvas.width : canvas.height);
      if (v != null) pr[prop] = v;
    }
    const fc = interpColor(layer.keyframes || [], "fill", t);
    if (fc != null) pr.fill = fc;
    for (const preset of layer.presets || []) {
      const P = PRESETS[preset.name];
      if (!P) continue;
      const [phase, , fn] = P, d = presetDuration(layer, preset), k = preset.params || {};
      if (phase === "in") { if (t < d) fn(pr, t / d, t, k); }
      else if (phase === "out") { if (t > duration - d) fn(pr, Math.max(0, (duration - t) / d), t, k); }
      else fn(pr, 1, t, k);
    }
    pr.opacity = clamp01(pr.opacity); pr.reveal = clamp01(pr.reveal); pr.scale = Math.max(0, pr.scale);
    pr.sx = Math.max(0, pr.sx); pr.sy = Math.max(0, pr.sy);
    if (pr.w != null) pr.w = Math.max(1, pr.w);
    if (pr.h != null) pr.h = Math.max(1, pr.h);
    return pr;
  }

  // same word grouping as render/layers.py _caption_groups
  function captionGroups(layer) {
    const groups = [], words = layer.words || [];
    let cur = [];
    words.forEach((w, i) => {
      if (cur.length) {
        const prev = words[cur[cur.length - 1]];
        const last = prev.text.trimEnd().slice(-1);
        if (cur.length >= layer.words_per_group || w.start - prev.end > 0.5 || ".?!".includes(last)) { groups.push(cur); cur = []; }
      }
      cur.push(i);
    });
    if (cur.length) groups.push(cur);
    return groups;
  }

  // Python's round(): halves go to the even neighbour
  function pyRound(x) {
    const f = Math.floor(x), d = x - f;
    if (Math.abs(d - 0.5) < 1e-9) return f % 2 === 0 ? f : f + 1;
    return Math.round(x);
  }

  const api = { ease, parsePos, propsAt, captionGroups, presetDuration, pyRound, mixHex, PRESETS, EASINGS };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.VAnim = api;
})(typeof window !== "undefined" ? window : globalThis);

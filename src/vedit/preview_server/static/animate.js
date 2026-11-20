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

  // CSS cubic-bezier, same steps as animate.py _bezier (Newton, then bisection)
  function bezier(x1, y1, x2, y2) {
    const coord = (t, a, b) => ((1 - 3 * b + 3 * a) * t + (3 * b - 6 * a)) * t * t + 3 * a * t;
    const slope = (t, a, b) => 3 * (1 - 3 * b + 3 * a) * t * t + 2 * (3 * b - 6 * a) * t + 3 * a;
    return (p) => {
      if (p <= 0 || p >= 1) return p;
      let t = p;
      for (let i = 0; i < 8; i++) {
        const d = slope(t, x1, x2);
        if (Math.abs(d) < 1e-7) break;
        const t2 = t - (coord(t, x1, x2) - p) / d;
        if (t2 < 0 || t2 > 1) break;
        t = t2;
      }
      if (Math.abs(coord(t, x1, x2) - p) > 1e-7) {
        let lo = 0, hi = 1; t = p;
        for (let i = 0; i < 40; i++) {
          const x = coord(t, x1, x2);
          if (Math.abs(x - p) < 1e-7) break;
          if (x < p) lo = t; else hi = t;
          t = (lo + hi) / 2;
        }
      }
      return coord(t, y1, y2);
    };
  }
  const NAMED_BEZIERS = { snap: [0.7, 0, 0.2, 1], smooth: [0.4, 0, 0.2, 1], expo_out: [0.16, 1, 0.3, 1], circ_out: [0, 0.55, 0.45, 1],
    anticipate: [0.36, -0.4, 0.5, 1], overshoot: [0.34, 1.56, 0.64, 1] };
  function parseBezier(name) {
    let s = String(name).trim().toLowerCase().replace(/ /g, "");
    const m = /^(?:cubic-bezier|bezier)\((.*)\)$/.exec(s);
    if (m) s = m[1]; else if (s.startsWith("bezier:")) s = s.slice(7); else return null;
    const v = s.split(",").map(Number);
    if (v.length !== 4 || v.some((x) => isNaN(x)) || !(v[0] >= 0 && v[0] <= 1 && v[2] >= 0 && v[2] <= 1)) return null;
    return v;
  }
  const BEZ = {};
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
  for (const [k, v] of Object.entries(NAMED_BEZIERS)) EASINGS[k] = bezier(...v);
  function ease(name, p) {
    let fn = EASINGS[name] || BEZ[name];
    if (!fn) { const b = parseBezier(name); fn = BEZ[name] = b ? bezier(...b) : EASINGS.linear; }
    return fn(clamp01(p));
  }

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
  // 3D objects (obj3d layers), as animate.py
  const AXES = { y: ["yaw"], x: ["pitch"], z: ["roll"], yx: ["yaw", "pitch"], xyz: ["yaw", "pitch", "roll"] };
  const TUMBLE = { yaw: 1.0, pitch: 0.61, roll: 0.37 };
  function turn(pr, axis, deg) {
    const axes = AXES[String(axis)];
    if (!axes) return;
    for (const a of axes) pr[a] += deg * (axes.length > 1 ? TUMBLE[a] : 1);
  }
  const pmod = (a, b) => ((a % b) + b) % b; // Python's % (always >= 0)
  function xyParam(v, canvas, def) {
    if (v == null) v = def;
    if (typeof v === "string") v = v.split(",").map((s) => s.trim());
    return [parsePos(v[0], canvas.width), parsePos(v[1], canvas.height)];
  }
  function travelAt(t, k, canvas) {
    const [ax, ay] = xyParam(k.from, canvas, ["15%", "50%"]), [bx, by] = xyParam(k.to, canvas, ["85%", "50%"]);
    const dur = Math.max(0.05, num(k.dur, 3.0)), loop = String(k.loop ?? "once"), left = bx < ax ? 1 : 0;
    if (loop === "continue") {
      const q = t / dur;
      let x = ax + (bx - ax) * q, y = ay + (by - ay) * q;
      const m = num(k.margin, 0.25), W0 = -m * canvas.width, W1 = canvas.width * (1 + m), H0 = -m * canvas.height, H1 = canvas.height * (1 + m);
      if (bx !== ax) x = W0 + pmod(x - W0, W1 - W0);
      if (by !== ay) y = H0 + pmod(y - H0, H1 - H0);
      return [x, y, left, pmod(q, 1)];
    }
    const q = t / dur;
    let leg = Math.floor(q), u = q - leg;
    const e = String(k.ease ?? (loop === "once" ? "ease_in_out" : "linear"));
    if (loop === "once") { u = Math.min(1, q); leg = 0; }
    const back = loop === "pingpong" && leg % 2 === 1;
    let f = ease(e, u);
    if (back) f = 1 - f;
    let facing = back ? 1 - left : left;
    if (loop === "pingpong" && leg >= 1 && u < 0.12) {
      const prev = back ? left : 1 - left;
      facing = prev + (facing - prev) * ease("ease_in_out", u / 0.12);
    }
    return [ax + (bx - ax) * f, ay + (by - ay) * f, facing, u];
  }
  function travel(pr, p, t, k, canvas) {
    const [x, y, facing, u] = travelAt(t, k, canvas);
    pr.x = x; pr.y = y;
    if (k.face ?? true) pr.yaw += 180 * facing;
    const hop = num(k.hop, 0);
    if (hop) pr.y -= Math.abs(Math.sin((Math.PI * t) / num(k.hop_period, 0.45))) * hop;
    const s0 = num(k.scale_from, 1), s1 = num(k.scale_to, 1);
    if (s0 !== 1 || s1 !== 1) {
      const loop = String(k.loop ?? "once");
      const f = loop !== "pingpong" || Math.floor(t / Math.max(0.05, num(k.dur, 3.0))) % 2 === 0 ? u : 1 - u;
      pr.scale *= s0 + (s1 - s0) * f;
    }
  }
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
    draw_in: ["in", 1.0, (pr, p, t, k) => { pr.draw *= ease(String(k.ease ?? "ease_in_out"), p); }],
    draw_out: ["out", 0.6, (pr, p, t, k) => { pr.draw *= ease(String(k.ease ?? "ease_in_out"), p); }],
    count_up: ["in", 1.2, (pr, p, t, k) => { pr.count *= ease(String(k.ease ?? "expo_out"), p); }],
    morph_loop: ["loop", 0, (pr, p, t, k) => { const per = num(k.period, 2.4); if (per > 0) pr.morph = 0.5 - 0.5 * Math.cos((2 * Math.PI * t) / per); }],
    spin3d: ["loop", 0, (pr, p, t, k) => { const per = num(k.period, 3.0); if (per) turn(pr, k.axis ?? "y", (360 * t) / per); }],
    spin3d_in: ["in", 0.8, (pr, p, t, k) => { turn(pr, k.axis ?? "y", -360 * num(k.turns, 1) * (1 - ease("ease_out", p))); pr.scale *= ease("back_out", p); pr.opacity *= Math.min(1, p * 3); }],
    sway3d: ["loop", 0, (pr, p, t, k) => { turn(pr, k.axis ?? "y", Math.sin((2 * Math.PI * t) / num(k.period, 3.0)) * num(k.degrees, 25)); }],
    travel: ["loop", 0, travel],
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

  const PROPS = ["x", "y", "scale", "rotation", "opacity", "reveal", "w", "h", "sx", "sy", "draw", "morph", "count", "yaw", "pitch", "roll"];
  const PROGRESS = ["draw", "morph", "count"];
  function propsAt(layer, t, duration, canvas) {
    if (layer.step_fps) t = Math.floor(t * layer.step_fps + 1e-9) / layer.step_fps; // stop-motion steps, as animate.py
    const pr = {
      x: parsePos(layer.x, canvas.width), y: parsePos(layer.y, canvas.height),
      scale: Number(layer.scale), rotation: Number(layer.rotation), opacity: Number(layer.opacity), reveal: 1,
      w: layer.width == null ? null : Number(layer.width), h: layer.height == null ? null : Number(layer.height),
      sx: layer.sx == null ? 1 : Number(layer.sx), sy: layer.sy == null ? 1 : Number(layer.sy),
      fill: layer.fill === undefined ? null : layer.fill,
      draw: 1, morph: 0, count: 1,
      yaw: layer.obj3d ? Number(layer.obj3d.yaw ?? -25) : 0, pitch: layer.obj3d ? Number(layer.obj3d.pitch ?? 18) : 0,
      roll: layer.obj3d ? Number(layer.obj3d.roll ?? 0) : 0,
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
      if (phase === "in") { if (t < d) fn(pr, t / d, t, k, canvas); }
      else if (phase === "out") { if (t > duration - d) fn(pr, Math.max(0, (duration - t) / d), t, k, canvas); }
      else fn(pr, 1, t, k, canvas);
    }
    pr.opacity = clamp01(pr.opacity); pr.reveal = clamp01(pr.reveal); pr.scale = Math.max(0, pr.scale);
    pr.sx = Math.max(0, pr.sx); pr.sy = Math.max(0, pr.sy);
    for (const k of PROGRESS) pr[k] = clamp01(pr[k]);
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

  // a counter's text at progress c (0..1): same digits as ops/motion.py counter_text
  function counterText(c, prog) {
    const from = Number(c.from ?? 0), to = Number(c.to ?? 100), d = Math.max(0, Math.min(6, Math.trunc(Number(c.decimals ?? 0))));
    const v = from + (to - from) * prog, f = 10 ** d;
    const n = Math.floor(Math.abs(v) * f + 0.5);
    let ip = String(Math.floor(n / f));
    const sep = c.separator ?? ",";
    if (sep) ip = ip.replace(/\B(?=(\d{3})+(?!\d))/g, sep);
    const frac = d ? "." + String(n % f).padStart(d, "0") : "";
    return `${c.prefix ?? ""}${v < 0 && n > 0 ? "-" : ""}${ip}${frac}${c.suffix ?? ""}`;
  }

  const api = { counterText, ease, parsePos, propsAt, travelAt, captionGroups, presetDuration, pyRound, mixHex, PRESETS, EASINGS };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.VAnim = api;
})(typeof window !== "undefined" ? window : globalThis);

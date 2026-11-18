// Port of src/vedit/ops/shapes.py (+ the drawing in ops/overlay_style.py render_shape) for the live preview: shapes
// are drawn as SVG, so stretching / animating their size stays crisp. Keep in sync with the Python files;
// tests/test_shapes_js.py checks the outlines match.
(function (root) {
  "use strict";
  const SAMPLES = 96, CORNER_STEPS = 8;
  const PRIMITIVE = ["rect", "ellipse", "line", "arrow", "ring"];

  function fit(pts, w, h) {
    let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
    for (const [x, y] of pts) { x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y); }
    return pts.map(([x, y]) => [((x - x0) / (x1 - x0 || 1)) * w, ((y - y0) / (y1 - y0 || 1)) * h]);
  }
  const HEART = (() => {
    const raw = [];
    for (let i = 0; i < SAMPLES; i++) {
      const a = (2 * Math.PI * i) / SAMPLES;
      raw.push([16 * Math.sin(a) ** 3, -(13 * Math.cos(a) - 5 * Math.cos(2 * a) - 2 * Math.cos(3 * a) - Math.cos(4 * a))]);
    }
    return fit(raw, 1, 1);
  })();

  function polygon(kind, w, h, sides = 6, points = 5, inner = 0.5) {
    const cx = w / 2, cy = h / 2, rx = w / 2, ry = h / 2;
    if (kind === "triangle") return [[cx, 0], [w, h], [0, h]];
    if (kind === "diamond") return [[cx, 0], [w, cy], [cx, h], [0, cy]];
    if (kind === "polygon") {
      const n = Math.max(3, Math.min(24, Math.trunc(sides))), out = [];
      for (let i = 0; i < n; i++) out.push([cx + rx * Math.sin((2 * Math.PI * i) / n), cy - ry * Math.cos((2 * Math.PI * i) / n)]);
      return fit(out, w, h);
    }
    if (kind === "star") {
      const n = Math.max(3, Math.min(24, Math.trunc(points))), k = Math.max(0.05, Math.min(0.95, Number(inner))), out = [];
      for (let i = 0; i < 2 * n; i++) {
        const a = (Math.PI * i) / n, r = i % 2 === 0 ? 1 : k;
        out.push([cx + rx * r * Math.sin(a), cy - ry * r * Math.cos(a)]);
      }
      return fit(out, w, h);
    }
    if (kind === "heart") return HEART.map(([x, y]) => [x * w, y * h]);
    if (kind === "blob") {
      const out = [];
      for (let i = 0; i < SAMPLES; i++) {
        const a = (2 * Math.PI * i) / SAMPLES, r = 0.82 + 0.1 * Math.sin(3 * a + 1.0) + 0.08 * Math.cos(5 * a + 2.0);
        out.push([cx + rx * r * Math.sin(a), cy - ry * r * Math.cos(a)]);
      }
      return fit(out, w, h);
    }
    throw new Error("not a polygon shape: " + kind);
  }

  function bubble(w, h, radius) {
    const bh = h * 0.78, r = Math.max(0, Math.min(radius, w / 2, bh / 2)), out = [];
    const corner = (cx, cy, a0) => {
      for (let i = 0; i <= CORNER_STEPS; i++) {
        const a = ((a0 + (90 * i) / CORNER_STEPS) * Math.PI) / 180;
        out.push([cx + r * Math.cos(a), cy + r * Math.sin(a)]);
      }
    };
    corner(r, r, 180); corner(w - r, r, 270); corner(w - r, bh - r, 0);
    out.push([w * 0.36, bh], [w * 0.12, h], [w * 0.18, bh]);
    corner(r, bh - r, 90);
    return out;
  }

  function outline(kind, w, h, sides = 6, points = 5, inner = 0.5, radius = 0) {
    return kind === "bubble" ? bubble(w, h, radius) : polygon(kind, w, h, sides, points, inner);
  }

  // ---- SVG ----------------------------------------------------------------------------------------------------
  const f = (n) => Math.round(n * 100) / 100;
  const poly = (pts) => "M" + pts.map(([x, y]) => `${f(x)},${f(y)}`).join("L") + "Z";
  const ell = (cx, cy, rx, ry) => `M${f(cx - rx)},${f(cy)}a${f(rx)},${f(ry)} 0 1,0 ${f(2 * rx)},0a${f(rx)},${f(ry)} 0 1,0 ${f(-2 * rx)},0Z`;
  function rrect(w, h, r) {
    r = Math.max(0, Math.min(r, w / 2, h / 2));
    if (!r) return `M0,0H${f(w)}V${f(h)}H0Z`;
    return `M${f(r)},0H${f(w - r)}A${f(r)},${f(r)} 0 0 1 ${f(w)},${f(r)}V${f(h - r)}A${f(r)},${f(r)} 0 0 1 ${f(w - r)},${f(h)}`
      + `H${f(r)}A${f(r)},${f(r)} 0 0 1 0,${f(h - r)}V${f(r)}A${f(r)},${f(r)} 0 0 1 ${f(r)},0Z`;
  }
  // the shape's area as a path, in a w x h box; k = screen px per canvas px (radius is in canvas px)
  function area(l, w, h, k) {
    const kind = l.shape || "rect";
    if (kind === "rect") return rrect(w, h, (l.radius || 0) * k);
    if (kind === "ellipse") return ell(w / 2, h / 2, w / 2, h / 2);
    if (kind === "line") return rrect(w, h, h / 2);
    if (kind === "arrow") {
      const head = Math.min(w * 0.45, h), sh = h * 0.38, mid = h / 2;
      return `M0,${f(mid - sh / 2)}H${f(w - head + 2 / 3)}V${f(mid + sh / 2)}H0Z` + poly([[w - head, 0], [w, mid], [w - head, h]]);
    }
    if (kind === "ring") {
      const i = Math.max(0.05, Math.min(0.95, l.inner ?? 0.5));
      return ell(w / 2, h / 2, w / 2, h / 2) + ell(w / 2, h / 2, (w * i) / 2, (h * i) / 2);
    }
    return poly(outline(kind, w, h, l.sides ?? 6, l.points ?? 5, l.inner ?? 0.5, (l.radius || 0) * k));
  }
  // CSS-like linear gradient across the box (same as overlay_style._gradient)
  function gradient(id, cols, angle, w, h) {
    const a = (angle * Math.PI) / 180, dx = Math.cos(a), dy = Math.sin(a), half = (Math.abs(w * dx) + Math.abs(h * dy)) / 2 || 1;
    const stops = cols.map((c, i) => `<stop offset="${cols.length > 1 ? i / (cols.length - 1) : 0}" stop-color="${hex(c).c}" stop-opacity="${hex(c).o}"/>`).join("");
    return `<linearGradient id="${id}" gradientUnits="userSpaceOnUse" x1="${f(w / 2 - dx * half)}" y1="${f(h / 2 - dy * half)}" x2="${f(w / 2 + dx * half)}" y2="${f(h / 2 + dy * half)}">${stops}</linearGradient>`;
  }
  function hex(v) {
    let s = String(v || "#FFFFFF").replace("#", "");
    if (s.length === 3 || s.length === 4) s = s.split("").map((c) => c + c).join("");
    return { c: "#" + s.slice(0, 6), o: s.length === 8 ? f(parseInt(s.slice(6, 8), 16) / 255) : 1 };
  }
  let SEQ = 0;
  // An SVG element string drawing layer l in a w x h box (screen px). fill: the (animated) fill, k: screen px per canvas px.
  function svg(l, w, h, fill, k = 1) {
    const id = "vs" + ++SEQ, d = area(l, w, h, k), kind = l.shape || "rect";
    const grad = l.fill_gradient && l.fill_gradient.length >= 2;
    const col = fill === undefined ? l.fill : fill;
    const bw = (l.border_width || 0) * k;
    let defs = "", body = "";
    const rule = kind === "ring" ? ' fill-rule="evenodd"' : "";
    if (kind === "line") {
      const c = hex(col || l.border_color);
      body = `<path d="${d}" fill="${c.c}" fill-opacity="${c.o}"/>`;
    } else {
      if (grad) { defs += gradient(id + "g", l.fill_gradient, l.gradient_angle ?? 90, w, h); body += `<path d="${d}"${rule} fill="url(#${id}g)"/>`; }
      else if (col) { const c = hex(col); body += `<path d="${d}"${rule} fill="${c.c}" fill-opacity="${c.o}"/>`; }
      if (bw > 0 && kind !== "arrow") {
        // a border inside the outline: a 2x-wide centred stroke clipped to the shape
        const b = hex(l.border_color || "#FFFFFF");
        defs += `<clipPath id="${id}c"><path d="${d}"${rule}/></clipPath>`;
        body += `<path d="${d}" fill="none" stroke="${b.c}" stroke-opacity="${b.o}" stroke-width="${f(bw * 2)}" stroke-linejoin="round" clip-path="url(#${id}c)"/>`;
      }
    }
    return `<svg xmlns="http://www.w3.org/2000/svg" width="${f(w)}" height="${f(h)}" viewBox="0 0 ${f(w)} ${f(h)}" overflow="visible"><defs>${defs}</defs>${body}</svg>`;
  }

  const api = { polygon, bubble, outline, svg, area, PRIMITIVE };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.VShapes = api;
})(typeof window !== "undefined" ? window : globalThis);

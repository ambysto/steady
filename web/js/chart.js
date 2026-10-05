// Latency chart: SVG, no library. Lines for router / Internet, red bars for packet loss,
// shaded outages, dashed markers where a tweak or manual step changed something.

import { el, svgEl } from "./dom.js";
import { number, t, timeOf } from "./i18n.js";

const W = 600, H = 140, PLOT_BOTTOM = 118, LOSS_H = 18;
const NICE = [25, 50, 100, 150, 250, 500, 1000, 2000, 5000];

function niceMax(values) {
  const sorted = values.filter(v => v !== null && v !== undefined).sort((a, b) => a - b);
  if (!sorted.length) return 50;
  const p98 = sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * 0.98))];
  return NICE.find(n => n >= p98 * 1.15) || NICE[NICE.length - 1];
}

/**
 * series:  [{ color: "var(--chart-router)", points: [[ts, ms|null], ...] }]
 * loss:    [[ts, pct, limited]]  outages: [[start, end]]   (limited: only ping lost it, drawn neutral)
 * markers: [{ ts, label }]       start, end: unix seconds of the visible range
 */
export function latencyChart({ series, loss = [], outages = [], markers = [], start, end }) {
  const yMax = niceMax(series.flatMap(s => s.points.map(p => p[1])));
  const x = ts => ((ts - start) / Math.max(1, end - start)) * W;
  const y = ms => PLOT_BOTTOM - (Math.min(ms, yMax) / yMax) * (PLOT_BOTTOM - 6);
  const svg = svgEl("svg", { class: "chart", viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: "none", role: "img" });

  for (const frac of [0.25, 0.5, 0.75, 1]) {
    svg.append(svgEl("line", { x1: 0, x2: W, y1: y(yMax * frac), y2: y(yMax * frac), stroke: "var(--separator)",
                               "stroke-width": 1, "vector-effect": "non-scaling-stroke" }));
  }
  for (const [a, b] of outages) {
    if (b < start || a > end) continue;
    const r = svgEl("rect", { class: "outage", x: x(Math.max(a, start)), y: 0, height: PLOT_BOTTOM,
                              width: Math.max(1.5, x(Math.min(b, end)) - x(Math.max(a, start))) });
    r.append(svgEl("title"));
    r.lastChild.textContent = `${t("ui.chart.outage")} ${timeOf(a)}`;
    svg.append(r);
  }
  const barW = loss.length > 1 ? Math.max(1, (W / loss.length) * 0.8) : 2;
  for (const [ts, pct, limited] of loss) {
    if (!pct) continue;
    svg.append(svgEl("rect", { class: limited ? "loss limited" : "loss", x: x(ts) - barW / 2, width: barW,
                               y: H - (Math.min(pct, 100) / 100) * LOSS_H, height: (Math.min(pct, 100) / 100) * LOSS_H }));
  }
  for (const s of series) {
    let d = "", pen = false;
    for (const [ts, ms] of s.points) {
      if (ms === null || ms === undefined) { pen = false; continue; }
      d += `${pen ? "L" : "M"}${x(ts).toFixed(1)},${y(ms).toFixed(1)}`;
      pen = true;
    }
    svg.append(svgEl("path", { d, fill: "none", stroke: s.color, "stroke-width": 1.6, "vector-effect": "non-scaling-stroke",
                               "stroke-linejoin": "round" }));
  }
  for (const m of markers) {
    if (m.ts < start || m.ts > end) continue;
    const g = svgEl("g");
    g.append(svgEl("line", { class: "marker", x1: x(m.ts), x2: x(m.ts), y1: 0, y2: PLOT_BOTTOM, "vector-effect": "non-scaling-stroke" }));
    g.append(svgEl("rect", { x: x(m.ts) - 4, y: 0, width: 8, height: PLOT_BOTTOM, fill: "transparent" }));   // hover target
    g.append(svgEl("title"));
    g.lastChild.textContent = `${timeOf(m.ts)} · ${m.label}`;
    svg.append(g);
  }

  const hasData = series.some(s => s.points.some(p => p[1] !== null && p[1] !== undefined));
  return el("div", { class: "chart-wrap" },
    el("div", { class: "chart-scale" }, `${number(yMax)} ms`),
    hasData ? svg : el("div", { class: "empty" }, t("ui.chart.no_data")),
    el("div", { class: "chart-axis" }, el("span", {}, timeOf(start)), el("span", {}, timeOf(end))));
}

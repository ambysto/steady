// Sweep chart in the style of a heart monitor: the trace is written left to right, wraps around,
// and overwrites itself; a short blank gap runs ahead of the pen. Canvas, no library.
// sweepLayout() is pure (tested with node, tests/js/); drawSweep() paints it.

export const SWEEP_S = 60;          // one pass across the chart
export const GAP_S = 3;             // blank space ahead of the pen
export const MAX_STEP_S = 2.5;      // a longer pause between samples breaks the line (no data)
const PAD_TOP = 6;

export const MBPS_STEPS = [0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000];
export const MS_STEPS = [25, 50, 100, 150, 250, 500, 1000, 2000, 5000];

/** Smallest step at or above the largest value plus headroom (the first step when there is none). */
export function niceScale(values, steps) {
  const top = Math.max(0, ...values.filter(v => v !== null && v !== undefined && Number.isFinite(v)));
  return steps.find(s => s >= top * 1.15) || steps[steps.length - 1];
}

/**
 * points: [[ts, value|null], ...] sorted by ts (null = the sample was lost, e.g. a ping).
 * now: the time the pen is at. Samples after `now` are not drawn yet, except that the segment to
 * the next one is drawn up to `now`, so the trace grows smoothly between one-second samples.
 * Returns { lines: [[{x, y, age}]], lost: [{x, age}], pen: {x, y} | null, max }.
 */
export function sweepLayout(points, now, { width, height, max, sweepS = SWEEP_S, gapS = GAP_S }) {
  const xOf = ts => (((ts % sweepS) + sweepS) % sweepS) / sweepS * width;
  const yOf = v => height - (Math.min(v, max) / max) * (height - PAD_TOP);
  const oldest = now - (sweepS - gapS);
  const lines = [], lost = [];
  let line = null, prev = null, pen = null;

  const visible = [];
  for (let i = 0; i < points.length; i++) {
    const [ts, value] = points[i];
    if (ts > now) {
      // interpolate the head between the last drawn sample and this one
      const last = visible[visible.length - 1];
      if (last && last[1] !== null && value !== null && ts - last[0] <= MAX_STEP_S) {
        const f = (now - last[0]) / (ts - last[0]);
        visible.push([now, last[1] + (value - last[1]) * f]);
      }
      break;
    }
    if (ts > oldest) visible.push([ts, value]);
  }

  for (const [ts, value] of visible) {
    const age = Math.min(1, Math.max(0, (now - ts) / (sweepS - gapS)));
    if (value === null) {
      lost.push({ x: xOf(ts), age });
      line = null;
      prev = null;
      continue;
    }
    const point = { x: xOf(ts), y: yOf(value), age };
    if (!line || ts - prev > MAX_STEP_S || point.x < line[line.length - 1].x) {
      line = [];
      lines.push(line);
    }
    line.push(point);
    prev = ts;
    pen = point;
  }
  return { lines: lines.filter(l => l.length), lost, pen, max };
}

function cssVar(name, fallback) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

/** Paint a layout on a canvas sized by CSS; colour is a CSS variable name such as "--accent". */
export function drawSweep(canvas, points, now, { max, colour, sweepS = SWEEP_S, gridEveryS = 10, grid = true }) {
  const ratio = window.devicePixelRatio || 1;
  const width = canvas.clientWidth, height = canvas.clientHeight;
  if (canvas.width !== Math.round(width * ratio) || canvas.height !== Math.round(height * ratio)) {
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
  }
  const g = canvas.getContext("2d");
  g.setTransform(ratio, 0, 0, ratio, 0, 0);
  g.clearRect(0, 0, width, height);

  if (grid) {
    g.strokeStyle = cssVar("--separator", "rgba(0,0,0,.09)");
    g.lineWidth = 1;
    g.beginPath();
    for (const frac of [0.25, 0.5, 0.75]) {
      const y = Math.round(height - frac * (height - PAD_TOP)) + 0.5;
      g.moveTo(0, y); g.lineTo(width, y);
    }
    for (let s = gridEveryS; s < sweepS; s += gridEveryS) {
      const x = Math.round((s / sweepS) * width) + 0.5;
      g.moveTo(x, PAD_TOP); g.lineTo(x, height);
    }
    g.stroke();
  }

  const layout = sweepLayout(points, now, { width, height, max, sweepS });
  const stroke = cssVar(colour, "#007aff");
  g.lineWidth = 1.6;
  g.lineJoin = g.lineCap = "round";
  g.strokeStyle = stroke;
  for (const line of layout.lines) {
    for (let i = 1; i < line.length; i++) {
      g.globalAlpha = 0.2 + 0.8 * (1 - line[i].age);     // older trace fades, like phosphor
      g.beginPath();
      g.moveTo(line[i - 1].x, line[i - 1].y);
      g.lineTo(line[i].x, line[i].y);
      g.stroke();
    }
  }
  g.fillStyle = cssVar("--bad", "#e5352b");
  for (const mark of layout.lost) {
    g.globalAlpha = 0.3 + 0.7 * (1 - mark.age);
    g.fillRect(mark.x - 1, height - 8, 2, 8);
  }
  g.globalAlpha = 1;
  if (layout.pen) {
    g.fillStyle = stroke;
    g.beginPath();
    g.arc(layout.pen.x, layout.pen.y, 2.5, 0, Math.PI * 2);
    g.fill();
  }
  return layout;
}

// A smooth line through chart points that never overshoots them (the speed test's live charts,
// ADR-0024): a monotone cubic (Fritsch-Carlson, the harmonic-mean tangents d3's monotoneX uses), so
// between two points the curve stays between their values and never dips below zero or above a peak.

const f = v => v.toFixed(1);

// The charts' top in Mbit/s, the next of these above the highest point (ecg.js niceScale): it moves in
// steps rather than with every new peak, and the line keeps most of the chart's height.
export const CHART_STEPS = [5, 10, 20, 30, 50, 75, 100, 150, 200, 300, 500, 750, 1000, 1500, 2000, 3000, 5000, 7500, 10000];

/** points: [[x, y], ...] with x increasing (a repeated x is skipped). Returns an SVG path's `d`. */
export function monotonePath(points) {
  const p = points.filter((q, i) => i === 0 || q[0] > points[i - 1][0]);
  if (p.length < 3) return p.map((q, i) => `${i ? "L" : "M"}${f(q[0])},${f(q[1])}`).join(" ");
  const n = p.length;
  const dx = [], secant = [];
  for (let i = 0; i < n - 1; i++) {
    dx.push(p[i + 1][0] - p[i][0]);
    secant.push((p[i + 1][1] - p[i][1]) / dx[i]);
  }
  const tangent = [secant[0]];
  for (let i = 1; i < n - 1; i++) {
    const a = secant[i - 1], b = secant[i];
    tangent.push(a * b <= 0 ? 0 : 3 * (dx[i - 1] + dx[i]) / ((2 * dx[i] + dx[i - 1]) / a + (dx[i] + 2 * dx[i - 1]) / b));
  }
  tangent.push(secant[n - 2]);
  let d = `M${f(p[0][0])},${f(p[0][1])}`;
  for (let i = 0; i < n - 1; i++) {
    const h = dx[i] / 3;
    d += ` C${f(p[i][0] + h)},${f(p[i][1] + tangent[i] * h)} ${f(p[i + 1][0] - h)},${f(p[i + 1][1] - tangent[i + 1] * h)} ${f(p[i + 1][0])},${f(p[i + 1][1])}`;
  }
  return d;
}

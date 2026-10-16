// node --test tests/js/   (run from tests/test_web.py when node is installed)
import assert from "node:assert/strict";
import { test } from "node:test";
import { CHART_STEPS, monotonePath } from "../../web/js/curve.js";
import { niceScale } from "../../web/js/ecg.js";

/** Every coordinate of a path's commands, as [x, y] pairs. */
const coordinates = d => [...d.matchAll(/(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)/g)].map(m => [Number(m[1]), Number(m[2])]);

test("curve: starts and ends on the points, one cubic per segment", () => {
  const d = monotonePath([[0, 80], [10, 40], [20, 50], [30, 20]]);
  assert.match(d, /^M0\.0,80\.0 C/);
  assert.ok(d.endsWith(" 30.0,20.0"));
  assert.equal((d.match(/C/g) || []).length, 3);
});

test("curve: control points stay between their segment's values (no overshoot)", () => {
  const points = [[0, 88], [7, 30], [14, 10], [21, 60], [28, 61], [35, 2], [42, 88], [49, 88]];
  const d = monotonePath(points);
  const pts = coordinates(d);
  for (let i = 0; i < points.length - 1; i++) {
    const lo = Math.min(points[i][1], points[i + 1][1]) - 0.05, hi = Math.max(points[i][1], points[i + 1][1]) + 0.05;
    for (const [, y] of pts.slice(1 + i * 3, 1 + i * 3 + 2)) assert.ok(y >= lo && y <= hi, `segment ${i}: ${y}`);
  }
});

test("curve: two points or fewer are straight; a repeated x is skipped", () => {
  assert.equal(monotonePath([[0, 1], [5, 2]]), "M0.0,1.0 L5.0,2.0");
  assert.equal(monotonePath([]), "");
  assert.equal((monotonePath([[0, 1], [0, 3], [5, 2], [9, 4]]).match(/C/g) || []).length, 2);
});

test("speed chart scale: moves in steps and only grows as points arrive", () => {
  const series = [12, 60, 140, 170, 181, 176, 179, 182, 175];
  const tops = series.map((_, i) => niceScale(series.slice(0, i + 1), CHART_STEPS));
  for (let i = 1; i < tops.length; i++) assert.ok(tops[i] >= tops[i - 1]);
  assert.equal(tops.at(-1), 300);                        // 182 * 1.15 > 200
  assert.equal(new Set(tops.slice(4)).size, 1);           // after the ramp the top no longer moves
});

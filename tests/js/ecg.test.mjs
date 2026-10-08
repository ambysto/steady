// node --test tests/js/   (run from tests/test_web.py when node is installed)
import assert from "node:assert/strict";
import { test } from "node:test";
import { GAP_S, MBPS_STEPS, MS_STEPS, niceScale, SWEEP_S, sweepLayout } from "../../web/js/ecg.js";
import { bitrate } from "../../web/js/metrics.js";

const BOX = { width: 600, height: 100, max: 10 };

test("scale: the next step above the values, with headroom", () => {
  assert.equal(niceScale([], MBPS_STEPS), 0.5);
  assert.equal(niceScale([0.3, null], MBPS_STEPS), 0.5);
  assert.equal(niceScale([9], MBPS_STEPS), 20);          // 9 * 1.15 > 10
  assert.equal(niceScale([40], MS_STEPS), 50);
  assert.equal(niceScale([1e9], MS_STEPS), 5000);         // capped at the last step
});

test("the pen position follows the clock and wraps every sweep", () => {
  const points = [[120, 5], [121, 5], [122, 5]];
  const layout = sweepLayout(points, 122, BOX);
  assert.equal(layout.lines.length, 1);
  assert.deepEqual(layout.lines[0].map(p => p.x), [0, 10, 20]);     // 120 s is a multiple of 60 s
  assert.equal(layout.pen.x, 20);
  assert.equal(layout.lines[0][0].y, 100 - 0.5 * (100 - 6));         // half the scale
});

test("the line breaks where it wraps, where data is missing, and at a lost sample", () => {
  const points = [[58, 1], [59, 1], [60, 1], [61, 1], [70, 1], [71, 1], [72, null], [73, 1], [74, 1]];
  const layout = sweepLayout(points, 74, BOX);
  assert.deepEqual(layout.lines.map(l => l.length), [2, 2, 2, 2]);   // 58-59 | 60-61 | 70-71 | 73-74
  assert.equal(layout.lost.length, 1);
  assert.equal(layout.lost[0].x, 120);
});

test("samples older than one sweep minus the gap are not drawn", () => {
  const points = [];
  for (let ts = 0; ts <= 200; ts++) points.push([ts, 3]);
  const layout = sweepLayout(points, 200, BOX);
  const drawn = layout.lines.reduce((n, l) => n + l.length, 0);
  assert.equal(drawn, SWEEP_S - GAP_S);
  assert.ok(layout.lines.flat().every(p => p.age >= 0 && p.age <= 1));
});

test("between two samples the head grows toward the next one", () => {
  const layout = sweepLayout([[10, 0], [11, 10]], 10.5, BOX);
  const head = layout.lines[0][layout.lines[0].length - 1];
  assert.equal(head.x, 105);
  assert.equal(head.y, 100 - 0.5 * (100 - 6));                        // halfway from 0 to 10
  assert.equal(layout.pen, head);
});

test("values above the scale are clipped to the top", () => {
  const layout = sweepLayout([[1, 1000]], 1, BOX);
  assert.equal(layout.lines[0][0].y, 6);
});

test("bitrate: kb/s below one megabit, one decimal below ten", () => {
  assert.equal(bitrate(null), null);
  assert.deepEqual(bitrate(512_000), { unit: "kbps", value: 512, decimals: 0 });
  assert.deepEqual(bitrate(2_500_000), { unit: "mbps", value: 2.5, decimals: 1 });
  assert.deepEqual(bitrate(87_000_000), { unit: "mbps", value: 87, decimals: 0 });
});

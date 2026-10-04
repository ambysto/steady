// node --test tests/js/   (run from tests/test_web.py when node is installed)
import assert from "node:assert/strict";
import { test } from "node:test";
import { bucketLoss, historySeries, liveNumbers, liveTicks, lossVerdict, probesOk } from "../../web/js/metrics.js";

const INET = ["cloudflare", "google"];

function samples(n, { inetLost = 0, routerLost = 0, oneTargetLost = 0 } = {}) {
  const out = [];
  for (let i = 0; i < n; i++) {
    out.push([i, "router", i < routerLost ? null : 2 + (i % 2)]);
    out.push([i, "cloudflare", i < inetLost || i < oneTargetLost ? null : 30]);
    out.push([i, "google", i < inetLost ? null : 40]);
  }
  return out;
}

test("Internet counts as lost only when every target is lost", () => {
  const ticks = liveTicks(samples(100, { oneTargetLost: 50 }), INET);
  assert.equal(liveNumbers(ticks).lossPct, 0);
  assert.equal(ticks[0].internet, 40);                       // best of the targets that answered
  assert.equal(liveNumbers(liveTicks(samples(100, { inetLost: 25 }), INET)).lossPct, 25);
});

test("router loss and jitter", () => {
  const nums = liveNumbers(liveTicks(samples(100, { routerLost: 10 }), INET));
  assert.equal(nums.routerLossPct, 10);
  assert.equal(nums.jitter, 1);                              // rtt alternates 2, 3
});

test("probes: fresh and successful", () => {
  assert.equal(probesOk({ probe: { ts: 100, ok: true } }, 130), true);
  assert.equal(probesOk({ probe: { ts: 100, ok: true } }, 200), false);   // stale
  assert.equal(probesOk({ probe: { ts: 100, ok: false } }, 110), false);
  assert.equal(probesOk({}, 110), false);
});

test("verdict: ping-only loss is 'limited', not a problem", () => {
  assert.deepEqual(lossVerdict(26.8, 2.8, true), { level: "limited", routerBad: true });  // 13:33 on the dev PC: both shown apart
  assert.deepEqual(lossVerdict(26.8, 0.5, true), { level: "limited", routerBad: false });
  assert.deepEqual(lossVerdict(26.8, 0.5, false), { level: "bad", routerBad: false });     // probes fail too: real
  assert.deepEqual(lossVerdict(0.5, 0, false), { level: "ok", routerBad: false });
  assert.deepEqual(lossVerdict(null, null, true), { level: "none", routerBad: false });
});

test("live loss bars carry the limited flag", () => {
  const bars = bucketLoss(liveTicks(samples(30, { inetLost: 15 }), INET), 15, true);
  assert.deepEqual(bars, [[7.5, 100, true], [22.5, 0, true]]);
});

test("history: a bucket is limited when the probes got through", () => {
  const rows = [
    { ts: 0, target: "router", sent: 60, lost: 0, avg: 2 },
    { ts: 0, target: "cloudflare", sent: 60, lost: 15, avg: 30 },
    { ts: 0, target: "google", sent: 60, lost: 18, avg: 40 },
    { ts: 0, target: "tcp_cloudflare", sent: 6, lost: 0, avg: 20 },
    { ts: 300, target: "router", sent: 60, lost: 0, avg: 2 },
    { ts: 300, target: "cloudflare", sent: 60, lost: 30, avg: 30 },
    { ts: 300, target: "tcp_cloudflare", sent: 6, lost: 6, avg: null },
  ];
  const s = historySeries(rows, INET);
  assert.deepEqual(s.loss, [[0, 25, true], [300, 50, false]]);
  assert.deepEqual(s.internet, [[0, 30], [300, 30]]);
  assert.deepEqual(s.router, [[0, 2], [300, 2]]);
});

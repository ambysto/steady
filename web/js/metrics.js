// Pure data shaping for the Overview (no DOM, no network) - tested with node (tests/js/).
//
// "Packet loss" means pings to the Internet that got no answer. Many ISPs rate-limit ICMP, so
// that number can be high while real traffic is fine; the monitor's TCP/HTTP probes tell the two
// apart (same rule as diagnostics check #5). `limited` marks loss that only ping sees.

export const LOSS_THRESHOLD_PCT = 1;     // like diagnostics: above 1% is worth showing as a problem
export const PROBE_FRESH_S = 60;         // a probe round older than this says nothing about now

const isProbe = target => /^(tcp|http)_/.test(target);

/** Live samples [[ts, target, rtt|null]] -> per tick: router rtt, best Internet rtt, Internet lost. */
export function liveTicks(samples, inetTargets) {
  const inet = new Set(inetTargets);
  const ticks = new Map();
  for (const [ts, target, rtt] of samples || []) {
    const key = Math.round(ts);
    const tick = ticks.get(key) || { ts: key, router: undefined, inet: [] };
    if (target === "router") tick.router = rtt;
    else if (inet.has(target)) tick.inet.push(rtt);
    ticks.set(key, tick);
  }
  return [...ticks.values()].sort((a, b) => a.ts - b.ts).map(tk => {
    const replies = tk.inet.filter(v => v !== null);
    return { ts: tk.ts, router: tk.router === undefined ? undefined : tk.router,
             internet: replies.length ? Math.min(...replies) : null, inetLost: tk.inet.length > 0 && !replies.length };
  });
}

/** Internet ping loss %, router loss %, router jitter (mean |Δrtt|) over the ticks. */
export function liveNumbers(ticks) {
  const withInet = ticks.filter(tk => tk.internet !== null || tk.inetLost);
  const withRouter = ticks.filter(tk => tk.router !== undefined);
  const pct = (part, all) => (all.length ? (100 * part.length) / all.length : null);
  const routerRtts = withRouter.map(tk => tk.router).filter(v => v !== null);
  let jitter = null;
  if (routerRtts.length > 1) {
    let sum = 0;
    for (let i = 1; i < routerRtts.length; i++) sum += Math.abs(routerRtts[i] - routerRtts[i - 1]);
    jitter = sum / (routerRtts.length - 1);
  }
  return { lossPct: pct(withInet.filter(tk => tk.inetLost), withInet),
           routerLossPct: pct(withRouter.filter(tk => tk.router === null), withRouter), jitter };
}

/** Median router and best-Internet rtt (ms) over the ticks that answered; null when none did. */
export function liveLatency(ticks) {
  const median = values => {
    if (!values.length) return null;
    const sorted = [...values].sort((a, b) => a - b);
    const mid = sorted.length >> 1;
    return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
  };
  return { router: median(ticks.map(tk => tk.router).filter(v => v !== null && v !== undefined)),
           internet: median(ticks.map(tk => tk.internet).filter(v => v !== null)) };
}

/** Is the latest probe round recent and successful? (live.probe = {ts, ok}) */
export function probesOk(live, now) {
  const probe = live?.probe;
  return Boolean(probe && probe.ok && now - probe.ts <= PROBE_FRESH_S);
}

/**
 * How to show the loss number:
 *   none     no data            ok    at or below the threshold
 *   limited  only ping loses packets, real connections work (show neutral, explain)
 *   bad      real loss
 * routerBad: loss to the router itself is above the threshold - always a real problem, shown on its
 * own: a few % lost on the Wi-Fi does not explain (or excuse) a large ping loss further out.
 */
export function lossVerdict(lossPct, routerLossPct, probesWork) {
  const routerBad = routerLossPct !== null && routerLossPct > LOSS_THRESHOLD_PCT;
  let level;
  if (lossPct === null) level = "none";
  else if (lossPct <= LOSS_THRESHOLD_PCT) level = "ok";
  else level = probesWork ? "limited" : "bad";
  return { level, routerBad };
}

/** Live ticks -> loss bars [[ts, pct, limited]] in `size`-second buckets. */
export function bucketLoss(ticks, size, limited) {
  const buckets = new Map();
  for (const tk of ticks) {
    if (tk.internet === null && !tk.inetLost) continue;
    const key = tk.ts - (tk.ts % size);
    const b = buckets.get(key) || [0, 0];
    b[0] += tk.inetLost ? 1 : 0;
    b[1] += 1;
    buckets.set(key, b);
  }
  return [...buckets].map(([ts, [lost, n]]) => [ts + size / 2, (100 * lost) / n, limited]);
}

/**
 * History rows (minute or bucketed stats) -> router avg, best Internet avg and loss bars.
 * A bucket's loss counts as `limited` when the TCP/HTTP probes in the same bucket got through
 * (best probe loss at or below the threshold).
 */
export function historySeries(rows, inetTargets) {
  const inet = new Set(inetTargets);
  const byTs = new Map();
  for (const r of rows || []) {
    const slot = byTs.get(r.ts) || { router: null, inet: [], probes: [] };
    const loss = r.sent ? (100 * r.lost) / r.sent : null;
    if (r.target === "router") slot.router = r.avg;
    else if (inet.has(r.target) && r.sent) slot.inet.push({ avg: r.avg, loss });
    else if (isProbe(r.target) && r.sent) slot.probes.push(loss);
    byTs.set(r.ts, slot);
  }
  const router = [], internet = [], loss = [];
  for (const [ts, slot] of [...byTs].sort((a, b) => a[0] - b[0])) {
    router.push([ts, slot.router]);
    const avgs = slot.inet.map(i => i.avg).filter(v => v !== null);
    internet.push([ts, avgs.length ? Math.min(...avgs) : null]);
    if (slot.inet.length) {
      const probesWork = slot.probes.length > 0 && Math.min(...slot.probes) <= LOSS_THRESHOLD_PCT;
      loss.push([ts, Math.min(...slot.inet.map(i => i.loss)), probesWork]);
    }
  }
  return { router, internet, loss };
}

/** Bits per second -> {unit: "kbps" | "mbps", value, decimals} for display (below 1 Mb/s in kb/s). */
export function bitrate(bps) {
  if (bps === null || bps === undefined || !Number.isFinite(bps)) return null;
  if (bps < 1e6) return { unit: "kbps", value: bps / 1e3, decimals: 0 };
  const mbps = bps / 1e6;
  return { unit: "mbps", value: mbps, decimals: mbps < 10 ? 1 : 0 };
}

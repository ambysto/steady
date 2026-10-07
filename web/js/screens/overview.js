// Overview: connection state, the connection check (ADR-0020), latency chart (live 15 min / 24 h /
// 7 days), numbers, recent incidents, quick actions, watchdog state.

import { get, runJob } from "../api.js";
import { latencyChart } from "../chart.js";
import { $, el, fill, icon, svgEl, toast } from "../dom.js";
import { duration, number, t, timeOf } from "../i18n.js";
import { bucketLoss, historySeries, liveLatency, liveNumbers, liveTicks, lossVerdict, probesOk } from "../metrics.js";
import { failoverKey, failoverRows, suggestionAction } from "../widgets.js";
import { loadBadge } from "./diagnostics.js";

const RANGES = { live: { label: "ui.range.live" }, day: { label: "ui.range.day", hours: 24, bucket: 5 },
                 week: { label: "ui.range.week", hours: 168, bucket: 30 } };
const OUTAGE_KINDS = ["router_down", "internet_down"];
const MARKER_KINDS = ["tweak_enabled", "tweak_disabled", "manual_step_done"];
const ACTIONS = [["reconnect", "refresh"], ["flush_dns", "trash"], ["renew_dhcp", "network"], ["restart_adapter", "cpu"]];

let range = "live";
let events = [];
let history = null;

const root = () => $("#screen-overview");

function internetTargets(ctx) {
  return Object.keys(ctx.state?.targets || {});
}

// --- data shaping (pure parts live in ../metrics.js) ---------------------------------------------

const ticksOf = ctx => liveTicks(ctx.live?.samples, internetTargets(ctx));
const nowOf = ctx => ctx.live?.ts || Date.now() / 1000;

function outageSpans(ctx) {
  const spans = events.filter(e => OUTAGE_KINDS.includes(e.kind)).map(e => [e.ts - (e.duration || 0), e.ts]);
  const now = ctx.live?.ts || Date.now() / 1000;
  for (const start of Object.values(ctx.live?.outages || {})) spans.push([start, now]);
  return spans;
}

// --- rendering --------------------------------------------------------------------------------

function heroBlock(ctx) {
  const live = ctx.live || {};
  const outages = live.outages || {};
  const kind = outages.router ? "router_down" : outages.internet ? "internet_down" : "online";
  const since = outages.router || outages.internet;
  const now = live.ts || Date.now() / 1000;
  const wifi = live.wifi;
  return el("div", { class: `group hero ${kind === "online" ? "" : "bad"}` },
    el("div", { class: "status-icon" }, icon(kind === "router_down" ? "wifi-off" : "wifi")),
    el("div", {}, el("h2", {}, t(`ui.state.${kind}`)),
      el("p", {}, kind === "online" ? t("ui.state.online_detail") : t(`ui.state.${kind}_detail`, { duration: duration(now - since) }))),
    el("div", { class: "ssid" },
      wifi && wifi.state === "connected"
        ? [el("strong", {}, wifi.ssid || "—"), `${wifi.radio_type || ""} · ch ${wifi.channel ?? "?"}`]
        : t("ui.hero.no_wifi")));
}

function chartBlock(ctx) {
  const now = ctx.live?.ts || Date.now() / 1000;
  const markers = events.filter(e => MARKER_KINDS.includes(e.kind)).map(e => ({ ts: e.ts, label: e.message }));
  let chart;
  if (range === "live") {
    const ticks = ticksOf(ctx);
    const limited = probesOk(ctx.live, now);
    chart = latencyChart({ series: [{ color: "var(--chart-internet)", points: ticks.map(tk => [tk.ts, tk.internet]) },
                                    { color: "var(--chart-router)", points: ticks.map(tk => [tk.ts, tk.router]) }],
                           loss: bucketLoss(ticks, 15, limited), outages: outageSpans(ctx), markers, start: now - 900, end: now });
  } else {
    const s = history ? historySeries(history.minute_stats, internetTargets(ctx)) : { router: [], internet: [], loss: [] };
    chart = latencyChart({ series: [{ color: "var(--chart-internet)", points: s.internet }, { color: "var(--chart-router)", points: s.router }],
                           loss: s.loss, outages: outageSpans(ctx), markers, start: now - RANGES[range].hours * 3600, end: now });
  }
  const seg = el("div", { class: "segmented" }, Object.entries(RANGES).map(([key, r]) =>
    el("button", { "aria-pressed": String(key === range), onclick: () => selectRange(ctx, key) }, t(r.label))));
  return el("div", { class: "group chart-card" },
    el("div", { class: "chart-head" }, seg,
      el("div", { class: "legend" },
        el("span", {}, el("i", { style: "background:var(--chart-router)" }), t("ui.overview.router")),
        el("span", {}, el("i", { style: "background:var(--chart-internet)" }), t("ui.overview.internet")),
        el("span", { class: "outage" }, el("i"), t("ui.chart.outage")),
        el("span", { class: "change" }, el("i"), t("ui.chart.change")))),
    chart);
}

let rangeRequest = 0;
async function selectRange(ctx, key) {
  const mine = ++rangeRequest;       // clicking Day then Week quickly: only the last click counts
  try {
    let loaded = null;
    if (key !== "live") {
      const r = RANGES[key];
      loaded = await get(`/api/history?hours=${r.hours}&bucket=${r.bucket}`);
    }
    if (mine !== rangeRequest) return;
    range = key;
    if (loaded) {
      history = loaded;
      events = loaded.events;
    }
    slots.chart && fill(slots.chart, chartBlock(ctx));
    drawSlow(ctx);
  } catch (err) {
    if (mine === rangeRequest) toast(t("ui.error.failed", { message: err.message }), { bad: true });
  }
}

function lossStat(ctx, lossPct, routerLossPct) {
  const verdict = lossVerdict(lossPct, routerLossPct, probesOk(ctx.live, nowOf(ctx)));
  const notes = [];
  if (verdict.level === "limited") notes.push(el("div", { class: "note" }, t("ui.overview.icmp_limited")));
  if (verdict.routerBad) notes.push(el("div", { class: "note bad" }, t("ui.overview.router_loss", { loss: routerLossPct })));
  return el("div", { class: `stat loss-${verdict.level}` }, el("div", { class: "k" }, t("ui.overview.loss")),
    el("div", { class: "v" }, lossPct === null ? "—" : number(lossPct, 2), el("small", {}, "%")), notes);
}

function statsBlock(ctx) {
  const { lossPct, routerLossPct, jitter } = liveNumbers(ticksOf(ctx));
  const wifi = ctx.live?.wifi || {};
  const midnight = new Date(); midnight.setHours(0, 0, 0, 0);
  const today = events.filter(e => OUTAGE_KINDS.includes(e.kind) && e.ts >= midnight / 1000).length
    + Object.keys(ctx.live?.outages || {}).length;
  const stats = [["ui.overview.jitter", jitter === null ? "—" : number(jitter, 1), " ms"],
                 ["ui.overview.signal", wifi.rssi ?? "—", " dBm"], ["ui.overview.link_rate", wifi.rx_mbps ?? "—", " Mbps"],
                 ["ui.overview.incidents_today", today, ""]];
  return el("div", { class: "group stats" }, lossStat(ctx, lossPct, routerLossPct), stats.map(([k, v, unit]) =>
    el("div", { class: "stat" }, el("div", { class: "k" }, t(k)), el("div", { class: "v" }, v, el("small", {}, unit)))));
}

function eventRow(e) {
  const tile = { bad: "bad", warn: "warn" }[e.level] || "";
  const secondary = [timeOf(e.ts)];
  if (e.duration && OUTAGE_KINDS.concat("monitor_gap").includes(e.kind)) secondary.push(t("ui.log.lasted", { duration: duration(e.duration) }));
  return el("div", { class: "row" }, el("div", { class: `icon-tile ${tile}` }, icon("wifi-off")),
    el("div", { class: "main" }, el("div", { class: "label" }, e.message), el("div", { class: "secondary" }, secondary.join(" · "))));
}

function watchdogPill(ctx) {
  const wd = ctx.state?.settings?.watchdog || {};
  const [key, cls] = wd.tripped_at ? ["tripped", "bad"] : !wd.enabled ? ["off", ""] : wd.dry_run ? ["dry_run", "accent"] : ["on", "ok"];
  return el("span", { class: `pill ${cls}` }, t(`ui.watchdog.state.${key}`));
}

function quickActions(ctx) {
  return el("div", { class: "group actions-grid" }, ACTIONS.map(([name, ic]) => {
    const label = el("span", {}, t(`ui.action.${name}`));
    const b = el("button", { class: "button" }, icon(ic), label, name === "restart_adapter" && !ctx.state?.is_admin ? icon("lock") : null);
    b.addEventListener("click", async () => {
      b.disabled = true;
      label.textContent = t("ui.action.running");
      try {
        const res = await runJob(`/api/actions/${name}`);
        toast(res.message || t("ui.action.done"), { bad: !res.ok });
      } catch (err) {
        toast(t("ui.error.failed", { message: err.message }), { bad: true });
      } finally {
        b.disabled = false;
        label.textContent = t(`ui.action.${name}`);
      }
    });
    return b;
  }));
}

let suggestions = null;

async function loadSuggestions(ctx) {
  suggestions = await get("/api/suggestions");
}

// --- connection check (ADR-0020): one big round button, the real progress, what was found ---------

const STATUS_ICON = { ok: "check", warn: "alert", bad: "x", info: "info" };
const RING_R = 70;
const RING = 2 * Math.PI * RING_R;
const check = { running: false, progress: null };   // progress: the diagnostics job's, see app/server.py

function bigButton(label, iconName, onClick) {
  return el("button", { class: "big-button", onclick: onClick }, icon(iconName), el("span", {}, label));
}

const statusCircle = (tile, iconName) => el("div", { class: `status-circle ${tile}` }, icon(iconName));

function progressRing(done, total) {
  const svg = svgEl("svg", { viewBox: "0 0 156 156", class: "ring" });
  svg.append(svgEl("circle", { class: "track", cx: 78, cy: 78, r: RING_R }),
             svgEl("circle", { class: "arc", cx: 78, cy: 78, r: RING_R, "stroke-dasharray": RING.toFixed(1),
                               "stroke-dashoffset": (RING * (1 - (total ? done / total : 0))).toFixed(1),
                               transform: "rotate(-90 78 78)" }));
  return el("div", { class: "ring-wrap" }, svg, el("div", { class: "ring-label" }, total ? `${done}/${total}` : "…"));
}

function checkHero({ circle, title, body, below }) {
  return el("div", { class: "check-hero" }, circle, el("h2", {}, title), body ? el("p", {}, body) : null,
    below ? el("div", { class: "below" }, below) : null);
}

/** "Nothing to fix" shows the numbers behind it: the last 15 minutes and the drops of the last day. */
function miniStats(ctx) {
  const ticks = ticksOf(ctx);
  const latency = liveLatency(ticks);
  const { lossPct } = liveNumbers(ticks);
  const since = nowOf(ctx) - 86400;
  const drops = events.filter(e => OUTAGE_KINDS.includes(e.kind) && e.ts >= since).length;
  const ms = v => (v === null ? "—" : number(v, 0));
  const stats = [["ui.overview.router", ms(latency.router), " ms"], ["ui.overview.internet", ms(latency.internet), " ms"],
                 ["ui.overview.loss", lossPct === null ? "—" : number(lossPct, 1), "%"], ["ui.check.drops_24h", drops, ""]];
  return el("div", { class: "mini-stats" }, stats.map(([k, v, unit]) =>
    el("div", { class: "stat" }, el("div", { class: "k" }, t(k)), el("div", { class: "v" }, v, el("small", {}, unit)))));
}

function problemRow(problem, ctx) {
  const onChange = () => loadSuggestions(ctx).then(() => drawSlow(ctx));
  const actions = problem.actions.map(a => el("div", { class: "problem-action" },
    el("div", { class: "main" }, el("div", { class: "label" }, a.title),
      a.kind === "manual" && a.body ? el("div", { class: "secondary" }, a.body) : null),
    suggestionAction(a, { onChange, isAdmin: ctx.state?.is_admin })));
  return el("div", { class: "row problem" },
    el("div", { class: `icon-tile ${problem.status}` }, icon(STATUS_ICON[problem.status])),
    el("div", { class: "main" },
      el("div", { class: "label" }, problem.title, " ",
        el("span", { class: `pill ${problem.kind === "app" ? "accent" : ""}` }, t(`ui.check.kind.${problem.kind}`))),
      el("div", { class: "secondary" }, problem.summary),
      problem.kind === "none" && problem.advice ? el("div", { class: "secondary" }, problem.advice) : null,
      actions.length ? el("div", { class: "problem-actions" }, actions) : null));
}

function checkCard(ctx) {
  const card = (...children) => el("div", { class: "group check-card" }, children);
  if (check.running) {
    const p = check.progress || { done: 0, total: suggestions?.checks_total || 0, finished: [] };
    return card(checkHero({ circle: progressRing(p.done, p.total), title: t("ui.check.running", { done: p.done, total: p.total }),
                            body: p.title }),
      el("div", { class: "check-list" }, (p.finished || []).map(f => el("div", { class: `item ${f.status}` },
        icon(STATUS_ICON[f.status] || "info", `icon s ${f.status}`), el("span", {}, f.title))),
        p.key ? el("div", { class: "item current" }, icon("refresh", "icon spin"), el("span", {}, p.title)) : null));
  }
  if (!suggestions) return el("div", { class: "group loading" }, "…");
  const start = () => runCheck(ctx);
  const summary = suggestions.check;
  if (!suggestions.run || !summary) {
    return card(checkHero({ circle: bigButton(t("ui.check.start"), "gauge", start), title: t("ui.check.idle_title"),
                            body: t("ui.check.idle_body", { count: suggestions.checks_total }) }));
  }
  const time = timeOf(suggestions.run.ts, { withDate: true });
  const again = el("button", { class: "button", onclick: start }, t("ui.check.again"));
  if (!summary.count) {
    return card(checkHero({ circle: statusCircle("ok", "check"), title: t("ui.check.clear_title"),
                            body: t("ui.check.clear_body", { total: summary.total, time }), below: again }),
      miniStats(ctx));
  }
  const worst = summary.problems.some(p => p.status === "bad") ? "bad" : "warn";
  return card(checkHero({ circle: statusCircle(worst, "alert"), title: t("ui.check.found_title", { count: summary.count }),
                          body: t("ui.check.found_body", { time, ok: summary.ok, total: summary.total }), below: again }),
    el("div", { class: "rows" }, summary.problems.map(p => problemRow(p, ctx))));
}

function drawCheck(ctx) {
  slots.check && fill(slots.check, checkCard(ctx));
}

async function runCheck(ctx) {
  if (check.running) return;
  Object.assign(check, { running: true, progress: null });
  drawCheck(ctx);
  try {
    await runJob("/api/diagnostics", {}, { interval: 300, timeout: 180000,
      onProgress: progress => { if (progress) { check.progress = progress; drawCheck(ctx); } } });
    await loadSuggestions(ctx);
    loadBadge().catch(() => {});        // the sidebar count and the Diagnostics page follow the new run
  } catch (err) {
    toast(t("ui.error.failed", { message: err.message }), { bad: true });
  } finally {
    check.running = false;
    drawCheck(ctx);
  }
}

// Static parts are built once per render; the live parts (state, chart, numbers) are swapped in
// place every poll so buttons in progress are never replaced under the user's pointer.
const slots = {};

function drawLive(ctx) {
  slots.hero && fill(slots.hero, heroBlock(ctx));
  slots.chart && fill(slots.chart, chartBlock(ctx));
  slots.stats && fill(slots.stats, statsBlock(ctx));
}

function drawSlow(ctx) {
  const recent = events.filter(e => OUTAGE_KINDS.includes(e.kind) || e.kind === "monitor_gap").slice(0, 3);
  drawCheck(ctx);
  slots.recent && fill(slots.recent, recent.length ? el("div", { class: "group" }, recent.map(eventRow))
                                              : el("div", { class: "group empty" }, t("ui.log.empty")));
  slots.watchdog && fill(slots.watchdog, watchdogPill(ctx));
  drawFailover(ctx);
}

function drawFailover(ctx) {
  // Backup connections: only worth a card when there is more than one way out (or we switched).
  const fo = failover;
  slots.failover && fill(slots.failover, fo && (fo.paths.length > 1 || fo.preferred != null)
    ? [el("div", { class: "section-title" }, t("ui.failover.title")),
       el("div", { class: "group" }, failoverRows(fo, { onChange: () => loadFailover().then(() => drawFailover(ctx)) }))]
    : null);
}

let failover = null;
async function loadFailover() {
  failover = await get("/api/failover").catch(() => null);
}

function draw(ctx) {
  for (const name of ["hero", "check", "chart", "stats", "recent", "watchdog", "failover"]) slots[name] = el("div");
  slots.stats.style.marginTop = "var(--space-3)";
  fill(root(), 
    el("header", { class: "content-header" }, el("h1", {}, t("ui.nav.overview"))),
    slots.hero,
    slots.check,
    el("div", { class: "section-title" }, t("ui.overview.latency")), slots.chart, slots.stats,
    el("div", { class: "two-col" },
      el("div", {}, el("div", { class: "section-title" }, t("ui.overview.recent")), slots.recent),
      el("div", {}, el("div", { class: "section-title" }, t("ui.overview.quick_actions")), quickActions(ctx),
        el("div", { class: "section-title" }, t("ui.watchdog.title")),
        el("a", { class: "group row", href: "#/settings" }, el("div", { class: "icon-tile accent" }, icon("shield")),
          el("div", { class: "main" }, el("div", { class: "label" }, t("ui.watchdog.title")),
            el("div", { class: "secondary" }, t("ui.watchdog.description"))), slots.watchdog))),
    slots.failover);
  drawLive(ctx);
  drawSlow(ctx);
}

export async function render(ctx) {
  const [live, ev] = await Promise.all([get("/api/live?window=900"), get("/api/events?limit=300"), loadSuggestions(ctx),
                                       loadFailover()]);
  ctx.live = live;
  events = ev.events;
  if (range !== "live") {
    const r = RANGES[range];
    history = await get(`/api/history?hours=${r.hours}&bucket=${r.bucket}`);
    events = history.events;
  }
  ctx.updateConnection();
  draw(ctx);
}

let tick = 0;
export async function refresh(ctx) {
  ctx.live = await get("/api/live?window=900");
  ctx.updateConnection();
  if (++tick % 15 === 0) {     // every ~30 s: events, suggestions and settings change slowly
    if (range === "live") events = (await get("/api/events?limit=300")).events;
    await Promise.all([loadSuggestions(ctx), loadFailover()]);
    if (!root().querySelector(".group button:disabled")) drawSlow(ctx);
  } else if (tick % 2 === 0) { // every ~4 s: which connection carries the traffic can change any moment
    const before = failoverKey(failover);
    await loadFailover();
    if (failoverKey(failover) !== before && !slots.failover?.querySelector("button:disabled")) drawFailover(ctx);
  }
  drawLive(ctx);
}

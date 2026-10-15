// The speed test (ADR-0024, as ADR-0023 on Apple): check #14's run read as download, upload, latency,
// jitter and packet loss, laid out like speed.cloudflare.com's top section. The Overview card, the
// Diagnostics Bufferbloat card and the floating bar all start the same job (POST /api/speedtest);
// the job's progress is the live view (app/speedtest.py Live.snapshot).

import { ApiError, get, runJob } from "./api.js";
import { confirmSheet, el, icon, svgEl, toast } from "./dom.js";
import { number, t, timeOf } from "./i18n.js";

const SEGMENTS = 24;                       // one per second: 4 idle, 10 download, 10 upload
const PHASE_SEGMENTS = { idle: [0, 4], download: [4, 14], upload: [14, 24] };
const LOAD_S = 10;

/** Shared by every card on the page: the latest result, the run in progress, and who to redraw. */
export const speed = { last: null, confirmed: false, bufferbloat: null, bufferbloatTs: null, live: null, running: false,
                        now: 0, given_up: null };
const listeners = new Set();

/** `fn` is called whenever the speed test's state changes (a run's progress, a new result). */
export function onSpeedChange(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

const changed = () => listeners.forEach(fn => { try { fn(); } catch (err) { console.error(err); } });

/** Reads the latest result; when a run started elsewhere (the floating bar) is going, follows it. */
export async function loadSpeed() {
  const got = await get("/api/speedtest");
  Object.assign(speed, { last: got.last, confirmed: got.confirmed, bufferbloat: got.bufferbloat,
                         bufferbloatTs: got.bufferbloat_ts, now: got.now, nowAt: Date.now() });
  // Join a run started elsewhere (the floating bar), unless this page already gave up waiting on it.
  if (got.job && !speed.running && got.job !== speed.given_up) follow({ join: true }, got.job);
  changed();
}

const nowSeconds = () => speed.now ? speed.now + (Date.now() - speed.nowAt) / 1000 : Date.now() / 1000;

/** Seconds left of the test server's wait from the last run, or 0. */
export function waitLeft() {
  const until = speed.last?.retry_until;
  return until ? Math.max(0, until - nowSeconds()) : 0;
}

export const canRun = () => !speed.running && waitLeft() === 0;

/** Asks the first time (the answer is remembered by the server), then runs and follows the job. */
export async function runSpeedTest() {
  if (!canRun()) return false;
  let body = {};
  if (!speed.confirmed) {
    const ok = await confirmSheet({ title: t("ui.speed.title"), body: t("ui.diag.bufferbloat_confirm"),
                                    confirm: t("ui.speed.confirm_run"), cancel: t("ui.sheet.cancel"),
                                    symbol: "gauge", tile: "accent" });
    if (!ok) return false;
    body = { confirmed: true };
  }
  return follow(body);
}

/** `{ join: true }` follows the run going now and never starts one (it may have just ended). */
async function follow(body = {}, jobId = null) {
  Object.assign(speed, { running: true, live: speed.live || { stage: "idle", fraction: 0 } });
  changed();
  let ok = false;
  try {
    // The line lock can hold a run back behind a measured tweak, hence the long wait.
    const result = await runJob("/api/speedtest", body, { interval: 250, timeout: 300000,
      onProgress: live => { if (live) { speed.live = live; changed(); } } });
    speed.confirmed = true;
    if (result.speed) speed.last = result.speed;
    else toast(t("ui.speed.failed"), { bad: true });   // check #14 could not run: nothing was measured
    speed.bufferbloat = result.results?.[0] || speed.bufferbloat;
    ok = Boolean(result.speed);
  } catch (err) {
    if (err instanceof ApiError && err.status === 0 && jobId) speed.given_up = jobId;   // timed out: do not rejoin
    // 409: the server wants a confirmation or a wait the page did not know of yet, or a joined run
    // has ended; loadSpeed shows the state.
    if (!(err instanceof ApiError && err.status === 409)) toast(t("ui.speed.failed"), { bad: true });
  } finally {
    Object.assign(speed, { running: false, live: null });
    await loadSpeed().catch(() => changed());
  }
  return ok;
}

// --- drawing --------------------------------------------------------------------------------------

/** Mbps: one decimal below 10. Latency: one decimal below 100, as speed.cloudflare.com shows it. */
const figure = value => value === null || value === undefined ? "—" : number(value, value < 10 ? 1 : 0);
const ms = value => value === null || value === undefined ? "—"
  : number(value, value < 100 && Math.round(value) !== value ? 1 : 0);

function chart(points, direction) {
  const width = 300, height = 90;
  const svg = svgEl("svg", { class: `speed-chart ${direction}`, viewBox: `0 0 ${width} ${height}`,
                             preserveAspectRatio: "none", "aria-hidden": "true" });
  svg.append(svgEl("line", { class: "base", x1: 0, y1: height - .5, x2: width, y2: height - .5 }));
  if (!points?.length) return svg;
  const top = Math.max(...points.map(p => p[1]), 1) * 1.15;
  const x = s => Math.min(width, Math.max(0, s / LOAD_S * width));
  const y = v => height - 2 - v / top * (height - 6);
  const line = points.map((p, i) => `${i ? "L" : "M"}${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join(" ");
  const last = points[points.length - 1];
  svg.append(svgEl("path", { class: "area", d: `${line} L${x(last[0]).toFixed(1)},${height} L${x(points[0][0]).toFixed(1)},${height} Z` }),
             svgEl("path", { class: "line", d: line }));
  return svg;
}

function direction(name, shown) {
  const mbps = shown[`${name}_mbps`];
  const capped = shown[`${name}_capped`];
  return el("div", { class: `speed-direction ${name}` },
    el("div", { class: "speed-label" }, icon(name === "download" ? "down" : "up"), t(`ui.speed.${name}`)),
    el("div", { class: "speed-figure" }, el("span", { class: "value" }, figure(mbps)),
      el("span", { class: "unit" }, t("ui.speed.unit.mbps"))),
    capped && mbps !== null && mbps !== undefined
      ? el("div", { class: "speed-note" }, t("ui.speed.at_least", { value: `${figure(mbps)} ${t("ui.speed.unit.mbps")}` })) : null,
    chart(shown[`${name}_series`], name));
}

function metric(title, value, unit, during) {
  return el("div", { class: "speed-metric" },
    el("div", { class: "speed-label" }, t(title)),
    el("div", { class: "speed-figure small" }, el("span", { class: "value" }, ms(value)), el("span", { class: "unit" }, unit)),
    during ? el("div", { class: "speed-during" },
      el("span", { class: "download" }, icon("down"), ms(during[0])),
      el("span", { class: "upload" }, icon("up"), ms(during[1]))) : null);
}

function latencyColumn(shown) {
  const unit = t("ui.speed.unit.ms");
  return el("div", { class: "speed-latency" },
    metric("ui.speed.latency", shown.idle?.median_ms, unit, [shown.during_download?.median_ms, shown.during_upload?.median_ms]),
    metric("ui.speed.jitter", shown.idle?.jitter_ms, unit, [shown.during_download?.jitter_ms, shown.during_upload?.jitter_ms]),
    metric("ui.speed.loss", shown.loss_pct, "%", null));
}

function progressBar(live) {
  const done = Math.floor((live.fraction || 0) * SEGMENTS + 1e-6);
  const phaseOf = i => Object.entries(PHASE_SEGMENTS).find(([, [a, b]]) => i >= a && i < b)[0];
  return el("div", { class: "speed-progress" },
    el("div", { class: "speed-segments", role: "progressbar", "aria-valuemin": 0, "aria-valuemax": 100,
                "aria-valuenow": Math.round((live.fraction || 0) * 100) },
      Array.from({ length: SEGMENTS }, (_, i) => el("span", { class: i < done ? `on ${phaseOf(i)}` : "" }))),
    el("div", { class: "speed-stage" }, t(`ui.speed.running.${live.stage || "idle"}`)));
}

/** Why a figure is missing: the server refused (with the wait while it lasts), or nothing moved. */
export function problemText(last) {
  if (!last) return null;
  if (last.refused_status) {
    if (!last.retry_until) return t("ui.speed.refused_later");
    const left = waitLeft();
    return left > 0 ? t("ui.speed.refused", { minutes: Math.max(1, Math.ceil(left / 60)) }) : null;
  }
  return last.download_mbps === null || last.download_mbps === undefined
    || last.upload_mbps === null || last.upload_mbps === undefined ? t("ui.speed.failed") : null;
}

function notes(last) {
  const measured = [t("ui.speed.measured", { time: timeOf(last.measured_at, { withDate: true }) }),
                    last.server ? t("ui.speed.server", { code: last.server }) : null].filter(Boolean).join(" · ");
  const problem = problemText(last);
  return el("div", { class: "speed-notes" },
    problem ? el("div", { class: "speed-problem" }, icon("alert"), problem) : null,
    el("div", {}, measured));
}

/** The Overview card. */
export function speedCard() {
  const shown = speed.live || speed.last;
  const button = el("button", { class: "button primary", disabled: !canRun(), onclick: () => runSpeedTest() },
    t(speed.last ? "ui.speed.again" : "ui.speed.start"));
  return el("div", { class: "group speed-card" },
    el("div", { class: "speed-header" },
      el("div", { class: "speed-title" }, el("div", { class: "icon-tile accent" }, icon("gauge")), t("ui.speed.title")),
      speed.running ? el("span", { class: "spinner", "aria-hidden": "true" }) : button),
    shown ? [el("div", { class: "speed-figures" }, direction("download", shown), direction("upload", shown), latencyColumn(shown)),
             speed.live ? progressBar(speed.live) : notes(speed.last)]
      : el("p", { class: "speed-body" }, t("ui.speed.idle_body")));
}

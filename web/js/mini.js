// The floating monitor, in two forms. The one-line bar (default): Optimize on the left, then the
// state dot, download, upload and ping, and at the right the speed test and the expand arrow. Expanded: the three metrics on a sweep chart,
// the apps that hold connections, and the connection's basic facts.
// Optimize only runs the regular check (read-only). When it finds what the app can fix, "Fix N"
// opens the result in the main window, which lists the changes and asks before any (ADR-0020).
// The speed test (ADR-0024) runs from the bar; on a metered connection the main window asks first, and a
// result opens there too.
// app/desktop.py pauses it (window.steadyMini.setActive(false)) while the window is hidden.
// Its look is its own, the bar and the table alike: dark by default, or light / system (localStorage
// "mini-theme", also set from Settings).

import { get, onReachability, runJob } from "./api.js";
import { $, el, fill } from "./dom.js";
import { drawSweep, MBPS_STEPS, MS_STEPS, niceScale } from "./ecg.js";
import { bitrate, liveNumbers, liveTicks, lossVerdict, probesOk } from "./metrics.js";
import { loadLanguage, number, t, translateStatic } from "./i18n.js";

const POLL_MS = 1000;
const STATE_EVERY_MS = 30000;
const DELAY_S = 1.2;          // the pen runs this far behind the newest sample, so it can draw toward it
const LIVE_WINDOW_S = 70;
const STATS_WINDOW_S = 60;    // loss and jitter on the Network tab
const CHECK_EVERY_MS = 30000; // the latest check's summary, for the Optimize button
const CHECK_FRESH_S = 3 * 3600;   // like the Overview: an older result may no longer hold
const GOOD_SHOWN_MS = 8000;   // "All good" after a check, then back to "Optimize"
const SPEED_EVERY_MS = 30000; // the latest speed test, for its button

const view = { metric: "down", tab: "apps", active: true, mode: "bar" };
// idle | busy | fix | issues | good. summary: GET /api/suggestions -> check (fresh runs only)
const optimize = { state: "idle", summary: null, progress: null, goodUntil: 0, loadedAt: 0 };
const data = { traffic: null, live: null, state: null, offset: 0, reachable: true };
// GET /api/speedtest; running while a job is followed, live = its progress (app/speedtest.py Live)
const speedTest = { last: null, ask: false, running: false, live: null, loadedAt: 0, now: 0, nowAt: 0, givenUp: null };
let pollTimer = null;
let frame = null;

const THEMES = ["system", "light", "dark"];
const DEFAULT_THEME = "dark";     // an overlay reads best dark until the user picks another look

function currentTheme() {
  let theme = DEFAULT_THEME;
  try { theme = localStorage.getItem("mini-theme") || DEFAULT_THEME; } catch { /* storage blocked: the default */ }
  return THEMES.includes(theme) ? theme : DEFAULT_THEME;
}

function applyTheme() {
  const theme = currentTheme();
  if (theme === "system") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", theme);
  document.querySelectorAll("#theme-choice button").forEach(b =>
    b.setAttribute("aria-pressed", String(b.dataset.themeValue === theme)));
}

function setTheme(theme) {
  try { localStorage.setItem("mini-theme", theme); } catch { /* applies until the window reloads */ }
  applyTheme();
}

// --- numbers -----------------------------------------------------------------------------------

function rateText(bps) {
  const r = bitrate(bps);
  return r ? t(`ui.mini.unit.${r.unit}`, { value: number(r.value, r.decimals) }) : "—";
}

const msText = ms => (ms === null || ms === undefined ? "—" : t("ui.mini.unit.ms", { value: number(ms, ms < 10 ? 1 : 0) }));

function internetTargets() {
  return Object.keys(data.state?.targets || {});
}

function ticks() {
  return liveTicks(data.live?.samples, internetTargets());
}

/** [[ts, value|null]] for the selected metric; ping: best Internet rtt, null when every target lost it. */
function series(metric) {
  if (metric === "ping") {
    return ticks().filter(tk => tk.internet !== null || tk.inetLost).map(tk => [tk.ts, tk.internet]);
  }
  const column = metric === "down" ? 1 : 2;
  return (data.traffic?.series || []).map(row => [row[0], row[column]]);
}

const COLOURS = { down: "--accent", up: "--chart-internet", ping: "--ok" };

// --- drawing -----------------------------------------------------------------------------------

function draw() {
  frame = null;
  if (!view.active) return;
  const now = Date.now() / 1000 + data.offset - DELAY_S;
  if (view.mode === "bar") return;       // the bar has no chart; setMode("full") starts drawing again
  const points = series(view.metric);
  const isPing = view.metric === "ping";
  const values = points.map(p => p[1]).filter(v => v !== null).map(v => (isPing ? v : v / 1e6));
  const max = niceScale(values, isPing ? MS_STEPS : MBPS_STEPS);
  const scaled = isPing ? points : points.map(([ts, v]) => [ts, v === null ? null : v / 1e6]);
  drawSweep($("#sweep"), scaled, now, { max, colour: COLOURS[view.metric] });
  $("#scale").textContent = isPing ? msText(max) : rateText(max * 1e6);
  frame = requestAnimationFrame(draw);
}

function startDrawing() {
  if (frame === null && view.active) frame = requestAnimationFrame(draw);
}

// --- tiles, header ----------------------------------------------------------------------------

function latest(points) {
  for (let i = points.length - 1; i >= 0; i--) if (points[i][1] !== null) return points[i][1];
  return null;
}

/** The three numbers both forms show: download, upload, ping. */
function figures() {
  const iface = data.traffic?.interface;
  // One lost ping is common (ISPs rate-limit ICMP): "Lost" only after two in a row.
  const ping = series("ping");
  const lostInARow = ping.length >= 2 && ping.slice(-2).every(p => p[1] === null);
  return { down: iface ? rateText(latest(series("down"))) : "—", up: iface ? rateText(latest(series("up"))) : "—",
           ping: lostInARow ? t("ui.mini.lost") : msText(latest(ping)) };
}

function renderTiles() {
  const f = figures();
  $("#value-down").textContent = f.down;
  $("#value-up").textContent = f.up;
  $("#value-ping").textContent = f.ping;
}

function stateLevel() {
  const outages = data.live?.outages || {};
  return !data.reachable || !data.live ? "info" : outages.router || outages.internet ? "bad" : "";
}

function renderBar() {
  const f = figures();
  $("#bar-down").textContent = f.down;
  $("#bar-up").textContent = f.up;
  $("#bar-ping").textContent = f.ping;
  $("#bar-dot").className = `dot ${stateLevel()}`;
  $("#bar-dot").title = $("#state-text").textContent;
  renderOptimize();
  renderSpeed();
}

// --- Optimize (the bar's button) ----------------------------------------------------------------

const OPTIMIZE_GLYPH = { idle: "bolt", busy: "search", fix: "bolt", issues: "alert", good: "check" };

function renderOptimize() {
  const button = $("#optimize");
  if (optimize.state === "good" && Date.now() > optimize.goodUntil) optimize.state = "idle";
  const { state, summary, progress: p } = optimize;
  // No text on the button: what it does or found goes in its label and tooltip.
  const label = { idle: () => t("ui.mini.optimize"),
                  busy: () => (p ? t("ui.mini.checking", { done: p.done, total: p.total }) : t("ui.mini.checking_start")),
                  fix: () => t("ui.mini.fix", { count: summary.fixable }),
                  issues: () => t("ui.mini.issues", { count: summary.count }),
                  good: () => t("ui.mini.all_good") }[state]();
  const hint = state === "fix" || state === "issues" ? t("ui.mini.fix_hint") : state === "idle" ? t("ui.mini.optimize_hint") : "";
  button.className = `optimize ${state}`;
  button.disabled = state === "busy";
  button.setAttribute("aria-label", label);
  button.title = hint ? `${label}\n${hint}` : label;
  $("#optimize-glyph").firstElementChild.setAttribute("href", `#i-${OPTIMIZE_GLYPH[state]}`);
  const percent = state === "busy" && p && p.total ? Math.round((100 * p.done) / p.total) : 0;
  $("#optimize-ring").style.strokeDasharray = `${percent} 100`;
  // A result shows its count on the button, in place of the icon.
  const count = state === "fix" ? summary.fixable : state === "issues" ? summary.count : null;
  $("#optimize-count").hidden = count === null;
  $("#optimize-glyph").style.display = count === null ? "" : "none";
  $("#optimize-count").textContent = count === null ? "" : String(count);
}

/** What the latest stored check found, if it is recent enough to act on. */
async function loadCheck() {
  const suggestions = await get("/api/suggestions");
  optimize.loadedAt = Date.now();
  const run = suggestions.run;
  const fresh = run && Date.now() / 1000 + data.offset - run.ts < CHECK_FRESH_S;
  optimize.summary = fresh ? suggestions.check : null;
  if (optimize.state === "busy" || optimize.state === "good") return;
  const summary = optimize.summary;
  optimize.state = summary?.fixable ? "fix" : summary?.count ? "issues" : "idle";
}

async function runOptimize() {
  if (optimize.state === "fix" || optimize.state === "issues") {
    // The main window lists what was found and what Fix turns on; nothing changes before Fix there.
    window.pywebview?.api?.open_result?.();
    return;
  }
  Object.assign(optimize, { state: "busy", progress: null });
  renderOptimize();
  try {
    await runJob("/api/diagnostics", {}, { interval: 500, timeout: 180000,
      onProgress: progress => { if (progress) { optimize.progress = progress; renderOptimize(); } } });
    optimize.state = "idle";
    await loadCheck();
    if (!optimize.summary?.count) Object.assign(optimize, { state: "good", goodUntil: Date.now() + GOOD_SHOWN_MS });
  } catch {
    optimize.state = "idle";
  }
  optimize.progress = null;
  renderOptimize();
}

// --- the speed test (the button in the tube) -----------------------------------------------------

const speedNow = () => speedTest.now ? speedTest.now + (Date.now() - speedTest.nowAt) / 1000 : Date.now() / 1000;
const mbps = v => v === null || v === undefined ? "—" : `${number(v, v < 10 ? 1 : 0)} ${t("ui.speed.unit.mbps")}`;
const ms = v => v === null || v === undefined ? "—" : `${number(v, v < 100 && Math.round(v) !== v ? 1 : 0)} ${t("ui.speed.unit.ms")}`;

/** running | done (the download figure) | problem (a recent run without one) | idle (the gauge). */
function speedState() {
  const last = speedTest.last;
  if (speedTest.running) return "running";
  if (!last || speedNow() - last.measured_at >= CHECK_FRESH_S) return "idle";
  if (last.download_mbps !== null && last.download_mbps !== undefined) return "done";
  return last.refused_status ? "idle" : "problem";   // refused: the gauge, with the wait while it lasts
}

function speedWait() {
  const until = speedTest.last?.retry_until;
  return until ? Math.max(0, until - speedNow()) : 0;
}

function renderSpeed() {
  const button = $("#speed");
  const { last, live } = speedTest;
  const state = speedState();
  const wait = state === "idle" ? speedWait() : 0;
  button.className = `speed ${state}`;
  button.disabled = state === "running" || wait > 0;
  $("#speed-ring").style.strokeDasharray = `${Math.round(100 * (live?.fraction || 0))} 100`;
  $("#speed-figure").hidden = state !== "done";
  $("#speed-figure").textContent = state === "done" ? number(last.download_mbps, last.download_mbps < 10 ? 1 : 0) : "";
  $("#speed-glyph").setAttribute("href", state === "problem" ? "#i-alert" : "#i-gauge");
  const label = state === "running" ? t(`ui.speed.running.${live?.stage || "idle"}`)
    : state === "done" ? `${t("ui.speed.mini.result", { download: mbps(last.download_mbps), upload: mbps(last.upload_mbps),
                                                       ping: ms(last.idle?.median_ms) })}\n${t("ui.speed.mini.open")}`
    : wait > 0 ? t("ui.speed.refused", { minutes: Math.max(1, Math.ceil(wait / 60)) })
    : state === "problem" ? `${t(last?.untrusted ? "ui.speed.untrusted" : "ui.speed.failed")}\n${t("ui.speed.mini.open")}`
    : t("ui.speed.mini.hint");
  button.title = label;
  button.setAttribute("aria-label", label);
}

async function loadSpeed() {
  const got = await get("/api/speedtest");
  Object.assign(speedTest, { last: got.last, ask: got.ask, loadedAt: Date.now(), now: got.now, nowAt: Date.now() });
  // Started in the main window: follow it, never start one (join), unless the bar gave up on it.
  if (got.job && !speedTest.running && got.job !== speedTest.givenUp) followSpeed(got.job);
}

async function followSpeed(jobId = null) {
  Object.assign(speedTest, { running: true, live: null });
  renderSpeed();
  let askNow = false;
  try {
    const result = await runJob("/api/speedtest", jobId ? { join: true } : {}, { interval: 500, timeout: 300000,
      onProgress: live => { if (live) { speedTest.live = live; renderSpeed(); } } });
    askNow = Boolean(result?.needs_confirmation);
  } catch (err) {   // the button goes back to the gauge; the main window says why
    if (jobId && err?.status === 0) speedTest.givenUp = jobId;
    askNow = !jobId && err?.status === 409;
  }
  Object.assign(speedTest, { running: false, live: null });
  await loadSpeed().catch(() => {});
  renderSpeed();
  // The route moved to a metered connection since the bar last looked: the main window asks.
  if (askNow && speedTest.ask) window.pywebview?.api?.open_speed?.(true);
}

function runSpeed() {
  const state = speedState();
  if (state === "running") return;
  // A result, or the question on a metered connection, is shown in the main window: the bar has no room to
  // explain how much data the test uses.
  if (state === "done" || state === "problem" || speedTest.ask) {
    window.pywebview?.api?.open_speed?.(state === "idle");
    return;
  }
  followSpeed();
}

function renderHeader() {
  const outages = data.live?.outages || {};
  $("#state-dot").className = `dot ${stateLevel()}`;
  $("#state-text").textContent = !data.reachable ? t("ui.error.offline")
    : outages.router ? t("ui.state.router_down") : outages.internet ? t("ui.state.internet_down")
    : data.live ? t("ui.state.online") : "";
}

// --- the Apps tab ------------------------------------------------------------------------------

function renderApps() {
  const apps = data.traffic?.apps;
  const list = $("#apps");
  if (apps === undefined || apps === null) {
    fill(list, el("div", { class: "empty" }, data.traffic ? t("ui.mini.apps.unavailable") : t("ui.mini.waiting")));
    return;
  }
  if (!apps.length) {
    fill(list, el("div", { class: "empty" }, t("ui.mini.apps.empty")));
    return;
  }
  fill(list, apps.map(app => el("div", { class: "row", title: app.exe },
    el("span", { class: "avatar" }, (app.name.match(/[\p{L}\p{N}]/u) || ["?"])[0].toUpperCase()),
    el("div", { class: "main" },
      el("div", { class: "label" }, app.name),
      app.processes > 1 ? el("div", { class: "secondary" }, t("ui.mini.apps.processes", { count: app.processes })) : null),
    el("div", { class: "end" },
      el("div", { class: "strong end" }, t("ui.mini.apps.connections", { count: app.tcp })),
      app.udp ? el("div", { class: "secondary" }, t("ui.mini.apps.udp", { count: app.udp })) : null))));
}

// --- the Network tab ---------------------------------------------------------------------------

function fact(labelKey, value, cls = "") {
  return el("div", { class: "row fact" }, el("span", { class: "label" }, t(labelKey)),
    el("span", { class: `end ${cls}`, title: typeof value === "string" ? value : null }, value ?? "—"));
}

function renderNetwork() {
  const iface = data.traffic?.interface;
  const conn = data.traffic?.connection || {};
  const live = data.live;
  const wifi = live?.wifi && iface?.kind === "wifi" && live.wifi.state?.toLowerCase() === "connected" ? live.wifi : null;
  const recent = ticks().filter(tk => tk.ts >= (live?.ts || 0) - STATS_WINDOW_S);
  const nums = liveNumbers(recent);
  const lastRouter = [...recent].reverse().find(tk => tk.router !== undefined);
  const lastInternet = [...recent].reverse().find(tk => tk.internet !== null || tk.inetLost);
  const verdict = lossVerdict(nums.lossPct, nums.routerLossPct, probesOk(live, live?.ts || 0));
  const lossClass = { ok: "ok", limited: "", bad: "bad", none: "" }[verdict.level];
  const outages = live?.outages || {};
  const rows = [];

  if (!data.traffic) {
    fill($("#network"), el("div", { class: "empty" }, t("ui.mini.waiting")));
    return;
  }
  rows.push(fact("ui.mini.net.status", outages.router ? t("ui.state.router_down")
    : outages.internet ? t("ui.state.internet_down") : live ? t("ui.state.online") : null,
    outages.router || outages.internet ? "bad" : live ? "ok" : ""));
  if (!iface) {
    rows.push(fact("ui.mini.net.connection", t("ui.mini.net.none")));
  } else {
    rows.push(fact("ui.mini.net.connection", t(`ui.mini.net.kind.${iface.kind}`)));
    rows.push(fact("ui.mini.net.adapter", iface.description || iface.alias));
    if (wifi) {
      rows.push(fact("ui.mini.net.wifi", wifi.ssid || "—"));
      rows.push(fact("ui.overview.signal", wifi.signal === null ? null
        : `${wifi.signal}%${wifi.rssi !== null ? ` · ${wifi.rssi} dBm` : ""}`));
      const band = [wifi.band, wifi.channel !== null ? t("ui.mini.net.channel", { channel: wifi.channel }) : null]
        .filter(Boolean).join(" · ");
      if (band) rows.push(fact("ui.mini.net.band", band));
    }
    const rx = wifi?.rx_mbps ?? (iface.rx_link_bps ? Math.round(iface.rx_link_bps / 1e6) : null);
    const tx = wifi?.tx_mbps ?? (iface.tx_link_bps ? Math.round(iface.tx_link_bps / 1e6) : null);
    if (rx !== null || tx !== null) {
      rows.push(fact("ui.overview.link_rate", t("ui.mini.net.link", { down: rx ?? "—", up: tx ?? "—" })));
    }
  }
  rows.push(fact("ui.mini.net.local_ip", conn.local_ipv4));
  const gateway = live?.targets?.router;
  rows.push(fact("ui.overview.router", gateway ? (lastRouter && lastRouter.router !== null
    ? `${gateway} · ${msText(lastRouter.router)}` : gateway) : null));
  rows.push(fact("ui.mini.net.dns", conn.dns?.length ? conn.dns.join(", ") : null));
  rows.push(fact("ui.overview.internet", lastInternet ? (lastInternet.inetLost ? t("ui.mini.lost") : msText(lastInternet.internet)) : null));
  rows.push(fact("ui.overview.loss", nums.lossPct === null ? null : `${number(nums.lossPct, 1)}%`, lossClass));
  rows.push(fact("ui.overview.jitter", msText(nums.jitter)));
  fill($("#network"), rows);
}

function render() {
  renderHeader();
  if (view.mode === "bar") {
    renderBar();
    return;
  }
  renderTiles();
  if (view.tab === "apps") renderApps();
  else renderNetwork();
}

// --- polling -----------------------------------------------------------------------------------

let lastState = 0;
async function poll() {
  clearTimeout(pollTimer);
  pollTimer = null;
  if (!view.active) return;
  try {
    const due = Date.now() - lastState > STATE_EVERY_MS;
    const [traffic, live, state] = await Promise.all([
      get("/api/traffic"), get(`/api/live?window=${LIVE_WINDOW_S}`), due ? get("/api/state") : null]);
    data.traffic = traffic;
    data.live = live;
    if (state) { data.state = state; lastState = Date.now(); }
    data.offset = traffic.ts - Date.now() / 1000;
    if (Date.now() - optimize.loadedAt > CHECK_EVERY_MS && optimize.state !== "busy") await loadCheck();
    if (Date.now() - speedTest.loadedAt > SPEED_EVERY_MS && !speedTest.running) await loadSpeed();
  } catch { /* the header says the monitor is offline */ }
  render();
  if (view.active) pollTimer = setTimeout(poll, POLL_MS);
}

function setActive(active) {
  view.active = Boolean(active);
  if (view.active) {
    applyTheme();
    if (pollTimer === null) poll();
    startDrawing();
  } else {
    clearTimeout(pollTimer);
    pollTimer = null;
    if (frame !== null) cancelAnimationFrame(frame);
    frame = null;
  }
}

function select(metric) {
  view.metric = metric;
  document.querySelectorAll(".tile").forEach(b => b.setAttribute("aria-selected", String(b.dataset.metric === metric)));
  $("#sweep").setAttribute("aria-label", t(`ui.mini.${{ down: "download", up: "upload", ping: "ping" }[metric]}`));
}

/** "bar" or "full": the page's layout; inside the desktop shell the window resizes to match. */
async function setMode(mode, { tellShell = true } = {}) {
  const api = window.pywebview?.api;
  if (tellShell && api?.set_mode) mode = await api.set_mode(mode);
  view.mode = mode;
  document.documentElement.dataset.mode = mode;
  render();
  startDrawing();
}

function showTab(tab) {
  view.tab = tab;
  document.querySelectorAll(".switcher button").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.tab === tab)));
  $("#panel-apps").hidden = tab !== "apps";
  $("#panel-network").hidden = tab !== "network";
  render();
}

async function start() {
  applyTheme();
  onReachability(ok => { data.reachable = ok; renderHeader(); });
  try { await loadLanguage(); } catch { data.reachable = false; }
  translateStatic();
  document.title = t("app.name");
  $("#close").title = t("ui.mini.close");
  $("#expand").title = t("ui.mini.expand");
  $("#collapse").title = t("ui.mini.collapse");
  $("#expand").addEventListener("click", () => setMode("full"));
  $("#collapse").addEventListener("click", () => setMode("bar"));
  $("#optimize").addEventListener("click", runOptimize);
  $("#speed").addEventListener("click", runSpeed);
  $("#appearance").title = t("ui.mini.appearance");
  $("#appearance").addEventListener("click", () => {
    const open = $("#options").hidden;
    $("#options").hidden = !open;
    $("#appearance").setAttribute("aria-expanded", String(open));
  });
  document.querySelectorAll("#theme-choice button").forEach(b => b.addEventListener("click", () => setTheme(b.dataset.themeValue)));
  applyTheme();
  document.querySelectorAll(".tile").forEach(b => b.addEventListener("click", () => select(b.dataset.metric)));
  document.querySelectorAll(".switcher button").forEach(b => b.addEventListener("click", () => showTab(b.dataset.tab)));
  // The close button exists only inside the desktop shell, which hides the window (the tray brings it back).
  const bridge = async () => {
    const api = window.pywebview?.api;
    if (!api?.hide_mini) return;
    $("#close").hidden = false;
    $("#close").addEventListener("click", () => api.hide_mini());
    await setMode(await api.get_mode(), { tellShell: false });
  };
  if (window.pywebview?.api) bridge();
  else window.addEventListener("pywebviewready", bridge, { once: true });
  window.addEventListener("storage", e => { if (e.key === "mini-theme") applyTheme(); });   // set from Settings
  select(view.metric);
  document.documentElement.dataset.mode = view.mode;
  render();
  setActive(true);
}

window.steadyMini = { setActive };
start();

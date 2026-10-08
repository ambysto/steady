// The floating monitor: three metrics (download, upload, ping) on a sweep chart, the apps that
// hold connections, and the connection's basic facts. Read-only; nothing here changes the machine.
// app/desktop.py pauses it (window.steadyMini.setActive(false)) while the window is hidden.

import { get, onReachability } from "./api.js";
import { $, el, fill } from "./dom.js";
import { drawSweep, MBPS_STEPS, MS_STEPS, niceScale } from "./ecg.js";
import { bitrate, liveNumbers, liveTicks, lossVerdict, probesOk } from "./metrics.js";
import { loadLanguage, number, t, translateStatic } from "./i18n.js";

const POLL_MS = 1000;
const STATE_EVERY_MS = 30000;
const DELAY_S = 1.2;          // the pen runs this far behind the newest sample, so it can draw toward it
const LIVE_WINDOW_S = 70;
const STATS_WINDOW_S = 60;    // loss and jitter on the Network tab

const view = { metric: "down", tab: "apps", active: true };
const data = { traffic: null, live: null, state: null, offset: 0, reachable: true };
let pollTimer = null;
let frame = null;

function applyTheme() {
  let theme = "system";
  try { theme = localStorage.getItem("theme") || "system"; } catch { /* storage blocked: follow the system */ }
  if (theme === "system") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", theme);
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
  const points = series(view.metric);
  const isPing = view.metric === "ping";
  const values = points.map(p => p[1]).filter(v => v !== null).map(v => (isPing ? v : v / 1e6));
  const max = niceScale(values, isPing ? MS_STEPS : MBPS_STEPS);
  const scaled = isPing ? points : points.map(([ts, v]) => [ts, v === null ? null : v / 1e6]);
  const now = Date.now() / 1000 + data.offset - DELAY_S;
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

function renderTiles() {
  $("#value-down").textContent = data.traffic?.interface ? rateText(latest(series("down"))) : "—";
  $("#value-up").textContent = data.traffic?.interface ? rateText(latest(series("up"))) : "—";
  const ping = series("ping");
  const last = ping[ping.length - 1];
  $("#value-ping").textContent = last && last[1] === null ? t("ui.mini.lost") : msText(latest(ping));
}

function renderHeader() {
  const outages = data.live?.outages || {};
  const level = !data.reachable || !data.live ? "info" : outages.router || outages.internet ? "bad" : "";
  $("#state-dot").className = `dot ${level}`;
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

function showTab(tab) {
  view.tab = tab;
  document.querySelectorAll(".segmented button").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.tab === tab)));
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
  document.querySelectorAll(".tile").forEach(b => b.addEventListener("click", () => select(b.dataset.metric)));
  document.querySelectorAll(".segmented button").forEach(b => b.addEventListener("click", () => showTab(b.dataset.tab)));
  // The close button exists only inside the desktop shell, which hides the window (the tray brings it back).
  const bridge = () => {
    if (!window.pywebview?.api?.hide_mini) return;
    $("#close").hidden = false;
    $("#close").addEventListener("click", () => window.pywebview.api.hide_mini());
  };
  if (window.pywebview?.api) bridge();
  else window.addEventListener("pywebviewready", bridge, { once: true });
  window.addEventListener("storage", e => { if (e.key === "theme") applyTheme(); });
  select(view.metric);
  render();
  setActive(true);
}

window.steadyMini = { setActive };
start();

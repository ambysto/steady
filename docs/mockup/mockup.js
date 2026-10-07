/* SIC-27 mockup logic. Sample data only. The text rules mirror app/i18n.py: messages are
   {key, params}; English is the fallback; {name} / {name:spec} only; plural dicts; decimal comma. */
"use strict";

const LOCALES = window.LOCALES || { en: {} };
const DECIMAL_COMMA = new Set(["vi", "fr", "de", "es", "pt", "it", "ru", "id", "tr", "nl", "pl", "cs", "uk", "sv",
  "da", "fi", "nb", "ro", "hu", "el"]);
const ONE_IF_ONE = new Set(["en", "de", "es", "it", "pt", "nl", "sv", "da", "nb", "fi", "el", "hu", "tr", "bg"]);
const PLACEHOLDER = /\{([A-Za-z_][A-Za-z0-9_]*)(?::([^{}]*))?\}/g;

let lang = "en";
const base = code => code.split("-")[0].toLowerCase();
const msg = (key, params = {}) => ({ key, params });
const isMessage = v => v && typeof v === "object" && typeof v.key === "string" && "params" in v;

function pluralCategory(code, count) {
  const n = Math.abs(Number(count));
  if (Number.isNaN(n)) return "other";
  if (ONE_IF_ONE.has(base(code))) return n === 1 ? "one" : "other";
  if (base(code) === "fr") return n < 2 ? "one" : "other";
  return "other";
}

function formatValue(value, spec) {
  if (isMessage(value)) return render(value);
  if (Array.isArray(value)) return value.map(v => formatValue(v, spec)).join(", ");
  let text = String(value);
  const m = spec && /^(\+)?\.(\d)([f%])$/.exec(spec);
  if (m && typeof value === "number") {
    const scaled = m[3] === "%" ? value * 100 : value;
    text = (m[1] && scaled >= 0 ? "+" : "") + scaled.toFixed(Number(m[2])) + (m[3] === "%" ? "%" : "");
  }
  if (typeof value === "number" && !Number.isInteger(value) && DECIMAL_COMMA.has(base(lang))) text = text.replace(".", ",");
  return text;
}

function t(key, params = {}) {
  let template = (LOCALES[lang] || {})[key] ?? (LOCALES.en || {})[key];
  if (template === undefined) return key;
  if (typeof template === "object") template = template[pluralCategory(lang, params.count)] ?? template.other;
  return template.replace(PLACEHOLDER, (all, name, spec) => (name in params ? formatValue(params[name], spec) : all));
}
const render = message => (isMessage(message) ? t(message.key, message.params) : String(message ?? ""));

function duration(seconds) {
  const h = Math.floor(seconds / 3600), m = Math.floor(seconds % 3600 / 60), s = Math.floor(seconds % 60);
  if (h && !m) return t("time.hours", { count: h });
  if (h) return t("time.hours_minutes", { hours: t("time.hours", { count: h }), minutes: t("time.minutes", { count: m }) });
  if (m && s) return t("time.minutes_seconds", { minutes: t("time.minutes", { count: m }), seconds: t("time.seconds", { count: s }) });
  if (m) return t("time.minutes", { count: m });
  return t("time.seconds", { count: s });
}

// --- sample data (shapes match the real API; values from the dev PC, 2026-10-04) -------------

const NOW = new Date("2026-10-04T12:00:00");
const ago = minutes => new Date(NOW - minutes * 60000);

const TWEAKS = [
  ["wifi_power_saving", "wifi_card", "low", true, true, { disrupts: true }],
  ["wifi_wake_magic", "wifi_card", "low", false, true, { disrupts: true }],
  ["wifi_wake_pattern", "wifi_card", "low", false, true, { disrupts: true }],
  ["wifi_roaming", "wifi_card", "low", false, false, { reason: msg("tweak.reason.no_property") }],
  ["wifi_bw20_5g", "wifi_card", "experimental", false, true, { disrupts: true }],
  ["wifi_mode_ac", "wifi_card", "experimental", false, true, { disrupts: true }],
  ["device_power_off", "power", "low", false, true, { disrupts: true }],
  ["power_wireless_max", "power", "low", false, true, {}],
  ["power_pcie_aspm_off", "power", "low", false, true, {}],
  ["tcp_timedwait", "stack", "medium", false, true, { reboot: true }],
  ["ipv6_off", "stack", "experimental", false, true, { disrupts: true }],
].map(([id, group, risk, enabled, supported, extra]) => ({ id, group, risk, enabled, supported, ...extra,
  hasNote: id !== "wifi_wake_pattern" }));

const CHECKS = [
  { id: 1, key: "driver", status: "bad", summary: msg("diag.driver.ihv_bad", { count: 29, ago: msg("diag.ago.hours", { count: 14 }) }),
    details: [msg("diag.driver.version", { description: "TP-Link Wi-Fi 6 PCIe Adapter", version: "25.40.2.585", date: "2025-12-05", provider: "MediaTek" }),
      msg("diag.driver.age", { count: 10 }), msg("diag.driver.ihv_detail", { count: 29, time: "2026-10-03 21:28" })],
    advice: msg("diag.driver.advice") },
  { id: 2, key: "signal", status: "warn", summary: msg("diag.signal.fair", { rssi: -65 }),
    details: [msg("diag.signal.network", { ssid: "HomeNet_5G", channel: 40, radio: "802.11ax" }), "RSSI -65 dBm (79%)",
      msg("diag.signal.rates", { rx: 1201, tx: 1201 })], advice: msg("diag.signal.advice") },
  { id: 3, key: "interference", status: "ok", summary: msg("diag.interference.quiet", { channel: 40, count: 0 }), details: [] },
  { id: 4, key: "drops", status: "bad", summary: msg("diag.drops.bad", { count: 36, ago: msg("diag.ago.hours", { count: 14 }) }),
    details: [msg("diag.drops.reason", { count: 36, reason: "The network is disconnected by the driver." }),
      msg("diag.drops.by_day", { days: "10-01: 5 · 10-02: 18 · 10-03: 11" })],
    advice: msg("diag.drops.advice"), tweak: "power_pcie_aspm_off" },
  { id: 5, key: "ping", status: "info", summary: msg("diag.ping.icmp_limited"),
    details: [msg("diag.ping.line", { target: "router", loss: 0.58, lost: 20, sent: 3473, jitter: msg("diag.ping.jitter", { jitter: 4.4 }), recent: "" }),
      msg("diag.ping.line", { target: "google", loss: 10.39, lost: 361, sent: 3473, jitter: msg("diag.ping.jitter", { jitter: 5.6 }), recent: "" }),
      msg("diag.ping.probe_line", { target: "tcp_cloudflare", loss: 0.0, lost: 0, sent: 349 })],
    advice: msg("diag.ping.advice_icmp_limited") },
  { id: 6, key: "dns", status: "warn", summary: msg("diag.dns.broken", { servers: ["1.0.0.1"] }),
    details: [msg("diag.dns.line", { label: msg("diag.dns.role.in_use"), server: "1.0.0.1", median: msg("diag.common.ms", { value: 120.6 }), failures: 2, sent: 5 }),
      msg("diag.dns.line", { label: msg("diag.dns.role.router"), server: "192.168.3.1", median: msg("diag.common.ms", { value: 7.4 }), failures: 0, sent: 5 })],
    advice: msg("diag.dns.advice_broken") },
  { id: 7, key: "tcp_ports", status: "ok", summary: msg("diag.tcp_ports.ok"), details: [msg("diag.tcp_ports.time_wait", { value: 31 })] },
  { id: 8, key: "vpn", status: "ok", summary: msg("diag.vpn.none"), details: [] },
  { id: 9, key: "wired", status: "ok", summary: msg("diag.wired.no_port"), details: [] },
  { id: 10, key: "tweaks", status: "info", summary: msg("diag.tweaks.todo", { count: 6 }), details: [], advice: msg("diag.tweaks.advice") },
  { id: 11, key: "wifi7_mlo", status: "ok", summary: msg("diag.wifi7_mlo.router_no_be"), details: [] },
  { id: 12, key: "modem_wifi", status: "ok", summary: msg("diag.modem_wifi.ok"), details: [] },
  { id: 13, key: "physical_link", status: "ok", summary: msg("diag.physical_link.ok"),
    details: [msg("diag.physical_link.bad_minutes", { bad: 2, total: 573, fraction: 2 / 573 }), msg("diag.physical_link.rssi", { rssi: -65.0 })] },
  { id: 15, key: "route", status: "ok", summary: msg("diag.route.ok", { near: 34 }), details: [] },
  { id: 16, key: "path_mtu", status: "ok", summary: msg("diag.path_mtu.ok", { mtu: 1492 }), details: [msg("diag.path_mtu.pppoe")] },
];

const EVENTS = [
  { at: ago(25), kind: "watchdog_dry_run", group: "watchdog", level: "info",
    message: msg("watchdog.event.dry_run", { action: msg("watchdog.action.reconnect_force"),
      reason: msg("watchdog.reason.acting", { problem: msg("watchdog.problem.router_unreachable"), seconds: 16 }) }) },
  { at: ago(26), kind: "router_down", group: "outages", level: "bad", duration: 43,
    message: msg("event.router_down", { router: "192.168.3.1" }) },
  { at: ago(140), kind: "tweak_enabled", group: "changes", level: "info",
    message: msg("tweak.event.enabled", { name: msg("tweak.wifi_power_saving.name") }) },
  { at: ago(150), kind: "settings_changed", group: "changes", level: "info",
    message: msg("event.settings_changed", { changes: '{"watchdog": {"dry_run": true}}' }) },
  { at: ago(60 * 14), kind: "internet_down", group: "outages", level: "bad", duration: 125,
    message: msg("event.internet_down.wan") },
  { at: ago(60 * 26), kind: "monitor_gap", group: "outages", level: "info", duration: 28804,
    message: msg("event.monitor_gap", { seconds: 28804 }) },
  { at: ago(60 * 27), kind: "watchdog_skip", group: "watchdog", level: "info",
    message: msg("watchdog.event.skip", { problem: msg("watchdog.problem.wifi_down"), reason: msg("watchdog.reason.user_disconnected") }) },
];

// Shapes follow GET /api/impact, /api/suggestions (app/impact.py, app/suggestions.py).
const caveat = msg("impact.caveat");
const IMPACT = {
  wifi_power_saving: { status: "better", summary: msg("impact.better"), preliminary: false,
    details: [msg("impact.metric.outages", { before: 12.0, after: 1.0 }), msg("impact.metric.offline_minutes", { before: 9.0, after: 1.0 }),
      msg("impact.metric.router_loss", { before: 2.1, after: 0.4 }), caveat] },
};
const SUGGESTIONS = [
  { kind: "manual", id: "driver_update", status: "bad", reason: msg("diag.driver.title") },
  { kind: "tweak", id: "power_pcie_aspm_off", status: "bad", reason: msg("diag.drops.title") },
  { kind: "manual", id: "move_closer", status: "warn", reason: msg("diag.signal.title") },
  { kind: "manual", id: "dns_server", status: "warn", reason: msg("diag.dns.title") },
  { kind: "manual", id: "antenna", status: "ok", reason: msg("diag.physical_link.title"), doneAt: ago(60 * 26),
    impact: { status: "better", summary: msg("impact.better"), preliminary: false,
      details: [msg("impact.metric.bad_link", { before: 0.23, after: 0.03 }), msg("impact.metric.router_loss", { before: 4.8, after: 0.6 }), caveat] } },
  { kind: "manual", id: "modem_wifi_off", status: "ok", reason: msg("diag.modem_wifi.title"), doneAt: ago(90),
    impact: { status: "collecting", summary: msg("impact.collecting", { hours: 1.5, needed: 2 }), details: [] } },
];
EVENTS.unshift(
  { at: ago(8), kind: "dns_changed", group: "changes", level: "warn",
    message: msg("event.dns_changed", { old: ["1.1.1.1", "1.0.0.1"], new: ["45.90.28.1"] }) },
  { at: ago(90), kind: "manual_step_done", group: "changes", level: "info",
    message: msg("manual.event.done", { step: msg("manual.modem_wifi_off.title") }) });

const STATE = { simulate: "online", downSince: 47 };

// --- helpers ------------------------------------------------------------------------------

const $ = sel => document.querySelector(sel);
const el = (tag, attrs = {}, ...children) => {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v; else if (k === "html") node.innerHTML = v; else node.setAttribute(k, v);
  }
  for (const c of children.flat()) if (c != null) node.append(c.nodeType ? c : document.createTextNode(c));
  return node;
};
const icon = (name, cls = "icon") => {
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("class", cls);
  const u = document.createElementNS("http://www.w3.org/2000/svg", "use");
  u.setAttribute("href", "#i-" + name);
  s.append(u);
  return s;
};
const STATUS_ICON = { ok: "check", warn: "alert", bad: "x", info: "info" };
const timeOf = d => d.toLocaleTimeString(lang, { hour: "2-digit", minute: "2-digit" });

// --- screens --------------------------------------------------------------------------------

function renderStatic() {
  document.documentElement.lang = lang;
  document.querySelectorAll("[data-t]").forEach(node => { node.textContent = t(node.dataset.t); });
  $("#chart-window").textContent = t("ui.overview.window", { count: 15 });
  $("#notify-after").textContent = t("ui.settings.notify_after", { duration: duration(30) });
}

function renderState() {
  const s = STATE.simulate;
  const level = s === "online" ? "ok" : "bad";
  const title = t("ui.state." + s);
  const detail = s === "online" ? t("ui.state.online_detail") : t(`ui.state.${s}_detail`, { duration: duration(STATE.downSince) });
  $("#hero").className = "group hero " + (level === "ok" ? "" : "bad");
  $("#hero .status-icon use").setAttribute("href", s === "router_down" ? "#i-wifi-off" : "#i-wifi");
  $("#hero-title").textContent = title;
  $("#hero-detail").textContent = detail;
  $("#sidebar-conn .dot").className = "dot " + (level === "ok" ? "" : "bad");
  $("#sidebar-conn-text").textContent = title;
  $("#tray-dot").className = "dot " + (level === "ok" ? "" : "bad");
  $("#tray-title").textContent = title;

  const down = s !== "online";
  const nums = [["ui.overview.router", down && s === "router_down" ? "—" : "2 ms"],
                ["ui.overview.internet", down ? "—" : "38 ms"], ["ui.overview.loss", formatValue(down ? 100.0 : 0.6, ".1f") + "%"]];
  $("#tray-nums").replaceChildren(...nums.map(([k, v]) => el("div", {}, el("div", { class: "k" }, t(k)), el("div", { class: "v" }, v))));

  const stats = [
    ["ui.overview.loss", formatValue(down ? 100.0 : 0.58, ".2f"), "%"], ["ui.overview.jitter", formatValue(4.4, ".1f"), " ms"],
    ["ui.overview.signal", "-65", " dBm"], ["ui.overview.link_rate", "1201", " Mbps"], ["ui.overview.incidents_today", down ? "3" : "2", ""]];
  $("#stats").replaceChildren(...stats.map(([k, v, unit]) => el("div", { class: "stat" },
    el("div", { class: "k" }, t(k)), el("div", { class: "v" }, v, el("small", {}, unit)))));
  renderChart(down ? s : null);
}

function renderChart(outage) {
  const svg = $("#chart");
  const n = 90, router = [], internet = [];
  let seed = 7;
  const rand = () => (seed = (seed * 9301 + 49297) % 233280) / 233280;
  for (let i = 0; i < n; i++) {
    const cut = outage && i > n - 12;
    router.push(cut && outage === "router_down" ? null : 2 + rand() * 3 + (i % 23 === 0 ? 12 : 0));
    internet.push(cut ? null : 34 + rand() * 10 + (i % 17 === 0 ? 30 : 0));
  }
  const y = v => 115 - Math.min(v, 100) * 1.05;
  const path = values => values.map((v, i) => (v == null ? "" : `${i && values[i - 1] != null ? "L" : "M"}${(i * 600 / (n - 1)).toFixed(1)},${y(v).toFixed(1)}`)).join("");
  svg.innerHTML = `
    <g stroke="var(--separator)" stroke-width="1">${[25, 50, 75, 100].map(v => `<line x1="0" x2="600" y1="${y(v)}" y2="${y(v)}"/>`).join("")}</g>
    ${outage ? `<rect x="${600 * (n - 12) / (n - 1)}" y="0" width="${600 * 12 / (n - 1)}" height="120" fill="var(--bad-soft)"/>` : ""}
    <path d="${path(internet)}" fill="none" stroke="var(--chart-internet)" stroke-width="1.6" vector-effect="non-scaling-stroke"/>
    <path d="${path(router)}" fill="none" stroke="var(--chart-router)" stroke-width="1.6" vector-effect="non-scaling-stroke"/>`;
}

function eventRow(e) {
  const tile = { bad: "bad", warn: "warn", info: e.group === "watchdog" ? "accent" : "" }[e.level] || "";
  const iconName = e.kind === "dns_changed" ? "globe" : { outages: "wifi-off", watchdog: "shield", changes: "sliders" }[e.group];
  const secondary = [timeOf(e.at)];
  if (e.duration) secondary.push(t("ui.log.lasted", { duration: duration(e.duration) }));
  return el("div", { class: "row" }, el("div", { class: "icon-tile " + tile }, icon(iconName)),
    el("div", { class: "main" }, el("div", { class: "label" }, render(e.message)), el("div", { class: "secondary" }, secondary.join(" · "))));
}

const IMPACT_PILL = { better: "ok", worse: "bad", mixed: "warn", no_change: "", collecting: "", no_baseline: "" };

function effectBox(impact) {
  if (!impact) return null;
  const metrics = impact.details.filter(d => d.key.startsWith("impact.metric."));
  const notes = impact.details.filter(d => !d.key.startsWith("impact.metric."));
  return el("div", { class: "effect" },
    el("div", { class: "head" }, el("span", { class: "pill " + IMPACT_PILL[impact.status] }, t("ui.impact.title")), render(impact.summary)),
    metrics.length ? el("ul", {}, metrics.map(d => el("li", {}, render(d)))) : null,
    notes.map(d => el("div", { class: "caveat" }, render(d))));
}

function suggestionRow(s, compact) {
  const title = s.kind === "tweak" ? t(`tweak.${s.id}.name`) : t(`manual.${s.id}.title`);
  const body = s.kind === "tweak" ? t(`tweak.${s.id}.note`) : t(`manual.${s.id}.body`);
  const tile = el("div", { class: "icon-tile " + (s.doneAt ? "ok" : s.status) },
    icon(s.doneAt ? "check" : s.kind === "tweak" ? "sliders" : "wifi"));
  const main = el("div", { class: "main" }, el("div", { class: "label" }, title),
    compact ? null : el("div", { class: "secondary" }, body),
    el("div", { class: "reason" }, t("ui.suggest.because", { reason: render(s.reason) })),
    s.doneAt && !compact ? effectBox(s.impact) : null);
  let action;
  if (s.doneAt) action = el("span", { class: "time" }, t("ui.suggest.done_at", { time: timeOf(s.doneAt) }));
  else {
    action = el("button", { class: "button" + (s.kind === "tweak" ? " primary" : "") },
      t(s.kind === "tweak" ? "ui.suggest.turn_on" : "ui.suggest.done_button"));
    action.addEventListener("click", () => {
      s.doneAt = new Date();
      s.impact = { status: "collecting", summary: msg("impact.collecting", { hours: 0.0, needed: 2 }), details: [] };
      if (s.kind === "tweak") { const tw = TWEAKS.find(x => x.id === s.id); if (tw) tw.enabled = true; }
      renderOverview(); renderOptimize();
    });
  }
  return el("div", { class: "row" }, tile, main, el("div", { class: "actions-col" }, action));
}

function renderOverview() {
  renderCheck();
  renderValue();
  $("#recent").replaceChildren(...EVENTS.filter(e => e.group === "outages").slice(0, 3).map(eventRow));
  const actions = [["reconnect", "refresh"], ["flush_dns", "trash"], ["renew_dhcp", "network"], ["restart_adapter", "cpu"]];
  $("#quick-actions").replaceChildren(...actions.map(([a, i]) => {
    const b = el("button", { class: "button" }, icon(i), el("span", {}, t("ui.action." + a)));
    if (a === "restart_adapter") b.append(icon("lock"));
    b.addEventListener("click", () => fakeRun(b, a));
    return b;
  }));
}

// --- check → fix → result (ADR-0020) -------------------------------------------------------
// A problem is a warn/bad result. What can be done comes from the suggestions: "batch" marks the
// low-risk tweaks the Fix button covers; other tweaks keep their own action on the Optimize page.
const PROBLEM_ACTIONS = {
  driver: { kind: "you", step: "driver_update" },
  drops: { kind: "app", tweak: "power_pcie_aspm_off", batch: true },
  signal: { kind: "you", step: "move_closer" },
  dns: { kind: "app", tweak: "dns_fastest" },
};
// Low-risk tweaks check #10 lists as not on yet: offered in the sheet, never counted as problems.
const ALSO_RECOMMENDED = ["wifi_wake_magic", "wifi_wake_pattern", "device_power_off", "power_wireless_max"];
const DROPS_BEFORE = 36;
const FLOW = { phase: "idle", done: 0, ranAt: null, selected: new Set(), fixed: [], fixDone: 0, stepsDone: {},
  scenario: "problems", result: "pending", value: "data" };
let flowTimer = null;

const RANK = { bad: 0, warn: 1 };
const flowChecks = () => (FLOW.scenario === "clear" ? CHECKS.map(c => ({ ...c, status: "ok" })) : CHECKS);
const problems = () => flowChecks().filter(c => c.status in RANK).sort((a, b) => RANK[a.status] - RANK[b.status] || a.id - b.id);
const batchProblems = () => problems().filter(c => PROBLEM_ACTIONS[c.key]?.batch);
const tweakOf = id => TWEAKS.find(x => x.id === id) || { id };

function goTo(screen) { document.querySelector(`.nav-item[data-screen="${screen}"]`).click(); }

function button(label, onClick, cls = "button") {
  const b = el("button", { class: cls }, label);
  if (onClick) b.addEventListener("click", onClick);
  return b;
}

function checkHead(tile, iconName, title, body, buttons = [], spin = false) {
  return el("div", { class: "check-head" },
    el("div", { class: "big-tile " + tile }, icon(iconName, "icon" + (spin ? " spin" : ""))),
    el("div", { class: "main" }, el("h2", {}, title), body ? el("p", {}, body) : null),
    buttons.length ? el("div", { class: "buttons" }, buttons) : null);
}

function startCheck() {
  clearTimeout(flowTimer);
  Object.assign(FLOW, { phase: "running", done: 0 });
  const delays = [260, 180, 420, 200, 380, 520, 160, 150, 170, 140, 240, 190, 300, 610, 340];
  const step = () => {
    FLOW.done += 1;
    if (FLOW.done >= CHECKS.length) {
      FLOW.ranAt = new Date();
      FLOW.phase = problems().length ? "found" : "clear";
    } else flowTimer = setTimeout(step, delays[FLOW.done % delays.length]);
    renderCheck();
  };
  flowTimer = setTimeout(step, delays[0]);
  renderCheck();
}

function checkList() {
  const list = flowChecks();
  return el("div", { class: "check-list" }, list.map((c, i) => {
    const state = i < FLOW.done ? c.status : i === FLOW.done ? "current" : "pending";
    const mark = state === "current" ? icon("refresh", "icon spin") : state === "pending" ? el("span", { class: "hollow" })
      : icon(STATUS_ICON[state], "icon s " + state);
    return el("div", { class: "item " + state }, mark, el("span", {}, t(`diag.${c.key}.title`)));
  }));
}

function problemRow(c) {
  const action = PROBLEM_ACTIONS[c.key] || { kind: "none" };
  const pill = el("span", { class: "pill " + { app: "accent", you: "", none: "" }[action.kind] }, t("ui.check.kind." + action.kind));
  const lines = [el("div", { class: "secondary" }, render(c.summary))];
  if (action.step) lines.push(el("div", { class: "secondary" }, t(`manual.${action.step}.body`)));
  if (action.kind === "none" && c.advice) lines.push(el("div", { class: "secondary" }, render(c.advice)));
  let side = null;
  if (action.step) {
    const doneAt = FLOW.stepsDone[action.step];
    side = doneAt ? el("span", { class: "time" }, t("ui.suggest.done_at", { time: timeOf(doneAt) }))
      : button(t("ui.suggest.done_button"), () => { FLOW.stepsDone[action.step] = new Date(); renderCheck(); });
  } else if (action.tweak && !action.batch) {
    side = button(t("ui.diag.open_tweak"), () => goTo("optimize"));
  }
  return el("div", { class: "row problem" },
    el("div", { class: "icon-tile " + c.status }, icon(STATUS_ICON[c.status])),
    el("div", { class: "main" }, el("div", { class: "label" }, t(`diag.${c.key}.title`), " ", pill), lines),
    side ? el("div", { class: "actions-col" }, side) : null);
}

function miniStats() {
  const stats = [["ui.overview.router", "2", " ms"], ["ui.overview.internet", "38", " ms"],
    ["ui.overview.loss", formatValue(0.6, ".1f"), "%"], ["ui.check.drops_24h", "0", ""]];
  return el("div", { class: "mini-stats" }, stats.map(([k, v, unit]) => el("div", { class: "stat" },
    el("div", { class: "k" }, t(k)), el("div", { class: "v" }, v, el("small", {}, unit)))));
}

function resultRow() {
  const variant = FLOW.result;
  const pillClass = { improved: "ok", pending: "accent", no_gain: "" }[variant];
  const loss = { improved: [0.58, 0.05], no_gain: [0.58, 0.61] }[variant];
  const body = [];
  if (variant === "no_gain") body.push(el("div", { class: "secondary" }, t("ui.check.result.no_gain_body")));
  if (loss) body.push(el("div", { class: "metric" }, t("impact.metric.router_loss", { before: loss[0], after: loss[1] })));
  body.push(el("div", { class: "secondary" }, t("ui.check.result.pending_body", { before: DROPS_BEFORE })));
  body.push(effectBox({ status: "collecting", summary: msg("impact.collecting", { hours: 0.0, needed: 2 }), details: [] }));
  body.push(el("div", { class: "chips" }, FLOW.fixed.map(id => el("span", { class: "pill ok" }, icon("check"), t(`tweak.${id}.name`)))));
  return el("div", { class: "row problem" },
    el("div", { class: "icon-tile " + (variant === "improved" ? "ok" : "accent") }, icon(variant === "improved" ? "check" : "gauge")),
    el("div", { class: "main" }, el("div", { class: "label" }, t("diag.drops.title"), " ",
      el("span", { class: "pill " + pillClass }, t("ui.check.result." + variant))), body),
    variant === "no_gain" ? el("div", { class: "actions-col" }, button(t("ui.check.undo"), undoFix)) : null);
}

function renderCheck() {
  const card = $("#check-card");
  const total = CHECKS.length;
  const p = problems(), fixable = batchProblems();
  const time = FLOW.ranAt ? timeOf(FLOW.ranAt) : "";
  const again = () => button(t("ui.check.again"), startCheck);
  let parts;
  switch (FLOW.phase) {
    case "running": {
      const current = flowChecks()[Math.min(FLOW.done, total - 1)];
      parts = [checkHead("accent", "refresh", t("ui.check.running", { done: FLOW.done, total }), t(`diag.${current.key}.title`), [], true),
        el("div", { class: "progress" }, el("i", { style: `width:${(100 * FLOW.done / total).toFixed(1)}%` })), checkList()];
      break;
    }
    case "found": {
      const ok = flowChecks().filter(c => c.status === "ok").length;
      const fix = button(t("ui.check.fix_some", { fixable: fixable.length, count: p.length }), openFixSheet, "button primary");
      if (!fixable.length) fix.disabled = true;
      parts = [checkHead(p.some(c => c.status === "bad") ? "bad" : "warn", "alert", t("ui.check.found_title", { count: p.length }),
        t("ui.check.found_body", { time, ok, total }), [again(), fix]),
        el("div", { class: "rows" }, p.map(problemRow))];
      break;
    }
    case "clear":
      parts = [checkHead("ok", "check", t("ui.check.clear_title"), t("ui.check.clear_body", { total, time }), [again()]), miniStats()];
      break;
    case "fixing":
    case "rechecking": {
      const fixing = FLOW.phase === "fixing";
      parts = [checkHead("accent", fixing ? "sliders" : "refresh",
        fixing ? t("ui.check.fixing", { done: Math.min(FLOW.fixDone + 1, FLOW.fixed.length), total: FLOW.fixed.length }) : t("ui.check.rechecking"),
        fixing ? null : t("diag.drops.title"), [], true),
        el("div", { class: "rows" }, FLOW.fixed.map((id, i) => {
          const state = i < FLOW.fixDone ? "ok" : i === FLOW.fixDone && fixing ? "current" : "pending";
          return el("div", { class: "row fix-row " + state },
            state === "current" ? icon("refresh", "icon spin") : state === "ok" ? icon("check", "icon s ok") : el("span", { class: "hollow" }),
            el("div", { class: "main" }, t(`tweak.${id}.name`)),
            state === "ok" ? el("span", { class: "pill ok" }, t("ui.check.fix.on")) : null);
        }))];
      break;
    }
    case "result": {
      const open = p.filter(c => !PROBLEM_ACTIONS[c.key]?.batch);
      parts = [checkHead({ improved: "ok", pending: "accent", no_gain: "info" }[FLOW.result], FLOW.result === "improved" ? "check" : "sliders",
        t("ui.check.result_title", { count: FLOW.fixed.length }), t("ui.diag.last_run", { time: timeOf(new Date()) }),
        [again(), button(t("ui.check.done"), () => { FLOW.phase = "idle"; renderCheck(); }, "button primary")]),
        el("div", { class: "rows" }, resultRow())];
      if (open.length) parts.push(el("div", { class: "sub-title" }, t("ui.check.still_open")), el("div", { class: "rows" }, open.map(problemRow)));
      break;
    }
    default:
      parts = [checkHead("accent", "gauge", t("ui.check.idle_title"), t("ui.check.idle_body", { count: total }),
        [button(t("ui.check.start"), startCheck, "button primary")])];
  }
  card.replaceChildren(...parts);
}

function renderFixSheet() {
  const item = (id, reason) => {
    const box = el("input", { type: "checkbox" });
    box.checked = FLOW.selected.has(id);
    box.addEventListener("change", () => { box.checked ? FLOW.selected.add(id) : FLOW.selected.delete(id); renderFixSheet(); });
    return el("label", { class: "fix-item" }, box, el("div", {},
      el("div", { class: "name" }, t(`tweak.${id}.name`)), el("div", { class: "for" }, t("ui.check.sheet.for", { reason: t(reason) }))),
      tweakOf(id).disrupts ? el("span", { title: t("ui.optimize.disrupts") }, icon("wifi-off")) : null);
  };
  const fixes = batchProblems().map(c => item(PROBLEM_ACTIONS[c.key].tweak, `diag.${c.key}.title`));
  const also = ALSO_RECOMMENDED.filter(id => !tweakOf(id).enabled).map(id => item(id, "diag.tweaks.title"));
  $("#fix-title").textContent = t("ui.check.sheet.title", { count: FLOW.selected.size });
  $("#fix-list").replaceChildren(el("div", { class: "fix-group-title" }, t("ui.check.sheet.fixes")), ...fixes,
    also.length ? el("div", { class: "fix-group-title" }, t("ui.check.sheet.also")) : null, ...also);
  $("#fix-disrupts").hidden = ![...FLOW.selected].some(id => tweakOf(id).disrupts);
  $("#fix-confirm").disabled = FLOW.selected.size === 0;
}

function openFixSheet() {
  FLOW.selected = new Set([...batchProblems().map(c => PROBLEM_ACTIONS[c.key].tweak),
    ...ALSO_RECOMMENDED.filter(id => !tweakOf(id).enabled)]);
  renderFixSheet();
  $("#fix-scrim").classList.add("open");
}

function runFix() {
  $("#fix-scrim").classList.remove("open");
  // Tweaks that do not drop the network go first (ADR-0020 point 6).
  FLOW.fixed = [...FLOW.selected].sort((a, b) => !!tweakOf(a).disrupts - !!tweakOf(b).disrupts);
  Object.assign(FLOW, { phase: "fixing", fixDone: 0 });
  const step = () => {
    tweakOf(FLOW.fixed[FLOW.fixDone]).enabled = true;
    FLOW.fixDone += 1;
    if (FLOW.fixDone < FLOW.fixed.length) flowTimer = setTimeout(step, 650);
    else {
      FLOW.phase = "rechecking";
      flowTimer = setTimeout(() => { FLOW.phase = "result"; renderCheck(); }, 1400);
      renderOptimize();
    }
    renderCheck();
  };
  flowTimer = setTimeout(step, 650);
  renderCheck();
}

function undoFix() {
  FLOW.fixed.forEach(id => { tweakOf(id).enabled = false; });
  FLOW.fixed = [];
  FLOW.phase = "found";
  renderOptimize(); renderCheck();
}

function renderValue() {
  const card = $("#value-card");
  if (FLOW.value === "empty") {
    card.replaceChildren(el("div", { class: "row" }, el("div", { class: "icon-tile" }, icon("shield")),
      el("div", { class: "main" }, el("div", { class: "label" }, t("ui.value.empty", { count: 3 })))));
    return;
  }
  const toLog = filter => () => { goTo("log"); $(`#log-filter [data-filter="${filter}"]`).click(); };
  const row = (tile, iconName, label, secondary, filter) => el("div", { class: "row" },
    el("div", { class: "icon-tile " + tile }, icon(iconName)),
    el("div", { class: "main" }, el("div", { class: "label" }, label), el("div", { class: "secondary" }, secondary)),
    button(t("ui.value.open_log"), toLog(filter)));
  card.replaceChildren(
    row("accent", "shield", t("ui.value.recovered", { count: 4 }), t("ui.value.recovered_detail", { duration: duration(23) }), "watchdog"),
    row("ok", "sliders", t("ui.value.tweak_helped", { count: 1 }),
      `${t("tweak.wifi_power_saving.name")} · ${t("impact.metric.outages", { before: 12.0, after: 1.0 })}`, "changes"),
    row("ok", "check", t("ui.value.step_helped", { count: 1 }),
      `${t("manual.antenna.title")} · ${t("impact.metric.bad_link", { before: 0.23, after: 0.03 })}`, "changes"));
}

function fakeRun(button, action) {
  const label = button.querySelector("span");
  button.disabled = true;
  label.textContent = t("ui.action.running");
  setTimeout(() => { label.textContent = t("ui.action.done"); }, 900);
  setTimeout(() => { button.disabled = false; label.textContent = t("ui.action." + action); }, 1900);
}

function renderOptimize() {
  $("#manual-steps").replaceChildren(...SUGGESTIONS.filter(s => s.kind === "manual").map(s => suggestionRow(s, false)));
  const enabled = TWEAKS.filter(x => x.enabled).length;
  $("#tweak-count").textContent = t("ui.optimize.enabled_count", { enabled, total: TWEAKS.length });
  const groups = ["wifi_card", "power", "stack"];
  $("#tweak-groups").replaceChildren(...groups.flatMap(g => [
    el("div", { class: "section-title" }, t("tweak.group." + g)),
    el("div", { class: "group" }, TWEAKS.filter(x => x.group === g).map(tweakRow)),
  ]));
}

function tweakRow(tw) {
  const pills = [el("span", { class: "pill " + { low: "ok", medium: "warn", experimental: "bad" }[tw.risk] }, t("ui.risk." + tw.risk))];
  if (tw.supported) pills.push(el("span", { class: "pill" }, icon("lock"), t("ui.optimize.needs_admin")));
  if (tw.disrupts) pills.push(el("span", { class: "pill" }, t("ui.optimize.disrupts")));
  if (tw.reboot) pills.push(el("span", { class: "pill warn" }, t("ui.optimize.needs_reboot")));
  const note = !tw.supported ? t("ui.optimize.not_supported") + " — " + render(tw.reason) : tw.hasNote ? t(`tweak.${tw.id}.note`) : "";
  const sw = el("button", { class: "switch", role: "switch", "aria-checked": String(tw.enabled) });
  if (!tw.supported) sw.disabled = true;
  sw.addEventListener("click", () => toggleTweak(tw, sw));
  return el("div", { class: "row" + (tw.supported ? "" : " disabled") },
    el("div", { class: "main" }, el("div", { class: "label" }, t(`tweak.${tw.id}.name`)),
      note ? el("div", { class: "secondary" }, note) : null, el("div", { class: "meta" }, pills),
      tw.enabled ? effectBox(IMPACT[tw.id]) : null), sw);
}

function toggleTweak(tw, sw) {
  const apply = () => {
    sw.classList.add("busy");
    sw.setAttribute("aria-label", t("ui.optimize.applying"));
    setTimeout(() => { tw.enabled = !tw.enabled; sw.classList.remove("busy"); renderOptimize(); }, 700);
  };
  if (!tw.enabled && tw.risk === "experimental") {
    $("#sheet-body").textContent = t("ui.sheet.experimental.body", { name: t(`tweak.${tw.id}.name`) });
    $("#scrim").classList.add("open");
    $("#sheet-confirm").onclick = () => { $("#scrim").classList.remove("open"); apply(); };
    $("#sheet-cancel").onclick = () => $("#scrim").classList.remove("open");
  } else apply();
}

function renderDiagnostics() {
  const count = s => CHECKS.filter(c => c.status === s).length;
  $("#diag-badge").textContent = count("bad") + count("warn");
  $("#diag-summary").replaceChildren(
    el("span", { class: "pill bad" }, t("ui.diag.count.bad", { count: count("bad") })),
    el("span", { class: "pill warn" }, t("ui.diag.count.warn", { count: count("warn") })),
    el("span", { class: "pill ok" }, t("ui.diag.count.ok", { count: count("ok") })),
    el("span", { class: "time", style: "margin-left:auto" }, t("ui.diag.last_run", { time: timeOf(ago(12)) })));
  $("#checks").replaceChildren(...CHECKS.map(c => {
    const row = el("div", { class: "row clickable check" },
      el("div", { class: "icon-tile " + c.status }, icon(STATUS_ICON[c.status])),
      el("div", { class: "main" },
        el("div", { class: "label" }, t(`diag.${c.key}.title`)),
        el("div", { class: "secondary" }, render(c.summary)),
        el("ul", { class: "details" }, c.details.map(d => el("li", {}, render(d)))),
        c.advice ? el("div", { class: "advice" }, el("b", {}, t("ui.diag.advice")), render(c.advice),
          c.tweak ? el("div", { style: "margin-top:6px" }, el("button", { class: "button" }, t("ui.diag.open_tweak"))) : null) : null),
      el("span", { class: "pill " + c.status }, t("ui.status." + c.status)),
      icon("chevron", "icon chevron"));
    row.addEventListener("click", ev => { if (!ev.target.closest(".button")) row.classList.toggle("open"); });
    return row;
  }));
  $("#checks .check").classList.add("open");
}

let logFilter = "all";
function renderLog() {
  const items = EVENTS.filter(e => logFilter === "all" || e.group === logFilter);
  const days = [["ui.log.today", e => e.at.getDate() === NOW.getDate()], ["ui.log.yesterday", e => e.at.getDate() !== NOW.getDate()]];
  const blocks = days.map(([label, test]) => [label, items.filter(test)]).filter(([, list]) => list.length);
  $("#log-days").replaceChildren(...(blocks.length ? blocks.flatMap(([label, list]) => [
    el("div", { class: "section-title" }, t(label)), el("div", { class: "group" }, list.map(eventRow))])
    : [el("p", { class: "subtitle", style: "margin-top:var(--space-6);text-align:center" }, t("ui.log.empty"))]));
}

function renderLanguages() {
  const select = $("#language");
  const codes = Object.keys(LOCALES).sort((a, b) => (a !== "en") - (b !== "en") ||
    (LOCALES[a]._meta?.english_name || a).localeCompare(LOCALES[b]._meta?.english_name || b));
  select.replaceChildren(el("option", { value: "auto" }, t("ui.language.auto")),
    ...codes.map(code => el("option", { value: code }, LOCALES[code]._meta?.name || code)));
  select.value = lang;
}

function renderAll() {
  renderStatic(); renderLanguages(); renderState(); renderOverview(); renderOptimize(); renderDiagnostics(); renderLog();
}

// --- wiring -----------------------------------------------------------------------------------

document.querySelectorAll(".nav-item").forEach(item => item.addEventListener("click", () => {
  document.querySelectorAll(".nav-item").forEach(n => n.removeAttribute("aria-current"));
  item.setAttribute("aria-current", "page");
  document.querySelectorAll(".screen").forEach(s => s.classList.toggle("active", s.id === "screen-" + item.dataset.screen));
  $("#content").scrollTop = 0;
}));
document.querySelectorAll(".switch:not([data-tweak])").forEach(sw => sw.addEventListener("click", () => {
  if (!sw.closest("#tweak-groups")) sw.setAttribute("aria-checked", String(sw.getAttribute("aria-checked") !== "true"));
}));
$("#language").addEventListener("change", e => {
  const wanted = e.target.value === "auto" ? (navigator.language || "en") : e.target.value;
  lang = LOCALES[wanted] ? wanted : Object.keys(LOCALES).find(c => base(c) === base(wanted)) || "en";
  renderAll();
  if (e.target.value === "auto") $("#language").value = "auto";
});
$("#theme").addEventListener("click", e => {
  const b = e.target.closest("button");
  if (!b) return;
  $("#theme").querySelectorAll("button").forEach(x => x.setAttribute("aria-pressed", String(x === b)));
  if (b.dataset.themeValue === "system") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", b.dataset.themeValue);
});
$("#log-filter").addEventListener("click", e => {
  const b = e.target.closest("button");
  if (!b) return;
  logFilter = b.dataset.filter;
  $("#log-filter").querySelectorAll("button").forEach(x => x.setAttribute("aria-pressed", String(x === b)));
  renderLog();
});
$("#simulate").addEventListener("change", e => { STATE.simulate = e.target.value; renderState(); });
$("#tray-toggle").addEventListener("change", e => $("#tray").classList.toggle("open", e.target.checked));
$("#run-diag").addEventListener("click", () => {
  const b = $("#run-diag"), label = b.querySelector("span");
  b.disabled = true; label.textContent = t("ui.diag.running");
  setTimeout(() => { b.disabled = false; label.textContent = t("ui.diag.run"); }, 1500);
});

$("#mock-scenario").addEventListener("change", e => {
  FLOW.scenario = e.target.value;
  if (FLOW.phase === "found" || FLOW.phase === "clear") FLOW.phase = problems().length ? "found" : "clear";
  renderCheck();
});
$("#mock-result").addEventListener("change", e => { FLOW.result = e.target.value; renderCheck(); });
$("#mock-value").addEventListener("change", e => { FLOW.value = e.target.value; renderValue(); });
$("#fix-cancel").addEventListener("click", () => $("#fix-scrim").classList.remove("open"));
$("#fix-confirm").addEventListener("click", runFix);

const params = new URLSearchParams(location.search);
if (params.get("lang") && LOCALES[params.get("lang")]) lang = params.get("lang");
if (params.get("theme")) document.documentElement.setAttribute("data-theme", params.get("theme"));
for (const [key, select] of [["scenario", "#mock-scenario"], ["result", "#mock-result"], ["value", "#mock-value"]]) {
  if (params.get(key)) { FLOW[key] = params.get(key); $(select).value = params.get(key); }
}
// ?flow=found|clear|result opens the Overview card in that state, for review and screenshots.
if (["found", "clear", "result"].includes(params.get("flow"))) {
  FLOW.ranAt = ago(1);
  FLOW.phase = params.get("flow");
  if (FLOW.phase === "result") {
    FLOW.fixed = [...batchProblems().map(c => PROBLEM_ACTIONS[c.key].tweak), ...ALSO_RECOMMENDED];
    FLOW.fixed.forEach(id => { tweakOf(id).enabled = true; });
  }
}
renderAll();
if (params.get("screen")) document.querySelector(`.nav-item[data-screen="${params.get("screen")}"]`)?.click();

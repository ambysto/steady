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
  const open = SUGGESTIONS.filter(s => !s.doneAt).slice(0, 3);
  $("#suggestions").replaceChildren(...(open.length ? open.map(s => suggestionRow(s, true))
    : [el("div", { class: "row" }, el("div", { class: "main secondary" }, t("ui.suggest.none")))]));
  $("#recent").replaceChildren(...EVENTS.filter(e => e.group === "outages").slice(0, 3).map(eventRow));
  const actions = [["reconnect", "refresh"], ["flush_dns", "trash"], ["renew_dhcp", "network"], ["restart_adapter", "cpu"]];
  $("#quick-actions").replaceChildren(...actions.map(([a, i]) => {
    const b = el("button", { class: "button" }, icon(i), el("span", {}, t("ui.action." + a)));
    if (a === "restart_adapter") b.append(icon("lock"));
    b.addEventListener("click", () => fakeRun(b, a));
    return b;
  }));
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

const params = new URLSearchParams(location.search);
if (params.get("lang") && LOCALES[params.get("lang")]) lang = params.get("lang");
if (params.get("theme")) document.documentElement.setAttribute("data-theme", params.get("theme"));
renderAll();
if (params.get("screen")) document.querySelector(`.nav-item[data-screen="${params.get("screen")}"]`)?.click();

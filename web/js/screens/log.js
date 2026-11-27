// Log: events newest first, grouped by day, filtered by kind. Messages arrive rendered by the API.

import { get, post } from "../api.js";
import { $, confirmSheet, el, fill, icon, toast } from "../dom.js";
import { dayLabel, duration, number, t, timeOf } from "../i18n.js";

const GROUPS = {
  outages: ["router_down", "internet_down", "monitor_gap", "wifi_state", "roam", "gateway_change", "route_change"],
  slowdowns: ["slowdown"],
  watchdog: ["watchdog_action", "watchdog_dry_run", "watchdog_skip", "watchdog_recovered", "watchdog_ineffective",
             "watchdog_tripped", "watchdog_error", "failover_no_backup", "failover_skip", "failover_tripped",
             "failover_dry_run", "failover_available", "failover_failback_available", "failover_switched",
             "failover_restored", "failover_error"],
  changes: ["tweak_enabled", "tweak_disabled", "tweak_failed", "settings_changed", "user_action", "manual_step_done",
            "dns_changed", "dns_observed"],
};
const ICON = { outages: "wifi-off", slowdowns: "gauge", watchdog: "shield", changes: "sliders" };
const WITH_DURATION = new Set(["router_down", "internet_down", "monitor_gap", "slowdown"]);

let filter = "all";
let events = [];
let speed = null;   // GET /api/slowdowns: sampling state, slowdowns going on, the latest sample

/** Open the Log on one group (the Overview's value card links here). */
export function showGroup(group) {
  filter = group in GROUPS ? group : "all";
  location.hash = "#/log";
}

const root = () => $("#screen-log");
const groupOf = kind => Object.keys(GROUPS).find(g => GROUPS[g].includes(kind));

function row(e) {
  const group = groupOf(e.kind);
  const tile = { bad: "bad", warn: "warn" }[e.level] || (group === "watchdog" ? "accent" : "");
  const secondary = [timeOf(e.ts)];
  if (e.duration && WITH_DURATION.has(e.kind)) secondary.push(t("ui.log.lasted", { duration: duration(e.duration) }));
  return el("div", { class: "row" },
    el("div", { class: `icon-tile ${tile}` }, icon(e.kind.startsWith("dns_") ? "globe" : ICON[group] || "info")),
    el("div", { class: "main" }, el("div", { class: "label" }, e.message || e.kind),
      el("div", { class: "secondary" }, secondary.join(" · "))));
}

const isoDay = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

/** A day inside the period before the current week, month or quarter. */
function previousPeriodDay(kind) {
  const d = new Date();
  if (kind === "week") d.setDate(d.getDate() - 7);
  else if (kind === "month") d.setDate(0);
  else d.setMonth(Math.floor(d.getMonth() / 3) * 3, 0);
  return isoDay(d);
}

async function createReport() {
  const kind = el("select", { class: "select", "aria-label": t("ui.isp_report.kind") },
    ["month", "week", "quarter"].map(k => el("option", { value: k }, t(`ui.isp_report.kind.${k}`))));
  const which = el("select", { class: "select", "aria-label": t("ui.isp_report.which") },
    ["previous", "current"].map(w => el("option", { value: w }, t(`ui.isp_report.which.${w}`))));
  const provider = el("input", { class: "input", type: "text", maxlength: "120", placeholder: t("ui.isp_report.provider"),
                                 "aria-label": t("ui.isp_report.provider") });
  const address = el("input", { class: "input", type: "text", maxlength: "120", placeholder: t("ui.isp_report.public_ip"),
                                "aria-label": t("ui.isp_report.public_ip") });
  const form = el("div", { class: "form" }, el("div", { class: "form-row" }, kind, which), provider, address,
    el("div", { class: "secondary" }, t("ui.isp_report.privacy")));
  if (!await confirmSheet({ title: t("ui.isp_report.title"), body: t("ui.isp_report.sheet_body"), confirm: t("ui.isp_report.create"),
                            cancel: t("ui.sheet.cancel"), content: form, symbol: "list", tile: "accent" })) return;
  try {
    const result = await post("/api/report", {
      kind: kind.value, date: which.value === "previous" ? previousPeriodDay(kind.value) : undefined,
      provider: provider.value, public_ip: address.value, open: true });
    toast(t(result.opened ? "ui.isp_report.opened" : "ui.isp_report.saved", { folder: result.folder }), { ms: 9000 });
  } catch (err) {
    toast(t("ui.error.failed", { message: err.message }), { bad: true });
  }
}

function speedRows() {
  if (!speed) return null;
  const cfg = speed.settings || {};
  const rows = [];
  const last = speed.last_sample;
  const lastText = last && (last.down_mbps !== null || last.up_mbps !== null)
    ? t("ui.speed.last", { down: number(last.down_mbps, 0), up: number(last.up_mbps, 0), time: timeOf(last.ts) })
    : t("ui.speed.no_sample");
  rows.push(el("div", { class: "row" }, el("div", { class: "icon-tile accent" }, icon("gauge")),
    el("div", { class: "main" },
      el("div", { class: "label" }, cfg.enabled ? t("ui.speed.on", { minutes: cfg.interval_min }) : t("ui.speed.off")),
      el("div", { class: "secondary" }, cfg.enabled ? lastText : t("ui.speed.off_hint"))),
    cfg.enabled ? null : el("button", { class: "button", onclick: () => { location.hash = "#/settings"; } }, t("ui.speed.turn_on"))));
  for (const item of speed.active) {
    rows.push(el("div", { class: "row" }, el("div", { class: "icon-tile bad" }, icon("alert")),
      el("div", { class: "main" },
        el("div", { class: "label" }, t("ui.speed.active", { direction: t(`ui.speed.direction.${item.direction}`),
          avg: number(item.avg_mbps, 1), baseline: number(item.baseline_mbps, 0) })),
        el("div", { class: "secondary" }, t("ui.speed.since", { time: timeOf(item.start_ts) })))));
  }
  rows.push(el("div", { class: "row" }, el("div", { class: "icon-tile" }, icon("list")),
    el("div", { class: "main" }, el("div", { class: "label" }, t("ui.isp_report.title")),
      el("div", { class: "secondary" }, t("ui.isp_report.hint"))),
    el("button", { class: "button primary", onclick: createReport }, t("ui.isp_report.create"))));
  return [el("div", { class: "section-title" }, t("ui.speed.title")), el("div", { class: "group" }, rows)];
}

function draw() {
  const seg = el("div", { class: "segmented" }, ["all", "outages", "slowdowns", "watchdog", "changes"].map(f =>
    el("button", { "aria-pressed": String(f === filter), onclick: () => { filter = f; draw(); } }, t(`ui.log.filter.${f}`))));
  const shown = events.filter(e => filter === "all" || GROUPS[filter].includes(e.kind));
  const days = [];
  for (const e of shown) {
    const label = dayLabel(e.ts);
    if (!days.length || days[days.length - 1].label !== label) days.push({ label, items: [] });
    days[days.length - 1].items.push(e);
  }
  fill(root(), 
    el("header", { class: "content-header" }, el("h1", {}, t("ui.nav.log")), el("div", { class: "actions" }, seg)),
    filter === "all" || filter === "slowdowns" ? speedRows() : null,
    days.length ? days.flatMap(d => [el("div", { class: "section-title" }, d.label), el("div", { class: "group" }, d.items.map(row))])
      : el("div", { class: "group empty" }, t("ui.log.empty")));
}

export async function render() {
  [events, speed] = await Promise.all([get("/api/events?limit=500").then(r => r.events),
                                       get("/api/slowdowns").catch(() => null)]);
  draw();
}

export const refresh = render;

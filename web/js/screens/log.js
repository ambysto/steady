// Log: events newest first, grouped by day, filtered by kind. Messages arrive rendered by the API.

import { get } from "../api.js";
import { $, el, fill, icon } from "../dom.js";
import { dayLabel, duration, t, timeOf } from "../i18n.js";

const GROUPS = {
  outages: ["router_down", "internet_down", "monitor_gap", "wifi_state", "roam", "gateway_change", "route_change"],
  watchdog: ["watchdog_action", "watchdog_dry_run", "watchdog_skip", "watchdog_recovered", "watchdog_ineffective",
             "watchdog_tripped", "watchdog_error", "failover_no_backup", "failover_skip", "failover_tripped",
             "failover_dry_run", "failover_available", "failover_failback_available", "failover_switched",
             "failover_restored", "failover_error"],
  changes: ["tweak_enabled", "tweak_disabled", "tweak_failed", "settings_changed", "user_action", "manual_step_done",
            "dns_changed", "dns_observed"],
};
const ICON = { outages: "wifi-off", watchdog: "shield", changes: "sliders" };
const WITH_DURATION = new Set(["router_down", "internet_down", "monitor_gap"]);

let filter = "all";
let events = [];

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

function draw() {
  const seg = el("div", { class: "segmented" }, ["all", "outages", "watchdog", "changes"].map(f =>
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
    days.length ? days.flatMap(d => [el("div", { class: "section-title" }, d.label), el("div", { class: "group" }, d.items.map(row))])
      : el("div", { class: "group empty" }, t("ui.log.empty")));
}

export async function render() {
  events = (await get("/api/events?limit=500")).events;
  draw();
}

export const refresh = render;

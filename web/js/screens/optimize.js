// Optimize: manual steps (from the latest diagnostics) and the tweak catalog with on/off switches.
// Turning a tweak on/off is a job; without Admin rights Windows asks through UAC (ADR-0005).

import { get } from "../api.js";
import { $, el, fill, icon, switchEl } from "../dom.js";
import { t, timeOf } from "../i18n.js";
import { effectBox, setTweak, suggestionRow } from "../widgets.js";

const RISK_PILL = { low: "ok", medium: "warn", experimental: "bad" };
let data = null;          // GET /api/tweaks
let suggestions = null;   // GET /api/suggestions
let busy = false;

const root = () => $("#screen-optimize");

async function load() {
  [data, suggestions] = await Promise.all([get("/api/tweaks"), get("/api/suggestions")]);
}

function tweakRow(ctx, st) {
  const pills = [el("span", { class: `pill ${RISK_PILL[st.risk] || ""}` }, t(`ui.risk.${st.risk}`))];
  if (st.needs_admin && st.supported) pills.push(el("span", { class: "pill" }, icon("lock"), t("ui.optimize.needs_admin")));
  if (st.disrupts_network) pills.push(el("span", { class: "pill" }, t("ui.optimize.disrupts")));
  if (st.needs_reboot) pills.push(el("span", { class: "pill warn" }, t("ui.optimize.needs_reboot")));
  const warning = st.supported && st.warning ? `${t("ui.optimize.warning")} — ${st.warning}` : "";
  // On only because the driver ships that way: the tool wrote nothing, so there is nothing to switch off.
  const note = !st.supported ? `${t("ui.optimize.not_supported")}${st.reason ? ` — ${st.reason}` : ""}`
    : st.on_by_default ? st.reason : warning || st.error || st.note;
  // A measured tweak (ADR-0016): the measurement behind the value, and whether it still fits this network.
  const m = st.enabled ? st.measurement : null;
  const measuredFrom = m && m.value ? t(`ui.optimize.measured.${st.id}`, {
    limit: m.value / 1e6, upload: m.upload_mbps, date: timeOf(m.measured_at, { withDate: true }) }) : null;
  for (const why of (m && st.stale) || []) pills.push(el("span", { class: "pill warn" }, t(`ui.optimize.stale.${why}`)));
  const sw = switchEl(st.enabled, {
    disabled: !st.supported || st.on_by_default || busy, label: st.name,
    onToggle: async node => {
      busy = true;
      root().querySelectorAll(".switch").forEach(s => { s.disabled = true; });
      const changed = await setTweak({ id: st.id, name: st.name, risk: st.risk, measured: st.measured }, !st.enabled,
                                     { button: node, isAdmin: ctx.state?.is_admin });
      busy = false;
      await reload(ctx, changed);
    },
  });
  return el("div", { class: `row${st.supported ? "" : " disabled"}` },
    el("div", { class: "main" }, el("div", { class: "label" }, st.name),
      note ? el("div", { class: "secondary" }, note) : null,
      measuredFrom ? el("div", { class: "secondary" }, measuredFrom) : null,
      m && st.stale?.length ? el("div", { class: "secondary" }, t("ui.optimize.stale.hint")) : null,
      el("div", { class: "meta" }, pills),
      st.enabled ? effectBox(data.impact?.[st.id]) : null),
    sw);
}

function draw(ctx) {
  const header = el("header", { class: "content-header" }, el("h1", {}, t("ui.nav.optimize")));
  if (!data?.states) {
    fill(root(), header, el("div", { class: "group loading" }, t("ui.optimize.loading")));
    return;
  }
  const states = data.states;
  header.append(el("div", { class: "actions" },
    el("span", { class: "pill" }, t("ui.optimize.enabled_count", { enabled: states.filter(s => s.enabled).length, total: states.length }))));
  const groups = [];
  for (const st of states) {
    let g = groups.find(x => x.name === st.group);
    if (!g) groups.push(g = { name: st.group, items: [] });
    g.items.push(st);
  }
  const manual = (suggestions?.items || []).filter(i => i.kind === "manual");
  fill(root(), header,
    el("p", { class: "subtitle" }, t("ui.optimize.subtitle")),
    manual.length ? [
      el("div", { class: "section-title" }, t("ui.suggest.manual_group")),
      el("p", { class: "subtitle hint" }, t("ui.suggest.manual_hint")),
      el("div", { class: "group" }, manual.map(item => suggestionRow(item, {
        isAdmin: ctx.state?.is_admin, onChange: () => reload(ctx, false) }))),
    ] : null,
    groups.flatMap(g => [el("div", { class: "section-title" }, g.name),
                         el("div", { class: "group" }, g.items.map(st => tweakRow(ctx, st)))]));
}

/** After a change the server starts re-reading every tweak (~10 s): poll until it is done. */
async function reload(ctx, waitForFresh) {
  const before = data?.refreshed_at;
  for (let i = 0; i < 40; i++) {
    await load();
    if (!data.refreshing && data.states && (!waitForFresh || data.refreshed_at !== before)) break;
    draw(ctx);
    await new Promise(r => setTimeout(r, 1500));
  }
  draw(ctx);
}

export async function render(ctx) {
  draw(ctx);
  await reload(ctx, false);
}

export async function refresh(ctx) {
  if (busy) return;
  await load();
  if (!busy) draw(ctx);
}

// Diagnostics: the latest saved run (or a new one, read-only), each check expandable with
// "What to do"; bufferbloat on demand after a confirmation (it moves real traffic).

import { get, runJob } from "../api.js";
import { $, confirmSheet, el, fill, icon, toast } from "../dom.js";
import { t, timeOf } from "../i18n.js";

const STATUS_ICON = { ok: "check", warn: "alert", bad: "x", info: "info" };
let run = null;            // { id, ts, results }
let bloat = null;          // latest bufferbloat result row
let running = false;

const root = () => $("#screen-diagnostics");

export async function loadBadge(ctx) {
  const runs = await get("/api/diagnostics/runs?limit=1");
  if (runs.runs.length) run = await get(`/api/diagnostics/runs/${runs.runs[0].id}`);
  updateBadge();
}

function updateBadge() {
  const badge = $("#diag-badge");
  const n = (run?.results || []).filter(r => r.status === "bad" || r.status === "warn").length;
  badge.textContent = n;
  badge.hidden = !n;
}

function checkRow(r, open) {
  const row = el("div", { class: `row clickable check${open ? " open" : ""}` },
    el("div", { class: `icon-tile ${r.status}` }, icon(STATUS_ICON[r.status] || "info")),
    el("div", { class: "main" },
      el("div", { class: "label" }, r.title),
      el("div", { class: "secondary" }, r.summary),
      el("ul", { class: "details" }, (r.details || []).map(d => el("li", {}, d))),
      r.advice ? el("div", { class: "advice" }, el("b", {}, t("ui.diag.advice")), r.advice,
        r.tweak ? el("div", { style: "margin-top:6px" }, el("a", { class: "button", href: "#/optimize" }, t("ui.diag.open_tweak"))) : null)
        : null),
    el("span", { class: `pill ${r.status}` }, t(`ui.status.${r.status}`)),
    icon("chevron", "icon chevron"));
  row.addEventListener("click", ev => { if (!ev.target.closest(".button")) row.classList.toggle("open"); });
  return row;
}

function draw() {
  const runButton = el("button", { class: "button primary", disabled: running }, icon("play"),
    el("span", {}, t(running ? "ui.diag.running" : "ui.diag.run")));
  runButton.addEventListener("click", runNow);
  const header = el("header", { class: "content-header" }, el("h1", {}, t("ui.nav.diagnostics")),
    el("div", { class: "actions" }, runButton));
  const results = run?.results || [];
  const count = s => results.filter(r => r.status === s).length;
  const firstProblem = results.findIndex(r => r.status === "bad" || r.status === "warn");
  const bloatButton = el("button", { class: "button", disabled: running }, icon("play"));
  bloatButton.addEventListener("click", runBufferbloat);
  fill(root(), header,
    run ? el("div", { class: "diag-summary" },
      el("span", { class: "pill bad" }, t("ui.diag.count.bad", { count: count("bad") })),
      el("span", { class: "pill warn" }, t("ui.diag.count.warn", { count: count("warn") })),
      el("span", { class: "pill ok" }, t("ui.diag.count.ok", { count: count("ok") })),
      el("span", { class: "time", style: "margin-left:auto" }, t("ui.diag.last_run", { time: timeOf(run.ts, { withDate: true }) })))
      : null,
    run ? el("div", { class: "group" }, results.map((r, i) => checkRow(r, i === firstProblem)))
      : el("div", { class: "group empty" }, t("ui.diag.never")),
    el("div", { class: "section-title" }, t("ui.diag.on_demand")),
    bloat ? el("div", { class: "group" }, checkRow(bloat, true))
      : el("div", { class: "group" }, el("div", { class: "row" }, el("div", { class: "icon-tile" }, icon("gauge")),
          el("div", { class: "main" }, el("div", { class: "label" }, t("diag.bufferbloat.title")),
            el("div", { class: "secondary" }, t("ui.diag.bufferbloat_run"))), bloatButton)));
}

async function runNow() {
  running = true;
  draw();
  try {
    const result = await runJob("/api/diagnostics", {}, { interval: 1000, timeout: 180000 });
    run = { id: result.run_id, ts: result.ts, results: result.results };
    updateBadge();
  } catch (err) {
    toast(t("ui.error.failed", { message: err.message }), { bad: true });
  } finally {
    running = false;
    draw();
  }
}

async function runBufferbloat() {
  const ok = await confirmSheet({ title: t("diag.bufferbloat.title"), body: t("ui.diag.bufferbloat_confirm"),
                                  confirm: t("ui.diag.measure"), cancel: t("ui.sheet.cancel") });
  if (!ok) return;
  running = true;
  draw();
  try {
    const result = await runJob("/api/diagnostics/bufferbloat", {}, { interval: 1000, timeout: 180000 });
    bloat = result.results[0] || null;
  } catch (err) {
    toast(t("ui.error.failed", { message: err.message }), { bad: true });
  } finally {
    running = false;
    draw();
  }
}

export async function render() {
  if (!running) await loadBadge();
  draw();
}

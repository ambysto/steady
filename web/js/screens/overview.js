// Overview: connection state, the connection check (ADR-0020), latency chart (live 15 min / 24 h /
// 7 days), numbers, recent incidents, quick actions, watchdog state.

import { get, runJob } from "../api.js";
import { latencyChart } from "../chart.js";
import { $, desktopOnly, el, fill, icon, svgEl, toast } from "../dom.js";
import { duration, number, t, timeOf } from "../i18n.js";
import { bucketLoss, historySeries, liveLatency, liveNumbers, liveTicks, lossVerdict, probesOk } from "../metrics.js";
import { failoverKey, failoverRows, suggestionAction } from "../widgets.js";
import { loadBadge } from "./diagnostics.js";
import { showGroup } from "./log.js";

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
let value = null;

async function loadSuggestions(ctx) {
  [suggestions, value] = await Promise.all([get("/api/suggestions"), get("/api/value").catch(() => null)]);
}

// --- what the app did (ADR-0020 point 8): counted from the log, a line only for what happened ---------

function valueRow(tile, iconName, label, secondary, group) {
  return el("div", { class: "row" }, el("div", { class: `icon-tile ${tile}` }, icon(iconName)),
    el("div", { class: "main" }, el("div", { class: "label" }, label),
      secondary.map(line => el("div", { class: "secondary" }, line))),
    el("button", { class: "button", onclick: () => showGroup(group) }, t("ui.value.open_log")));
}

/** One line per measured improvement: what it was, and the first number behind it. */
const helpedLines = list => list.map(x => (x.impact?.details?.length ? `${x.title} · ${x.impact.details[0]}` : x.title));

function valueCard(ctx) {
  if (!value) return null;
  const rows = [];
  if (value.recovered.count) {
    rows.push(valueRow("accent", "shield", t("ui.value.recovered", { count: value.recovered.count }),
      [t("ui.value.recovered_detail", { duration: duration(value.recovered.avg_s) })], "watchdog"));
  }
  if (value.tweaks_helped.length) {
    rows.push(valueRow("ok", "sliders", t("ui.value.tweak_helped", { count: value.tweaks_helped.length }),
      helpedLines(value.tweaks_helped), "changes"));
  }
  if (value.steps_helped.length) {
    rows.push(valueRow("ok", "check", t("ui.value.step_helped", { count: value.steps_helped.length }),
      helpedLines(value.steps_helped), "changes"));
  }
  if (value.failover_switches) {
    rows.push(valueRow("accent", "network", t("ui.value.failover", { count: value.failover_switches }), [], "watchdog"));
  }
  if (!rows.length) {
    const days = value.watching_since ? Math.floor((nowOf(ctx) - value.watching_since) / 86400) : 0;
    rows.push(el("div", { class: "row" }, el("div", { class: "icon-tile" }, icon("shield")),
      el("div", { class: "main" }, el("div", { class: "label" },
        days >= 1 ? t("ui.value.empty", { count: days }) : t("ui.value.empty_new")))));
  }
  return [el("div", { class: "section-title section-title-row" }, el("span", {}, t("ui.value.title")),
            el("span", { class: "time" }, t("ui.value.window"))),
          el("div", { class: "group" }, rows)];
}

// --- connection check (ADR-0020): one big round button, the real progress, what was found ---------

const STATUS_ICON = { ok: "check", warn: "alert", bad: "x", info: "info" };
const RING_R = 84;
const RING = 2 * Math.PI * RING_R;
const check = { running: false, progress: null };   // progress: the diagnostics job's, see app/server.py
const STALE_AFTER_S = 3 * 3600;                     // older than this, "Check again" is the big button

/** Six arcs around the disc (the button and the status share it). */
function segments() {
  const r = 84, gap = 7, arc = 2 * Math.PI * r / 6 - gap;
  const svg = svgEl("svg", { viewBox: "0 0 180 180", class: "segments", "aria-hidden": "true" });
  svg.append(svgEl("circle", { cx: 90, cy: 90, r, "stroke-dasharray": `${arc.toFixed(1)} ${gap}`, transform: "rotate(-75 90 90)" }));
  return svg;
}

/** The label, which turns into a symbol (a magnifier to check, sliders to fix) while the pointer is on it
 *  and the ring turns. */
function bigButton(label, onClick, glyph = "search") {
  return el("button", { class: "big-button", onclick: onClick, "aria-label": label }, segments(),
    el("span", { class: "disc" }, el("span", { class: "label" }, label), icon(glyph, "icon glyph")));
}

const statusCircle = (tile, iconName) => el("div", { class: `status-circle ${tile}` }, segments(),
  el("span", { class: "disc" }, icon(iconName)));

/** A thin ring and the share of checks finished, in percent of the real count. */
function progressRing(done, total) {
  const svg = svgEl("svg", { viewBox: "0 0 180 180", class: "ring" });
  svg.append(svgEl("circle", { class: "track", cx: 90, cy: 90, r: RING_R }),
             svgEl("circle", { class: "arc", cx: 90, cy: 90, r: RING_R, "stroke-dasharray": RING.toFixed(1),
                               "stroke-dashoffset": (RING * (1 - (total ? done / total : 0))).toFixed(1),
                               transform: "rotate(-90 90 90)" }));
  const label = total ? [String(Math.round(100 * done / total)), el("small", {}, "%")] : "…";
  return el("div", { class: "ring-wrap" }, svg, el("div", { class: "ring-label" }, label));
}

function checkHero({ circle, title, body, below, running = false }) {
  return el("div", { class: `check-hero${running ? " running" : ""}` }, circle, el("h2", {}, title), body ? el("p", {}, body) : null,
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
    const counted = (fix.phase === "rechecking" ? `${t("ui.check.rechecking")} · ` : "")
      + t("ui.check.running", { done: p.done, total: p.total });
    // Under the ring: what is being checked now, then how far along (the count the percent comes from).
    return card(checkHero({ circle: progressRing(p.done, p.total), title: p.title || counted, body: p.title ? counted : null,
                            running: true }),
      el("div", { class: "check-list" }, (p.finished || []).map(f => el("div", { class: `item ${f.status}` },
        icon(STATUS_ICON[f.status] || "info", `icon s ${f.status}`), el("span", {}, f.title))),
        p.key ? el("div", { class: "item current" }, icon("refresh", "icon spin"), el("span", {}, p.title)) : null));
  }
  if (fix.phase === "applying" || fix.phase === "undoing") return busyCard(ctx, card);
  if (fix.phase === "result" && fix.outcome) return resultCard(ctx, card);
  if (!suggestions) return el("div", { class: "group loading" }, "…");
  if (result.open && suggestions.check?.count) return resultView(ctx);
  const start = () => runCheck(ctx);
  const summary = suggestions.check;
  if (!suggestions.run || !summary) {
    return card(checkHero({ circle: bigButton(t("ui.check.start"), start), title: t("ui.check.idle_title"),
                            body: t("ui.check.idle_body", { count: suggestions.checks_total }) }));
  }
  const time = timeOf(suggestions.run.ts, { withDate: true });
  // An old result may no longer hold: checking again becomes the big button, the result stays below.
  const age = nowOf(ctx) - suggestions.run.ts;
  const stale = age > STALE_AFTER_S;
  const again = stale ? null : el("button", { class: "button", onclick: start }, t("ui.check.again"));
  const centre = fallback => (stale ? bigButton(t("ui.check.again"), start) : fallback);
  const staleNote = stale ? el("p", { class: "stale" }, t("ui.check.stale", { duration: duration(age) })) : null;
  if (!summary.count) {
    // Nothing to fix: a plain "all good", the numbers behind it, and where to go for more (never a push).
    const more = alsoIds(summary).length;
    const optimize = el("p", { class: "more" }, more ? t("ui.check.clear_more", { count: more }) : t("ui.check.clear_more_none"), " ",
      el("a", { class: "link", href: "#/optimize" }, t("ui.check.open_optimize")));
    return card(checkHero({ circle: centre(clearArt()), title: t("ui.check.clear_title"),
                            body: t("ui.check.clear_body", { total: summary.total, time }),
                            below: [optimize, again || staleNote] }),
      miniStats(ctx));
  }
  const worst = summary.problems.some(p => p.status === "bad") ? "bad" : "warn";
  // What the app can fix is the big button; an old result gives that place to checking again.
  const fixLabel = t("ui.check.fix_some", { fixable: summary.fixable, count: summary.count });
  const open = () => openResult(ctx);
  const circle = stale || !summary.fixable ? centre(statusCircle(worst, "alert")) : bigButton(fixLabel, open, "sliders");
  const details = el("button", { class: `button${stale || !summary.fixable ? " primary" : ""}`, onclick: open }, t("ui.check.see_details"));
  return card(checkHero({ circle, title: t("ui.check.found_title", { count: summary.count }),
                          body: t("ui.check.found_body", { time, ok: summary.ok, total: summary.total }),
                          below: again ? [details, again] : [staleNote, details] }));
}

/** A screen with a tick: what "nothing to fix" looks like (flat, in the accent colour). */
function clearArt() {
  const svg = svgEl("svg", { viewBox: "0 0 120 100", class: "clear-art", "aria-hidden": "true" });
  svg.append(svgEl("rect", { x: 8, y: 6, width: 104, height: 68, rx: 8 }),
             svgEl("path", { d: "M50 74v12M70 74v12M38 88h44" }),
             svgEl("path", { class: "tick", d: "M44 40l11 11 22-22" }));
  return svg;
}

// --- the result (ADR-0020): after a check that found problems, what to fix together, ticked, each ---------
// problem's detail, then Back or Fix. It takes the whole Overview until Back.

// focus: "summary", "also" or a problem's key; selected: the tweak ids the Fix button turns on.
const result = { open: false, focus: "summary", selected: new Set() };

/** Low-risk tweaks the run suggests for no counted problem (check #10 lists what is not on yet). */
const alsoIds = summary => {
  const fromProblems = new Set(summary.problems.flatMap(p => p.batch));
  return summary.also.filter(id => !fromProblems.has(id));
};

function openResult(ctx) {
  const summary = suggestions.check;
  Object.assign(result, { open: true, focus: "summary",
                          selected: new Set([...summary.problems.flatMap(p => p.batch), ...alsoIds(summary)]) });
  drawCheck(ctx);
  root().scrollIntoView?.({ block: "start" });
}

/** The result view for the latest stored run, when it found problems (the floating bar's "Fix N"). */
export async function showResult(ctx) {
  await loadSuggestions(ctx);
  if (suggestions?.check?.count && !check.running && !fix.phase) openResult(ctx);
  else drawCheck(ctx);
}

function closeResult(ctx) {
  result.open = false;
  drawCheck(ctx);
}

function resultView(ctx) {
  const summary = suggestions.check;
  const also = alsoIds(summary);
  const fixable = summary.problems.filter(p => p.batch.length);
  const others = summary.problems.filter(p => !p.batch.length);
  const selected = result.selected;
  const boxes = [];             // [checkbox, ids]: kept in step without redrawing (the details keep their scroll)
  const countNote = el("span", { class: "count" });
  const fixButton = el("button", { class: "button primary", onclick: () => { closeResult(ctx); runFix(ctx, [...selected]); } },
    t("ui.check.fix"));
  const disruptsNote = el("p", { class: "note" }, icon("wifi-off"), el("span", {}, t("ui.check.sheet.disrupts")));
  const sync = () => {
    for (const [box, ids] of boxes) {
      const on = ids.filter(id => selected.has(id)).length;
      box.checked = on === ids.length;
      box.indeterminate = on > 0 && on < ids.length;
    }
    countNote.textContent = t("ui.check.selected", { count: selected.size });
    fixButton.disabled = !selected.size;
    disruptsNote.hidden = ![...selected].some(id => tweakItem(id).disrupts);
  };
  const tick = ids => {
    const box = el("input", { type: "checkbox", "aria-label": t("ui.check.fix") });
    box.addEventListener("click", e => e.stopPropagation());
    box.addEventListener("change", () => {
      for (const id of ids) box.checked ? selected.add(id) : selected.delete(id);
      sync();
    });
    boxes.push([box, ids]);
    return box;
  };
  const show = focus => { result.focus = focus; drawCheck(ctx); };
  const sideItem = (focus, lead, label, trail) => el("button", {
    class: "side-item", "aria-current": result.focus === focus ? "true" : null, onclick: () => show(focus) },
    lead, el("span", { class: "name" }, label), trail);
  const statusMark = p => icon(STATUS_ICON[p.status], `icon s ${p.status}`);

  const side = el("div", { class: "result-side" },
    sideItem("summary", icon("list"), t("ui.check.summary")),
    fixable.length || also.length ? el("div", { class: "side-group" }, t("ui.check.sheet.fixes")) : null,
    fixable.map(p => sideItem(p.key, tick(p.batch), p.title, statusMark(p))),
    also.length ? sideItem("also", tick(also), t("ui.check.sheet.also"), el("span", { class: "pill" }, also.length)) : null,
    others.length ? el("div", { class: "side-group" }, t("ui.check.group.other")) : null,
    others.map(p => sideItem(p.key, statusMark(p), p.title, null)));

  const problem = summary.problems.find(p => p.key === result.focus);
  const main = problem ? problemDetail(problem, ctx, tick)
    : result.focus === "also" ? alsoDetail(also, tick)
    : summaryDetail(summary, fixable, also, others, show);

  const foot = el("div", { class: "result-foot" },
    el("div", { class: "notes" }, disruptsNote,
      ctx.state?.is_admin ? null : el("p", { class: "note" }, icon("lock"), el("span", {}, t("ui.check.sheet.admin")))),
    el("div", { class: "buttons" }, countNote,
      el("button", { class: "button", onclick: () => closeResult(ctx) }, t("ui.check.back")), fixButton));
  sync();
  return el("div", { class: "group result-view" }, side, el("div", { class: "result-main" }, main, foot));
}

/** One card per finding: what was measured, and what happens to it. A card opens the finding. */
function summaryDetail(summary, fixable, also, others, show) {
  const names = ids => ids.map(id => tweakItem(id).title).join(" · ");
  const findingCard = (p, line) => el("button", { class: "finding", onclick: () => show(p.key) },
    el("div", { class: `icon-tile ${p.status}` }, icon(STATUS_ICON[p.status])),
    el("div", { class: "main" }, el("div", { class: "big" }, p.summary), el("div", { class: "secondary" }, p.title), line),
    icon("chevron", "icon chevron"));
  const time = timeOf(suggestions.run.ts, { withDate: true });
  return el("div", { class: "result-scroll" },
    el("h2", {}, t("ui.check.found_title", { count: summary.count })),
    el("p", { class: "lead" }, t("ui.check.found_body", { time, ok: summary.ok, total: summary.total })),
    fixable.map(p => findingCard(p, el("div", { class: "turns-on" }, t("ui.check.turns_on", { names: names(p.batch) })))),
    also.length ? el("button", { class: "finding", onclick: () => show("also") },
      el("div", { class: "icon-tile accent" }, icon("sliders")),
      el("div", { class: "main" }, el("div", { class: "big" }, t("ui.check.also_count", { count: also.length })),
        el("div", { class: "secondary" }, names(also))),
      icon("chevron", "icon chevron")) : null,
    others.map(p => findingCard(p, el("div", { class: "kind" }, t(`ui.check.kind.${p.kind}`)))));
}

/** A tweak the Fix button can turn on, with its own tick; `reason` says what it is for. */
function fixRow(id, tick, reason = null) {
  const item = tweakItem(id);
  return el("label", { class: "fix-item" }, tick([id]),
    el("div", {}, el("div", { class: "name" }, item.title), reason ? el("div", { class: "for" }, reason) : null),
    item.disrupts ? el("span", { title: t("ui.optimize.disrupts") }, icon("wifi-off")) : null);
}

function problemDetail(problem, ctx, tick) {
  const onChange = () => loadSuggestions(ctx).then(() => drawCheck(ctx));
  // Suggestions the Fix button does not take (manual steps, tweaks that need their own confirmation).
  const rest = problem.actions.filter(a => !problem.batch.includes(a.id));
  return el("div", { class: "result-scroll" },
    el("div", { class: "detail-head" }, el("div", { class: `icon-tile ${problem.status}` }, icon(STATUS_ICON[problem.status])),
      el("div", {}, el("h2", {}, problem.title),
        el("span", { class: `pill ${problem.kind === "app" ? "accent" : ""}` }, t(`ui.check.kind.${problem.kind}`)))),
    el("p", { class: "big" }, problem.summary),
    problem.advice ? el("div", { class: "advice" }, el("b", {}, t("ui.diag.advice")), problem.advice) : null,
    problem.batch.length ? [el("div", { class: "side-group" }, t("ui.check.fix_together")),
                            el("div", { class: "fix-list" }, problem.batch.map(id => fixRow(id, tick)))] : null,
    rest.length ? el("div", { class: "problem-actions" }, rest.map(a => el("div", { class: "problem-action" },
      el("div", { class: "main" }, el("div", { class: "label" }, a.title),
        a.kind === "manual" && a.body ? el("div", { class: "secondary" }, a.body) : null),
      suggestionAction(a, { onChange, isAdmin: ctx.state?.is_admin })))) : null);
}

function alsoDetail(also, tick) {
  return el("div", { class: "result-scroll" },
    el("div", { class: "detail-head" }, el("div", { class: "icon-tile accent" }, icon("sliders")),
      el("div", {}, el("h2", {}, t("ui.check.also_count", { count: also.length })),
        el("span", { class: "pill accent" }, t("ui.check.kind.app")))),
    el("p", { class: "lead" }, t("ui.check.also_body")),
    el("div", { class: "fix-list" }, also.map(id => {
      const reason = tweakItem(id).reason?.title;
      return fixRow(id, tick, reason ? t("ui.check.sheet.for", { reason }) : null);
    })));
}

// --- Fix (ADR-0020 points 5-7): the low-risk tweaks together, one UAC prompt, then check again ----------

const RANK = { ok: 0, info: 0, warn: 1, bad: 2 };
// phase: null, "applying", "rechecking", "result" or "undoing"; before/after: the check summaries around it.
const fix = { phase: null, ids: [], before: null, after: null, outcome: null };

const tweakItem = id => (suggestions?.items || []).find(i => i.kind === "tweak" && i.id === id) || { id, title: id };

async function runFix(ctx, ids) {
  Object.assign(fix, { phase: "applying", ids, before: suggestions.check, after: null, outcome: null });
  drawCheck(ctx);
  try {
    const result = await runJob("/api/tweaks/batch", { ids, enable: true });
    if (!result.results?.length) {             // UAC declined, or no answer came back: nothing changed here
      toast(result.message || t("ui.error.failed", { message: "" }), { bad: !result.ok });
      fix.phase = null;
      return;
    }
    fix.outcome = result;
    toast(result.message, { bad: !result.ok });
    fix.phase = "rechecking";
    const checked = await runCheck(ctx);
    // Without a new run there is nothing honest to compare: show the plain card, which still lists the changes.
    fix.after = checked ? suggestions.check : null;
    fix.phase = fix.after ? "result" : null;
  } catch (err) {
    toast(t("ui.error.failed", { message: err.message }), { bad: true });
    fix.phase = null;
  } finally {
    drawCheck(ctx);
  }
}

async function undoFix(ctx, ids) {
  fix.phase = "undoing";
  fix.ids = ids;
  drawCheck(ctx);
  try {
    const result = await runJob("/api/tweaks/batch", { ids, enable: false });
    toast(result.message || "", { bad: !result.ok });
    await loadSuggestions(ctx);
  } catch (err) {
    toast(t("ui.error.failed", { message: err.message }), { bad: true });
  } finally {
    fix.phase = null;
    drawCheck(ctx);
  }
}

/** Turning tweaks on or off: the ring turns while Windows (and the helper) do it. */
function busyCard(ctx, card) {
  const key = fix.phase === "undoing" ? "ui.check.undoing" : "ui.check.applying";
  return card(checkHero({ circle: el("div", { class: "status-circle accent busy" }, segments(), el("span", { class: "disc" }, icon("sliders"))),
                          title: t(key, { count: fix.ids.length }),
                          body: ctx.state?.is_admin ? null : t("ui.optimize.waiting_uac"), running: true }),
    el("div", { class: "check-list" }, fix.ids.map(id => el("div", { class: "item" }, icon("sliders", "icon"), el("span", {}, tweakItem(id).title)))));
}

const OUTCOME_PILL = { changed: ["ok", "ui.check.fix.on"], unchanged: ["", "tweak.result.already_on"],
                       unsupported: ["", "ui.check.fix.not_supported"], failed: ["bad", "ui.check.fix.failed"] };

/** After fixing: what was turned on, and for each problem it was for, how it looks now. "Better now" only
 *  when checking again shows it; a finding about past days says it is being measured; else, plainly, no change. */
function resultCard(ctx, card) {
  const results = fix.outcome.results;
  const changed = new Set(results.filter(r => r.status === "changed").map(r => r.id));
  const nameOf = id => results.find(r => r.id === id)?.name || tweakItem(id).title;
  const after = new Map((fix.after?.problems || []).map(p => [p.key, p]));
  const rows = fix.before.problems.filter(p => p.batch.some(id => changed.has(id))).map(p => {
    const now = after.get(p.key)?.status || "ok";
    const variant = RANK[now] < RANK[p.status] ? "improved" : p.history ? "pending" : "no_gain";
    const ids = p.batch.filter(id => changed.has(id));
    return { problem: p, now, variant, ids };
  });
  const shownKeys = new Set(rows.map(r => r.problem.key));
  const open = (fix.after?.problems || []).filter(p => !shownKeys.has(p.key));
  const improved = rows.some(r => r.variant === "improved");
  const done = el("button", { class: "button primary", onclick: () => { fix.phase = null; drawCheck(ctx); } }, t("ui.check.done"));
  return card(
    checkHero({ circle: statusCircle(improved ? "ok" : "accent", improved ? "check" : "sliders"),
                title: t("ui.check.result_title", { count: changed.size }), body: fix.outcome.message, below: done }),
    el("div", { class: "rows" },
      el("div", { class: "row outcome-list" }, el("div", { class: "main" }, results.map(r => {
        const [cls, key] = OUTCOME_PILL[r.status] || OUTCOME_PILL.failed;
        return el("div", { class: "outcome" }, el("span", { class: "name" }, r.name), el("span", { class: `pill ${cls}` }, t(key)),
          r.ok ? null : el("div", { class: "secondary" }, r.message));
      }))),
      rows.map(({ problem, now, variant, ids }) => el("div", { class: "row problem" },
        el("div", { class: `icon-tile ${variant === "improved" ? "ok" : variant === "pending" ? "accent" : "info"}` },
          icon(variant === "improved" ? "check" : "sliders")),
        el("div", { class: "main" },
          el("div", { class: "label" }, problem.title, " ",
            el("span", { class: `pill ${{ improved: "ok", pending: "accent", no_gain: "" }[variant]}` }, t(`ui.check.result.${variant}`))),
          el("div", { class: "metric" }, `${t(`ui.status.${problem.status}`)} → ${t(`ui.status.${now}`)}`),
          variant === "improved" ? null : el("div", { class: "secondary" }, t(variant === "pending" ? "ui.check.result.pending_body" : "ui.check.result.no_gain_body")),
          el("div", { class: "chips" }, ids.map(id => el("span", { class: "pill ok" }, icon("check"), nameOf(id))))),
        variant === "no_gain" ? el("div", { class: "actions-col" },
          el("button", { class: "button", onclick: () => undoFix(ctx, ids) }, t("ui.check.undo"))) : null))),
    open.length ? [el("div", { class: "sub-title" }, t("ui.check.still_open")), el("div", { class: "rows" }, open.map(p => problemRow(p, ctx)))] : null);
}

function drawCheck(ctx) {
  root().classList.toggle("result-open", result.open && Boolean(suggestions?.check?.count) && !check.running && !fix.phase);
  slots.check && fill(slots.check, checkCard(ctx));
  slots.value && fill(slots.value, valueCard(ctx));
}

/** Resolves true when a new run was saved and loaded. */
async function runCheck(ctx) {
  if (check.running) return false;
  Object.assign(check, { running: true, progress: null });
  drawCheck(ctx);
  let ok = false;
  try {
    await runJob("/api/diagnostics", {}, { interval: 300, timeout: 180000,
      onProgress: progress => { if (progress) { check.progress = progress; drawCheck(ctx); } } });
    await loadSuggestions(ctx);
    loadBadge().catch(() => {});        // the sidebar count and the Diagnostics page follow the new run
    ok = true;
    if (fix.phase !== "rechecking" && suggestions.check?.count) {
      check.running = false;
      openResult(ctx);                  // a fresh result opens on what was found
    }
  } catch (err) {
    toast(t("ui.error.failed", { message: err.message }), { bad: true });
  } finally {
    check.running = false;
    drawCheck(ctx);
  }
  return ok;
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
  for (const name of ["hero", "check", "value", "chart", "stats", "recent", "watchdog", "failover"]) slots[name] = el("div");
  slots.check.className = "check-slot";
  slots.stats.style.marginTop = "var(--space-3)";
  fill(root(), 
    el("header", { class: "content-header" }, el("h1", {}, t("ui.nav.overview")),
      el("div", { class: "actions" },
        desktopOnly(el("button", { class: "button", onclick: () => ctx.openMini() }, icon("pulse"), t("ui.tray.mini"))))),
    slots.hero,
    slots.check, slots.value,
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
    if (!result.open && !root().querySelector(".group button:disabled")) drawSlow(ctx);
  } else if (tick % 2 === 0) { // every ~4 s: which connection carries the traffic can change any moment
    const before = failoverKey(failover);
    await loadFailover();
    if (failoverKey(failover) !== before && !slots.failover?.querySelector("button:disabled")) drawFailover(ctx);
  }
  drawLive(ctx);
}

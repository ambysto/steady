// Pieces shared by several screens: measured effect, suggestion rows, turning a tweak on/off.

import { ApiError, post, runJob } from "./api.js";
import { confirmSheet, el, icon, toast } from "./dom.js";
import { t, timeOf } from "./i18n.js";

const IMPACT_PILL = { better: "ok", worse: "bad", mixed: "warn", no_change: "", collecting: "", no_baseline: "" };
const SEVERITY_TILE = { 3: "bad", 2: "warn", 1: "info", 0: "" };

/** Before/after result of a change (app/impact.py), already rendered by the API. */
export function effectBox(impact) {
  if (!impact) return null;
  const details = impact.details || [];
  const metrics = details.filter((_, i) => i < (impact.findings || []).length);
  const notes = details.slice(metrics.length);
  return el("div", { class: "effect" },
    el("div", { class: "head" }, el("span", { class: `pill ${IMPACT_PILL[impact.status] || ""}` }, t("ui.impact.title")),
       impact.summary),
    metrics.length ? el("ul", {}, metrics.map(d => el("li", {}, d))) : null,
    notes.map(d => el("div", { class: "caveat" }, d)));
}

/** One suggestion (GET /api/suggestions item). `compact` drops the long text (Overview card). */
export function suggestionRow(item, { compact = false, onChange, isAdmin }) {
  const tile = el("div", { class: `icon-tile ${item.done_at ? "ok" : SEVERITY_TILE[item.severity] || ""}` },
    icon(item.done_at ? "check" : item.kind === "tweak" ? "sliders" : "wifi"));
  const main = el("div", { class: "main" }, el("div", { class: "label" }, item.title),
    !compact && item.body ? el("div", { class: "secondary" }, item.body) : null,
    item.reason ? el("div", { class: "reason" }, t("ui.suggest.because", { reason: item.reason.summary })) : null,
    item.done_at && !compact ? effectBox(item.impact) : null);
  return el("div", { class: "row" }, tile, main, el("div", { class: "actions-col" }, suggestionAction(item, { onChange, isAdmin })));
}

/** What a suggestion offers: "Turn on" for a tweak, "I did this" for a manual step, or when it was done. */
export function suggestionAction(item, { onChange, isAdmin }) {
  if (item.done_at) return el("span", { class: "time" }, t("ui.suggest.done_at", { time: timeOf(item.done_at) }));
  if (item.kind === "tweak") {
    const action = el("button", { class: "button primary" }, t("ui.suggest.turn_on"));
    action.addEventListener("click", async () => {
      if (await setTweak({ id: item.id, name: item.title, risk: item.risk, measured: item.measured }, true,
                         { button: action, isAdmin })) onChange?.();
    });
    return action;
  }
  const action = el("button", { class: "button" }, t("ui.suggest.done_button"));
  action.addEventListener("click", async () => {
    action.disabled = true;
    try {
      await post(`/api/manual/${item.id}/done`);
      onChange?.();
    } catch (err) {
      action.disabled = false;
      toast(t("ui.error.failed", { message: err.message }), { bad: true });
    }
  });
  return action;
}

/**
 * Turn a tweak on or off through the API (a job; without Admin rights Windows shows a UAC
 * prompt). Experimental tweaks ask first, and so do measured ones (they generate traffic, ADR-0016).
 * Resolves true when something changed.
 */
export async function setTweak(tweak, enable, { button, isAdmin } = {}) {
  if (enable && tweak.measured) {
    const ok = await confirmSheet({ title: t("ui.sheet.measured.title"),
                                    body: t("ui.sheet.measured.body", { name: tweak.name }),
                                    confirm: t("ui.sheet.confirm"), cancel: t("ui.sheet.cancel") });
    if (!ok) return false;
  }
  if (enable && tweak.risk === "experimental") {
    const ok = await confirmSheet({ title: t("ui.sheet.experimental.title"),
                                    body: t("ui.sheet.experimental.body", { name: tweak.name }),
                                    confirm: t("ui.sheet.confirm"), cancel: t("ui.sheet.cancel") });
    if (!ok) return false;
  }
  const label = button?.textContent;
  if (button) {
    button.disabled = true;
    button.classList.add("busy");
    if (button.classList.contains("button")) {
      button.textContent = t(enable && tweak.measured ? "ui.optimize.measuring" : "ui.optimize.applying");
    }
  }
  if (enable && tweak.measured) toast(t("ui.optimize.measuring"), { ms: 15000 });
  else if (!isAdmin) toast(t("ui.optimize.waiting_uac"), { ms: 15000 });
  try {
    const result = await runJob(`/api/tweaks/${encodeURIComponent(tweak.id)}`, { enable });
    toast(result.message || "", { bad: !result.ok });
    return Boolean(result.ok && (result.changed ?? true));
  } catch (err) {
    toast(t("ui.error.failed", { message: err instanceof ApiError ? err.message : String(err) }), { bad: true });
    return false;
  } finally {
    if (button) {
      button.disabled = false;
      button.classList.remove("busy");
      if (button.classList.contains("button")) button.textContent = label;
    }
  }
}

/** Backup connections (GET /api/failover, ADR-0008): one row per path, buttons to switch now or back. */
/** What the backup-connection rows are drawn from; redraw only when this changes (no flicker,
 *  no button replaced under the pointer), but never show buttons from a state seconds old. */
export function failoverKey(state) {
  if (!state) return "";
  return JSON.stringify([state.primary, state.preferred, state.tripped,
                         (state.paths || []).map(p => [p.index, p.primary, p.preferred, p.healthy])]);
}

export function failoverRows(state, { onChange } = {}) {
  const paths = state?.paths || [];
  if (paths.length < 2 && state?.preferred == null) {
    return [el("div", { class: "row" }, el("div", { class: "main secondary" }, t("ui.failover.no_backup")))];
  }
  const run = async (button, path, body) => {
    button.disabled = true;
    if (!state.is_admin) toast(t("ui.optimize.waiting_uac"), { ms: 15000 });
    try {
      const result = await runJob(path, body);
      toast(result.message || "", { bad: !result.ok });
    } catch (err) {
      toast(t("ui.error.failed", { message: err.message }), { bad: true });
    } finally {
      button.disabled = false;
      onChange?.();
    }
  };
  const rows = paths.map(p => {
    const inUse = state.preferred != null ? p.preferred : p.primary;
    const pills = p.healthy == null ? []      // not measured: failover is off
      : [el("span", { class: `pill ${p.healthy ? "ok" : "bad"}` }, t(p.healthy ? "ui.failover.working" : "ui.failover.not_working"))];
    if (inUse) pills.unshift(el("span", { class: "pill accent" }, t("ui.failover.in_use")));
    let action = null;
    if (p.preferred) {
      action = el("button", { class: "button" }, t("ui.failover.switch_back"));
      action.addEventListener("click", () => run(action, "/api/failover/restore", {}));
    } else if (!inUse && p.healthy !== false && state.preferred == null) {
      action = el("button", { class: "button" }, t("ui.failover.switch_now"));
      action.addEventListener("click", () => run(action, `/api/failover/prefer/${p.index}`, {}));
    }
    return el("div", { class: "row" },
      el("div", { class: `icon-tile ${inUse ? "accent" : ""}` }, icon(p.kind === "wifi" ? "wifi" : "network")),
      el("div", { class: "main" }, el("div", { class: "label" }, p.name),
        el("div", { class: "secondary" }, `${t(`ui.failover.kind.${p.kind}`)} · ${p.gateway}`),
        el("div", { class: "meta" }, pills)),
      action);
  });
  if (state.preferred != null && !paths.some(p => p.preferred)) {
    // The backup we switched to is unplugged: its changed metric is still recorded, keep a way back.
    const back = el("button", { class: "button" }, t("ui.failover.switch_back"));
    back.addEventListener("click", () => run(back, "/api/failover/restore", {}));
    rows.push(el("div", { class: "row" },
      el("div", { class: "icon-tile" }, icon("network")),
      el("div", { class: "main" }, el("div", { class: "label" }, state.preferred_name || `#${state.preferred}`),
        el("div", { class: "secondary" }, t("ui.failover.disconnected"))),
      back));
  }
  return rows;
}

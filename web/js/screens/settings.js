// Settings: language, appearance, start with Windows (read-only), notifications, watchdog.
// Changes go through POST /api/settings, which validates every key (app/server.py).

import { get, post } from "../api.js";
import { $, el, fill, icon, switchEl, toast } from "../dom.js";
import { duration, language, t } from "../i18n.js";
import { failoverKey, failoverRows } from "../widgets.js";

const root = () => $("#screen-settings");
let autostart = null;
let failoverState = null;

function currentTheme() {
  try { return localStorage.getItem("theme") || "system"; } catch { return "system"; }
}

async function save(ctx, changes) {
  try {
    await post("/api/settings", changes);
    await ctx.refreshState();
    return true;
  } catch (err) {
    toast(t("ui.error.failed", { message: err.message }), { bad: true });
    return false;
  }
}

function row(tileClass, iconName, label, secondary, control) {
  return el("div", { class: "row" }, el("div", { class: `icon-tile ${tileClass}` }, icon(iconName)),
    el("div", { class: "main" }, el("div", { class: "label" }, label), secondary ? el("div", { class: "secondary" }, secondary) : null),
    control);
}

function settingSwitch(ctx, section, key, label) {
  const value = Boolean(ctx.state?.settings?.[section]?.[key]);
  return switchEl(value, { label, onToggle: async sw => {
    sw.disabled = true;
    if (await save(ctx, { [section]: { [key]: !value } })) draw(ctx);
    else sw.disabled = false;
  } });
}

function languageSelect(ctx) {
  const select = el("select", { class: "select", "aria-label": t("ui.language.label") },
    el("option", { value: "auto" }, t("ui.language.auto")),
    language.available.map(l => el("option", { value: l.code }, l.name)));
  select.value = language.setting;
  select.addEventListener("change", async () => {
    select.disabled = true;
    if (await save(ctx, { ui: { language: select.value } })) await ctx.rerenderAll();
    select.disabled = false;
  });
  return select;
}

function themeControl(ctx) {
  const theme = currentTheme();
  return el("div", { class: "segmented" }, ["system", "light", "dark"].map(value =>
    el("button", { "aria-pressed": String(value === theme), onclick: () => { ctx.setTheme(value); draw(ctx); } },
      t(`ui.settings.theme.${value}`))));
}

function draw(ctx) {
  const wd = ctx.state?.settings?.watchdog || {};
  const startup = autostart === null ? el("span", { class: "pill" }, "…")
    : el("span", { class: `pill ${autostart.installed ? "ok" : ""}` }, t(autostart.installed ? "ui.common.on" : "ui.common.off"));
  fill(root(), 
    el("header", { class: "content-header" }, el("h1", {}, t("ui.nav.settings"))),
    el("div", { class: "section-title" }, t("ui.settings.general")),
    el("div", { class: "group" },
      row("accent", "globe", t("ui.language.label"), t("ui.language.hint"), languageSelect(ctx)),
      row("", "sun", t("ui.settings.appearance"), null, themeControl(ctx)),
      row("", "power", t("ui.settings.startup"), t("ui.settings.startup_hint"), startup)),
    el("div", { class: "section-title" }, t("ui.settings.notifications")),
    el("div", { class: "group" },
      row("bad", "bell", t("ui.settings.notify_outages"),
        t("ui.settings.notify_after", { duration: duration(ctx.state?.notify_after_s ?? 30) }),
        settingSwitch(ctx, "notify", "enabled", t("ui.settings.notify_outages")))),
    el("div", { class: "section-title" }, t("ui.watchdog.title")),
    el("div", { class: "group" },
      row("accent", "shield", t("ui.settings.watchdog_enabled"),
        wd.tripped_at ? t("ui.watchdog.state.tripped") : t("ui.watchdog.description"),
        settingSwitch(ctx, "watchdog", "enabled", t("ui.settings.watchdog_enabled"))),
      row("", "flask", t("ui.settings.watchdog_dry_run"), null,
        settingSwitch(ctx, "watchdog", "dry_run", t("ui.settings.watchdog_dry_run")))),
    el("div", { class: "section-title" }, t("ui.failover.title")),
    el("div", { class: "group" },
      row("accent", "network", t("ui.failover.enabled"), ctx.state?.is_admin ? t("ui.failover.description")
        : `${t("ui.failover.description")} ${t("ui.failover.admin_hint")}`,
        settingSwitch(ctx, "failover", "enabled", t("ui.failover.enabled"))),
      row("", "flask", t("ui.failover.dry_run"), null, settingSwitch(ctx, "failover", "dry_run", t("ui.failover.dry_run"))),
      failoverRows(failoverState, { onChange: () => loadFailover(ctx) })));
}

async function loadFailover(ctx) {
  failoverState = await get("/api/failover").catch(() => null);
  draw(ctx);
}

// The backup-connection rows show which path is in use; a button drawn from an old state once
// made the user "switch" to the path already in use. Re-read every poll (4 s), redraw only on change.
export async function refresh(ctx) {
  if (document.querySelector(".screen.active button:disabled")) return;
  const next = await get("/api/failover").catch(() => null);
  if (failoverKey(next) === failoverKey(failoverState)) return;
  failoverState = next;
  draw(ctx);
}

export async function render(ctx) {
  await ctx.refreshState();
  draw(ctx);
  loadFailover(ctx);
  if (autostart === null) {
    autostart = await get("/api/autostart").catch(() => ({ installed: false }));
    draw(ctx);
  }
}

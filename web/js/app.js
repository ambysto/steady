// App shell: navigation (#/screen), shared state, polling, language and theme.
// Each screen module exports render(ctx) and, optionally, refresh(ctx) for periodic updates.

import { get, onReachability } from "./api.js";
import { $ } from "./dom.js";
import { loadLanguage, t, translateStatic } from "./i18n.js";
import * as overview from "./screens/overview.js";
import * as optimize from "./screens/optimize.js";
import * as diagnostics from "./screens/diagnostics.js";
import * as log from "./screens/log.js";
import * as settings from "./screens/settings.js";

const SCREENS = { overview, optimize, diagnostics, log, settings };
const POLL_MS = { overview: 2000, log: 15000, optimize: 15000, diagnostics: 30000, settings: 4000 };
const STATE_EVERY_MS = 10000;

const ctx = {
  state: null,          // GET /api/state
  live: null,           // GET /api/live (overview keeps it fresh)
  screen: null,
  rerenderAll,          // after a language change
  refreshState,
  go: name => { location.hash = `#/${name}`; },
};

function applyTheme() {
  let theme = "system";
  try { theme = localStorage.getItem("theme") || "system"; } catch { /* storage blocked: follow the system */ }
  if (theme === "system") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", theme);
}
ctx.setTheme = theme => {
  try { localStorage.setItem("theme", theme); } catch { /* ignore */ }
  applyTheme();
};

async function refreshState() {
  ctx.state = await get("/api/state");
  return ctx.state;
}

function updateConnection() {
  const outages = ctx.live?.outages || {};
  const level = outages.router || outages.internet ? "bad" : ctx.live ? "" : "info";
  const text = outages.router ? t("ui.state.router_down") : outages.internet ? t("ui.state.internet_down")
    : ctx.live ? t("ui.state.online") : "…";
  $("#sidebar-conn .dot").className = `dot ${level}`;
  $("#sidebar-conn-text").textContent = text;
}
ctx.updateConnection = updateConnection;

// Inside the desktop shell (app/desktop.py) the page can open the floating monitor; in a plain
// browser tab those controls ([data-desktop-only]) stay hidden.
ctx.openMini = () => window.pywebview?.api?.open_mini?.();
function syncDesktopOnly() {
  const available = Boolean(window.pywebview?.api?.open_mini);
  document.querySelectorAll("[data-desktop-only]").forEach(node => { node.hidden = !available; });
}
window.addEventListener("pywebviewready", syncDesktopOnly);

// "Fix N" on the floating bar (app/desktop.py open_check_result): show what the latest check found,
// with what Fix would turn on, on the Overview. Nothing changes until the user presses Fix there.
window.steadyApp = {
  async openResult() {
    if (ctx.screen !== "overview") {
      history.replaceState(null, "", "#/overview");
      await show("overview");
    }
    await overview.showResult(ctx);
  },
};

let pollTimer;
async function poll() {
  clearTimeout(pollTimer);
  const screen = SCREENS[ctx.screen];
  if (document.visibilityState === "visible" && screen?.refresh) {
    try { await screen.refresh(ctx); } catch { /* shown by the offline banner */ }
  }
  pollTimer = setTimeout(poll, POLL_MS[ctx.screen] || 10000);
}

function show(name) {
  if (!SCREENS[name]) name = "overview";
  ctx.screen = name;
  document.querySelectorAll(".nav-item").forEach(a => a.toggleAttribute("aria-current", a.dataset.screen === name));
  document.querySelectorAll(".nav-item[aria-current]").forEach(a => a.setAttribute("aria-current", "page"));
  document.querySelectorAll(".screen").forEach(s => s.classList.toggle("active", s.id === `screen-${name}`));
  $("#content").scrollTop = 0;
  return SCREENS[name].render(ctx).catch(err => console.error(err)).finally(() => { syncDesktopOnly(); poll(); });
}

async function rerenderAll() {
  await loadLanguage();
  translateStatic();
  updateConnection();
  document.title = t("app.name");
  show(ctx.screen || "overview");
}

async function start() {
  applyTheme();
  onReachability(ok => { $("#offline").hidden = ok; });
  $("#retry").addEventListener("click", () => location.reload());   // start() again would add a second set of timers
  try {
    await Promise.all([loadLanguage(), refreshState()]);
  } catch {
    $("#offline").hidden = false;
    translateStatic();
    return;
  }
  translateStatic();
  document.title = t("app.name");
  updateConnection();
  window.addEventListener("hashchange", () => show(location.hash.replace(/^#\//, "")));
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") poll(); });
  const background = async () => {
    await refreshState();
    if (ctx.screen !== "overview") {       // the overview polls live data itself, more often
      ctx.live = await get("/api/live?window=1");
      updateConnection();
    }
  };
  setInterval(() => background().catch(() => {}), STATE_EVERY_MS);
  background().catch(() => {});
  diagnostics.loadBadge(ctx).catch(() => {});
  show(location.hash.replace(/^#\//, "") || "overview");
}

start();

// Text for the UI. The catalog comes from GET /api/i18n (English already filled in for missing
// keys); the rules mirror app/i18n.py: {name} / {name:spec} only, plural dicts, decimal comma.
// Messages from the backend arrive already rendered by the API; t() is for the UI's own labels.

import { get } from "./api.js";

const DECIMAL_COMMA = new Set(["vi", "fr", "de", "es", "pt", "it", "ru", "id", "tr", "nl", "pl", "cs", "uk", "sv",
  "da", "fi", "nb", "ro", "hu", "el"]);
const ONE_IF_ONE = new Set(["en", "de", "es", "it", "pt", "nl", "sv", "da", "nb", "fi", "el", "hu", "tr", "bg"]);
const PLACEHOLDER = /\{([A-Za-z_][A-Za-z0-9_]*)(?::([^{}]*))?\}/g;

export const language = { code: "en", setting: "en", available: [], windows: null };
let catalog = {};

const base = code => code.split("-")[0].toLowerCase();

export async function loadLanguage() {
  const data = await get("/api/i18n");
  catalog = data.messages || {};
  Object.assign(language, { code: data.language, setting: data.setting, available: data.available || [],
                            windows: data.windows_language });
  document.documentElement.lang = data.language;
  return language;
}

function pluralCategory(count) {
  const n = Math.abs(Number(count));
  if (Number.isNaN(n)) return "other";
  if (ONE_IF_ONE.has(base(language.code))) return n === 1 ? "one" : "other";
  if (base(language.code) === "fr") return n < 2 ? "one" : "other";
  return "other";
}

/** A number with `decimals` places, using the decimal comma where the language does. */
export function number(value, decimals = 0) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  const text = Number(value).toFixed(decimals);
  return DECIMAL_COMMA.has(base(language.code)) ? text.replace(".", ",") : text;
}

function formatValue(value, spec) {
  if (Array.isArray(value)) return value.map(v => formatValue(v, spec)).join(", ");
  const m = spec && /^(\+)?\.(\d)([f%])$/.exec(spec);
  if (m && typeof value === "number") {
    const scaled = m[3] === "%" ? value * 100 : value;
    return (m[1] && scaled >= 0 ? "+" : "") + number(scaled, Number(m[2])) + (m[3] === "%" ? "%" : "");
  }
  return typeof value === "number" && !Number.isInteger(value) ? number(value, 1) : String(value);
}

export function t(key, params = {}) {
  let template = catalog[key];
  if (template === undefined) return key;
  if (typeof template === "object") template = template[pluralCategory(params.count)] ?? template.other;
  return template.replace(PLACEHOLDER, (all, name, spec) => (name in params ? formatValue(params[name], spec) : all));
}

export function duration(seconds) {
  seconds = Math.max(0, Math.floor(seconds));
  const h = Math.floor(seconds / 3600), m = Math.floor(seconds % 3600 / 60), s = seconds % 60;
  if (h && !m) return t("time.hours", { count: h });
  if (h) return t("time.hours_minutes", { hours: t("time.hours", { count: h }), minutes: t("time.minutes", { count: m }) });
  if (m && s) return t("time.minutes_seconds", { minutes: t("time.minutes", { count: m }), seconds: t("time.seconds", { count: s }) });
  if (m) return t("time.minutes", { count: m });
  return t("time.seconds", { count: s });
}

/** Unix seconds -> local time ("14:05"); with the date when it is not today. */
export function timeOf(ts, { withDate = false } = {}) {
  const d = new Date(ts * 1000);
  const sameDay = d.toDateString() === new Date().toDateString();
  const opts = { hour: "2-digit", minute: "2-digit" };
  if (withDate || !sameDay) Object.assign(opts, { day: "numeric", month: "short" });
  return d.toLocaleString(language.code, opts);
}

/** Label for the day of `ts`: Today / Yesterday / a date. */
export function dayLabel(ts) {
  const d = new Date(ts * 1000), today = new Date();
  const yesterday = new Date(today); yesterday.setDate(today.getDate() - 1);
  if (d.toDateString() === today.toDateString()) return t("ui.log.today");
  if (d.toDateString() === yesterday.toDateString()) return t("ui.log.yesterday");
  return d.toLocaleDateString(language.code, { weekday: "long", day: "numeric", month: "long" });
}

/** Fill every [data-t] element under `root` with its translation. */
export function translateStatic(root = document) {
  root.querySelectorAll("[data-t]").forEach(node => {
    const text = t(node.dataset.t);
    if (text !== node.dataset.t) node.textContent = text;   // keep the English fallback in the HTML
  });
}

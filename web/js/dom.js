// Small DOM helpers. Text always goes in as text nodes (never parsed as HTML), so nothing the API
// returns - SSIDs, adapter names, event messages - can inject markup.

export const $ = (selector, root = document) => root.querySelector(selector);

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return node;
}

/** replaceChildren that accepts nested arrays and skips null/false (like el()). */
export function fill(node, ...children) {
  node.replaceChildren(...children.flat(Infinity).filter(c => c !== null && c !== undefined && c !== false));
  return node;
}

/** Shown only inside the desktop shell, whose bridge (window.pywebview.api) can open the floating
 *  monitor; app.js shows them again when the bridge arrives after the screen was drawn. */
export function desktopOnly(node) {
  node.dataset.desktopOnly = "";
  node.hidden = !window.pywebview?.api?.open_mini;
  return node;
}

const SVG = "http://www.w3.org/2000/svg";

export function icon(name, cls = "icon") {
  const svg = document.createElementNS(SVG, "svg");
  svg.setAttribute("class", cls);
  const use = document.createElementNS(SVG, "use");
  use.setAttribute("href", `#i-${name}`);
  svg.append(use);
  return svg;
}

export function svgEl(tag, attrs = {}) {
  const node = document.createElementNS(SVG, tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  return node;
}

let toastTimer;
export function toast(text, { bad = false, ms = 4000 } = {}) {
  const box = $("#toast");
  box.textContent = text;
  box.className = "toast" + (bad ? " bad" : "");
  box.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { box.hidden = true; }, ms);
}

/** A confirmation sheet; resolves true when confirmed. `content` (a node) goes under the text, and a
 *  `wide` sheet is left-aligned, for a list to choose from; `symbol`/`tile` pick the badge on top. */
export function confirmSheet({ title, body, confirm, cancel, content = null, wide = false, symbol = "flask", tile = "warn" }) {
  return new Promise(resolve => {
    const scrim = $("#scrim");
    const badge = $(".sheet > .icon-tile", scrim);
    badge.className = `icon-tile ${tile}`;
    fill(badge, icon(symbol));
    $("#sheet-title").textContent = title;
    $("#sheet-body").textContent = body || "";
    $("#sheet-body").hidden = !body;
    fill($("#sheet-content"), content);
    $(".sheet", scrim).classList.toggle("wide", wide);
    $("#sheet-confirm").disabled = false;
    $("#sheet-confirm").textContent = confirm;
    $("#sheet-cancel").textContent = cancel;
    const done = value => { scrim.classList.remove("open"); resolve(value); };
    $("#sheet-confirm").onclick = () => done(true);
    $("#sheet-cancel").onclick = () => done(false);
    scrim.onclick = e => { if (e.target === scrim) done(false); };
    scrim.classList.add("open");
    $("#sheet-cancel").focus();
  });
}

export function switchEl(checked, { disabled = false, label = "", onToggle } = {}) {
  const sw = el("button", { class: "switch", role: "switch", "aria-checked": String(Boolean(checked)),
                            "aria-label": label, disabled });
  if (onToggle) sw.addEventListener("click", () => onToggle(sw));
  return sw;
}

// Small DOM helpers shared by the editor modules.
export const $ = id => document.getElementById(id);
export const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

export function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key === "html") node.innerHTML = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (key === "dataset") Object.assign(node.dataset, value);
    else node.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of children.flat()) if (child !== null && child !== undefined && child !== false) node.append(child);
  return node;
}

export function icon(name, cls = "icon-svg") {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", cls);
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `#i-${name}`);
  svg.append(use);
  return svg;
}

export function svgEl(tag, attrs = {}) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [key, value] of Object.entries(attrs)) if (value !== undefined && value !== null) node.setAttribute(key, String(value));
  return node;
}

let toastTimer;
export function toast(message, { action, onAction, duration = 2200 } = {}) {
  const node = $("toast");
  node.replaceChildren(message);
  if (action) {
    const button = el("button", { class: "toast-action", text: action, onclick: () => { node.classList.remove("show"); onAction?.(); } });
    node.append(button);
    node.style.pointerEvents = "auto";
  } else node.style.pointerEvents = "none";
  node.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.classList.remove("show"), action ? Math.max(duration, 5000) : duration);
}

export function busy(text) {
  const pill = $("busy-pill");
  if (text === null) { pill.hidden = true; return; }
  $("busy-text").textContent = text;
  pill.hidden = false;
}

export const isMac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);

export function plural(n, word) { return `${n} ${word}${n === 1 ? "" : "s"}`; }

// A modal dialog. buttons: [{ label, kind, value, disabled }]; resolves with the
// clicked button's value (null on close). body(container) fills the body and
// may return a getter whose value is resolved instead.
export function dialog({ title, body, buttons = [{ label: "Close", value: null }], width = 440 }) {
  return new Promise(resolve => {
    const backdrop = $("dialog");
    const panel = backdrop.querySelector(".dialog");
    panel.style.width = `${width}px`;
    $("dialog-title").textContent = title;
    const container = $("dialog-body");
    container.replaceChildren();
    const api = { close: value => finish(value), buttons: {} };
    const getter = body?.(container, api);
    const foot = $("dialog-foot");
    foot.replaceChildren();
    for (const spec of buttons) {
      const button = el("button", { class: `btn ${spec.kind || ""}`, text: spec.label, type: "button" });
      button.disabled = !!spec.disabled;
      button.addEventListener("click", async () => {
        if (spec.value === null || spec.value === undefined) return finish(null);
        const value = typeof getter === "function" ? await getter(spec.value) : spec.value;
        if (value === undefined) return; // validation failed: keep open
        finish(value);
      });
      api.buttons[spec.value ?? "close"] = button;
      foot.append(button);
    }
    const onKey = event => {
      if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); finish(null); }
      if (event.key === "Enter" && !event.target.closest("textarea") && !event.isComposing) {
        const primary = foot.querySelector(".btn.primary:not(:disabled)");
        if (primary) { event.preventDefault(); primary.click(); }
      }
    };
    function finish(value) {
      backdrop.hidden = true;
      document.removeEventListener("keydown", onKey, true);
      $("dialog-close").onclick = null;
      resolve(value);
    }
    $("dialog-close").onclick = () => finish(null);
    document.addEventListener("keydown", onKey, true);
    backdrop.hidden = false;
    setTimeout(() => (container.querySelector("[data-autofocus], [autofocus]") || foot.querySelector(".btn.primary"))?.focus(), 0);
  });
}

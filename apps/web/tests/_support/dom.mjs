/**
 * Shared DOM for Web unit tests: a linkedom window on `globalThis`, Web
 * Storage, the browser APIs React and the app touch, and a React render
 * helper.
 *
 *   import { installDom, render, setRect } from "../_support/dom.mjs";
 *   installDom({ pathname: "/chat" });
 *   const { createElement } = await import("react");
 *   const view = await render(createElement(Thing));
 *   view.container.querySelector("button").click();
 *   await view.unmount();
 *
 * Call installDom() before importing react-dom or app code: react-dom decides
 * whether a DOM exists when it is first evaluated.
 *
 * Stubbed, not emulated: linkedom computes no layout or styles. Rects are
 * zero unless set with setRect(); ResizeObserver only fires through
 * `resizeObservers.trigger()`; matchMedia answers `matches: false` unless
 * given a `media` function.
 */
import { parseHTML } from "linkedom";

/** A Map-backed Storage with the real method surface. */
export function memoryStorage(initial = {}) {
  const map = new Map(Object.entries(initial));
  return {
    get length() { return map.size; },
    key: (index) => [...map.keys()][index] ?? null,
    getItem: (key) => (map.has(String(key)) ? map.get(String(key)) : null),
    setItem: (key, value) => { map.set(String(key), String(value)); },
    removeItem: (key) => { map.delete(String(key)); },
    clear: () => { map.clear(); },
  };
}

const rects = new WeakMap();
const ZERO_RECT = { x: 0, y: 0, top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0 };

/** Give an element the rect getBoundingClientRect() reports. */
export function setRect(element, { left = 0, top = 0, width = 0, height = 0 } = {}) {
  rects.set(element, { x: left, y: top, left, top, width, height, right: left + width, bottom: top + height });
}

/** Every live ResizeObserver; `trigger(target?)` calls their callbacks. */
export const resizeObservers = {
  instances: new Set(),
  trigger(target) {
    for (const observer of this.instances) {
      const entries = [...observer.targets]
        .filter((observed) => !target || observed === target)
        .map((observed) => ({ target: observed, contentRect: observed.getBoundingClientRect() }));
      if (entries.length) observer.callback(entries, observer);
    }
  },
};

class StubResizeObserver {
  constructor(callback) { this.callback = callback; this.targets = new Set(); resizeObservers.instances.add(this); }
  observe(target) { this.targets.add(target); }
  unobserve(target) { this.targets.delete(target); }
  disconnect() { this.targets.clear(); resizeObservers.instances.delete(this); }
}

class StubIntersectionObserver {
  constructor(callback) { this.callback = callback; }
  observe() {}
  unobserve() {}
  disconnect() {}
  takeRecords() { return []; }
}

const WINDOW_GLOBALS = [
  "Event", "CustomEvent", "KeyboardEvent", "MouseEvent", "FocusEvent", "InputEvent",
  "Node", "Element", "HTMLElement", "HTMLInputElement", "HTMLTextAreaElement", "HTMLButtonElement",
  "HTMLAnchorElement", "HTMLImageElement", "SVGElement", "DocumentFragment", "Text", "MutationObserver",
  "customElements",
];

/**
 * Put a linkedom window on globalThis. Options:
 * - `html`: the document (default: a body with `<div id="root">`).
 * - `pathname`, `search`, `hash`: window.location (a plain object; tests may reassign fields).
 * - `locale`: stored as `agentic_locale` so the real i18n module answers in it (default "en").
 * - `storage`: initial localStorage entries.
 * - `media`: `(query) => boolean` for matchMedia.
 * Returns the window.
 */
export function installDom({
  html = '<!doctype html><html><head></head><body><div id="root"></div></body></html>',
  pathname = "/",
  search = "",
  hash = "",
  locale = "en",
  storage = {},
  media = () => false,
} = {}) {
  const { window } = parseHTML(html);
  const { document } = window;
  globalThis.window = window;
  globalThis.document = document;
  for (const name of WINDOW_GLOBALS) {
    if (window[name]) globalThis[name] = window[name];
  }
  // React only takes the native `input` event path when the DOM advertises it.
  document.oninput = null;

  const local = memoryStorage({ ...(locale ? { agentic_locale: locale } : {}), ...storage });
  const session = memoryStorage();
  for (const [name, value] of [["localStorage", local], ["sessionStorage", session]]) {
    Object.defineProperty(globalThis, name, { value, configurable: true, writable: true });
    Object.defineProperty(window, name, { value, configurable: true, writable: true });
  }

  Object.defineProperty(window, "location", {
    value: { pathname, search, hash, origin: "http://localhost", href: `http://localhost${pathname}${search}${hash}` },
    configurable: true,
    writable: true,
  });
  window.history ??= { pushState() {}, replaceState() {}, back() {}, forward() {} };
  globalThis.history = window.history;

  const matchMedia = (query) => ({
    media: query,
    matches: Boolean(media(query)),
    onchange: null,
    addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {},
    dispatchEvent: () => false,
  });
  window.matchMedia = matchMedia;
  globalThis.matchMedia = matchMedia;

  window.ResizeObserver = StubResizeObserver;
  globalThis.ResizeObserver = StubResizeObserver;
  window.IntersectionObserver = StubIntersectionObserver;
  globalThis.IntersectionObserver = StubIntersectionObserver;

  const raf = (callback) => setTimeout(() => callback(performance.now()), 0);
  window.requestAnimationFrame = globalThis.requestAnimationFrame = raf;
  window.cancelAnimationFrame = globalThis.cancelAnimationFrame = (id) => clearTimeout(id);

  window.HTMLElement.prototype.getBoundingClientRect = function getBoundingClientRect() {
    return { ...(rects.get(this) ?? ZERO_RECT), toJSON() { return this; } };
  };
  window.HTMLElement.prototype.scrollIntoView ??= function scrollIntoView() {};

  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  return window;
}

/**
 * Render a React element into a fresh container (or `options.container`)
 * inside `act`. Returns the container, `rerender(element)`, `act(fn)` and
 * `unmount()`.
 */
export async function render(element, { container } = {}) {
  const { act } = await import("react");
  const { createRoot } = await import("react-dom/client");
  const host = container ?? document.body.appendChild(document.createElement("div"));
  const root = createRoot(host);
  await act(async () => { root.render(element); });
  return {
    container: host,
    root,
    act: (fn) => act(async () => { await fn(); }),
    rerender: (next) => act(async () => { root.render(next); }),
    async unmount() {
      await act(async () => { root.unmount(); });
      if (!container) host.remove();
    },
  };
}

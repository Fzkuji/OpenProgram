import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { parseHTML } from "linkedom";

const source = readFileSync(new URL("../../../../scripts/docs_site/assets/theme.js", import.meta.url), "utf8");

function page({ saved = null, dark = false, blocked = false, legacyMedia = false } = {}) {
  const { document, CustomEvent, Event } = parseHTML(`<html lang="en"><head>
    <link id="pyg-light"><link id="pyg-dark"></head><body>
    <button id="lang-toggle"></button><div class="theme-wrap">
    <button id="theme-toggle" aria-expanded="false"></button><div id="theme-menu">
    ${["system", "light", "dark"].map((mode) => `<button class="theme-opt" data-theme-choice="${mode}"><span></span></button>`).join("")}
    </div></div></body></html>`);
  Object.defineProperty(document, "readyState", { value: "loading", configurable: true });
  let focus = null;
  Object.defineProperty(document, "activeElement", { get: () => focus });
  document.querySelectorAll("button").forEach((el) => { el.focus = () => { focus = el; }; });
  const values = new Map(saved === null ? [] : [["op-docs-theme", saved]]);
  const writes = [];
  const storage = {
    getItem(key) { if (blocked) throw Error("blocked"); return values.get(key) ?? null; },
    setItem(key, value) { if (blocked) throw Error("blocked"); values.set(key, value); writes.push([key, value]); },
  };
  const mediaListeners = [];
  const media = { matches: dark };
  if (legacyMedia) media.addListener = (listener) => mediaListeners.push(listener);
  else media.addEventListener = (type, listener) => { assert.equal(type, "change"); mediaListeners.push(listener); };
  const listeners = new Map();
  const events = [];
  const window = {
    localStorage: storage,
    matchMedia(query) { assert.equal(query, "(prefers-color-scheme: dark)"); return media; },
    addEventListener(type, listener) { const list = listeners.get(type) || []; list.push(listener); listeners.set(type, list); },
    dispatchEvent(event) { events.push(event); for (const listener of listeners.get(event.type) || []) listener(event); },
  };
  vm.runInNewContext(source, { document, window, CustomEvent });
  const root = document.documentElement;
  const button = document.getElementById("theme-toggle");
  const option = (mode) => document.querySelector(`[data-theme-choice="${mode}"]`);
  return {
    document, root, button, option, values, writes, events, window, storage,
    ready() { document.dispatchEvent(new Event("DOMContentLoaded")); },
    system(value) { media.matches = value; for (const listener of mediaListeners) listener({ matches: value }); },
    stored(value, extra = {}) { window.dispatchEvent({ type: "storage", key: "op-docs-theme", newValue: value, storageArea: storage, ...extra }); },
    key(target, key) { const e = new Event("keydown", { bubbles: true, cancelable: true }); e.key = key; target.dispatchEvent(e); },
  };
}

function palette(p, expected) {
  assert.equal(p.root.getAttribute("data-theme"), expected);
  assert.equal(p.root.style.colorScheme, expected);
  assert.equal(p.document.getElementById("pyg-dark").media, expected === "dark" ? "all" : "not all");
  assert.equal(p.document.getElementById("pyg-light").media, expected === "light" ? "all" : "not all");
}

test("system theme initializes before DOM ready and follows changes without persistence", () => {
  const p = page({ dark: true });
  palette(p, "dark");
  assert.equal(p.root.getAttribute("data-theme-mode"), "system");
  p.ready();
  p.system(false); palette(p, "light");
  p.system(true); palette(p, "dark");
  assert.deepEqual(p.writes, []);
  assert.equal(p.events.filter((event) => event.type === "documentThemeChange").length, 3);
});

test("legacy manual preferences stay pinned and selecting system resumes immediately", () => {
  const p = page({ saved: "light", dark: true });
  p.ready(); palette(p, "light");
  p.system(false); p.system(true); palette(p, "light");
  p.option("system").click(); palette(p, "dark");
  assert.equal(p.values.get("op-docs-theme"), "system");
  p.option("dark").click(); p.system(false); palette(p, "dark");
  assert.equal(p.option("dark").getAttribute("aria-checked"), "true");
  assert.equal(p.option("system").getAttribute("aria-checked"), "false");
});

test("blocked storage and invalid values still follow system and permit in-memory overrides", () => {
  for (const options of [{ blocked: true }, { saved: "invalid" }]) {
    const p = page({ ...options, dark: true, legacyMedia: true });
    p.ready(); palette(p, "dark");
    p.system(false); palette(p, "light");
    p.option("dark").click(); palette(p, "dark");
    p.option("system").click(); palette(p, "light");
  }
});

test("other tabs update preferences without write loops, including storage clear", () => {
  const p = page(); p.ready();
  p.stored("dark"); palette(p, "dark");
  p.stored("light", { storageArea: {} }); palette(p, "dark");
  p.stored("system"); p.system(true); palette(p, "dark");
  p.stored("light"); palette(p, "light");
  p.stored(null, { key: null }); palette(p, "dark");
  assert.deepEqual(p.writes, []);
});

test("menu supports keyboard selection, escape, focus and localized labels", () => {
  const p = page(); p.ready();
  p.key(p.button, "ArrowDown");
  assert.equal(p.button.getAttribute("aria-expanded"), "true");
  assert.equal(p.document.activeElement, p.option("system"));
  p.key(p.option("system"), "End");
  assert.equal(p.document.activeElement, p.option("dark"));
  p.key(p.option("dark"), "Escape");
  assert.equal(p.button.getAttribute("aria-expanded"), "false");
  assert.equal(p.document.activeElement, p.button);
  p.root.setAttribute("lang", "zh");
  p.window.dispatchEvent({ type: "documentLangChange" });
  assert.equal(p.button.getAttribute("aria-label"), "主题：跟随系统");
  assert.equal(p.option("system").textContent, "跟随系统");
  p.button.click(); p.option("light").click();
  assert.equal(p.button.getAttribute("aria-label"), "主题：浅色");
  assert.equal(p.button.getAttribute("aria-expanded"), "false");
});

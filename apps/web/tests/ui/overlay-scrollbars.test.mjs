import assert from "node:assert/strict";
import test from "node:test";
import { build } from "esbuild";
import { createRequire } from "node:module";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { parseHTML } from "linkedom";

const require = createRequire(import.meta.url);
const bundle = await build({
  entryPoints: [fileURLToPath(new URL("../../lib/runtime-bridge/dom/scrollbar.ts", import.meta.url))],
  bundle: true, write: false, format: "cjs", platform: "node",
});
const temporary = mkdtempSync(join(tmpdir(), "op-scrollbar-"));
let api;
try {
  const output = join(temporary, "module.cjs");
  writeFileSync(output, bundle.outputFiles[0].text);
  api = require(output);
} finally { rmSync(temporary, { recursive: true, force: true }); }

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

test("a box gets its thumb on its first scroll, never on hover", async () => {
  const { window, document } = parseHTML("<html><body><div id=box></div></body></html>");
  Object.assign(globalThis, { window, document, HTMLElement: window.HTMLElement, Event: window.Event, MutationObserver: window.MutationObserver,
    requestAnimationFrame: (cb) => setTimeout(cb, 0), cancelAnimationFrame: clearTimeout,
    getComputedStyle: () => ({ borderTopRightRadius: "0", borderBottomRightRadius: "0" }) });
  window.innerWidth = 800;
  const box = document.getElementById("box");
  Object.assign(box, { scrollHeight: 1000, clientHeight: 200, scrollWidth: 100, clientWidth: 100, scrollTop: 0 });
  box.getBoundingClientRect = () => ({ top: 0, left: 0, right: 100, bottom: 200, width: 100, height: 200 });

  api.initOverlayScrollbars();
  box.dispatchEvent(new window.Event("mouseenter"));
  await wait(10);
  assert.equal(document.querySelectorAll(".overlay-thumb").length, 0, "hovering installs nothing");

  box.scrollTop = 300;
  // linkedom has no capture phase; a bubbling scroll reaches the
  // document's listener the way a real scroll's capture phase does.
  box.dispatchEvent(new window.Event("scroll", { bubbles: true }));
  await wait(10);
  const thumbs = document.querySelectorAll(".overlay-thumb");
  assert.equal(thumbs.length, 1, "one vertical thumb; the box does not overflow sideways");
  assert.ok(thumbs[0].classList.contains("visible"), "scrolling shows it");
  assert.ok(box.classList.contains("scroll-overlay"));

  box.remove();
  document.body.appendChild(document.createElement("i"));
  await wait(10);
  assert.equal(document.querySelectorAll(".overlay-thumb").length, 0, "a removed box takes its thumb with it");

  // A box marked data-scrollbar-layer="content" keeps its thumb in the
  // content layer, under a control floating on its edge.
  const list = document.createElement("div");
  list.setAttribute("data-scrollbar-layer", "content");
  Object.assign(list, { scrollHeight: 1000, clientHeight: 200, scrollWidth: 100, clientWidth: 100, scrollTop: 300 });
  list.getBoundingClientRect = box.getBoundingClientRect;
  document.body.appendChild(list);
  list.dispatchEvent(new window.Event("scroll", { bubbles: true }));
  await wait(10);
  const low = document.querySelectorAll(".overlay-thumb");
  assert.equal(low.length, 1);
  assert.ok(low[0].classList.contains("overlay-thumb-content"), "the marked box's thumb is in the content layer");
});

test("a scroll caused by the box changing size does not show the thumb; a later one does", async () => {
  const { window, document } = parseHTML("<html><body><div id=box></div></body></html>");
  let resized;
  Object.assign(globalThis, { window, document, HTMLElement: window.HTMLElement, Event: window.Event, MutationObserver: window.MutationObserver,
    ResizeObserver: class { constructor(callback) { resized = callback; } observe() {} disconnect() {} },
    requestAnimationFrame: (cb) => setTimeout(cb, 0), cancelAnimationFrame: clearTimeout,
    getComputedStyle: () => ({ borderTopRightRadius: "0", borderBottomRightRadius: "0" }) });
  window.innerWidth = 800;
  const box = document.getElementById("box");
  Object.assign(box, { scrollHeight: 1000, clientHeight: 200, scrollWidth: 300, clientWidth: 300, scrollTop: 0 });
  box.getBoundingClientRect = () => ({ top: 0, left: 0, right: 300, bottom: 200, width: 300, height: 200 });
  const detach = api.attachOverlayScrollbar(box);
  await wait(150);
  const thumb = document.querySelector(".overlay-thumb");
  const scroll = () => box.dispatchEvent(new window.Event("scroll", { bubbles: true }));

  box.clientWidth = 200;
  resized();
  box.scrollTop = 40;
  scroll();
  await wait(10);
  assert.equal(thumb.classList.contains("visible"), false, "the drag's own scroll stays quiet");

  await wait(230);
  scroll();
  await wait(10);
  assert.ok(thumb.classList.contains("visible"), "someone scrolling afterwards still shows it");
  detach();
  delete globalThis.ResizeObserver;
});

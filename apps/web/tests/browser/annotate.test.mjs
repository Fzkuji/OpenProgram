import assert from "node:assert/strict";
import { createRequire } from "node:module";
import test from "node:test";
import { parseHTML } from "linkedom";

import {
  adjustText,
  annotationFileName,
  annotationMessage,
  noteTarget,
} from "../../lib/browser/capture/annotate.ts";

// The overlay is desktop code injected into the page; its pure parts are
// exercised here against a parsed document.
const require = createRequire(import.meta.url);
const { shortSelector, installAnnotate } = require("../../../desktop/main/web-views/annotate-overlay.js");

const note = (fields) => ({
  n: 1, kind: "element", selector: "h1#firstHeading", tag: "h1", text: "", comment: "",
  rect: { x: 0, y: 0, width: 0, height: 0 }, adjust: [], ...fields,
});

function page(html) {
  return parseHTML(`<!doctype html><html><head><title>Example</title></head><body>${html}</body></html>`);
}

test("a selector stops at an id", () => {
  const { document } = page('<main><h1 id="firstHeading">Title</h1></main>');
  assert.equal(shortSelector(document.querySelector("h1")), "h1#firstHeading");
});

test("a selector keeps a readable class and counts same-tag siblings", () => {
  const { document } = page('<nav id="top"><ul class="menu"><li>a</li><li>b</li><li>c</li></ul></nav><ul class="menu"><li>x</li><li>y</li></ul>');
  const second = document.querySelectorAll("#top li")[1];
  const selector = shortSelector(second);
  assert.match(selector, /li:nth-of-type\(2\)$/);
  assert.equal(document.querySelectorAll(selector).length, 1, "the selector finds exactly that element");
  assert.equal(selector, "nav#top > ul.menu > li:nth-of-type(2)");
});

test("a generated class is skipped and the walk is at most four levels", () => {
  const { document } = page('<div><div><div><div><div><span class="css-1x2y345">deep</span></div></div></div></div></div><span>other</span>');
  const selector = shortSelector(document.querySelector("span"));
  assert.ok(!selector.includes("css-1x2y345"), selector);
  assert.ok(selector.split(" > ").length <= 4, selector);
  assert.equal(shortSelector(document.body), "body");
});

test("an Adjust change reads as property, before and after", () => {
  assert.equal(adjustText({ property: "font-size", from: "28px", to: "34px" }), "font-size 28px → 34px");
});

test("a note names its element with text, or its region", () => {
  assert.equal(noteTarget(note({ text: '  The  "Main" page ' })), "`h1#firstHeading` (\"The 'Main' page\")");
  assert.equal(noteTarget(note({ selector: "", tag: "img" })), "<img>");
  assert.equal(
    noteTarget(note({ kind: "region", rect: { x: 40, y: 220, width: 312, height: 100 } })),
    "region 312×100 at (40, 220)",
  );
});

test("the message gives the page, then each note by number with its Adjust values", () => {
  const message = annotationMessage(
    { url: "https://en.wikipedia.org/wiki/Cat", title: "Cat - Wikipedia" },
    [
      note({ comment: "Make it bigger\nand bolder", adjust: [{ property: "font-size", from: "28px", to: "34px" }, { property: "color", from: "#202122", to: "#3366cc" }] }),
      note({ n: 2, kind: "region", rect: { x: 40, y: 220, width: 312, height: 100 }, comment: "Remove this banner" }),
      note({ n: 3, selector: "a.mw-logo" }),
    ],
    true,
  );
  assert.equal(message, [
    "Notes on [Cat - Wikipedia](https://en.wikipedia.org/wiki/Cat):",
    "",
    "1. `h1#firstHeading`: Make it bigger and bolder",
    "   Adjust: font-size 28px → 34px; color #202122 → #3366cc",
    "2. region 312×100 at (40, 220): Remove this banner",
    "3. `a.mw-logo`",
    "",
    "The attached screenshot shows the page with each note's number pinned where it applies.",
  ].join("\n"));
  assert.equal(
    annotationMessage({ url: "https://example.com/" }, [note({ comment: "x" })], false),
    "Notes on https://example.com/:\n\n1. `h1#firstHeading`: x",
  );
});

test("the screenshot is named after the site", () => {
  assert.equal(annotationFileName("https://www.github.com/a/b"), "notes-github.com.png");
  assert.equal(annotationFileName("not a url"), "notes-page.png");
});

// A parsed page has no layout: the pointer hit test and computed styles
// are given, and frames run on the next tick.
function overlayPage() {
  const { window, document } = page('<h1 id="firstHeading" style="color: red">Cat</h1><p>Body</p>');
  const h1 = document.querySelector("h1");
  Object.assign(window, {
    innerWidth: 1200, innerHeight: 800, scrollX: 0, scrollY: 300,
    requestAnimationFrame: (fn) => setTimeout(fn, 0),
    cancelAnimationFrame: (id) => clearTimeout(id),
    setTimeout,
    getComputedStyle: () => ({ fontSize: "28px", padding: "0px", color: "rgb(32, 33, 34)" }),
  });
  Object.defineProperty(window, "location", { value: { href: "https://en.wikipedia.org/wiki/Cat" }, configurable: true });
  document.elementsFromPoint = () => [h1];
  h1.getBoundingClientRect = () => ({ left: 100, top: 50, width: 300, height: 40 });
  // linkedom's style declaration has no priorities.
  const styleProto = Object.getPrototypeOf(h1.style);
  styleProto.getPropertyPriority ??= () => "";
  const api = installAnnotate(window, shortSelector, "open");
  const root = () => document.querySelector("openprogram-annotate")?.shadowRoot;
  const fire = (target, type, fields = {}) => {
    const event = new window.Event(type, { bubbles: true, cancelable: true });
    Object.assign(event, { button: 0, clientX: 0, clientY: 0, ...fields });
    target.dispatchEvent(event);
  };
  return { window, document, h1, api, root, fire };
}

test("a click pins an element note; Adjust previews on the page and Send reports both", async () => {
  const { window, h1, api, root, fire } = overlayPage();
  api.enter({ labels: { send: "Send to chat" }, theme: { accent: "#3b82f6" } });
  assert.deepEqual(await api.next(), { type: "state", active: true, count: 0 });
  assert.equal(root().querySelector(".hint").hidden, false, "the hint shows while annotating");

  fire(root().querySelector(".layer"), "mousedown", { clientX: 120, clientY: 60 });
  fire(window, "mouseup", { clientX: 120, clientY: 60 });
  assert.deepEqual(await api.next(), { type: "state", active: true, count: 1 });
  assert.equal(root().querySelector(".pin").textContent, "1");
  assert.equal(root().querySelector(".sel").textContent, "h1#firstHeading");
  assert.equal(root().querySelector(".count").textContent, "1 note on this page");

  const textarea = root().querySelector("textarea");
  textarea.value = "Make it bigger";
  fire(textarea, "input");
  const size = root().querySelector(".adjust input[type=range]");
  size.value = "34";
  fire(size, "input");
  assert.equal(h1.style.getPropertyValue("font-size"), "34px", "the page previews the new size");

  fire(root().querySelector(".bar .primary"), "click");
  assert.equal(root().querySelector(".card").hidden, true, "the card leaves the screenshot");
  assert.equal(root().querySelector(".bar").hidden, true, "the bar leaves the screenshot");
  const sent = await api.next();
  assert.equal(sent.type, "send");
  assert.equal(sent.url, "https://en.wikipedia.org/wiki/Cat");
  assert.equal(sent.title, "Example");
  assert.deepEqual(sent.notes, [{
    n: 1, kind: "element", selector: "h1#firstHeading", tag: "h1", text: "Cat", comment: "Make it bigger",
    rect: { x: 100, y: 50, width: 300, height: 40 },
    adjust: [{ property: "font-size", from: "28px", to: "34px" }],
  }]);

  api.sent();
  assert.deepEqual(await api.next(), { type: "state", active: false, count: 0 });
  assert.equal(h1.style.getPropertyValue("font-size"), "", "sending takes the preview off the page");
  assert.equal(h1.style.getPropertyValue("color"), "red", "the page's own style stays");
  assert.equal(root(), undefined, "the overlay leaves the page");
});

test("a drag pins a region; Esc leaves the mode with the notes kept, Discard clears them", async () => {
  const { window, api, root, fire } = overlayPage();
  api.enter({});
  await api.next();
  const layer = root().querySelector(".layer");
  fire(layer, "mousedown", { clientX: 40, clientY: 20 });
  fire(layer, "mousemove", { clientX: 352, clientY: 120 });
  fire(window, "mouseup", { clientX: 352, clientY: 120 });
  assert.deepEqual(await api.next(), { type: "state", active: true, count: 1 });
  assert.equal(root().querySelector(".sel").textContent, "region 312×100");
  assert.match(root().querySelector(".outline").className, /region/);

  fire(window, "keydown", { key: "Escape" });
  assert.deepEqual(await api.next(), { type: "state", active: false, count: 1 });
  assert.ok(root(), "unsent notes stay on the page");
  assert.equal(root().querySelector(".layer").hidden, true, "the page takes clicks again");
  assert.equal(root().querySelector(".bar").hidden, false);

  fire(root().querySelector(".bar .ghost"), "click");
  assert.deepEqual(await api.next(), { type: "state", active: false, count: 0 });
  assert.equal(root(), undefined);
});

test("the host commands are short scripts; only start installs the overlay", () => {
  const { annotateCommandSource } = require("../../../desktop/main/web-views/annotate-overlay.js");
  const start = annotateCommandSource("start", { labels: { hint: "x" } });
  assert.match(start, /attachShadow/);
  assert.match(start, /"hint":"x"/);
  assert.doesNotMatch(annotateCommandSource("discard"), /attachShadow/);
  assert.equal(annotateCommandSource("next"), "window.__openprogramAnnotate ? window.__openprogramAnnotate.next() : null");
});


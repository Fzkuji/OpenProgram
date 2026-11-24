import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { registerHooks } from "node:module";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { parseHTML } from "linkedom";

const webRoot = new URL("../../", import.meta.url);
registerHooks({
  resolve(specifier, context, nextResolve) {
    const base = specifier.startsWith("@/") ? new URL(specifier.slice(2), webRoot).href
      : specifier.startsWith(".") && !/\.[a-z]+$/i.test(specifier) ? new URL(specifier, context.parentURL).href : null;
    if (base) {
      for (const suffix of [".ts", ".tsx", "/index.ts"]) {
        if (existsSync(fileURLToPath(base + suffix))) return { url: base + suffix, shortCircuit: true };
      }
    }
    return nextResolve(specifier, context);
  },
});

const captures = [];
const { window } = parseHTML("<!doctype html><html><body></body></html>");
globalThis.window = window;
globalThis.document = window.document;
window.location = { pathname: "/chat", hash: "", search: "", hostname: "127.0.0.1" };
window.devicePixelRatio = 1;
window.openprogramDesktop = { webTab: { capture: (tabId) => new Promise(resolve => captures.push({ tabId, resolve })) } };
globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
globalThis.Image = class { constructor() { this.naturalWidth = 800; this.naturalHeight = 600; } async decode() {} };
const createElement = document.createElement.bind(document);
document.createElement = (name) => name === "canvas"
  ? { width: 0, height: 0, getContext: () => ({ drawImage() {} }), toDataURL: () => "data:image/jpeg;base64,x" }
  : createElement(name);

const { refreshWebResourceThumbnail, useWebResourceThumbnails } =
  await import("../../lib/browser/web-tab/web-resource-thumbnails.ts");

const flush = async () => { for (let i = 0; i < 10; i++) await Promise.resolve(); };

test("a picture asked for while one is being taken is taken right after it", async () => {
  refreshWebResourceThumbnail("tab-1", "op-1");
  assert.equal(captures.length, 1);
  // The Agent operated the page again before the first picture came back.
  refreshWebResourceThumbnail("tab-1", "op-2");
  assert.equal(captures.length, 1, "pictures of one tab are never taken at once");
  captures[0].resolve("data:image/png;base64,first");
  await flush();
  assert.equal(captures.length, 2, "the later request is not lost");
  captures[1].resolve("data:image/png;base64,second");
  await flush();
  assert.equal(useWebResourceThumbnails.getState().shots["tab-1"].operationId, "op-2");
  // The picture for this operation exists: asking again takes none.
  refreshWebResourceThumbnail("tab-1", "op-2");
  assert.equal(captures.length, 2);
});

import assert from "node:assert/strict";
import test from "node:test";
import { parseHTML } from "linkedom";

const { window } = parseHTML("<!doctype html><html><body></body></html>");
globalThis.window = window;
globalThis.document = window.document;

const { renderMd } = await import("../../../lib/runtime-bridge/dom/markdown-render.ts");

test("a reply's footnotes render as notes, not a dead link", () => {
  const html = renderMd("脚注测试[^1]。\n\n[^1]: 脚注内容");
  assert.match(html, /<sup><a id="footnote-ref-1" href="#footnote-1" data-footnote-ref[^>]*>1<\/a><\/sup>/);
  assert.match(html, /<section class="footnotes" data-footnotes="?"?>/);
  assert.match(html, /<li id="footnote-1">\s*<p>脚注内容 <a href="#footnote-ref-1" data-footnote-backref/);
});

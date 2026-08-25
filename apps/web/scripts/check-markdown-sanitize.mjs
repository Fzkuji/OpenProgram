/** Chat markdown must pass through the sanitizer before it reaches a bubble.
 *
 *  marked does not sanitize: raw HTML in the source goes straight to the
 *  DOM. The threat is not a user typing `<script>` at themselves — it is
 *  content the model relays from a tool (a fetched page, a repo file, an
 *  MCP result, an inbound channel message). Any of those reaching a
 *  bubble would run `<img onerror>` / `<svg onload>` / `javascript:` hrefs.
 *
 *  The sanitizer itself needs a real DOM parser, so its behaviour is
 *  verified in-browser rather than here. What this guards is the wiring:
 *  every `marked.parse` feeding chat HTML stays wrapped, and the
 *  sanitizer keeps covering the vectors it was written for. Both are the
 *  parts a refactor silently drops.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { parseHTML } from "linkedom";

const root = fileURLToPath(new URL("../", import.meta.url));
const source = (p) => readFileSync(root + p, "utf8");

const helpers = source("lib/runtime-bridge/helpers.ts");
const markdownRenderer = source("lib/runtime-bridge/markdown-render.ts");

// 1) renderMd — the chat bubble path — must sanitize what marked returns.
assert.match(
  markdownRenderer,
  /sanitizeHtml\(\s*markdown\.parse\(/,
  "renderMd must wrap marked.parse in sanitizeHtml — chat bubbles render "
    + "tool-relayed content and marked does not sanitize",
);

// 2) The sanitizer must keep covering each vector class. A tag-only
//    filter misses attribute injection, which is the common case.
for (const [what, re] of [
  ["strips every on* handler", /name\.startsWith\("on"\)/],
  ["filters URL-bearing attributes", /URL_ATTRS\.has\(name\)/],
  ["allow-lists safe URL schemes", /SAFE_URL/],
  ["drops embedding tags", /IFRAME[\s\S]*OBJECT[\s\S]*EMBED/],
  ["drops SVG and MathML namespaces", /DROP_NAMESPACES\.has\(el\.namespaceURI\s*\?\?\s*""\)/],
  ["parses detached so nothing loads", /createElement\("template"\)/],
]) {
  assert.match(markdownRenderer, re, `sanitizeHtml no longer ${what}`);
}

// 3) data: URLs are the subtle one — images are fine, text/html is a
//    script vector. The allow-list must not have been widened to bare `data:`.
const safeUrl = markdownRenderer.match(/const SAFE_URL = (\/.*\/[a-z]*);/)?.[1];
assert.ok(safeUrl, "SAFE_URL pattern not found");
assert.ok(
  !/\|data:\)/.test(safeUrl) && /data:image\\\//.test(safeUrl),
  `SAFE_URL must allow only data:image/*, got ${safeUrl}`,
);

// 4) Math is restored after sanitization so KaTeX still sees the original
// delimiters. The restored source must be escaped text, not raw HTML, and the
// replacement must be a callback so `$` inside `$$...$$` is not interpreted
// as String.replace replacement syntax.
for (const delimiter of [
  /str\.replace\(\/\\\$\\\$\(\[\\s\\S\]\*\?\)\\\$\\\$\/g, stash\)/,
  /str\.replace\(\/\\\\\\\[\(\[\\s\\S\]\*\?\)\\\\\\\]\/g, stash\)/,
  /str\.replace\(\/\\\\\\\(\(\[\\s\\S\]\*\?\)\\\\\\\)\/g, stash\)/,
  /str\.replace\(\/\\\$\(\[\^\$\\n\]\+\?\)\\\$\/g, stash\)/,
]) {
  assert.match(markdownRenderer, delimiter, "renderMd must continue stashing every supported math delimiter");
}
assert.match(
  markdownRenderer,
  /html\s*=\s*html\.replace\(\s*"%%MATH"\s*\+\s*i\s*\+\s*"%%",\s*\(\)\s*=>\s*escHtml\(mathBlocks\[i\]\)\s*\)/,
  "math blocks must be restored as escaped text through a replacement callback",
);
assert.doesNotMatch(
  markdownRenderer,
  /html\s*=\s*html\.replace\(\s*"%%MATH"\s*\+\s*i\s*\+\s*"%%",\s*mathBlocks\[i\]\s*\)/,
  "raw math restoration bypasses sanitizeHtml and mishandles dollar signs",
);

// 5) Execute the production renderer against an actual DOM implementation.
// Static wiring checks cannot detect an identity escHtml implementation; these
// assertions fail if restored formula text can become an element or handler.
const { window } = parseHTML("<!doctype html><html><body></body></html>");
globalThis.window = window;
globalThis.document = window.document;
const { renderMd } = await import("../lib/runtime-bridge/markdown-render.ts");
const cached = renderMd("hello **world**");
assert.equal(renderMd("hello **world**"), cached, "same src must reuse the sanitized HTML");
const unsafeControl = document.createElement("div");
unsafeControl.innerHTML = '$$<img src="x" onerror="window.__workflow_xss=1">$$';
assert.ok(
  unsafeControl.querySelector("img"),
  "unsafe control must prove this DOM test detects raw math restoration",
);
for (const input of [
  '$$<img src="x" onerror="window.__workflow_xss=1">$$',
  '$<img src="x" onerror="window.__workflow_xss=1">$',
  '\\[<img src="x" onerror="window.__workflow_xss=1">\\]',
  '\\(<img src="x" onerror="window.__workflow_xss=1">\\)',
]) {
  const host = document.createElement("div");
  host.innerHTML = renderMd(input);
  assert.equal(host.querySelector("img") !== null, false, `math HTML became an element for ${input.slice(0, 2)}`);
  assert.equal(host.querySelector("[onerror]") !== null, false, `math HTML retained an event handler for ${input.slice(0, 2)}`);
  assert.equal(host.textContent.trim(), input, `math delimiters were not preserved for ${input.slice(0, 2)}`);
}

// SVG SMIL can mutate href after an attribute-only sanitizer has approved the
// original value. Reject the namespace instead of trying to enumerate SVG's
// executable attributes.
for (const input of [
  '<svg><a href="#safe"><text>run</text><animate attributeName="href" values="javascript:window.__smil_xss=1"/></a></svg>',
  '<math><mtext href="javascript:window.__mathml_xss=1">run</mtext></math>',
]) {
  const host = document.createElement("div");
  host.innerHTML = renderMd(input);
  assert.equal(host.querySelector("svg, math, animate") !== null, false, "foreign namespace survived sanitization");
  assert.equal(host.innerHTML.includes("javascript:"), false, "foreign namespace URL survived sanitization");
}

console.log("check-markdown-sanitize: ok");

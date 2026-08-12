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

const root = fileURLToPath(new URL("../", import.meta.url));
const source = (p) => readFileSync(root + p, "utf8");

const helpers = source("lib/runtime-bridge/helpers.ts");

// 1) renderMd — the chat bubble path — must sanitize what marked returns.
assert.match(
  helpers,
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
  ["parses detached so nothing loads", /createElement\("template"\)/],
]) {
  assert.match(helpers, re, `sanitizeHtml no longer ${what}`);
}

// 3) data: URLs are the subtle one — images are fine, text/html is a
//    script vector. The allow-list must not have been widened to bare `data:`.
const safeUrl = helpers.match(/const SAFE_URL = (\/.*\/[a-z]*);/)?.[1];
assert.ok(safeUrl, "SAFE_URL pattern not found");
assert.ok(
  !/\|data:\)/.test(safeUrl) && /data:image\\\//.test(safeUrl),
  `SAFE_URL must allow only data:image/*, got ${safeUrl}`,
);

console.log("check-markdown-sanitize: ok");

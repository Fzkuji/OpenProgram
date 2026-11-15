/**
 * Rule: chat markdown is sanitized at the source. `renderMd` wraps
 * `marked.parse` in `sanitizeHtml` (marked does not sanitize, and tool-relayed
 * content reaches chat bubbles), and `SAFE_URL` allows `data:` only for
 * images. Payload behaviour is in tests/chat/messages/markdown-sanitize.test.mjs.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const renderer = readFileSync(
  new URL("../../lib/runtime-bridge/dom/markdown-render.ts", import.meta.url),
  "utf8",
);

assert.match(
  renderer,
  /sanitizeHtml\(\s*(?:npmMarked|markdown)\.parse\(/,
  "renderMd must wrap marked.parse in sanitizeHtml",
);

const safeUrl = renderer.match(/const SAFE_URL = (\/.*\/[a-z]*);/)?.[1] ?? "";
assert.ok(
  /data:image\\\//.test(safeUrl) && !/data:(?!image)/.test(safeUrl),
  `SAFE_URL must allow only data:image/*, got ${safeUrl || "nothing"}`,
);

console.log("check-markdown-sanitize: ok");

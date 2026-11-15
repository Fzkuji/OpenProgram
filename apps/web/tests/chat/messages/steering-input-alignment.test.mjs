import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const css = readFileSync(new URL("../../../app/styles/chat/messages/stream-blocks.css", import.meta.url), "utf8");

test("a steered input lines up with the other user bubbles", () => {
  // The stream body has no side inset, so the steered user bubble needs
  // no offset of its own, never a fixed pixel one.
  assert.doesNotMatch(css, /\.chat-stream-body\s*\{[^}]*margin-(?:left|right)/);
  const rule = css.match(/\.steering-input\s*\{([^}]*)\}/)?.[1] ?? "";
  assert.doesNotMatch(rule, /-\d+px/);
});

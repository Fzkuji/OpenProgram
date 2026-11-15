// Rule: the turn file-change card is set in the UI font; no rule for a .turn-files-* selector uses the monospace font.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const css = readFileSync(new URL("../../app/styles/chat/cards/turn-files-card.css", import.meta.url), "utf8");
for (const [, selector] of css.matchAll(/([^{}]+)\{[^}]*font-family:\s*var\(--font-mono\)/g)) {
  assert.doesNotMatch(selector, /\.turn-files-/, `mono font in the turn files card: ${selector.trim()}`);
}
console.log("turn files card font checks passed");

// Rule: on the Programs page the monospace font is only for paths (the breadcrumb) and code.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const css = readFileSync(new URL("../../components/programs/programs-page.module.css", import.meta.url), "utf8");
for (const [, selector] of css.matchAll(/([^{}]+)\{[^}]*font-family:\s*var\(--font-mono\)/g)) {
  assert.match(selector.trim(), /^(?:\.breadcrumb|(?:[.\w>\s-]+code,?\s*)+)$/, `mono font outside paths and code: ${selector.trim()}`);
}
console.log("program page font checks passed");

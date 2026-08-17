import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const root = new URL("../", import.meta.url);
const route = readFileSync(new URL("app/(shell)/programs/page.tsx", root), "utf8");
const page = readFileSync(new URL("components/programs/programs-page.tsx", root), "utf8");
const logic = readFileSync(new URL("components/programs/programs-logic.ts", root), "utf8");
const css = readFileSync(new URL("components/programs/programs-page.module.css", root), "utf8");

assert.match(route, /@\/components\/programs\/programs-page/);
assert.match(page, /\/api\/programs\/explorer/);
assert.match(page, /\/api\/programs\/logic/);
assert.match(page, /data-testid="programs-explorer"/);
assert.match(page, /data-testid="programs-call-tree"/);
assert.match(page, /data-testid="programs-call-graph"/);
assert.match(page, /Call tree/);
assert.match(page, /Graph/);
assert.match(logic, /for \(const edge of logic\.edges\)/);
assert.match(logic, /rows\.length >= limit/);
assert.match(page, /logic\.edges\.map/);
assert.doesNotMatch(page, /graphColumns|graphArrow/);
assert.match(page, /cancelled = true/);
assert.match(css, /grid-template-columns:\s*var\(--programs-explorer-width\)\s+minmax\(0,\s*1fr\)/);
assert.doesNotMatch(page, /All Programs|Uncategorized|ProfileNavRow/);

console.log("program workspace checks passed");

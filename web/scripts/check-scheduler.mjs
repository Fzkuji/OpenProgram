import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const page = readFileSync(new URL("../components/scheduler/scheduler-page.tsx", import.meta.url), "utf8");
const sidebar = readFileSync(new URL("../components/sidebar/sidebar.tsx", import.meta.url), "utf8");
const memory = readFileSync(new URL("../components/memory/index.tsx", import.meta.url), "utf8");

assert.match(page, /\/api\/scheduler\/tasks/);
assert.match(page, /\/api\/memory\/refs/);
assert.match(page, /"once" \| "recurring" \| "monitor"/);
assert.match(page, /role="alert"/);
assert.match(page, /if \(!response\.ok\)/);
assert.match(page, /aria-label=/);
assert.match(page, /loadedOnce/);
assert.match(sidebar, /href="\/scheduler"/);
assert.doesNotMatch(memory, /Commitments|commitments/);

console.log("scheduler checks passed");

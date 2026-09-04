import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
const page = readFileSync(new URL("../components/scheduler/scheduler-page.tsx", import.meta.url), "utf8");
const sidebarPrimaryNav = readFileSync(
  new URL("../components/sidebar/sidebar-primary-nav.tsx", import.meta.url),
  "utf8",
);
const memory = readFileSync(new URL("../components/memory/index.tsx", import.meta.url), "utf8");

assert.match(page, /\/api\/scheduler\/tasks/);
assert.match(page, /\/api\/memory\/refs/);
assert.match(page, /"once" \| "recurring" \| "monitor"/);
assert.match(page, /role="alert"/);
assert.match(page, /if \(!response\.ok\)/);
assert.match(page, /aria-label=/);
assert.match(page, /loadedOnce/);
assert.match(page, /ManagePageHeader/);
assert.match(page, /ManageRow/);
assert.match(page, /managePageStyles as shared/);
assert.match(page, /styles\.layout/);
assert.match(page, /taskIndex/);
assert.match(page, /actionAccessibleName/);
assert.match(page, /shouldShowSuggestions/);
assert.doesNotMatch(page, /styles\.intro/);
assert.match(sidebarPrimaryNav, /href:\s*"\/scheduler"/);
assert.doesNotMatch(memory, /Commitments|commitments/);

assert.equal((page.match(/form: \{/g) || []).length, 3);

console.log("scheduler checks passed");

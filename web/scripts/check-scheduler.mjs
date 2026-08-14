import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  actionAccessibleName,
  filterTasks,
  numberedTasks,
  shouldShowSuggestions,
  taskCounts,
} from "../components/scheduler/scheduler-view-model.mjs";

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
assert.match(page, /ManagePageHeader/);
assert.match(page, /ManageRow/);
assert.match(page, /managePageStyles as shared/);
assert.match(page, /shared\.splitBody/);
assert.match(page, /taskIndex/);
assert.match(page, /actionAccessibleName/);
assert.match(page, /shouldShowSuggestions/);
assert.doesNotMatch(page, /styles\.intro/);
assert.match(sidebar, /href="\/scheduler"/);
assert.doesNotMatch(memory, /Commitments|commitments/);

const tasks = [
  { id: "1", title: "Daily brief", type: "recurring", prompt: "Priorities", cron: "0 8 * * 1-5" },
  { id: "2", title: "Follow up", type: "monitor", prompt: "Review reply", cron: "0 9 * * 1-5" },
  { id: "3", title: "Submit form", type: "once", command: "submit", cron: "" },
  { id: "4", title: "Weekly review", type: "recurring", prompt: "Review work", cron: "0 16 * * 5" },
];

assert.deepEqual(taskCounts(tasks), { all: 4, once: 1, recurring: 2, monitor: 1 });
assert.deepEqual(filterTasks(tasks, "recurring", "review").map((task) => task.id), ["4"]);
assert.deepEqual(numberedTasks(filterTasks(tasks, "recurring", "")).map(({ number }) => number), [1, 2]);
assert.equal(shouldShowSuggestions([], "all", ""), true);
assert.equal(shouldShowSuggestions(tasks, "all", ""), false);
assert.equal(shouldShowSuggestions([], "monitor", ""), false);
assert.equal(shouldShowSuggestions([], "all", "daily"), false);
assert.equal(actionAccessibleName("Delete", "Daily brief"), "Delete Daily brief");
assert.equal((page.match(/form: \{/g) || []).length, 3);

console.log("scheduler checks passed");

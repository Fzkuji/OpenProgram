import assert from "node:assert/strict";
import test from "node:test";

import {
  actionAccessibleName,
  filterTasks,
  numberedTasks,
  shouldShowSuggestions,
  taskCounts,
} from "../components/scheduler/scheduler-view-model.mjs";

const tasks = [
  { id: "1", title: "Daily brief", type: "recurring", prompt: "Priorities", cron: "0 8 * * 1-5" },
  { id: "2", title: "Follow up", type: "monitor", prompt: "Review reply", cron: "0 9 * * 1-5" },
  { id: "3", title: "Submit form", type: "once", command: "submit", cron: "" },
  { id: "4", title: "Weekly review", type: "recurring", prompt: "Review work", cron: "0 16 * * 5" },
];

test("scheduler task counts include every task type", () => {
  assert.deepEqual(taskCounts(tasks), { all: 4, once: 1, recurring: 2, monitor: 1 });
});

test("scheduler filters compose type and search queries", () => {
  assert.deepEqual(filterTasks(tasks, "recurring", "review").map((task) => task.id), ["4"]);
  assert.deepEqual(numberedTasks(filterTasks(tasks, "recurring", "")).map(({ number }) => number), [1, 2]);
});

test("scheduler suggestions only appear for an empty unfiltered list", () => {
  assert.equal(shouldShowSuggestions([], "all", ""), true);
  assert.equal(shouldShowSuggestions(tasks, "all", ""), false);
  assert.equal(shouldShowSuggestions([], "monitor", ""), false);
  assert.equal(shouldShowSuggestions([], "all", "daily"), false);
});

test("scheduler actions expose task-specific accessible names", () => {
  assert.equal(actionAccessibleName("Delete", "Daily brief"), "Delete Daily brief");
});

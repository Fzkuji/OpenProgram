import assert from "node:assert/strict";
import test from "node:test";

import {
  actionAccessibleName,
  filterTasks,
  numberedTasks,
  shouldShowSuggestions,
  taskCounts,
  formFromTask,
  taskPayload,
} from "../../components/scheduler/scheduler-view-model.mjs";

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

test("the edit form shows a task's own values in local time", () => {
  const runAt = new Date(2026, 10, 12, 14, 30);
  const form = formFromTask({ id: "t1", title: "Call", type: "once", run_at: runAt.toISOString(), prompt: "Ring", memory_refs: [{ workspace_id: "w", memory_id: "m1" }] });
  assert.deepEqual(form, { title: "Call", type: "once", prompt: "Ring", runAt: "2026-11-12T14:30", cron: "0 9 * * *", memoryId: "m1" });
  const weekly = formFromTask({ id: "t2", title: "Report", type: "recurring", cron: "0 9 * * 1", command: "make report" });
  assert.equal(weekly.cron, "0 9 * * 1");
  assert.equal(weekly.prompt, "");
  assert.equal(weekly.runAt, "");
});

test("a new task sends everything; an existing one only what the server lets change", () => {
  const ref = { workspace_id: "w", memory_id: "m1", topic_path: "a/b", content: "x" };
  const form = { title: "Call", type: "once", prompt: "Ring", runAt: "2026-11-12T14:30", cron: "0 9 * * *", memoryId: "m1" };
  const created = taskPayload(form, ref);
  assert.equal(created.type, "once");
  assert.equal(created.run_at, new Date("2026-11-12T14:30").toISOString());
  assert.deepEqual(created.memory_refs, [{ workspace_id: "w", memory_id: "m1" }]);
  const edited = taskPayload(form, ref, { id: "t1", type: "once", prompt: "Old", memory_refs: [] });
  assert.deepEqual(Object.keys(edited).sort(), ["memory_refs", "prompt", "run_at", "title"], "the type stays");
  const command = taskPayload({ ...form, type: "recurring", cron: "0 8 * * *" }, undefined, { id: "t2", type: "recurring", command: "make report" });
  assert.deepEqual(command, { title: "Call", cron: "0 8 * * *" }, "a command task keeps its command");
  const hook = taskPayload({ ...form, type: "webhook" }, undefined, { id: "t3", type: "webhook", prompt: "On call" });
  assert.deepEqual(Object.keys(hook).sort(), ["memory_refs", "prompt", "title"], "a webhook has no schedule to change");
});

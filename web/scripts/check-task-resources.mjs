import assert from "node:assert/strict";

const {
  queueResourceSummary,
  taskResourceDetails,
} = await import("../lib/task-resource.ts");
const branchItem = await (await import("node:fs/promises")).readFile(
  new URL("../components/right-sidebar/branches/branch-item.tsx", import.meta.url),
  "utf8",
);

const resource = {
  task_id: "task-1",
  status: "queued",
  resource_state: "queued",
  reason_code: "quota.queue_full",
  reason_key: "resource.reason.quota.queue_full",
  retryable: true,
  limits: { scheduler_capacity: 4, limits: {} },
  capacity: {
    scheduler_capacity: 4,
    session_live: { used: 1, limit: 2 },
    session_queued: { used: 2, limit: 8 },
    session_tasks: { used: 3, limit: 100 },
    queue_position: 2,
  },
  budget: {
    scope: "task_with_shared_ancestors",
    tokens: { actual: 12, reserved: 4, limit: 100 },
    cost_usd: {
      actual: "0.38",
      reserved: "0.12",
      limit: "2.00",
      known: true,
      unknown_events: 0,
    },
    runtime_seconds: { used: 91, limit: 600 },
    idle_seconds: { used: 4, limit: 120 },
    shared_remaining: {
      tokens: 70,
      cost_usd: "1.25",
      cost_unknown_events: 0,
    },
  },
};

assert.equal(
  queueResourceSummary(resource),
  "Queue #2 · Session 1/2 live · 2/8 queued · 3/100 tasks · Scheduler 4",
);
assert.deepEqual(taskResourceDetails(resource), [
  { key: "state", value: "queued" },
  { key: "tokens", value: "70" },
  { key: "cost", value: "$1.25" },
  { key: "runtime", value: "509s" },
  { key: "idle", value: "116s" },
  { key: "reason", value: "quota.queue_full" },
]);

const sharedOnly = structuredClone(resource);
sharedOnly.budget.tokens.limit = null;
sharedOnly.budget.cost_usd.limit = null;
sharedOnly.budget.shared_remaining.tokens = 30;
sharedOnly.budget.shared_remaining.cost_usd = "0.30";
assert.deepEqual(taskResourceDetails(sharedOnly).slice(0, 3), [
  { key: "state", value: "queued" },
  { key: "tokens", value: "30" },
  { key: "cost", value: "$0.30" },
]);

const unknownCost = structuredClone(resource);
unknownCost.budget.cost_usd.actual = null;
unknownCost.budget.cost_usd.known = false;
unknownCost.budget.cost_usd.unknown_events = 2;
assert.deepEqual(taskResourceDetails(unknownCost)[2], {
  key: "cost",
  value: "Unknown cost (2 events)",
});

const sharedUnknownCost = structuredClone(resource);
sharedUnknownCost.budget.cost_usd.limit = null;
sharedUnknownCost.budget.shared_remaining.cost_usd = null;
sharedUnknownCost.budget.shared_remaining.cost_unknown_events = 1;
assert.deepEqual(taskResourceDetails(sharedUnknownCost)[2], {
  key: "cost",
  value: "Unknown cost (1 events)",
});

const unlimitedCost = structuredClone(sharedUnknownCost);
unlimitedCost.budget.shared_remaining.cost_unknown_events = 0;
assert.deepEqual(taskResourceDetails(unlimitedCost)[2], {
  key: "cost",
  value: "Unlimited",
});

const unlimitedTime = structuredClone(resource);
unlimitedTime.budget.runtime_seconds.limit = null;
unlimitedTime.budget.idle_seconds.limit = null;
assert.deepEqual(taskResourceDetails(unlimitedTime).slice(3, 5), [
  { key: "runtime", value: "Unlimited" },
  { key: "idle", value: "Unlimited" },
]);

const preciseCost = structuredClone(resource);
preciseCost.budget.cost_usd.limit = "0.000003";
preciseCost.budget.cost_usd.actual = "0.000001";
preciseCost.budget.cost_usd.reserved = "0.000001";
assert.deepEqual(taskResourceDetails(preciseCost)[2], {
  key: "cost",
  value: "$0.000001",
});

assert.equal(queueResourceSummary(undefined), null);
assert.deepEqual(taskResourceDetails(undefined), []);

for (const required of [
  "taskResourceDetails", "<details", "<summary aria-label=", "resourceDetails",
]) {
  assert.ok(branchItem.includes(required), `branch resource details missing: ${required}`);
}

console.log("task-resource checks passed");

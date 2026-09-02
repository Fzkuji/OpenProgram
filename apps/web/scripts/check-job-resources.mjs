import assert from "node:assert/strict";

const {
  queueResourceSummary,
  jobResourceDetails,
} = await import("../lib/job-resource.ts");
const branchItem = await (await import("node:fs/promises")).readFile(
  new URL("../components/right-sidebar/branches/branch-item.tsx", import.meta.url),
  "utf8",
);

const resource = {
  job_id: "job-1",
  execution_id: "job-1",
  status: "queued",
  execution: { reason_code: "quota.queue_full" },
  resource: {
    resource_state: "queued",
    queue_wait: { state: "queued", reason_code: "quota.queue_full", position: 2 },
    limits: {},
    usage: {
    scope: "job_with_shared_ancestors",
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
  },
};

assert.equal(
  queueResourceSummary(resource),
  "Queue #2 · queued",
);
assert.deepEqual(jobResourceDetails(resource), [
  { key: "state", value: "queued" },
  { key: "tokens", value: "70" },
  { key: "cost", value: "$1.25" },
  { key: "runtime", value: "509s" },
  { key: "idle", value: "116s" },
  { key: "reason", value: "quota.queue_full" },
]);

const sharedOnly = structuredClone(resource);
sharedOnly.resource.usage.tokens.limit = null;
sharedOnly.resource.usage.cost_usd.limit = null;
sharedOnly.resource.usage.shared_remaining.tokens = 30;
sharedOnly.resource.usage.shared_remaining.cost_usd = "0.30";
assert.deepEqual(jobResourceDetails(sharedOnly).slice(0, 3), [
  { key: "state", value: "queued" },
  { key: "tokens", value: "30" },
  { key: "cost", value: "$0.30" },
]);

const unknownCost = structuredClone(resource);
unknownCost.resource.usage.cost_usd.actual = null;
unknownCost.resource.usage.cost_usd.known = false;
unknownCost.resource.usage.cost_usd.unknown_events = 2;
assert.deepEqual(jobResourceDetails(unknownCost)[2], {
  key: "cost",
  value: "Unknown cost (2 events)",
});

const sharedUnknownCost = structuredClone(resource);
sharedUnknownCost.resource.usage.cost_usd.limit = null;
sharedUnknownCost.resource.usage.shared_remaining.cost_usd = null;
sharedUnknownCost.resource.usage.shared_remaining.cost_unknown_events = 1;
assert.deepEqual(jobResourceDetails(sharedUnknownCost)[2], {
  key: "cost",
  value: "Unknown cost (1 events)",
});

const unlimitedCost = structuredClone(sharedUnknownCost);
unlimitedCost.resource.usage.shared_remaining.cost_unknown_events = 0;
assert.deepEqual(jobResourceDetails(unlimitedCost)[2], {
  key: "cost",
  value: "Unlimited",
});

const unlimitedTime = structuredClone(resource);
unlimitedTime.resource.usage.runtime_seconds.limit = null;
unlimitedTime.resource.usage.idle_seconds.limit = null;
assert.deepEqual(jobResourceDetails(unlimitedTime).slice(3, 5), [
  { key: "runtime", value: "Unlimited" },
  { key: "idle", value: "Unlimited" },
]);

const preciseCost = structuredClone(resource);
preciseCost.resource.usage.cost_usd.limit = "0.000003";
preciseCost.resource.usage.cost_usd.actual = "0.000001";
preciseCost.resource.usage.cost_usd.reserved = "0.000001";
assert.deepEqual(jobResourceDetails(preciseCost)[2], {
  key: "cost",
  value: "$0.000001",
});

assert.equal(queueResourceSummary(undefined), null);
assert.deepEqual(jobResourceDetails(undefined), []);

for (const required of [
  "jobResourceDetails", "<details", "<summary aria-label=", "resourceDetails",
]) {
  assert.ok(branchItem.includes(required), `branch resource details missing: ${required}`);
}

console.log("job-resource checks passed");

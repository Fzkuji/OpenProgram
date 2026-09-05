const assert = require("node:assert/strict");
const {
  createRecoveryState,
  createRecoveryCoordinator,
  recordWorkerCommandExit,
  startRecoveryCycle,
  beginRecoveryProbe,
  finishRecoveryProbe,
} = require("../worker-recovery-state");

const state = createRecoveryState();
const coordinator = createRecoveryCoordinator();
startRecoveryCycle(state);
assert.equal(beginRecoveryProbe(state, 0), true);
assert.equal(beginRecoveryProbe(state, 0), false, "overlapping probes must be skipped");
assert.equal(finishRecoveryProbe(state, coordinator, false, false, 0), "spawn");

assert.equal(beginRecoveryProbe(state, 3_000), true);
assert.equal(
  finishRecoveryProbe(state, coordinator, false, false, 3_000),
  null,
  "a recovery cycle must spawn the worker at most once",
);

recordWorkerCommandExit(coordinator, "start", 1);
assert.equal(beginRecoveryProbe(state, 6_000), true);
assert.equal(
  finishRecoveryProbe(state, coordinator, false, false, 6_000),
  null,
  "a rejected start must allow a grace period for a busy worker",
);
assert.equal(beginRecoveryProbe(state, 9_000), true);
assert.equal(
  finishRecoveryProbe(state, coordinator, false, false, 9_000),
  "restart",
  "a live but unresponsive worker is restarted after four failed probes",
);
assert.equal(coordinator.restartIssued, true);
assert.equal(beginRecoveryProbe(state, 12_000), true);
assert.equal(
  finishRecoveryProbe(state, coordinator, true, true, 12_000),
  "load",
);
assert.equal(coordinator.startRejected, false);
assert.equal(coordinator.unreachableProbes, 0);

startRecoveryCycle(state);
assert.equal(beginRecoveryProbe(state, 15_000), true);
assert.equal(
  finishRecoveryProbe(state, coordinator, true, false, 15_000),
  null,
  "a reachable worker without an authenticated URL must keep waiting",
);

assert.equal(beginRecoveryProbe(state, 18_000), true);
assert.equal(finishRecoveryProbe(state, coordinator, true, true, 18_000), "load");
assert.equal(state.active, false);
assert.equal(beginRecoveryProbe(state, 18_000), false);

startRecoveryCycle(state);
assert.equal(
  beginRecoveryProbe(state, 18_000),
  false,
  "a failed recovered navigation must wait before probing again",
);
assert.equal(beginRecoveryProbe(state, 21_000), true);
assert.equal(
  finishRecoveryProbe(state, coordinator, false, false, 21_000),
  "spawn",
  "a new recovery cycle may start the worker once",
);

const firstWindow = createRecoveryState();
const secondWindow = createRecoveryState();
const sharedCoordinator = createRecoveryCoordinator();
startRecoveryCycle(firstWindow);
startRecoveryCycle(secondWindow);
assert.equal(beginRecoveryProbe(firstWindow, 0), true);
assert.equal(beginRecoveryProbe(secondWindow, 0), true);
assert.deepEqual(
  [
    finishRecoveryProbe(firstWindow, sharedCoordinator, false, false, 0),
    finishRecoveryProbe(secondWindow, sharedCoordinator, false, false, 0),
  ],
  ["spawn", null],
  "two windows in the same backend outage must spawn the worker once",
);

console.log("worker recovery checks passed");

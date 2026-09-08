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

const failedRestartState = createRecoveryState();
const failedRestartCoordinator = createRecoveryCoordinator();
startRecoveryCycle(failedRestartState);
const failedActions = [];
for (let i = 0; i < 20; i += 1) {
  assert.equal(beginRecoveryProbe(failedRestartState, i * 3_000), true);
  const action = finishRecoveryProbe(
    failedRestartState, failedRestartCoordinator, false, false, i * 3_000,
  );
  if (action) {
    failedActions.push(action);
    recordWorkerCommandExit(failedRestartCoordinator, action === "spawn" ? "start" : "restart", 1);
  }
}
assert.deepEqual(
  failedActions, ["spawn", "restart"],
  "a failed restart must not repeatedly kill a worker waiting for system access",
);
assert.equal(beginRecoveryProbe(failedRestartState, 60_000), true);
assert.equal(
  finishRecoveryProbe(failedRestartState, failedRestartCoordinator, true, true, 60_000), "load",
  "health checks must recover the page after the worker becomes available",
);
assert.equal(failedRestartCoordinator.restartIssued, false);
startRecoveryCycle(failedRestartState);
assert.equal(beginRecoveryProbe(failedRestartState, 63_000), true);
assert.equal(
  finishRecoveryProbe(failedRestartState, failedRestartCoordinator, false, false, 63_000), "spawn",
  "verified recovery resets the allowance for a later outage",
);

console.log("worker recovery checks passed");

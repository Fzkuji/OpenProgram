const assert = require("node:assert/strict");
const {
  UNRESPONSIVE_AFTER_MS,
  createRecoveryState,
  createRecoveryCoordinator,
  recordWorkerCommandExit,
  startRecoveryCycle,
  beginRecoveryProbe,
  finishRecoveryProbe,
  workerVersionAction,
} = require("../main/worker/worker-recovery-state");

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

// While an installer holds the App install lock the backend is down on
// purpose: the App neither starts nor restarts a worker, and the outage
// clock restarts once the installer is gone.
const installState = createRecoveryState();
const installCoordinator = createRecoveryCoordinator();
startRecoveryCycle(installState);
for (let i = 0; i < 30; i += 1) {
  assert.equal(beginRecoveryProbe(installState, i * 1_000), true);
  assert.equal(
    finishRecoveryProbe(installState, installCoordinator, false, false, i * 1_000, 1_000, true),
    "installing",
    "no worker is started while the App is being installed",
  );
}
assert.equal(installCoordinator.workerSpawned, false);
assert.equal(installCoordinator.unreachableProbes, 0);
assert.equal(beginRecoveryProbe(installState, 30_000), true);
assert.equal(
  finishRecoveryProbe(installState, installCoordinator, false, false, 30_000, 1_000),
  "spawn",
  "once the installer is gone the worker is started as usual",
);
recordWorkerCommandExit(installCoordinator, "start", 1);
// The spawn probe was the first unreachable one; the restart comes once
// UNRESPONSIVE_AFTER_MS of one-second probes have failed.
const restartProbe = 30 + UNRESPONSIVE_AFTER_MS / 1_000 - 1;
for (let i = 31; i < restartProbe; i += 1) {
  assert.equal(beginRecoveryProbe(installState, i * 1_000), true);
  assert.equal(
    finishRecoveryProbe(installState, installCoordinator, false, false, i * 1_000, 1_000),
    null,
    `a live worker gets ${UNRESPONSIVE_AFTER_MS / 1_000}s to boot at a 1s cadence (probe ${i})`,
  );
}
assert.equal(beginRecoveryProbe(installState, restartProbe * 1_000), true);
assert.equal(
  finishRecoveryProbe(installState, installCoordinator, false, false, restartProbe * 1_000, 1_000),
  "restart",
  "the restart allowance is a duration, not a probe count",
);
assert.equal(beginRecoveryProbe(installState, (restartProbe + 1) * 1_000), true);
assert.equal(
  finishRecoveryProbe(installState, installCoordinator, true, true, (restartProbe + 1) * 1_000, 1_000, true),
  "load",
  "a reachable worker loads even while the lock is still held",
);

// An App update leaves the old background worker running. The App replaces it
// once, when it can tell the worker is from another version.
{
  const coordinator = createRecoveryCoordinator();
  assert.equal(workerVersionAction(coordinator, null, "1.0.1"), null, "an unknown version proves nothing");
  assert.equal(workerVersionAction(coordinator, "unknown", "1.0.1"), null);
  assert.equal(coordinator.workerVersionChecked, false, "an unknown answer may be asked again");
  assert.equal(workerVersionAction(coordinator, "1.0.0", null), null, "a development App keeps any worker");
  assert.equal(workerVersionAction(coordinator, "1.0.0", "1.0.1"), "restart");
  assert.equal(workerVersionAction(coordinator, "1.0.0", "1.0.1"), null, "one restart per launch, never a loop");

  const matching = createRecoveryCoordinator();
  assert.equal(workerVersionAction(matching, "1.0.1", "1.0.1"), null);
  assert.equal(matching.workerVersionChecked, true);

  const source = require("node:fs").readFileSync(require.resolve("../main.js"), "utf8");
  assert.match(source, /if \(await restartWorkerFromOtherVersion\(authenticated\)\) return errorPageUrl\(\);/,
    "the first window waits while the other version's worker is replaced");
  assert.match(source, /spawnWorker\("restart"\);\n  return true;/);
  assert.match(source, /await probe\(HEALTH_URL, 1000\) && await workerVersionSettled\(\)/,
    "the old worker's health answer does not count while it is being replaced");
  assert.match(source, /INSTANCE\.development \|\| !app\.isPackaged \|\| process\.env\.OPENPROGRAM_DESKTOP_URL/,
    "development instances and a pointed-at server are left alone");
}

console.log("worker recovery checks passed");

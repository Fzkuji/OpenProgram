const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "..", "main.js"), "utf8");
const helpers = source.match(
  /\/\/ BEGIN WORKER RECOVERY STATE\n([\s\S]*?)\/\/ END WORKER RECOVERY STATE/,
);
assert.ok(helpers, "worker recovery state helpers are missing from main.js");

const sandbox = { module: { exports: {} } };
vm.runInNewContext(
  `${helpers[1]}\nmodule.exports = {` +
    "createRecoveryState, createRecoveryCoordinator, startRecoveryCycle, " +
    "beginRecoveryProbe, finishRecoveryProbe" +
    "};",
  sandbox,
  { filename: "desktop/main.js#worker-recovery-state" },
);

const {
  createRecoveryState,
  createRecoveryCoordinator,
  startRecoveryCycle,
  beginRecoveryProbe,
  finishRecoveryProbe,
} = sandbox.module.exports;

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

assert.equal(beginRecoveryProbe(state, 6_000), true);
assert.equal(
  finishRecoveryProbe(state, coordinator, true, false, 6_000),
  null,
  "a reachable worker without an authenticated URL must keep waiting",
);

assert.equal(beginRecoveryProbe(state, 9_000), true);
assert.equal(finishRecoveryProbe(state, coordinator, true, true, 9_000), "load");
assert.equal(state.active, false);
assert.equal(beginRecoveryProbe(state, 9_000), false);

startRecoveryCycle(state);
assert.equal(
  beginRecoveryProbe(state, 9_000),
  false,
  "a failed recovered navigation must wait before probing again",
);
assert.equal(beginRecoveryProbe(state, 12_000), true);
assert.equal(
  finishRecoveryProbe(state, coordinator, false, false, 12_000),
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

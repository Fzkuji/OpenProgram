function createRecoveryState() {
  return { active: false, probeInFlight: false, nextProbeAt: 0, timer: null };
}

function createRecoveryCoordinator() {
  return {
    workerSpawned: false,
    startRejected: false,
    restartIssued: false,
    unreachableProbes: 0,
    workerVersionChecked: false,
  };
}

/**
 * Whether the worker already running belongs to another version of this App,
 * as after an App update, when the old background process keeps serving. Asked
 * once per App launch, so a version that never matches cannot cause a loop.
 */
function workerVersionAction(coordinator, runningVersion, expectedVersion) {
  if (coordinator.workerVersionChecked || !expectedVersion) return null;
  if (!/^\d+\.\d+/.test(String(runningVersion || ""))) return null;
  coordinator.workerVersionChecked = true;
  return runningVersion === expectedVersion ? null : "restart";
}

function recordWorkerCommandExit(coordinator, action, exitCode) {
  if (exitCode === 0) return;
  coordinator.workerSpawned = false;
  if (action === "start") coordinator.startRejected = true;
  // A failed restart can leave the worker waiting for system authorization.
  // Only a successful health probe renews the forced-restart allowance.
}

function startRecoveryCycle(state) {
  if (state.active) return;
  state.active = true;
  state.probeInFlight = false;
}

function beginRecoveryProbe(state, now) {
  if (!state.active || state.probeInFlight || now < state.nextProbeAt) return false;
  state.probeInFlight = true;
  return true;
}

// How long a live worker may stay unreachable after a rejected start before
// it is restarted: long enough for a cold boot (restore, channels, MCP).
const UNRESPONSIVE_AFTER_MS = 12_000;

/**
 * Decide what a finished health probe calls for. `installing` says an
 * installer holds the App install lock: the backend is down because it is
 * being replaced, so the App only waits for it (starting a worker from a
 * half-installed runtime would run stale code, and the installer boots it
 * out anyway).
 */
function finishRecoveryProbe(
  state,
  coordinator,
  reachable,
  hasAuthenticatedUrl,
  now,
  retryIntervalMs = 3_000,
  installing = false,
) {
  state.probeInFlight = false;
  if (!state.active) return null;
  state.nextProbeAt = now + retryIntervalMs;
  if (reachable) {
    coordinator.workerSpawned = false;
    coordinator.startRejected = false;
    coordinator.restartIssued = false;
    coordinator.unreachableProbes = 0;
  } else if (installing) {
    // The outage is the installer's; its end starts the clock afresh.
    coordinator.unreachableProbes = 0;
    return "installing";
  } else {
    coordinator.unreachableProbes += 1;
  }
  if (reachable && hasAuthenticatedUrl) {
    state.active = false;
    return "load";
  }
  if (
    !reachable
    && !coordinator.workerSpawned
    && coordinator.startRejected
  ) {
    // ``worker start`` exits non-zero when the PID is alive. Failed health
    // probes over UNRESPONSIVE_AFTER_MS distinguish a genuinely unresponsive
    // worker from a temporarily busy/startup phase.
    const probesBeforeRestart = Math.max(1, Math.ceil(UNRESPONSIVE_AFTER_MS / retryIntervalMs));
    if (coordinator.unreachableProbes >= probesBeforeRestart && !coordinator.restartIssued) {
      coordinator.workerSpawned = true;
      coordinator.restartIssued = true;
      return "restart";
    }
    return null;
  }
  if (!reachable && !coordinator.workerSpawned) {
    coordinator.workerSpawned = true;
    return "spawn";
  }
  return null;
}

module.exports = {
  UNRESPONSIVE_AFTER_MS,
  createRecoveryState,
  createRecoveryCoordinator,
  recordWorkerCommandExit,
  startRecoveryCycle,
  beginRecoveryProbe,
  finishRecoveryProbe,
  workerVersionAction,
};

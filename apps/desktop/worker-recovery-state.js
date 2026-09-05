function createRecoveryState() {
  return { active: false, probeInFlight: false, nextProbeAt: 0, timer: null };
}

function createRecoveryCoordinator() {
  return {
    workerSpawned: false,
    startRejected: false,
    restartIssued: false,
    unreachableProbes: 0,
  };
}

function recordWorkerCommandExit(coordinator, action, exitCode) {
  if (exitCode === 0) return;
  coordinator.workerSpawned = false;
  if (action === "start") coordinator.startRejected = true;
  if (action === "restart") {
    coordinator.restartIssued = false;
    coordinator.unreachableProbes = 0;
  }
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

function finishRecoveryProbe(
  state,
  coordinator,
  reachable,
  hasAuthenticatedUrl,
  now,
  retryIntervalMs = 3_000,
) {
  state.probeInFlight = false;
  if (!state.active) return null;
  state.nextProbeAt = now + retryIntervalMs;
  if (reachable) {
    coordinator.workerSpawned = false;
    coordinator.startRejected = false;
    coordinator.restartIssued = false;
    coordinator.unreachableProbes = 0;
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
    // ``worker start`` exits non-zero when the PID is alive.  Four failed
    // health probes (~12 seconds at the production cadence) distinguish a
    // genuinely unresponsive worker from a temporarily busy/startup phase.
    if (coordinator.unreachableProbes >= 4 && !coordinator.restartIssued) {
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
  createRecoveryState,
  createRecoveryCoordinator,
  recordWorkerCommandExit,
  startRecoveryCycle,
  beginRecoveryProbe,
  finishRecoveryProbe,
};

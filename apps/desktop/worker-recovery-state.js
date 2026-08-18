function createRecoveryState() {
  return { active: false, probeInFlight: false, nextProbeAt: 0, timer: null };
}

function createRecoveryCoordinator() {
  return { workerSpawned: false };
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
  if (reachable) coordinator.workerSpawned = false;
  if (reachable && hasAuthenticatedUrl) {
    state.active = false;
    return "load";
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
  startRecoveryCycle,
  beginRecoveryProbe,
  finishRecoveryProbe,
};

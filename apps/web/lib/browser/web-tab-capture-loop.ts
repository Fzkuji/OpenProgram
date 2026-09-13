export type WebTabCaptureTarget = { tabId: string; generation: number };

export type WebTabCaptureLoop = {
  stop: () => void;
  pause: () => void;
  resume: () => void;
};

export function startWebTabCaptureLoop(options: {
  tabId: string;
  generation: number;
  isCurrent: () => WebTabCaptureTarget | null;
  capture: (tabId: string) => Promise<string | null>;
  onFrame: (tabId: string, dataUrl: string) => void;
  onUnavailable: (tabId: string) => void;
  intervalMs?: number;
  schedule?: (fn: () => void, ms: number) => unknown;
  cancel?: (handle: unknown) => void;
}): WebTabCaptureLoop {
  const intervalMs = options.intervalMs ?? 400;
  const schedule = options.schedule ?? ((fn, ms) => setTimeout(fn, ms));
  const cancel = options.cancel ?? ((handle) => clearTimeout(handle as ReturnType<typeof setTimeout>));
  let stopped = false;
  let paused = false;
  let timer: unknown;
  let inFlight = false;
  let epoch = 0;

  const stale = () => {
    if (stopped) return true;
    const current = options.isCurrent();
    return !current || current.tabId !== options.tabId || current.generation !== options.generation;
  };

  const clearTimer = () => {
    if (timer !== undefined) cancel(timer);
    timer = undefined;
  };

  const settle = (startedEpoch: number, apply: () => void) => {
    inFlight = false;
    if (stopped || stale()) return;
    if (startedEpoch !== epoch) {
      if (!paused) tick();
      return;
    }
    if (paused) return;
    apply();
    if (!stale() && !paused) timer = schedule(tick, intervalMs);
  };

  const tick = () => {
    if (stale() || paused || inFlight) return;
    const startedEpoch = epoch;
    inFlight = true;
    void options.capture(options.tabId).then((dataUrl) => {
      settle(startedEpoch, () => {
        if (dataUrl) options.onFrame(options.tabId, dataUrl);
        else options.onUnavailable(options.tabId);
      });
    }, () => {
      settle(startedEpoch, () => {
        options.onUnavailable(options.tabId);
      });
    });
  };

  tick();
  return {
    stop() {
      stopped = true;
      paused = false;
      epoch += 1;
      clearTimer();
    },
    pause() {
      if (stopped) return;
      paused = true;
      epoch += 1;
      clearTimer();
    },
    resume() {
      if (stopped || !paused) return;
      paused = false;
      if (!inFlight) tick();
    },
  };
}

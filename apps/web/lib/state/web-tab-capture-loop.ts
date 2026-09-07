export type WebTabCaptureTarget = { tabId: string; generation: number };

export type WebTabCaptureLoop = { stop: () => void };

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
  let timer: unknown;
  let inFlight = false;

  const stale = () => {
    if (stopped) return true;
    const current = options.isCurrent();
    return !current || current.tabId !== options.tabId || current.generation !== options.generation;
  };

  const tick = () => {
    if (stale() || inFlight) return;
    inFlight = true;
    void options.capture(options.tabId).then((dataUrl) => {
      inFlight = false;
      if (stale()) return;
      if (dataUrl) options.onFrame(options.tabId, dataUrl);
      else options.onUnavailable(options.tabId);
      if (!stale()) timer = schedule(tick, intervalMs);
    }, () => {
      inFlight = false;
      if (stale()) return;
      options.onUnavailable(options.tabId);
      if (!stale()) timer = schedule(tick, intervalMs);
    });
  };

  tick();
  return {
    stop() {
      stopped = true;
      if (timer !== undefined) cancel(timer);
    },
  };
}

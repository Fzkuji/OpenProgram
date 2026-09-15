import type { HistoryDirection } from './history-window';
import type { HistoryState } from './session-history';

/** Prefetch only around the visible transcript's edges. */
export function startHistoryAutoload(
  area: HTMLElement,
  source: {
    read: () => HistoryState | undefined;
    subscribe: (changed: () => void) => () => void;
    load: (direction: HistoryDirection) => Promise<unknown>;
  },
): () => void {
  let stopped = false;
  let busy = false;
  let frame = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let failures = 0;
  let generation: number | undefined;
  let lastTop = area.scrollTop;
  let lastTime = performance.now();
  let velocity = 0;
  let latency = 750;

  const schedule = () => {
    if (!stopped && !frame) frame = requestAnimationFrame(check);
  };
  const check = () => {
    frame = 0;
    if (stopped) return;
    const page = source.read();
    if (page?.generation !== generation) {
      generation = page?.generation;
      failures = 0;
      clearTimeout(timer);
      timer = undefined;
    }
    if (page?.loading) {
      failures = 0;
      clearTimeout(timer);
      timer = undefined;
      return;
    }
    const now = performance.now();
    const elapsed = Math.max(16, now - lastTime);
    const delta = area.scrollTop - lastTop;
    if (delta) velocity = delta / elapsed;
    else velocity *= 0.5;
    lastTop = area.scrollTop; lastTime = now;
    const ahead = Math.min(12_000, Math.max(1600, area.clientHeight * 2, Math.abs(velocity) * latency * 1.5));
    const bottom = area.scrollHeight - area.clientHeight - area.scrollTop;
    const older = !!page?.before && area.scrollTop <= ahead;
    const newer = !!page?.after && bottom <= ahead;
    const direction: HistoryDirection | null = older && (!newer || velocity < 0 || area.scrollTop <= bottom) ? 'older' : newer ? 'newer' : null;
    if (busy || timer !== undefined || !page || !direction || page.loading
        || area.clientHeight <= 0 || document.visibilityState === 'hidden') return;
    busy = true;
    const started = performance.now();
    // The data layer owns generation/head validation; this controller owns
    // retries, including a successful response that made no cursor progress.
    void source.load(direction).catch(() => {}).finally(() => {
      busy = false;
      lastTop = area.scrollTop; lastTime = performance.now(); velocity = 0;
      latency = Math.min(5000, Math.max(250, (latency + performance.now() - started) / 2));
      if (stopped) return;
      const next = source.read();
      if (next?.loading) {
        schedule();
        return;
      }
      if (next?.generation === page.generation
          && (next.error || (direction === 'older' ? next.before === page.before : next.after === page.after))) {
        const delay = Math.min(30_000, 1000 * 2 ** Math.min(failures++, 5));
        timer = setTimeout(() => { timer = undefined; schedule(); }, delay);
      } else {
        failures = 0;
        schedule();
      }
    });
  };
  const unsubscribe = source.subscribe(schedule);
  const resize = new ResizeObserver(schedule);
  resize.observe(area);
  area.addEventListener('scroll', schedule, { passive: true });
  document.addEventListener('visibilitychange', schedule);
  const recover = () => {
    if (document.visibilityState === 'hidden') return;
    failures = 0;
    clearTimeout(timer);
    timer = undefined;
    schedule();
  };
  window.addEventListener('online', recover);
  window.addEventListener('op:browser-connection', recover);
  schedule();
  return () => {
    stopped = true;
    unsubscribe();
    resize.disconnect();
    area.removeEventListener('scroll', schedule);
    document.removeEventListener('visibilitychange', schedule);
    window.removeEventListener('online', recover);
    window.removeEventListener('op:browser-connection', recover);
    if (frame) cancelAnimationFrame(frame);
    clearTimeout(timer);
  };
}

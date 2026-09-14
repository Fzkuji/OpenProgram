import type { HistoryState } from './session-history';

/** Prefetch only around the visible transcript's upper edge. */
export function startHistoryAutoload(
  area: HTMLElement,
  source: {
    read: () => HistoryState | undefined;
    subscribe: (changed: () => void) => () => void;
    load: () => Promise<void>;
  },
): () => void {
  let stopped = false;
  let busy = false;
  let frame = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let failures = 0;
  let generation: number | undefined;

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
    if (busy || timer !== undefined || !page?.before || page.loading
        || area.clientHeight <= 0 || document.visibilityState === 'hidden'
        || area.scrollTop > Math.max(600, area.clientHeight)) return;
    busy = true;
    // The data layer owns generation/head validation; this controller owns
    // retries, including a successful response that made no cursor progress.
    void source.load().catch(() => {}).finally(() => {
      busy = false;
      if (stopped) return;
      const next = source.read();
      if (next?.generation === page.generation
          && (next.error || next.before === page.before)) {
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
  window.addEventListener('online', schedule);
  schedule();
  return () => {
    stopped = true;
    unsubscribe();
    resize.disconnect();
    area.removeEventListener('scroll', schedule);
    document.removeEventListener('visibilitychange', schedule);
    window.removeEventListener('online', schedule);
    if (frame) cancelAnimationFrame(frame);
    clearTimeout(timer);
  };
}

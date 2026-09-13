export const SPLIT_CHAT_MIN_WIDTH = 360;
export const SPLIT_WEB_MIN_WIDTH = 480;
export const SPLIT_DIVIDER_WIDTH = 6;

export function createSplitLayoutMeasureScheduler(
  measure: () => void,
  timing: {
    requestFrame: (callback: () => void) => number;
    cancelFrame: (id: number) => void;
    setTimer: (callback: () => void, delay: number) => number;
    clearTimer: (id: number) => void;
  } = {
    requestFrame: (callback) => requestAnimationFrame(callback),
    cancelFrame: (id) => cancelAnimationFrame(id),
    setTimer: (callback, delay) => window.setTimeout(callback, delay),
    clearTimer: (id) => window.clearTimeout(id),
  },
) {
  let frame: number | null = null;
  let timer: number | null = null;
  const cancel = () => {
    if (frame !== null) timing.cancelFrame(frame);
    if (timer !== null) timing.clearTimer(timer);
    frame = null;
    timer = null;
  };
  const run = () => {
    cancel();
    measure();
  };
  const schedule = () => {
    cancel();
    frame = timing.requestFrame(run);
    timer = timing.setTimer(run, 0);
  };
  return { schedule, cancel };
}

export function isSplitLayoutAvailable(width: number): boolean {
  return (
    width >= SPLIT_CHAT_MIN_WIDTH + SPLIT_WEB_MIN_WIDTH + SPLIT_DIVIDER_WIDTH
  );
}

export function clampSplitRatioForWidth(ratio: number, width: number): number {
  if (width <= 0) return ratio;
  const minRatio = SPLIT_CHAT_MIN_WIDTH / width;
  const maxRatio = (width - SPLIT_WEB_MIN_WIDTH - SPLIT_DIVIDER_WIDTH) / width;
  return Math.min(maxRatio, Math.max(minRatio, ratio));
}

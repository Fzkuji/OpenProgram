type ScheduleFrame = (callback: FrameRequestCallback) => number;
type CancelFrame = (handle: number) => void;

export function afterTwoAnimationFrames(
  callback: () => void,
  schedule: ScheduleFrame = requestAnimationFrame,
  cancel: CancelFrame = cancelAnimationFrame,
) {
  let cancelled = false;
  let secondFrame = 0;
  const firstFrame = schedule(() => {
    if (cancelled) return;
    secondFrame = schedule(() => {
      if (!cancelled) callback();
    });
  });

  return () => {
    cancelled = true;
    cancel(firstFrame);
    if (secondFrame) cancel(secondFrame);
  };
}

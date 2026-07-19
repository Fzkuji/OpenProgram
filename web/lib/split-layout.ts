export const SPLIT_CHAT_MIN_WIDTH = 360;
export const SPLIT_WEB_MIN_WIDTH = 480;
export const SPLIT_DIVIDER_WIDTH = 6;

export function clampSplitRatioForWidth(ratio: number, width: number): number {
  if (width <= 0) return ratio;
  const minRatio = SPLIT_CHAT_MIN_WIDTH / width;
  const maxRatio = (width - SPLIT_WEB_MIN_WIDTH - SPLIT_DIVIDER_WIDTH) / width;
  return Math.min(maxRatio, Math.max(minRatio, ratio));
}

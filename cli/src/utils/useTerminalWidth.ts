import { useEffect, useState } from 'react';
import { useStdout } from 'ink';

/** Hard cap so the panel doesn't stretch into a thin strip on a 200-col window. */
export const MAX_PANEL_WIDTH = 100;

/**
 * Returns the current terminal column count and re-renders when the
 * window resizes. Falls back to 80 if stdout can't report a size.
 */
export function useTerminalWidth(): number {
  const { stdout } = useStdout();
  const [cols, setCols] = useState<number>(stdout?.columns ?? 80);

  useEffect(() => {
    if (!stdout) return;
    const handler = () => setCols(stdout.columns ?? 80);
    stdout.on('resize', handler);
    return () => {
      stdout.off('resize', handler);
    };
  }, [stdout]);

  return cols;
}

/**
 * Width every top-level panel uses so Welcome / input box / bottom bar
 * line up edge-to-edge.
 */
export function usePanelWidth(): number {
  const cols = useTerminalWidth();
  return Math.min(cols, MAX_PANEL_WIDTH);
}

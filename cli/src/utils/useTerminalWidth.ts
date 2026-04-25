import { useEffect, useState } from 'react';
import { useStdout } from 'ink';

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

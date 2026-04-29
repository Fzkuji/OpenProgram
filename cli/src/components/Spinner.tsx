import React, { useEffect, useState } from 'react';
import { Box, Text } from '../runtime/index';
import { useColors } from '../theme/ThemeProvider.js';
import { usePanelWidth } from '../utils/useTerminalWidth.js';

const FRAMES = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'];

export interface SpinnerProps {
  /** What the agent is currently doing. */
  verb: string;
  /** Optional secondary line — usually the tool input or token preview. */
  detail?: string;
  /** Elapsed seconds since the turn started. */
  elapsed?: number;
}

export const Spinner: React.FC<SpinnerProps> = ({ verb, detail, elapsed }) => {
  const colors = useColors();
  const width = usePanelWidth();
  const [frame, setFrame] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setFrame((f) => (f + 1) % FRAMES.length), 80);
    return () => clearInterval(t);
  }, []);

  return (
    <Box paddingX={1} flexShrink={0}>
      <Text color={colors.warning}>{FRAMES[frame]} </Text>
      <Text color={colors.text}>{verb}</Text>
      {typeof elapsed === 'number' ? (
        <Text color={colors.muted}> ({elapsed.toFixed(0)}s)</Text>
      ) : null}
      {detail ? (
        <>
          <Text color={colors.border}> · </Text>
          <Box width={Math.max(8, width - verb.length - 16)} flexShrink={1}>
            <Text color={colors.muted} wrap="truncate-end">{detail}</Text>
          </Box>
        </>
      ) : null}
    </Box>
  );
};

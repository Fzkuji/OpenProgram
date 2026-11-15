import React from 'react';
import { Box, Text } from '../../runtime/index';
import { useColors } from '../../theme/ThemeProvider.js';
import { previewLine, type QueuedMessage } from '../../utils/messageQueue.js';

/**
 * The messages waiting for the running turn, one dim row each above the
 * input (Codex's `↳` preview). The selected row, while ↑ has moved into
 * the queue, takes the accent colour.
 */
export const QueuedMessages: React.FC<{
  queued: QueuedMessage[];
  selected: number | null;
  width: number;
}> = ({ queued, selected, width }) => {
  const colors = useColors();
  if (!queued.length) return null;
  return (
    <Box flexDirection="column" width={width} paddingX={1} flexShrink={0}>
      {queued.map((q, i) => {
        const { line, more } = previewLine(q.text);
        const active = selected === i;
        return (
          <Box key={q.id}>
            <Text color={active ? colors.accent : colors.border}>{active ? '› ' : '↳ '}</Text>
            <Text color={active ? colors.text : colors.muted} bold={active} wrap="truncate-end">
              {line || ' '}
            </Text>
            {more > 0 ? <Text color={colors.border}>{` +${more} line${more === 1 ? '' : 's'}`}</Text> : null}
          </Box>
        );
      })}
    </Box>
  );
};

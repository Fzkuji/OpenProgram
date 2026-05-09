import React from 'react';
import { Box, Text } from '../runtime/index';
import { useColors } from '../theme/ThemeProvider.js';
import { usePanelWidth } from '../utils/useTerminalWidth.js';
import type { ChannelActivity } from '../screens/repl/types.js';

export interface ChannelActivityFeedProps {
  activities: Record<string, ChannelActivity>;
  currentConvId?: string;
}

const MAX_ROWS = 3;
const STALE_MS = 30_000;

function formatRow(a: ChannelActivity, width: number): {
  prefix: string;
  body: string;
  streaming: boolean;
} {
  const src = a.source ?? 'channel';
  const peer = a.peerDisplay ? `:${a.peerDisplay}` : '';
  const tail = a.streaming
    ? (a.streamingText || '').replace(/\s+/g, ' ')
    : (a.finalText ?? a.streamingText ?? '').replace(/\s+/g, ' ');
  const prefix = `${a.streaming ? '◐' : '✓'} ${src}${peer}`;
  const userPart = a.userText ? ` « ${a.userText.replace(/\s+/g, ' ')}` : '';
  const replyPart = tail ? ` » ${tail}` : '';
  const body = `${userPart}${replyPart}`;
  const budget = Math.max(10, width - prefix.length - 4);
  const trimmed = body.length > budget ? body.slice(body.length - budget) : body;
  return { prefix, body: trimmed, streaming: a.streaming };
}

export const ChannelActivityFeed: React.FC<ChannelActivityFeedProps> = ({
  activities,
  currentConvId,
}) => {
  const colors = useColors();
  const width = usePanelWidth();

  const rows = Object.values(activities)
    .filter((a) => a.convId !== currentConvId)
    .filter((a) => a.streaming || Date.now() - a.lastUpdate < STALE_MS)
    .sort((x, y) => y.lastUpdate - x.lastUpdate)
    .slice(0, MAX_ROWS);

  if (rows.length === 0) return null;

  return (
    <Box flexDirection="column" paddingX={1} flexShrink={0}>
      {rows.map((a) => {
        const r = formatRow(a, width);
        return (
          <Box key={a.convId} flexDirection="row">
            <Text color={r.streaming ? colors.warning : colors.muted}>
              {r.prefix}
            </Text>
            <Box flexShrink={1}>
              <Text color={colors.muted} wrap="truncate-end">{r.body}</Text>
            </Box>
          </Box>
        );
      })}
    </Box>
  );
};

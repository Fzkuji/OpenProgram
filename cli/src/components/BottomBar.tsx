import React from 'react';
import { Box, Text } from 'ink';
import { colors } from '../theme/colors.js';
import { useTerminalWidth, usePanelWidth } from '../utils/useTerminalWidth.js';

export interface BottomBarProps {
  agent?: string;
  model?: string;
  conversationId?: string;
  /** Human title — preferred over conv_id when present. */
  conversationTitle?: string;
  busy?: boolean;
  /** When true, the input is in slash-command mode. */
  slashMode?: boolean;
  /** Last context stats (input/output tokens). */
  tokens?: { input?: number; output?: number };
  /** Tools available for next turn. */
  toolsOn?: boolean;
  /** ws connection state. */
  connState?: 'connecting' | 'connected' | 'disconnected';
  /** Total context window in tokens (for the % indicator). */
  contextWindow?: number;
}

const formatTokens = (n?: number): string | null => {
  if (typeof n !== 'number' || n <= 0) return null;
  if (n >= 10000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
};

export const BottomBar: React.FC<BottomBarProps> = ({
  agent,
  model,
  conversationId,
  conversationTitle,
  busy,
  slashMode,
  tokens,
  toolsOn,
  connState,
  contextWindow,
}) => {
  const cols = useTerminalWidth();

  // Pick a hint length that fits — long form on wide terminals,
  // shorter on narrow, drop entirely on very narrow.
  const hintLong = slashMode
    ? '↑↓ choose · enter run · tab fill · esc cancel'
    : busy
    ? 'esc to stop · ctrl+c quit'
    : 'type / for commands · enter send · ctrl+c quit';
  const hintShort = slashMode
    ? '↑↓ enter tab esc'
    : busy
    ? 'esc stop'
    : '/ commands';
  const showHint = cols >= 60;
  const hint = cols >= 100 ? hintLong : hintShort;

  const inTokens = formatTokens(tokens?.input);
  const outTokens = formatTokens(tokens?.output);

  // On very narrow terminals, drop the conversation id so the right side
  // stays on a single line.
  const showConv = cols >= 80;
  const showTokens = cols >= 90 && (inTokens || outTokens);
  const showBusyTag = cols >= 70;

  // Cap matches Welcome / PromptInput so the bar doesn't extend past
  // the input box edge on wide terminals.
  const width = usePanelWidth();

  return (
    <Box paddingX={1} justifyContent="space-between" width={width}>
      <Box flexShrink={1}>
        <Text color={toolsOn ? colors.success : colors.muted}>
          {toolsOn ? '▸▸ tools on' : '▸▸ tools off'}
        </Text>
        {showHint ? (
          <>
            <Text color={colors.border}> · </Text>
            <Text color={colors.muted} wrap="truncate-end">
              {hint}
            </Text>
          </>
        ) : null}
      </Box>
      <Box flexShrink={0}>
        <Text color={colors.muted}>
          {connState && connState !== 'connected' ? (
            <>
              <Text color={connState === 'disconnected' ? colors.error : colors.warning}>
                {connState === 'disconnected' ? '○ offline' : '◌ connecting'}
              </Text>
              <Text color={colors.border}> · </Text>
            </>
          ) : null}
          {agent ?? '—'}
          <Text color={colors.border}> · </Text>
          {model ?? '—'}
          {showConv ? (
            <>
              <Text color={colors.border}> · </Text>
              {(conversationTitle ?? conversationId ?? '(new)').slice(0, 24)}
            </>
          ) : null}
          {showTokens ? (
            <>
              <Text color={colors.border}> · </Text>
              {inTokens ? <Text color={colors.muted}>↓{inTokens}</Text> : null}
              {inTokens && outTokens ? <Text color={colors.border}> </Text> : null}
              {outTokens ? <Text color={colors.muted}>↑{outTokens}</Text> : null}
            </>
          ) : null}
          {showTokens && contextWindow && tokens?.input ? (
            <>
              <Text color={colors.border}> · </Text>
              <Text color={
                tokens.input / contextWindow > 0.85 ? colors.error
                : tokens.input / contextWindow > 0.65 ? colors.warning
                : colors.muted
              }>
                {Math.round((tokens.input / contextWindow) * 100)}%
              </Text>
            </>
          ) : null}
          {busy && showBusyTag ? (
            <>
              <Text color={colors.border}> · </Text>
              <Text color={colors.warning}>working</Text>
            </>
          ) : null}
        </Text>
      </Box>
    </Box>
  );
};

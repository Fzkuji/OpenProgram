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
  /** Permission mode for tool calls: ask / auto / bypass. */
  permissionMode?: 'ask' | 'auto' | 'bypass';
  /** Thinking budget cycle: off / low / medium / high. */
  thinkingEffort?: 'off' | 'low' | 'medium' | 'high';
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
  permissionMode,
  thinkingEffort,
  connState,
  contextWindow,
}) => {
  const cols = useTerminalWidth();

  // Bottom-bar left hint is now context-only (slash menu / busy / quit).
  // The "type / for commands" placeholder lives inside the input box,
  // and ↵ enter is rendered there too — no need to duplicate.
  const hintLong = slashMode
    ? '↑↓ choose · enter run · tab fill · esc cancel'
    : busy
    ? 'esc to stop · ctrl+c quit'
    : 'ctrl+c quit · ctrl+r history · @ files';
  const hintShort = slashMode
    ? '↑↓ enter tab esc'
    : busy
    ? 'esc stop'
    : 'ctrl+c quit';
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
        {/* Permission cycle indicator (shift+tab) */}
        <Text color={
          permissionMode === 'bypass' ? colors.error
          : permissionMode === 'auto' ? colors.warning
          : colors.muted
        }>
          {permissionMode === 'bypass' ? '▸▸ bypass'
            : permissionMode === 'auto' ? '▸▸ auto'
            : '▸▸ ask'}
        </Text>
        <Text color={colors.border}> · </Text>
        {/* Thinking effort cycle (tab) */}
        <Text color={
          thinkingEffort === 'high' ? colors.primary
          : thinkingEffort === 'off' ? colors.muted
          : colors.warning
        }>
          {`✦${thinkingEffort ?? 'medium'}`}
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
                {formatTokens(tokens.input)}/{formatTokens(contextWindow)} ({Math.round((tokens.input / contextWindow) * 100)}%)
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

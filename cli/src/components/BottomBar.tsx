import React from 'react';
import { Box, Text } from '../runtime/index';
import { useColors } from '../theme/ThemeProvider.js';
import { useTerminalWidth, usePanelWidth } from '../utils/useTerminalWidth.js';

export interface BottomBarProps {
  agent?: string;
  model?: string;
  conversationId?: string;
  /** Retained for callers that already track titles; BottomBar shows ids. */
  conversationTitle?: string;
  busy?: boolean;
  /** Last context stats (input/output tokens). */
  tokens?: { input?: number; output?: number };
  /** Tools available for next turn. */
  toolsOn?: boolean;
  /** Permission mode for tool calls: ask / auto / bypass. */
  permissionMode?: 'ask' | 'auto' | 'bypass';
  /** Thinking budget cycle: off / minimal / low / medium / high / xhigh. */
  thinkingEffort?: 'off' | 'minimal' | 'low' | 'medium' | 'high' | 'xhigh';
  /** ws connection state. */
  connState?: 'connecting' | 'connected' | 'disconnected';
  /** Current session state for the right-side conversation indicator. */
  sessionStatus?: 'empty' | 'loaded' | 'active';
  /** Total context window in tokens (for the % indicator). */
  contextWindow?: number;
  /** Cache statistics from /api/sessions/{id}/tokens. cache_hit_rate is
   *  cache_read / (input + cache_read). cacheReadTotal is the running
   *  cache_read count across the branch. Both surface when the model
   *  is benefiting from prompt caching. */
  cacheHitRate?: number;
  cacheReadTotal?: number;
  /** Token measurement source mix from the server (provider_usage /
   *  tiktoken / heuristic). When mostly heuristic the bar prints a
   *  trailing '~' so the user knows the numbers are estimates. */
  tokenSourceMix?: Record<string, number>;
  /** True after the first Ctrl+C while the 800 ms double-press window
   *  is open. Replaces the regular hint with a confirm-to-exit prompt. */
  exitPending?: boolean;
}

const formatTokens = (n?: number): string | null => {
  if (typeof n !== 'number' || n <= 0) return null;
  if (n >= 10000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
};

const compact = (s: string | undefined, max: number): string | undefined => {
  if (!s) return s;
  if (s.length <= max) return s;
  if (max <= 1) return '…';
  return `${s.slice(0, max - 1)}…`;
};

export const BottomBar: React.FC<BottomBarProps> = ({
  agent,
  model,
  conversationId,
  busy,
  tokens,
  toolsOn,
  permissionMode,
  thinkingEffort,
  connState,
  sessionStatus = 'empty',
  contextWindow,
  cacheHitRate,
  cacheReadTotal,
  tokenSourceMix,
  exitPending,
}) => {
  const colors = useColors();
  const cols = useTerminalWidth();

  // Bottom-bar left hint is context-only. Slash/file picker instructions
  // live beside those pickers, not in the persistent bottom bar.
  const hintLong = exitPending
    ? 'press ctrl+c again to exit'
    : busy
    ? 'esc to stop'
    : 'ctrl+r search context';
  const hintShort = exitPending
    ? 'ctrl+c again to exit'
    : busy
    ? 'esc stop'
    : 'ctrl+r search';
  const showHint = cols >= 60 && hintLong.length > 0;
  const hint = cols >= 100 ? hintLong : hintShort;

  const inTokens = formatTokens(tokens?.input);
  const outTokens = formatTokens(tokens?.output);

  const showToolsOn = cols >= 72;
  const showSession = cols >= 86;
  const showTokens = cols >= 96 && (inTokens || outTokens);
  const showContextPct = cols >= 112 && contextWindow && tokens?.input;
  const showBusyTag = cols >= 74;

  // Cap matches Welcome / PromptInput so the bar doesn't extend past
  // the input box edge on wide terminals.
  const width = usePanelWidth();
  const rightParts: React.ReactNode[] = [];
  const pushPart = (node: React.ReactNode) => {
    if (rightParts.length > 0) {
      rightParts.push(<Text key={`sep-${rightParts.length}`} color={colors.border}> · </Text>);
    }
    rightParts.push(<React.Fragment key={`part-${rightParts.length}`}>{node}</React.Fragment>);
  };

  if (connState && connState !== 'connected') {
    pushPart(
      <Text color={connState === 'disconnected' ? colors.error : colors.warning}>
        {connState === 'disconnected' ? '○ offline' : '◌ connecting'}
      </Text>,
    );
  }
  pushPart(compact(agent ?? '—', cols >= 100 ? 28 : 16) ?? '—');
  if (cols >= 54) pushPart(compact(model ?? '—', cols >= 100 ? 32 : 18) ?? '—');
  if (showSession) {
    const sessionLabel = compact(conversationId ?? 'disconnected', 24) ?? 'disconnected';
    const dotColor = sessionStatus === 'active' ? colors.success : colors.muted;
    pushPart(
      <>
        <Text color={dotColor}>{sessionStatus === 'active' ? '●' : '○'}</Text>
        <Text color={colors.muted}> {sessionLabel}</Text>
      </>,
    );
  }
  if (showTokens) {
    pushPart(
      <>
        {inTokens ? <Text color={colors.muted}>↑{inTokens}</Text> : null}
        {inTokens && outTokens ? <Text color={colors.border}> </Text> : null}
        {outTokens ? <Text color={colors.muted}>↓{outTokens}</Text> : null}
      </>,
    );
  }
  if (showContextPct) {
    const ratio = tokens.input! / contextWindow;
    // Hint of measurement quality: if more than half the rows are
    // heuristic estimates, append a tilde so the user knows the %
    // is approximate. Provider-reported numbers print cleanly.
    let estimateHint = '';
    if (tokenSourceMix) {
      const total = Object.values(tokenSourceMix).reduce((a, b) => a + b, 0);
      const heur = tokenSourceMix['heuristic'] || 0;
      if (total > 0 && heur / total > 0.5) estimateHint = '~';
    }
    pushPart(
      <Text color={
        ratio > 0.85 ? colors.error
        : ratio > 0.65 ? colors.warning
        : colors.muted
      }>
        {formatTokens(tokens.input)}/{formatTokens(contextWindow)} ({Math.round(ratio * 100)}{estimateHint}%)
      </Text>,
    );
  }
  // Cache pill — only show when caching is actually happening, and only
  // on wide terminals so the bar stays compact. Green to celebrate the
  // savings, but muted on narrow widths to avoid clutter.
  if (cols >= 100 && (cacheReadTotal && cacheReadTotal > 0)) {
    const cachePct = Math.round(((cacheHitRate ?? 0)) * 100);
    pushPart(
      <Text color={colors.success}>cache {cachePct}%</Text>,
    );
  }
  if (busy && showBusyTag) pushPart(<Text color={colors.warning}>working</Text>);

  return (
    <Box
      paddingX={1}
      justifyContent="space-between"
      width={width}
      height={1}
      overflow="hidden"
      flexShrink={0}
    >
      <Box flexShrink={1}>
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
        {toolsOn === false ? (
          <Text color={colors.warning}>tools off</Text>
        ) : showToolsOn ? (
          <Text color={colors.muted}>tools on</Text>
        ) : null}
        {toolsOn === false || showToolsOn ? <Text color={colors.border}> · </Text> : null}
        <Text color={
          thinkingEffort === 'xhigh' ? colors.bottomBar.effortXhigh
          : thinkingEffort === 'high' ? colors.primary
          : thinkingEffort === 'off' ? colors.muted
          : colors.warning
        }>
          {`✦${thinkingEffort ?? 'xhigh'}`}
        </Text>
        {showHint ? (
          <>
            <Text color={colors.border}> · </Text>
            <Text color={exitPending ? colors.warning : colors.muted} wrap="truncate-end">
              {hint}
            </Text>
          </>
        ) : null}
      </Box>
      <Box flexShrink={1} marginLeft={1}>
        <Text color={colors.muted} wrap="truncate-start">
          {rightParts}
        </Text>
      </Box>
    </Box>
  );
};

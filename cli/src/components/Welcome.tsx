import React from 'react';
import { Box, Text } from '@openprogram/ink';
import { useColors } from '../theme/ThemeProvider.js';
import { useTerminalWidth, usePanelWidth, useTerminalHeight } from '../utils/useTerminalWidth.js';

export interface WelcomeStats {
  agent?: { id?: string; name?: string; model?: string } | null;
  agents_count?: number;
  programs_count?: number;
  skills_count?: number;
  conversations_count?: number;
  top_programs?: Array<{ name?: string; category?: string }>;
  top_functions?: Array<{ name?: string; category?: string }>;
  top_applications?: Array<{ name?: string; category?: string }>;
  top_skills?: Array<{ name?: string; slug?: string }>;
  top_agents?: Array<{ name?: string; id?: string }>;
  top_sessions?: Array<{ id?: string; title?: string }>;
  top_tools?: string[];
  top_providers?: string[];
  top_channels?: Array<{ channel?: string; id?: string }>;
  // Counts for the split-out tiles. If absent, derived from top_*.length.
  functions_count?: number;
  applications_count?: number;
}

export interface WelcomeProps {
  stats?: WelcomeStats;
}

const fmt = (n?: number): string => (typeof n === 'number' ? String(n) : '—');

interface ColumnSpec {
  count: string;
  label: string;
  items: string[];
}

const Column: React.FC<{
  spec: ColumnSpec;
  /** When true, items wrap into 2 sub-columns inside the column. */
  twoCols: boolean;
  /** Cap on number of item rows shown (truncates with "+N more"). */
  maxRows: number;
}> = ({ spec, twoCols, maxRows }) => {
  const colors = useColors();
  // No explicit pixel widths anywhere in Column — every cell uses
  // flex distribution. Why: explicit widths combined with paddingX
  // and equal-distribution rounding repeatedly tripped yoga's
  // invalid-dimension path (ink renderer.ts:55-72) at boundary
  // sizes (107×41, 119×50, 110×36 — every fix moved the failure
  // to a new size because each fix only tightened ONE rounding
  // edge). flex={1} with flexBasis=0 lets yoga distribute the
  // available width however it wants without needing pixel-perfect
  // arithmetic from us.
  const limitedItems = spec.items.slice(0, twoCols ? maxRows * 2 : maxRows);
  const overflow = spec.items.length - limitedItems.length;
  const rows: Array<[string, string | undefined]> = [];
  if (twoCols) {
    const half = Math.ceil(limitedItems.length / 2);
    for (let i = 0; i < half; i++) {
      rows.push([limitedItems[i] ?? '', limitedItems[i + half]]);
    }
  } else {
    for (const it of limitedItems) rows.push([it, undefined]);
  }
  return (
    <Box flexDirection="column" flexGrow={1} flexShrink={1} flexBasis={0} paddingX={1}>
      <Box>
        <Text bold color={colors.primary}>
          {spec.count}
        </Text>
        <Text bold color={colors.text}>{` ${spec.label}`}</Text>
      </Box>
      {rows.map(([a, b], i) => (
        <Box key={i}>
          <Box flexGrow={1} flexShrink={1} flexBasis={0}>
            <Text color={colors.muted} wrap="truncate-end">
              {a}
            </Text>
          </Box>
          {twoCols && b ? (
            <Box flexGrow={1} flexShrink={1} flexBasis={0}>
              <Text color={colors.muted} wrap="truncate-end">
                {b}
              </Text>
            </Box>
          ) : null}
        </Box>
      ))}
      {overflow > 0 ? (
        <Text color={colors.border}> +{overflow}</Text>
      ) : null}
    </Box>
  );
};

export const Welcome: React.FC<WelcomeProps> = ({ stats }) => {
  const colors = useColors();
  const cols = useTerminalWidth();
  const rows = useTerminalHeight();
  const width = usePanelWidth();
  const agentName = stats?.agent?.name ?? stats?.agent?.id ?? '—';
  const model = stats?.agent?.model ?? '—';

  const skills: ColumnSpec = {
    count: fmt(stats?.skills_count),
    label: 'skills',
    items: (stats?.top_skills ?? [])
      .map((s) => s.name)
      .filter((s): s is string => !!s),
  };
  const agentsCol: ColumnSpec = {
    count: fmt(stats?.agents_count),
    label: 'agents',
    items: (stats?.top_agents ?? [])
      .map((a) => a.name ?? a.id)
      .filter((s): s is string => !!s),
  };
  const sessionsCol: ColumnSpec = {
    count: fmt(stats?.conversations_count),
    label: 'sessions',
    items: (stats?.top_sessions ?? [])
      .map((s) => s.title ?? s.id)
      .filter((s): s is string => !!s),
  };
  const tools: ColumnSpec = {
    count: fmt(stats?.top_tools?.length),
    label: 'tools',
    items: stats?.top_tools ?? [],
  };
  const providers: ColumnSpec = {
    count: fmt(stats?.top_providers?.length),
    label: 'providers',
    items: stats?.top_providers ?? [],
  };
  const channels: ColumnSpec = {
    count: fmt(stats?.top_channels?.length),
    label: 'channels',
    items: (stats?.top_channels ?? []).map((c) =>
      c.channel && c.id ? `${c.channel}:${c.id}` : c.channel ?? c.id ?? '',
    ),
  };
  // Programs are split into "functions" (meta/builtin/external runtime
  // helpers) and "applications" (the app/ subdir projects). Fall back to
  // top_programs when the server hasn't been updated yet.
  const fallbackPrograms = stats?.top_programs ?? [];
  const fnFromFallback = fallbackPrograms.filter(
    (p) => p.category && p.category !== 'app',
  );
  const appFromFallback = fallbackPrograms.filter((p) => p.category === 'app');
  const functions: ColumnSpec = {
    count: fmt(
      stats?.functions_count
        ?? (stats?.top_functions?.length
          ?? (fnFromFallback.length || stats?.programs_count)),
    ),
    label: 'functions',
    items: (stats?.top_functions ?? fnFromFallback)
      .map((p) => p.name)
      .filter((s): s is string => !!s),
  };
  const applications: ColumnSpec = {
    count: fmt(
      stats?.applications_count
        ?? (stats?.top_applications?.length ?? appFromFallback.length),
    ),
    label: 'applications',
    items: (stats?.top_applications ?? appFromFallback)
      .map((p) => p.name)
      .filter((s): s is string => !!s),
  };

  // Always 4×2 grid (8 tiles). Layout order:
  //   skills · agents · sessions · tools
  //   providers · channels · functions · applications
  const row1 = [skills, agentsCol, sessionsCol, tools];
  const row2 = [providers, channels, functions, applications];
  const rowAll = [...row1, ...row2];

  // Three display modes by available height. Budget breakdown:
  //   outside reserved (input box 3 + bottom bar 1 + welcome marginBottom 1
  //   + safety 1) = 6 rows
  //   welcome chrome (border × 2 + title + 3 marginTop + tip) = 7 rows
  //   one-row variant chrome = 6 rows
  // Tile rows:
  //   compact   = 2 (count + label)
  //   with items, worst case = maxRows + 3 (count + label + items + overflow)
  //
  // Mode thresholds:
  //   rows >= 21  → two rows of tiles, items per tile = (rows - 19) / 2
  //   rows >= 17  → two rows compact (no items, just count + label)
  //   rows >= 14  → one row of tiles (drop row2), compact
  //   else        → skip welcome entirely
  type Mode = 'none' | 'one-row' | 'two-rows-compact' | 'two-rows-items';
  let mode: Mode;
  let itemsPerTile = 0;
  if (rows >= 21) {
    mode = 'two-rows-items';
    itemsPerTile = Math.min(8, Math.max(0, Math.floor((rows - 19) / 2)));
  } else if (rows >= 17) {
    mode = 'two-rows-compact';
  } else if (rows >= 14) {
    mode = 'one-row';
  } else {
    mode = 'none';
  }

  // Welcome panel chrome: round border (1+1=2) + paddingX={2} (2+2=4).
  // Total = 6 cols of overhead, content area = width - 6.
  //
  // Tile layout uses pure flex distribution — no explicit pixel
  // widths anywhere — so yoga's invalid-dimension path can never
  // fire (tracked back through user reports at 107×41 / 119×50 /
  // 110×36 to ink renderer.ts:55-72 returning a height=0 screen
  // when computed dims are NaN; explicit-width arithmetic kept
  // tripping that path at boundary sizes).
  //
  // The threshold below decides 4-across vs 2-across based on
  // available content width. Each column needs ~12 cols for the
  // "0 channels" + item text plus 2 col paddingX = 14 per slot.
  // Four across wants 4×14=56; add 4 col margin to avoid layouts
  // that look squished.
  const innerPanel = Math.max(0, width - 6);
  const minTileSlot = 14;
  const fourAcrossMin = minTileSlot * 4 + 4;
  const fourAcross = innerPanel >= fourAcrossMin;
  const twoSubCols = cols >= 130;
  // The 4 most useful tiles when only one row fits.
  const oneRowSubset = [skills, agentsCol, sessionsCol, tools];

  // Smallest fallback — no room for a panel. Still print one line so the
  // user knows what's going on.
  if (mode === 'none') {
    return (
      <Box paddingX={1} marginBottom={0}>
        <Text color={colors.error} bold>
          OpenProgram
        </Text>
        <Text color={colors.border}> · </Text>
        <Text color={colors.muted}>
          {agentName} · {model}
        </Text>
      </Box>
    );
  }

  return (
    <Box
      flexDirection="column"
      borderStyle="round"
      borderColor={colors.primary}
      paddingX={2}
      paddingY={0}
      marginBottom={1}
      width={width}
    >
      {/* Title row — same flexShrink trick as BottomBar so the right
          side (agent · model) stays anchored to the right edge when
          the window narrows; only the left "OpenProgram" label
          truncates. width={width-4} matches the Welcome panel's inner
          width (border 2 + paddingX*2 = 4) — without an explicit width
          on this row Yoga sizes it to content and space-between has
          no extra space to distribute. */}
      {/* width-7 (not width-6) so the row is strictly narrower than the
          panel inner area; eliminates another exact-fit yoga edge. */}
      <Box justifyContent="space-between" width={Math.max(20, width - 7)}>
        <Box flexShrink={1}>
          <Text bold color={colors.error} wrap="truncate-end">
            OpenProgram
          </Text>
        </Box>
        <Box flexShrink={0}>
          <Text color={colors.muted}>
            {agentName} <Text color={colors.border}>·</Text> {model}
          </Text>
        </Box>
      </Box>

      {/* Tile layout — mode switches based on terminal height. */}
      {mode === 'two-rows-items' && fourAcross ? (
        <>
          <Box marginTop={1}>
            {row1.map((c) => (
              <Column
                key={c.label}
                spec={c}
                twoCols={twoSubCols}
                maxRows={itemsPerTile}
              />
            ))}
          </Box>
          <Box marginTop={1}>
            {row2.map((c) => (
              <Column
                key={c.label}
                spec={c}
                twoCols={twoSubCols}
                maxRows={itemsPerTile}
              />
            ))}
          </Box>
        </>
      ) : mode === 'two-rows-compact' && fourAcross ? (
        <>
          <Box marginTop={1}>
            {row1.map((c) => (
              <Column
                key={c.label}
                spec={c}
                twoCols={false}
                maxRows={0}
              />
            ))}
          </Box>
          <Box marginTop={1}>
            {row2.map((c) => (
              <Column
                key={c.label}
                spec={c}
                twoCols={false}
                maxRows={0}
              />
            ))}
          </Box>
        </>
      ) : mode === 'one-row' && fourAcross ? (
        <Box marginTop={1}>
          {oneRowSubset.map((c) => (
            <Column
              key={c.label}
              spec={c}
              twoCols={false}
              maxRows={0}
            />
          ))}
        </Box>
      ) : (
        // Narrow (<minTileSlot*2+SAFETY_BUFFER cols) fallback: 2-across grid.
        <Box flexDirection="column" marginTop={1}>
          {Array.from({ length: Math.ceil(rowAll.length / 2) }).map((_, r) => (
            <Box key={r}>
              {rowAll.slice(r * 2, r * 2 + 2).map((c) => (
                <Column
                  key={c.label}
                  spec={c}
                  twoCols={false}
                  maxRows={mode === 'two-rows-items' ? Math.max(0, itemsPerTile - 1) : 0}
                />
              ))}
            </Box>
          ))}
        </Box>
      )}

      <Box marginTop={1}>
        <Text color={colors.muted}>
          Type a message and press <Text color={colors.primary}>enter</Text>, or type{' '}
          <Text color={colors.primary}>/</Text> to browse commands.
        </Text>
      </Box>
    </Box>
  );
};

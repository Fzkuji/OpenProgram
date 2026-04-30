import React from 'react';
import { Box, Text } from '../runtime/index';
import { useColors } from '../theme/ThemeProvider.js';
import { useTerminalWidth, usePanelWidth, useTerminalHeight } from '../utils/useTerminalWidth.js';

export interface WelcomeStats {
  agent?: { id?: string; name?: string; model?: string } | null;
  // Total counts — server sends these so 0 is reachable. Welcome
  // falls back to top_*.length when an old server omits the count.
  agents_count?: number;
  programs_count?: number;
  skills_count?: number;
  conversations_count?: number;
  functions_count?: number;
  applications_count?: number;
  tools_count?: number;
  providers_count?: number;
  channels_count?: number;
  // Top-N preview lists.
  top_programs?: Array<{ name?: string; category?: string }>;
  top_functions?: Array<{ name?: string; category?: string }>;
  top_applications?: Array<{ name?: string; category?: string }>;
  top_skills?: Array<{ name?: string; slug?: string }>;
  top_agents?: Array<{ name?: string; id?: string }>;
  top_sessions?: Array<{ id?: string; title?: string }>;
  top_tools?: string[];
  top_providers?: string[];
  top_channels?: Array<{ channel?: string; id?: string }>;
}

export interface WelcomeProps {
  stats?: WelcomeStats;
  /** Expand the panel to consume the opening screen's available space. */
  fillAvailable?: boolean;
}

export type WelcomeMode =
  | 'inline'
  | 'summary'
  | 'one-row'
  | 'two-rows-compact'
  | 'two-rows-items';

export interface WelcomeLayout {
  mode: WelcomeMode;
  itemsPerTile: number;
}

export function getWelcomeLayout(
  layoutRows: number,
  fillAvailable: boolean,
): WelcomeLayout {
  const rows = Math.max(0, Math.floor(layoutRows));
  const itemsModeAt = fillAvailable ? 17 : 21;

  if (rows >= itemsModeAt) {
    return {
      mode: 'two-rows-items',
      itemsPerTile: fillAvailable
        ? Math.min(24, Math.max(0, Math.floor((rows - 11) / 2)))
        : Math.min(8, Math.max(0, Math.floor((rows - 19) / 2))),
    };
  }
  if (rows >= 11) return { mode: 'two-rows-compact', itemsPerTile: 0 };
  if (rows >= 8) return { mode: 'one-row', itemsPerTile: 0 };
  if (rows >= 5) return { mode: 'summary', itemsPerTile: 0 };
  return { mode: 'inline', itemsPerTile: 0 };
}

const fmt = (n?: number): string => (typeof n === 'number' ? String(n) : '—');

interface ColumnSpec {
  count: string;
  label: string;
  items: string[];
}

const Column: React.FC<{
  spec: ColumnSpec;
  width: number;
  /** When true, items wrap into 2 sub-columns inside the column. */
  twoCols: boolean;
  /** Cap on number of item rows shown (truncates with "+N more"). */
  maxRows: number;
}> = ({ spec, width, twoCols, maxRows }) => {
  const colors = useColors();
  const innerWidth = Math.max(8, width - 2);
  const subWidth = twoCols ? Math.floor(innerWidth / 2) : innerWidth;
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
    <Box flexDirection="column" width={width} paddingX={1}>
      {/* Count + label flush-left with the items below — uniform vertical
          alignment is easier to scan than centered headers over ragged
          lists. Count in primary orange, label in bold white so the
          section header reads distinct from the dim-gray items. */}
      <Box>
        <Text bold color={colors.primary}>
          {spec.count}
        </Text>
        <Text bold color={colors.text}>{` ${spec.label}`}</Text>
      </Box>
      {rows.map(([a, b], i) => (
        <Box key={i}>
          <Box width={subWidth}>
            <Text color={colors.muted} wrap="truncate-end">
              {a}
            </Text>
          </Box>
          {twoCols && b ? (
            <Box width={subWidth}>
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

export const Welcome: React.FC<WelcomeProps> = ({ stats, fillAvailable = false }) => {
  const colors = useColors();
  const cols = useTerminalWidth();
  const rows = useTerminalHeight();
  const width = usePanelWidth();
  const agentName = stats?.agent?.name ?? stats?.agent?.id ?? '—';
  const model = stats?.agent?.model ?? '—';

  // pickCount: prefer the explicit total count from the server. Fall
  // back to the preview-list length when the server is older. Returns
  // a number so fmt(0) renders as '0' instead of '—'. The previous
  // logic mixed undefined and 0 in a way that produced em-dash even
  // when the server sent valid zeros.
  const pickCount = (
    explicit: number | undefined,
    listLen: number | undefined,
  ): number | undefined => {
    if (typeof explicit === 'number') return explicit;
    if (typeof listLen === 'number') return listLen;
    return undefined;
  };

  const skills: ColumnSpec = {
    count: fmt(pickCount(stats?.skills_count, stats?.top_skills?.length)),
    label: 'skills',
    items: (stats?.top_skills ?? [])
      .map((s) => s.name)
      .filter((s): s is string => !!s),
  };
  const agentsCol: ColumnSpec = {
    count: fmt(pickCount(stats?.agents_count, stats?.top_agents?.length)),
    label: 'agents',
    items: (stats?.top_agents ?? [])
      .map((a) => a.name ?? a.id)
      .filter((s): s is string => !!s),
  };
  const sessionsCol: ColumnSpec = {
    count: fmt(pickCount(stats?.conversations_count, stats?.top_sessions?.length)),
    label: 'sessions',
    items: (stats?.top_sessions ?? [])
      .map((s) => s.title ?? s.id)
      .filter((s): s is string => !!s),
  };
  const tools: ColumnSpec = {
    count: fmt(pickCount(stats?.tools_count, stats?.top_tools?.length)),
    label: 'tools',
    items: stats?.top_tools ?? [],
  };
  const providers: ColumnSpec = {
    count: fmt(pickCount(stats?.providers_count, stats?.top_providers?.length)),
    label: 'providers',
    items: stats?.top_providers ?? [],
  };
  const channels: ColumnSpec = {
    count: fmt(pickCount(stats?.channels_count, stats?.top_channels?.length)),
    label: 'channels',
    items: (stats?.top_channels ?? []).map((c) =>
      c.channel && c.id ? `${c.channel}:${c.id}` : c.channel ?? c.id ?? '',
    ),
  };
  // Programs split: "functions" = meta/builtin/external runtime helpers,
  // "applications" = the app/ subdir projects. When server doesn't
  // ship the split lists yet, derive them from top_programs by
  // category. Crucially, count and items now derive from the SAME
  // source — no more "1 functions" while listing 6 names.
  const fallbackPrograms = stats?.top_programs ?? [];
  const fnFromFallback = fallbackPrograms.filter(
    (p) => p.category && p.category !== 'app',
  );
  const appFromFallback = fallbackPrograms.filter((p) => p.category === 'app');
  const fnItems = stats?.top_functions ?? fnFromFallback;
  const appItems = stats?.top_applications ?? appFromFallback;
  const functions: ColumnSpec = {
    count: fmt(pickCount(stats?.functions_count, fnItems.length)),
    label: 'functions',
    items: fnItems.map((p) => p.name).filter((s): s is string => !!s),
  };
  const applications: ColumnSpec = {
    count: fmt(pickCount(stats?.applications_count, appItems.length)),
    label: 'applications',
    items: appItems.map((p) => p.name).filter((s): s is string => !!s),
  };

  // Always 4×2 grid (8 tiles). Layout order:
  //   skills · agents · sessions · tools
  //   providers · channels · functions · applications
  const row1 = [skills, agentsCol, sessionsCol, tools];
  const row2 = [providers, channels, functions, applications];
  const rowAll = [...row1, ...row2];

  // Opening screen mode uses the vertical space that is otherwise empty
  // between Welcome and the bottom input. Once turns exist, Welcome goes
  // back to its natural compact height so the transcript gets the space.
  const targetPanelHeight = fillAvailable ? Math.max(0, rows - 5) : 0;
  const layoutRows = fillAvailable ? targetPanelHeight : rows;

  // Display modes by available height. In the opening screen,
  // layoutRows already excludes the fixed prompt/bottom bar area, so
  // the thresholds are based on panel rows, not terminal rows.
  const { mode, itemsPerTile } = getWelcomeLayout(layoutRows, fillAvailable);

  // Width per tile. Always 4 columns when cols >= 50; below that fall back
  // to a 2-col grid (4 rows of 2 tiles). Clamp to a minimum so a sudden
  // resize down to ~10 cols doesn't produce negative widths and crash Ink.
  const fourAcross = cols >= 50;
  const rawTileWidth = fourAcross
    ? Math.floor((width - 4) / 4)
    : Math.floor((width - 4) / 2);
  const tileWidth = Math.max(8, rawTileWidth);
  const twoSubCols = cols >= 130;
  // The 4 most useful tiles when only one row fits.
  const oneRowSubset = [skills, agentsCol, sessionsCol, tools];

  // Smallest fallback — no room for a panel. Still print one line so the
  // user knows what's going on.
  if (mode === 'inline') {
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
      minHeight={fillAvailable ? targetPanelHeight : undefined}
      width={width}
    >
      {/* Title row — same flexShrink trick as BottomBar so the right
          side (agent · model) stays anchored to the right edge when
          the window narrows; only the left "OpenProgram" label
          truncates. width={width-4} matches the Welcome panel's inner
          width (border 2 + paddingX*2 = 4) — without an explicit width
          on this row Yoga sizes it to content and space-between has
          no extra space to distribute. */}
      <Box justifyContent="space-between" width={Math.max(20, width - 6)}>
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
      {mode === 'summary' ? null : mode === 'two-rows-items' && fourAcross ? (
        <>
          <Box marginTop={1}>
            {row1.map((c) => (
              <Column
                key={c.label}
                spec={c}
                width={tileWidth}
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
                width={tileWidth}
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
                width={tileWidth}
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
                width={tileWidth}
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
              width={tileWidth}
              twoCols={false}
              maxRows={0}
            />
          ))}
        </Box>
      ) : (
        // Narrow (<50 cols) fallback: 2-across grid, drop items if tight.
        <Box flexDirection="column" marginTop={1}>
          {Array.from({ length: Math.ceil(rowAll.length / 2) }).map((_, r) => (
            <Box key={r}>
              {rowAll.slice(r * 2, r * 2 + 2).map((c) => (
                <Column
                  key={c.label}
                  spec={c}
                  width={tileWidth}
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

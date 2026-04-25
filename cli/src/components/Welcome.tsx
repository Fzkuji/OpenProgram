import React from 'react';
import { Box, Text } from 'ink';
import { colors } from '../theme/colors.js';
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
  width: number;
  /** When true, items wrap into 2 sub-columns inside the column. */
  twoCols: boolean;
  /** Cap on number of item rows shown (truncates with "+N more"). */
  maxRows: number;
}> = ({ spec, width, twoCols, maxRows }) => {
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
      <Box justifyContent="center">
        <Text bold color={colors.primary}>
          {spec.count}
        </Text>
      </Box>
      <Box justifyContent="center">
        <Text color={colors.muted}>{spec.label}</Text>
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
        <Text color={colors.border}>  +{overflow}</Text>
      ) : null}
    </Box>
  );
};

export const Welcome: React.FC<WelcomeProps> = ({ stats }) => {
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

  // Fixed chrome cost inside the welcome panel:
  //   1 top border + 1 title + 1 marginTop + (row 1 height) +
  //   1 marginTop + (row 2 height) + 1 marginTop + 1 tip + 1 bottom border
  // → 7 chrome rows + 2 × tileHeight
  // Reserve outside the panel: input box (3) + bottom bar (1) + safety (2).
  const reservedOutside = 6;
  const available = Math.max(8, rows - reservedOutside);
  const chrome = 7;
  // Per-tile minimum: 2 (count + label).
  // Items per tile = (available - chrome) / 2 - 2.
  const itemsPerTile = Math.max(0, Math.floor((available - chrome) / 2) - 2);

  // Width per tile. Always 4 columns when cols >= 50; below that fall back
  // to a 2-col grid (4 rows of 2 tiles).
  const fourAcross = cols >= 50;
  const tileWidth = fourAcross
    ? Math.floor((width - 4) / 4)
    : Math.floor((width - 4) / 2);
  const twoSubCols = cols >= 130;

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
      {/* Title row */}
      <Box justifyContent="space-between">
        <Text bold color={colors.primary}>
          OpenProgram
        </Text>
        <Text color={colors.muted}>
          {agentName} <Text color={colors.border}>·</Text> {model}
        </Text>
      </Box>

      {/* 4-across layout: two rows of 4 tiles each.
          On narrow terminals, fall back to 2-across with 4 rows. */}
      {fourAcross ? (
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
      ) : (
        <Box flexDirection="column" marginTop={1}>
          {Array.from({ length: Math.ceil(rowAll.length / 2) }).map((_, r) => (
            <Box key={r}>
              {rowAll.slice(r * 2, r * 2 + 2).map((c) => (
                <Column
                  key={c.label}
                  spec={c}
                  width={tileWidth}
                  twoCols={false}
                  maxRows={Math.max(0, itemsPerTile - 1)}
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

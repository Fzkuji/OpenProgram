import React, { useState } from 'react';
import { Box, Text, useInput } from '../runtime/index';
import { useColors } from '../theme/ThemeProvider.js';
import { usePanelWidth, useTerminalHeight } from '../utils/useTerminalWidth.js';

/** One resolved setting, matching `openprogram.config_schema.get_settings()`. */
export interface SettingRow {
  key: string;
  group: string;
  label: string;
  widget: 'number' | 'toggle' | 'enum' | 'status';
  apply: 'live' | 'next_start';
  help?: string;
  value?: unknown;
  choices?: string[];
  set?: boolean;
  /** For read-only `status` rows: the slash command to run on enter. */
  action?: string;
}

/** A row that, instead of editing a value, launches an existing flow
 * (a dedicated picker or a login flow) by running a slash command. */
export interface ActionRow {
  label: string;
  command: string; // e.g. "/model", "/theme", "/login"
  hint?: string;
}

export interface SettingsPanelProps {
  rows: SettingRow[];
  /** Rows that delegate to an existing picker/flow (model, theme, …). */
  actions?: ActionRow[];
  /** Persist one setting over the worker WS (action `set_setting`). */
  onSet: (key: string, value: unknown) => void;
  /** Run a slash command (for action rows). */
  onRun?: (command: string) => void;
  onClose: () => void;
}

/**
 * In-app settings hub for the TUI — the visual counterpart to the
 * `openprogram config` / `setup` CLI. Schema-driven config settings edit
 * inline (toggles flip, enums cycle with ←/→, numbers open a digit buffer
 * on enter); "action" rows delegate to the existing dedicated pickers /
 * flows by running their slash command. Type to filter (handy for the long
 * Tools list); the view scroll-windows so it never overflows the terminal.
 * Values are owned by the parent: the panel sends `onSet` and re-renders
 * `rows` from the server's `setting_result`.
 */
export const SettingsPanel: React.FC<SettingsPanelProps> = ({
  rows, actions = [], onSet, onRun, onClose,
}) => {
  const colors = useColors();
  const width = usePanelWidth();
  const height = useTerminalHeight();
  const [index, setIndex] = useState(0);
  const [editKey, setEditKey] = useState<string | null>(null);
  const [buffer, setBuffer] = useState('');
  const [filter, setFilter] = useState('');

  // Filter by label (and group). Settings + actions both filterable.
  const fl = filter.toLowerCase();
  const hit = (label: string, group?: string) =>
    !fl || label.toLowerCase().includes(fl) || (group ?? '').toLowerCase().includes(fl);
  const vSettings = rows.filter((r) => hit(r.label, r.group));
  const vActions = actions.filter((a) => hit(a.label));

  const total = vSettings.length + vActions.length;
  const at = total ? Math.min(index, total - 1) : 0;
  const curSetting = at < vSettings.length ? vSettings[at] : undefined;
  const curAction = at >= vSettings.length ? vActions[at - vSettings.length] : undefined;

  // Display lines: settings grouped, then a "More" actions section. Each
  // setting/action carries its nav index `i` (position in the filtered list).
  type Line =
    | { kind: 'header'; label: string }
    | { kind: 'setting'; row: SettingRow; i: number }
    | { kind: 'action'; row: ActionRow; i: number };
  const lines: Line[] = [];
  let last = '';
  vSettings.forEach((row, i) => {
    if (row.group !== last) { lines.push({ kind: 'header', label: row.group }); last = row.group; }
    lines.push({ kind: 'setting', row, i });
  });
  if (vActions.length) {
    lines.push({ kind: 'header', label: 'More' });
    vActions.forEach((row, j) => lines.push({ kind: 'action', row, i: vSettings.length + j }));
  }

  // Scroll window: keep the selected row visible without overflowing.
  const maxVisible = Math.max(6, (height || 24) - 10);
  const selLine = lines.findIndex((l) => l.kind !== 'header' && l.i === at);
  let start = 0;
  if (lines.length > maxVisible) {
    start = Math.min(
      Math.max(0, selLine - Math.floor(maxVisible / 2)),
      lines.length - maxVisible,
    );
  }
  const windowed = lines.slice(start, start + maxVisible);
  const moreAbove = start;
  const moreBelow = lines.length - (start + maxVisible);

  const commitNumber = () => {
    if (editKey === null) return;
    const v = buffer.trim();
    if (v.length) onSet(editKey, v);
    setEditKey(null);
    setBuffer('');
  };

  useInput((input, key) => {
    if (editKey !== null) {
      if (key.return) return commitNumber();
      if (key.escape) { setEditKey(null); setBuffer(''); return; }
      if (key.backspace || key.delete) { setBuffer((b) => b.slice(0, -1)); return; }
      if (/^[0-9]$/.test(input)) setBuffer((b) => (b + input).slice(0, 5));
      return;
    }

    if (key.escape) {
      if (filter) { setFilter(''); setIndex(0); return; }
      return onClose();
    }
    if (total) {
      if (key.upArrow) { setIndex((i) => (Math.min(i, total - 1) - 1 + total) % total); return; }
      if (key.downArrow) { setIndex((i) => (Math.min(i, total - 1) + 1) % total); return; }
    }

    if (curAction) {
      if (key.return) { onClose(); onRun?.(curAction.command); return; }
    } else if (curSetting) {
      if (curSetting.widget === 'status' && key.return) {
        onClose(); onRun?.(curSetting.action ?? '/login'); return;
      }
      if (curSetting.widget === 'toggle' && (key.return || input === ' ')) {
        onSet(curSetting.key, !curSetting.value); return;
      }
      if (curSetting.widget === 'enum' && (key.leftArrow || key.rightArrow)) {
        const opts = curSetting.choices ?? [];
        if (opts.length) {
          const c = Math.max(0, opts.indexOf(String(curSetting.value)));
          const n = key.rightArrow ? (c + 1) % opts.length : (c - 1 + opts.length) % opts.length;
          onSet(curSetting.key, opts[n]);
        }
        return;
      }
      if (curSetting.widget === 'number' && key.return) {
        setEditKey(curSetting.key); setBuffer(String(curSetting.value ?? '')); return;
      }
    }

    // Type-to-filter (space is reserved for toggling).
    if (key.backspace || key.delete) { setFilter((f) => f.slice(0, -1)); setIndex(0); return; }
    if (input && input.length === 1 && !key.ctrl && !key.meta && /[\w.\-]/.test(input)) {
      setFilter((f) => f + input); setIndex(0);
    }
  });

  const renderValue = (row: SettingRow, selected: boolean): React.ReactNode => {
    if (editKey === row.key) return <Text color={colors.primary}>{buffer}▌</Text>;
    if (row.widget === 'status') {
      const ok = !!row.value;
      return <Text color={ok ? colors.success : colors.muted}>{ok ? '✓ set' : '✗ not set'}</Text>;
    }
    if (row.widget === 'toggle') {
      const on = !!row.value;
      return <Text color={on ? colors.success : colors.muted}>{on ? 'on' : 'off'}</Text>;
    }
    if (row.widget === 'enum') {
      return <Text color={selected ? colors.text : colors.muted}>‹ {String(row.value)} ›</Text>;
    }
    return <Text color={selected ? colors.text : colors.muted}>{String(row.value ?? '')}</Text>;
  };

  const footer = editKey !== null
    ? 'type digits · enter save · esc cancel'
    : curAction ? '↑↓ · enter open · type filter · esc'
    : curSetting?.widget === 'status' ? '↑↓ · enter configure · type filter · esc'
    : curSetting?.widget === 'toggle' ? '↑↓ · space toggle · type filter · esc'
    : curSetting?.widget === 'enum' ? '↑↓ · ←→ change · type filter · esc'
    : '↑↓ · enter edit · type filter · esc';

  const labelW = Math.max(20, Math.floor(width * 0.42));

  return (
    <Box flexDirection="column" borderStyle="round" borderColor={colors.primary}
         paddingX={1} marginBottom={1} width={width}>
      <Box justifyContent="space-between">
        <Text bold color={colors.primary}>
          Settings{filter ? <Text color={colors.text}> /{filter}</Text> : null}
        </Text>
        <Text color={colors.muted}>{footer}</Text>
      </Box>

      <Box marginTop={1} flexDirection="column">
        {moreAbove > 0 ? <Text color={colors.muted}>  ↑ {moreAbove} more</Text> : null}
        {total === 0 ? <Text color={colors.muted}>  no settings match “{filter}”</Text> : null}
        {windowed.map((ln, li) => {
          if (ln.kind === 'header') {
            return (
              <Box key={`h-${ln.label}-${li}`} marginTop={li === 0 || moreAbove > 0 ? 0 : 1}>
                <Text bold color={colors.border}>{ln.label}</Text>
              </Box>
            );
          }
          const selected = ln.i === at;
          if (ln.kind === 'action') {
            return (
              <Box key={`a-${ln.row.command}`}>
                <Text color={selected ? colors.primary : colors.border}>{selected ? '▌ ' : '  '}</Text>
                <Box width={labelW}>
                  <Text color={selected ? colors.primary : colors.text} bold={selected} wrap="truncate-end">
                    {ln.row.label}
                  </Text>
                </Box>
                <Text color={colors.muted}>{ln.row.hint ?? ln.row.command} ›</Text>
              </Box>
            );
          }
          const next = ln.row.apply === 'next_start';
          return (
            <Box key={ln.row.key}>
              <Text color={selected ? colors.primary : colors.border}>{selected ? '▌ ' : '  '}</Text>
              <Box width={labelW}>
                <Text color={selected ? colors.primary : colors.text} bold={selected} wrap="truncate-end">
                  {ln.row.label}
                </Text>
              </Box>
              <Box width={Math.max(8, Math.floor(width * 0.18))}>
                {renderValue(ln.row, selected)}
              </Box>
              {next ? <Text color={colors.muted}>· next start</Text> : null}
            </Box>
          );
        })}
        {moreBelow > 0 ? <Text color={colors.muted}>  ↓ {moreBelow} more</Text> : null}
      </Box>

      {curSetting?.help ? (
        <Box marginTop={1}>
          <Text color={colors.muted} wrap="truncate-end">{curSetting.help}</Text>
        </Box>
      ) : null}
    </Box>
  );
};

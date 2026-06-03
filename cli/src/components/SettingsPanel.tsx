import React, { useMemo, useState } from 'react';
import { Box, Text, useInput } from '../runtime/index';
import { useColors } from '../theme/ThemeProvider.js';
import { usePanelWidth } from '../utils/useTerminalWidth.js';

/** One resolved setting, matching `openprogram.config_schema.get_settings()`. */
export interface SettingRow {
  key: string;
  group: string;
  label: string;
  widget: 'number' | 'toggle' | 'enum';
  apply: 'live' | 'next_start';
  help?: string;
  value?: unknown;
  choices?: string[];
  set?: boolean;
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
 * flows (model, theme, effort, providers, channels) by running their
 * slash command. Values are owned by the parent: the panel sends `onSet`
 * and re-renders `rows` from the server's `setting_result`.
 */
export const SettingsPanel: React.FC<SettingsPanelProps> = ({
  rows, actions = [], onSet, onRun, onClose,
}) => {
  const colors = useColors();
  const width = usePanelWidth();
  const [index, setIndex] = useState(0);
  const [editKey, setEditKey] = useState<string | null>(null);
  const [buffer, setBuffer] = useState('');

  // Navigable items: every setting, then every action.
  const total = rows.length + actions.length;
  const at = Math.min(index, Math.max(0, total - 1));
  const curSetting = at < rows.length ? rows[at] : undefined;
  const curAction = at >= rows.length ? actions[at - rows.length] : undefined;

  // Display lines: settings grouped by group, then an Actions section.
  const lines = useMemo(() => {
    type Line =
      | { kind: 'header'; label: string }
      | { kind: 'setting'; row: SettingRow; i: number }
      | { kind: 'action'; row: ActionRow; i: number };
    const out: Line[] = [];
    let last = '';
    rows.forEach((row, i) => {
      if (row.group !== last) {
        out.push({ kind: 'header', label: row.group });
        last = row.group;
      }
      out.push({ kind: 'setting', row, i });
    });
    if (actions.length) {
      out.push({ kind: 'header', label: 'More' });
      actions.forEach((row, j) => out.push({ kind: 'action', row, i: rows.length + j }));
    }
    return out;
  }, [rows, actions]);

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

    if (key.escape) return onClose();
    if (key.upArrow) { setIndex((i) => (i - 1 + total) % total); return; }
    if (key.downArrow) { setIndex((i) => (i + 1) % total); return; }

    if (curAction) {
      if (key.return) { onClose(); onRun?.(curAction.command); }
      return;
    }
    if (!curSetting) return;
    if (curSetting.widget === 'toggle' && (key.return || input === ' ')) {
      onSet(curSetting.key, !curSetting.value);
      return;
    }
    if (curSetting.widget === 'enum' && (key.leftArrow || key.rightArrow)) {
      const opts = curSetting.choices ?? [];
      if (!opts.length) return;
      const cur = Math.max(0, opts.indexOf(String(curSetting.value)));
      const next = key.rightArrow ? (cur + 1) % opts.length : (cur - 1 + opts.length) % opts.length;
      onSet(curSetting.key, opts[next]);
      return;
    }
    if (curSetting.widget === 'number' && key.return) {
      setEditKey(curSetting.key);
      setBuffer(String(curSetting.value ?? ''));
      return;
    }
  });

  const renderValue = (row: SettingRow, selected: boolean): React.ReactNode => {
    if (editKey === row.key) return <Text color={colors.primary}>{buffer}▌</Text>;
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
    : curAction ? '↑↓ move · enter open · esc close'
    : curSetting?.widget === 'toggle' ? '↑↓ move · space toggle · esc close'
    : curSetting?.widget === 'enum' ? '↑↓ move · ←→ change · esc close'
    : '↑↓ move · enter edit · esc close';

  const labelW = Math.max(20, Math.floor(width * 0.42));

  return (
    <Box flexDirection="column" borderStyle="round" borderColor={colors.primary}
         paddingX={1} marginBottom={1} width={width}>
      <Box justifyContent="space-between">
        <Text bold color={colors.primary}>Settings</Text>
        <Text color={colors.muted}>{footer}</Text>
      </Box>

      <Box marginTop={1} flexDirection="column">
        {lines.map((ln, li) => {
          if (ln.kind === 'header') {
            return (
              <Box key={`h-${ln.label}-${li}`} marginTop={li === 0 ? 0 : 1}>
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
      </Box>

      {curSetting?.help ? (
        <Box marginTop={1}>
          <Text color={colors.muted} wrap="truncate-end">{curSetting.help}</Text>
        </Box>
      ) : null}
    </Box>
  );
};

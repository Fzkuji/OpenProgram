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

export interface SettingsPanelProps {
  rows: SettingRow[];
  /** Persist one setting over the worker WS (action `set_setting`). */
  onSet: (key: string, value: unknown) => void;
  onClose: () => void;
}

/**
 * In-app settings editor for the TUI — the visual counterpart to the
 * `openprogram ports` / `setup` CLI. Rows are schema-driven (one
 * `SettingSpec` server-side becomes one row here), grouped by section.
 * Editing happens inline: toggles flip, enums cycle with ←/→, numbers
 * open a digit buffer on enter. Every change is sent to the worker via
 * `onSet`; the panel is otherwise stateless about values (the parent
 * re-renders `rows` from the `setting_result` the server echoes back).
 */
export const SettingsPanel: React.FC<SettingsPanelProps> = ({ rows, onSet, onClose }) => {
  const colors = useColors();
  const width = usePanelWidth();
  const [index, setIndex] = useState(0);
  const [editKey, setEditKey] = useState<string | null>(null);
  const [buffer, setBuffer] = useState('');

  const n = rows.length;
  const cur = rows[Math.min(index, Math.max(0, n - 1))];

  // Rows in declared order, with a header emitted whenever the group changes.
  const lines = useMemo(() => {
    const out: Array<{ kind: 'header'; group: string } | { kind: 'row'; row: SettingRow; i: number }> = [];
    let last = '';
    rows.forEach((row, i) => {
      if (row.group !== last) {
        out.push({ kind: 'header', group: row.group });
        last = row.group;
      }
      out.push({ kind: 'row', row, i });
    });
    return out;
  }, [rows]);

  const commitNumber = () => {
    if (editKey === null) return;
    const v = buffer.trim();
    if (v.length) onSet(editKey, v);
    setEditKey(null);
    setBuffer('');
  };

  useInput((input, key) => {
    // Number edit mode owns all keys until enter/esc.
    if (editKey !== null) {
      if (key.return) return commitNumber();
      if (key.escape) { setEditKey(null); setBuffer(''); return; }
      if (key.backspace || key.delete) { setBuffer((b) => b.slice(0, -1)); return; }
      if (/^[0-9]$/.test(input)) setBuffer((b) => (b + input).slice(0, 5));
      return;
    }

    if (key.escape) return onClose();
    if (key.upArrow) { setIndex((i) => (i - 1 + n) % n); return; }
    if (key.downArrow) { setIndex((i) => (i + 1) % n); return; }
    if (!cur) return;

    if (cur.widget === 'toggle' && (key.return || input === ' ')) {
      onSet(cur.key, !cur.value);
      return;
    }
    if (cur.widget === 'enum' && (key.leftArrow || key.rightArrow)) {
      const opts = cur.choices ?? [];
      if (!opts.length) return;
      const at = Math.max(0, opts.indexOf(String(cur.value)));
      const next = key.rightArrow
        ? (at + 1) % opts.length
        : (at - 1 + opts.length) % opts.length;
      onSet(cur.key, opts[next]);
      return;
    }
    if (cur.widget === 'number' && key.return) {
      setEditKey(cur.key);
      setBuffer(String(cur.value ?? ''));
      return;
    }
  });

  const renderValue = (row: SettingRow, selected: boolean): React.ReactNode => {
    if (editKey === row.key) {
      return <Text color={colors.primary}>{buffer}▌</Text>;
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
    : cur?.widget === 'toggle' ? '↑↓ move · space toggle · esc close'
    : cur?.widget === 'enum' ? '↑↓ move · ←→ change · esc close'
    : '↑↓ move · enter edit · esc close';

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
              <Box key={`h-${ln.group}-${li}`} marginTop={li === 0 ? 0 : 1}>
                <Text bold color={colors.border}>{ln.group}</Text>
              </Box>
            );
          }
          const selected = ln.i === index;
          const next = ln.row.apply === 'next_start';
          return (
            <Box key={ln.row.key}>
              <Text color={selected ? colors.primary : colors.border}>{selected ? '▌ ' : '  '}</Text>
              <Box width={Math.max(20, Math.floor(width * 0.42))}>
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

      {cur?.help ? (
        <Box marginTop={1}>
          <Text color={colors.muted} wrap="truncate-end">{cur.help}</Text>
        </Box>
      ) : null}
    </Box>
  );
};

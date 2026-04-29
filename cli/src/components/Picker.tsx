import React, { useEffect, useState } from 'react';
import { Box, useInput, RawAnsi } from '../runtime/index';
import chalk from 'chalk';
import { useColors } from '../theme/ThemeProvider.js';
import { usePanelWidth, useTerminalHeight } from '../utils/useTerminalWidth.js';

export interface PickerItem<V> {
  label: string;
  description?: string;
  value: V;
}

export interface PickerProps<V> {
  title: string;
  items: PickerItem<V>[];
  onSelect: (item: PickerItem<V>) => void;
  onCancel: () => void;
  /** Cap on visible rows. Default 12. */
  maxVisible?: number;
}

const FOOTER = 'type to filter · ↑↓ choose · enter pick · esc cancel';
const RESERVE = 2;     // BottomBar + 1-row margin below the picker box
const MIN_INNER = 5;   // floor that fits worst case: title + ↑ + 2 visible + ↓

/**
 * Self-contained picker overlay. The body is built into a single
 * pre-styled string array and emitted via <RawAnsi> as one Yoga leaf —
 * the bordered Box's height equals lines.length + 2, so it never drifts
 * past the visible terminal even on tight resizes.
 *
 * Drop priority when the terminal is short (matches user spec):
 *   1. spacers (2 rows, paired) — go first
 *   2. footer hint                — goes next
 *   3. visible row count          — drops to 2 with scroll indicators last
 * Title and the selected row are never sacrificed.
 *
 * This component assumes it owns the screen space below the BottomBar:
 * REPL hides <Welcome> while a picker is mounted so terminal_rows is
 * effectively the picker's own budget.
 */
export function Picker<V>({
  title, items, onSelect, onCancel, maxVisible = 12,
}: PickerProps<V>): React.ReactElement {
  const colors = useColors();
  const [index, setIndex] = useState(0);
  const [filter, setFilter] = useState('');
  const rows = useTerminalHeight();
  const panelWidth = usePanelWidth();

  const needle = filter.toLowerCase();
  const filtered = needle
    ? items.filter((it) =>
        it.label.toLowerCase().includes(needle) ||
        (it.description?.toLowerCase().includes(needle) ?? false))
    : items;

  useEffect(() => { if (index >= filtered.length) setIndex(0); }, [filtered.length, index]);

  useInput((input, key) => {
    if (key.escape) return onCancel();
    if (key.return) { const it = filtered[index]; if (it) onSelect(it); return; }
    const n = Math.max(1, filtered.length);
    if (key.upArrow) return setIndex((i) => (i - 1 + n) % n);
    if (key.downArrow) return setIndex((i) => (i + 1) % n);
    if (key.backspace || key.delete) return setFilter((f) => f.slice(0, -1));
    if (input && !key.ctrl && !key.meta) setFilter((f) => f + input);
  });

  // Pick the richest layout whose row count fits inner budget. The floor
  // (MIN_INNER) is sized so the worst-case minimum layout (visible=2 plus
  // both ↑ and ↓ indicators) always fits — guarantees ansiLines.length
  // never exceeds inner, so content can't leak past the bordered box.
  const inner = Math.max(MIN_INNER, rows - RESERVE - 2 /* border */);
  const wanted = Math.min(maxVisible, filtered.length);
  const layout =
       fits(rowsNeeded(wanted, filtered.length, true,  true),  inner) ? { spacers: true,  footer: true,  visible: wanted }
     : fits(rowsNeeded(wanted, filtered.length, false, true),  inner) ? { spacers: false, footer: true,  visible: wanted }
     : fits(rowsNeeded(wanted, filtered.length, false, false), inner) ? { spacers: false, footer: false, visible: wanted }
     : { spacers: false, footer: false, visible: Math.max(2, inner - 1 - 2) };

  const off = Math.max(0, Math.min(index - Math.floor(layout.visible / 2),
                                   filtered.length - layout.visible));
  const above = Math.max(0, off);
  const below = Math.max(0, filtered.length - off - layout.visible);
  const slice = filtered.slice(off, off + layout.visible);

  const innerWidth = Math.max(20, panelWidth - 4);
  const labelWidth = Math.max(8, Math.min(28, Math.floor(innerWidth / 3)));

  const cP = chalk.hex(colors.primary);
  const cM = chalk.hex(colors.muted);
  const cB = chalk.hex(colors.border);

  const indicator = filter
    ? `filter: ${filter} · ${filtered.length === 0 ? '(no matches)' : `${index + 1}/${filtered.length}`}`
    : (filtered.length === 0 ? '(no matches)' : `${index + 1}/${filtered.length}`);
  const pad = Math.max(1, innerWidth - title.length - indicator.length);

  const lines: string[] = [];
  lines.push(`${cP.bold(title)}${' '.repeat(pad)}${cM(indicator)}`);
  if (layout.spacers) lines.push('');
  if (above > 0) lines.push(`  ${cB('↑')} ${cM(`${above} more`)}`);
  for (let i = 0; i < slice.length; i++) {
    const it = slice[i]!;
    const sel = off + i === index;
    const label = fit(it.label, labelWidth);
    const desc = it.description ? `  ${it.description}` : '';
    lines.push(sel
      ? `${cP('▌ ')}${cP.bold(label)}${cP(desc)}`
      : `  ${label}${cM(desc)}`);
  }
  if (below > 0) lines.push(`  ${cB('↓')} ${cM(`${below} more`)}`);
  if (layout.spacers) lines.push('');
  if (layout.footer) lines.push(cM(FOOTER));

  return (
    <Box
      flexDirection="column"
      borderStyle="round"
      borderColor={colors.primary}
      paddingX={1}
      marginBottom={1}
      width={panelWidth}
      flexShrink={0}
    >
      <RawAnsi lines={lines} width={innerWidth} />
    </Box>
  );
}

function rowsNeeded(visible: number, total: number, spacers: boolean, footer: boolean): number {
  const indicators = visible < total ? 2 : 0; // worst case: both ↑ and ↓
  return 1 /* title */ + (spacers ? 2 : 0) + indicators + visible + (footer ? 1 : 0);
}

function fits(needed: number, budget: number): boolean { return needed <= budget; }

function fit(s: string, w: number): string {
  return s.length === w ? s : s.length > w ? s.slice(0, w) : s + ' '.repeat(w - s.length);
}

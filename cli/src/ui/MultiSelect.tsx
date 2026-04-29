/**
 * <MultiSelect> — pick multiple items with space-toggle, enter-confirm.
 *
 * The standard pattern for "configure tools" / "enable channels" /
 * "tag filters" UIs. Visual is similar to <Select> but each row
 * renders [x]/[ ] to indicate selection state; ↑↓ moves cursor,
 * space toggles, enter commits the entire selection set, esc
 * cancels.
 *
 * Filter (typed text) narrows visible items just like Select.
 *
 * The component is implemented with the same RawAnsi technique as
 * Picker — single Yoga leaf, no per-row Box churn during cursor
 * moves (matters when there are 50+ items).
 */
import React, { useEffect, useState } from 'react';
import { Box, RawAnsi, useInput } from '../runtime/index';
import chalk from 'chalk';
import { useColors } from '../theme/ThemeProvider.js';
import { useTerminalSize } from './hooks.js';

export interface MultiSelectOption<V = string> {
  label: string;
  description?: string;
  value: V;
  /** Initial state. Default false. */
  initiallyChecked?: boolean;
}

export interface MultiSelectProps<V = string> {
  title: string;
  options: MultiSelectOption<V>[];
  onSubmit: (values: V[]) => void;
  onCancel?: () => void;
  maxVisible?: number;
}

const FOOTER = 'space toggle · ↑↓ move · enter confirm · esc cancel';

export function MultiSelect<V = string>({
  title, options, onSubmit, onCancel, maxVisible = 12,
}: MultiSelectProps<V>): React.ReactElement {
  const colors = useColors();
  const { columns: cols, rows: termRows } = useTerminalSize();
  const [index, setIndex] = useState(0);
  const [filter, setFilter] = useState('');
  const [checked, setChecked] = useState<Set<number>>(() => {
    const init = new Set<number>();
    options.forEach((o, i) => { if (o.initiallyChecked) init.add(i); });
    return init;
  });

  // Maintain mapping between filtered display index and original
  // index. Toggling a filtered row should toggle the original.
  const needle = filter.toLowerCase();
  const filteredPairs = options
    .map((o, i) => ({ o, i }))
    .filter(({ o }) =>
      !needle
        || o.label.toLowerCase().includes(needle)
        || (o.description?.toLowerCase().includes(needle) ?? false));

  useEffect(() => {
    if (index >= filteredPairs.length) setIndex(0);
  }, [filteredPairs.length, index]);

  useInput((input, key) => {
    if (key.escape) return onCancel?.();
    if (key.return) {
      const out: V[] = [];
      checked.forEach((i) => out.push(options[i]!.value));
      onSubmit(out);
      return;
    }
    const n = Math.max(1, filteredPairs.length);
    if (key.upArrow) return setIndex((i) => (i - 1 + n) % n);
    if (key.downArrow) return setIndex((i) => (i + 1) % n);
    if (input === ' ') {
      const orig = filteredPairs[index]?.i;
      if (orig === undefined) return;
      setChecked((s) => {
        const next = new Set(s);
        if (next.has(orig)) next.delete(orig);
        else next.add(orig);
        return next;
      });
      return;
    }
    if (key.backspace || key.delete) return setFilter((f) => f.slice(0, -1));
    if (input && !key.ctrl && !key.meta && input.length === 1 && input >= '!') {
      setFilter((f) => f + input);
    }
  });

  const panelWidth = Math.max(24, cols);
  const innerWidth = Math.max(20, panelWidth - 4);
  const visible = Math.min(maxVisible, filteredPairs.length);
  const off = Math.max(0, Math.min(
    index - Math.floor(visible / 2),
    filteredPairs.length - visible,
  ));

  const cP = chalk.hex(colors.primary);
  const cM = chalk.hex(colors.muted);

  const lines: string[] = [];
  const indicator = filter
    ? `filter: ${filter} · ${filteredPairs.length}/${options.length} · ${checked.size} checked`
    : `${checked.size}/${options.length} checked`;
  const pad = Math.max(1, innerWidth - title.length - indicator.length);
  lines.push(`${cP.bold(title)}${' '.repeat(pad)}${cM(indicator)}`);
  lines.push('');

  const slice = filteredPairs.slice(off, off + visible);
  for (let i = 0; i < slice.length; i++) {
    const { o, i: orig } = slice[i]!;
    const sel = off + i === index;
    const mark = checked.has(orig) ? '[x]' : '[ ]';
    const desc = o.description ? `  ${o.description}` : '';
    lines.push(sel
      ? `${cP('▌ ')}${cP.bold(`${mark} ${o.label}`)}${cP(desc)}`
      : `  ${mark} ${o.label}${cM(desc)}`);
  }

  lines.push('');
  lines.push(cM(FOOTER));

  // Don't grow taller than the terminal; the lines array is already
  // capped by maxVisible. termRows is consulted to defensively warn
  // future maintainers that ridiculously tall MultiSelects need
  // pagination — we leave that to a later iteration.
  void termRows;

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

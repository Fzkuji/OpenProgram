/**
 * FormPicker — runtime.form's multi-field form rendered in the input
 * slot (the TUI counterpart of the web composer's FormMode). A
 * kind="form" question carries a flat-object field schema (field name →
 * {type, title, enum, default, …}); this lays the fields out vertically
 * and lets the user fill them in one screen:
 *   - ↑↓            move between fields
 *   - text / number type to edit; Backspace deletes
 *   - boolean       Space toggles
 *   - enum          ←→ cycles the choices
 *   - Enter         submit all fields → question_reply { answer: {…} }
 *   - Esc           question_reject
 *
 * Mirrors web/components/chat/composer/modes/question/form-mode.tsx so
 * both surfaces resolve runtime.form identically.
 */
import React, { useState } from 'react';
import { Box, RawAnsi, useInput } from '../../../runtime/index';
import { useColors } from '../../../theme/ThemeProvider.js';
import { paint, paintBold } from '../../../theme/paint.js';
import { usePanelWidth } from '../../../utils/useTerminalWidth.js';
import type { BackendClient } from '../../../ws/client.js';
import type { PendingDecision, FormFieldSchema } from '../types.js';
import { replyAction, rejectAction } from '../questionDecision.js';

type FieldValue = string | boolean;

interface FormPickerProps {
  client: BackendClient;
  decision: PendingDecision;
  onResolve: (id: string) => void;
}

function seed(f: FormFieldSchema): FieldValue {
  if (f.default !== undefined) return typeof f.default === 'boolean' ? f.default : String(f.default);
  if (f.type === 'boolean') return false;
  if (f.enum && f.enum.length) return f.enum[0]!;
  return '';
}

export function FormPicker({ client, decision: q, onResolve }: FormPickerProps): React.ReactElement {
  const colors = useColors();
  const panelWidth = usePanelWidth();
  const fields = q.schema ?? {};
  const names = Object.keys(fields); // insertion order = display order
  const [cursor, setCursor] = useState(0);
  const [values, setValues] = useState<Record<string, FieldValue>>(() => {
    const init: Record<string, FieldValue> = {};
    for (const n of names) init[n] = seed(fields[n]!);
    return init;
  });

  const setVal = (name: string, v: FieldValue): void =>
    setValues((cur) => ({ ...cur, [name]: v }));

  const submit = (): void => {
    const answer: Record<string, unknown> = {};
    for (const n of names) {
      const f = fields[n]!;
      const v = values[n];
      if ((f.type === 'integer' || f.type === 'number') && typeof v === 'string') {
        answer[n] = v === '' ? null : Number(v);
      } else {
        answer[n] = v;
      }
    }
    client.send(replyAction(q.id, answer as never));
    onResolve(q.id);
  };

  useInput((input, key) => {
    if (key.escape) {
      client.send(rejectAction(q.id));
      onResolve(q.id);
      return;
    }
    if (key.return) return submit();
    const n = Math.max(1, names.length);
    if (key.upArrow) return setCursor((i) => (i - 1 + n) % n);
    if (key.downArrow) return setCursor((i) => (i + 1) % n);

    const name = names[cursor];
    if (name === undefined) return;
    const f = fields[name]!;

    if (f.type === 'boolean') {
      if (input === ' ') return setVal(name, !values[name]);
      return;
    }
    if (f.enum && f.enum.length) {
      const opts = f.enum;
      const idx = Math.max(0, opts.indexOf(String(values[name])));
      if (key.leftArrow) return setVal(name, opts[(idx - 1 + opts.length) % opts.length]!);
      if (key.rightArrow) return setVal(name, opts[(idx + 1) % opts.length]!);
      return;
    }
    // text / number free edit
    if (key.backspace || key.delete) {
      return setVal(name, String(values[name] ?? '').slice(0, -1));
    }
    if (input && !key.ctrl && !key.meta) {
      let t = input.replace(/[ -]/g, '');
      if (f.type === 'integer' || f.type === 'number') t = t.replace(/[^0-9.\-]/g, '');
      if (t) setVal(name, String(values[name] ?? '') + t);
    }
  });

  const innerWidth = Math.max(20, panelWidth - 4);
  const cP = paint(colors.primary);
  const cPB = paintBold(colors.primary);
  const cM = paint(colors.muted);
  const cT = paint(colors.text);

  const lines: string[] = [];
  lines.push(cPB('Fill in the form'));
  lines.push(cPB(q.prompt));
  if (q.detail) lines.push(cM(q.detail));
  lines.push('');

  for (let i = 0; i < names.length; i++) {
    const name = names[i]!;
    const f = fields[name]!;
    const sel = i === cursor;
    const label = f.title || name;
    let valStr: string;
    if (f.type === 'boolean') {
      valStr = values[name] ? '[x]' : '[ ]';
    } else if (f.enum && f.enum.length) {
      valStr = `‹ ${String(values[name])} ›`;
    } else {
      valStr = String(values[name] ?? '') + (sel ? '█' : '');
    }
    const row = `${label}: ${valStr}`;
    lines.push(sel ? `${cP('▌ ')}${cPB(row)}` : `  ${cT(row)}`);
    if (sel && f.description) lines.push(`    ${cM(f.description)}`);
  }

  lines.push('');
  lines.push(cM('↑↓ field · type/←→/space edit · enter submit · esc cancel'));

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

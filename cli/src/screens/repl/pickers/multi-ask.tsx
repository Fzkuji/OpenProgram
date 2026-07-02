/**
 * MultiAskPicker — runtime.ask_many in the TUI input slot: a packed group
 * of questions answered one screen with switching, submitted together.
 *
 * Mirrors the web MultiAskMode (multi-ask-mode.tsx):
 *   - ←/→ (or Tab)  switch between questions
 *   - ↑/↓           move within the current question's options
 *   - Space         toggle (multi); Enter on an option picks it (single →
 *                   auto-advance to next), or submits the whole group once
 *                   every question is answered
 *   - type          free text for the current question (allow_custom)
 *   - Ctrl+Enter    submit the whole group (when all answered)
 *   - Esc           reject the group
 *
 * Answer is a list (one per question): str for single / free-text,
 * string[] for multi. Sent as question_reply { id, answer: [...] }.
 */
import React, { useState } from 'react';
import { Box, RawAnsi, useInput } from '../../../runtime/index';
import { useColors } from '../../../theme/ThemeProvider.js';
import { paint, paintBold } from '../../../theme/paint.js';
import { usePanelWidth } from '../../../utils/useTerminalWidth.js';
import type { BackendClient } from '../../../ws/client.js';
import type { PendingDecision, AskOne } from '../types.js';
import { replyAction, rejectAction } from '../questionDecision.js';

interface MultiAskPickerProps {
  client: BackendClient;
  decision: PendingDecision;
  onResolve: (id: string) => void;
}

type One = { picked: Set<string>; custom: string };

function answered(a: One): boolean {
  return a.picked.size > 0 || a.custom.trim().length > 0;
}

function finalize(q: AskOne, a: One): string | string[] {
  const arr = Array.from(a.picked);
  if (a.custom.trim()) arr.push(a.custom.trim());
  return q.multi ? arr : (arr[0] ?? '');
}

export function MultiAskPicker({ client, decision: q, onResolve }: MultiAskPickerProps): React.ReactElement {
  const colors = useColors();
  const panelWidth = usePanelWidth();
  const questions = q.questions ?? [];
  const [qi, setQi] = useState(0);   // current question index
  const [oi, setOi] = useState(0);   // option cursor within current question
  const [answers, setAnswers] = useState<One[]>(() =>
    questions.map(() => ({ picked: new Set<string>(), custom: '' })),
  );

  const cur = questions[qi];
  const curAns = answers[qi] ?? { picked: new Set<string>(), custom: '' };
  const allAnswered = answers.every(answered);

  const patch = (i: number, next: Partial<One>): void =>
    setAnswers((cur) => cur.map((a, k) => (k === i ? { ...a, ...next } : a)));

  const submit = (): void => {
    const value = questions.map((qq, i) => finalize(qq, answers[i]!));
    client.send(replyAction(q.id, value as never));
    onResolve(q.id);
  };

  useInput((input, key) => {
    if (key.escape) {
      client.send(rejectAction(q.id));
      onResolve(q.id);
      return;
    }
    // Ctrl+Enter submits the whole group when complete.
    if (key.return && (key.ctrl || key.meta)) {
      if (allAnswered) submit();
      return;
    }
    // Switch questions: ←/→ or Tab (shift+tab goes back). The REPL's
    // global permission-cycle skips shift+tab while a picker is open,
    // so there's no double-fire here.
    const nq = Math.max(1, questions.length);
    if (key.leftArrow || (key.tab && key.shift)) { setQi((i) => (i - 1 + nq) % nq); setOi(0); return; }
    if (key.rightArrow || (key.tab && !key.shift)) { setQi((i) => (i + 1) % nq); setOi(0); return; }

    if (!cur) return;
    const opts = cur.options;
    const no = Math.max(1, opts.length);
    if (key.upArrow) return setOi((i) => (i - 1 + no) % no);
    if (key.downArrow) return setOi((i) => (i + 1) % no);

    if (cur.multi && input === ' ') {
      const opt = opts[oi];
      if (opt === undefined) return;
      const next = new Set(curAns.picked);
      next.has(opt) ? next.delete(opt) : next.add(opt);
      return patch(qi, { picked: next });
    }
    if (key.return) {
      // On an option: single → pick + auto-advance; multi → just (de)pick.
      const opt = opts[oi];
      if (opt !== undefined && !cur.multi) {
        patch(qi, { picked: new Set([opt]), custom: '' });
        if (qi < questions.length - 1) { setQi(qi + 1); setOi(0); }
        else if (allAnswered) submit();
        return;
      }
      if (allAnswered) submit();
      return;
    }
    // Free text.
    if (cur.allow_custom) {
      if (key.backspace || key.delete) return patch(qi, { custom: curAns.custom.slice(0, -1) });
      if (input && !key.ctrl && !key.meta) {
        const t = input.replace(/[ -]/g, '');
        if (t) patch(qi, { custom: curAns.custom + t });
      }
    }
  });

  const innerWidth = Math.max(20, panelWidth - 4);
  const cP = paint(colors.primary);
  const cPB = paintBold(colors.primary);
  const cM = paint(colors.muted);
  const cT = paint(colors.text);

  const lines: string[] = [];
  // Header: badge + progress dots + N/M.
  const dots = questions.map((_, i) =>
    i === qi ? '●' : answered(answers[i]!) ? '◉' : '○').join(' ');
  lines.push(`${cPB('需要你的输入')}   ${cM(dots)}  ${cM(`${qi + 1}/${questions.length}`)}`);
  if (q.prompt) lines.push(cM(q.prompt));
  lines.push('');

  if (cur) {
    lines.push(cPB(cur.prompt));
    for (let i = 0; i < cur.options.length; i++) {
      const opt = cur.options[i]!;
      const sel = i === oi;
      const mark = cur.multi ? (curAns.picked.has(opt) ? '[x] ' : '[ ] ') : '';
      lines.push(sel ? `${cP('▌ ')}${cPB(mark + opt)}` : `  ${cT(mark + opt)}`);
    }
    if (cur.allow_custom) {
      lines.push(`  ${cM('自由输入: ')}${cT(curAns.custom)}${cP('█')}`);
    }
  }

  lines.push('');
  lines.push(cM('←→/tab 切题 · ↑↓ 选 · space 多选 · enter 选/提交 · ⌃enter 提交 · esc 拒绝'));

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

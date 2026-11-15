/**
 * QuestionPicker — runtime.ask / confirm / tool-approval rendered in the
 * input slot (the TUI counterpart of the web composer's question /
 * approval mode). One self-contained component owns its own `useInput`
 * so the prompt, option list, and free-text line coordinate on one
 * screen — unlike Picker / MultiSelect / LineInput, each of which grabs
 * `useInput` exclusively and can't be stacked.
 *
 * Behaviour mirrors web/components/chat/composer/modes/question/
 * question-mode.tsx so both surfaces resolve identically:
 *   - options + single / confirm → ↑↓ choose, Enter submits that option
 *   - options + multi            → ↑↓ move, Space toggles, Enter submits set
 *   - allow_custom               → start typing to enter free text; Enter
 *                                  submits it (single) or appends to the set
 *   - approval (kind)            → a danger summary (tool + args) above
 *                                  the allow / reject options
 *   - Esc / reject               → question_reject
 *
 * Wire (matches the web contract, ws/client.ts):
 *   submit → { action: 'question_reply', id, answer }   (string | string[])
 *   reject → { action: 'question_reject', id }
 * The queue head is popped optimistically (onResolve) so the next
 * decision surfaces without waiting for the backend's question.replied
 * broadcast.
 */
import React, { useState } from 'react';
import { Box, RawAnsi, useInput } from '../../../runtime/index';
import { useColors } from '../../../theme/ThemeProvider.js';
import { paint, paintBold } from '../../../theme/paint.js';
import { usePanelWidth } from '../../../utils/useTerminalWidth.js';
import type { BackendClient } from '../../../ws/client.js';
import type { PendingDecision } from '../types.js';
import { replyAction, rejectAction } from '../questionDecision.js';
import { FormPicker } from './form.js';
import { MultiAskPicker } from './multi-ask.js';

interface QuestionPickerProps {
  client: BackendClient;
  decision: PendingDecision;
  /** Pop this decision from the queue (optimistic, local). */
  onResolve: (id: string) => void;
}

/** Truncate an args object to a one-line danger summary, head+tail. */
function summariseArgs(args: Record<string, unknown> | undefined): string {
  if (!args || Object.keys(args).length === 0) return '';
  let s: string;
  try {
    s = JSON.stringify(args);
  } catch {
    s = String(args);
  }
  const MAX = 160;
  if (s.length <= MAX) return s;
  return s.slice(0, MAX - 20) + ' … ' + s.slice(-16);
}

// Synthetic approval row: sends the server's approve option with
// scope='always', which persists a project-level allow rule for the tool
// (same as the web's 总是允许 button). Display-only string — the wire
// answer is always q.options[0].
const ALWAYS_ALLOW = 'always allow this tool (project rule)';

export function QuestionPicker({
  client, decision: q, onResolve,
}: QuestionPickerProps): React.ReactElement {
  const colors = useColors();
  const panelWidth = usePanelWidth();
  const [index, setIndex] = useState(0);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [custom, setCustom] = useState('');
  // Approval cards get the extra always-allow row after the approve
  // option; everything below operates on this display list.
  const options =
    q.kind === 'approval' && q.options.length >= 1
      ? [q.options[0]!, ALWAYS_ALLOW, ...q.options.slice(1)]
      : q.options;
  // typing === true once the user enters free text, so arrows/Enter act
  // on the text line instead of the option list.
  const [typing, setTyping] = useState(q.allow_custom && options.length === 0);

  const submit = (answer: string | string[]): void => {
    client.send(replyAction(q, answer));
    onResolve(q.id);
  };
  const submitAlwaysAllow = (): void => {
    client.send(replyAction(q, { answer: q.options[0]!, scope: 'always' }));
    onResolve(q.id);
  };
  const reject = (): void => {
    client.send(rejectAction(q));
    onResolve(q.id);
  };

  const submitMultiOrCustom = (): void => {
    if (q.multi) {
      const arr = Array.from(picked);
      if (custom.trim()) arr.push(custom.trim());
      if (arr.length) submit(arr);
    } else if (custom.trim()) {
      submit(custom.trim());
    }
  };

  useInput((input, key) => {
    if (key.escape) return reject();

    // Free-text editing mode (allow_custom). Enter submits (single) or,
    // for multi, folds the text into the checked set on the shared
    // submit. Backspace edits; printable chars append.
    if (typing) {
      if (key.return) {
        if (q.multi) submitMultiOrCustom();
        else if (custom.trim()) submit(custom.trim());
        return;
      }
      if (key.backspace || key.delete) return setCustom((c) => c.slice(0, -1));
      // Toggle back to the option list with up-arrow when text is empty.
      if (key.upArrow && custom.length === 0 && options.length > 0) {
        return setTyping(false);
      }
      if (input && !key.ctrl && !key.meta) {
        const t = input.replace(/[\x00-\x1f\x7f]/g, '');
        if (t) setCustom((c) => c + t);
      }
      return;
    }

    // Option-list navigation.
    const n = Math.max(1, options.length);
    if (key.upArrow) return setIndex((i) => (i - 1 + n) % n);
    if (key.downArrow) {
      // Past the last option, drop into the free-text line if allowed.
      if (index === n - 1 && q.allow_custom) return setTyping(true);
      return setIndex((i) => (i + 1) % n);
    }
    if (q.multi && input === ' ') {
      const opt = options[index];
      if (opt === undefined) return;
      return setPicked((s) => {
        const next = new Set(s);
        next.has(opt) ? next.delete(opt) : next.add(opt);
        return next;
      });
    }
    if (key.return) {
      const opt = options[index];
      if (opt === undefined) return;
      if (opt === ALWAYS_ALLOW) return submitAlwaysAllow();
      if (q.multi) submitMultiOrCustom();
      else submit(opt); // single / confirm — pick submits immediately
      return;
    }
    // Any printable key jumps into free text (when allowed).
    if (q.allow_custom && input && !key.ctrl && !key.meta && input >= ' ') {
      setTyping(true);
      const t = input.replace(/[\x00-\x1f\x7f]/g, '');
      if (t) setCustom(t);
    }
  });

  const innerWidth = Math.max(20, panelWidth - 4);
  const cP = paint(colors.primary);
  const cPB = paintBold(colors.primary);
  const cM = paint(colors.muted);
  const cWarn = paint(colors.warning);

  const badge =
    q.kind === 'approval' ? '⚠ Approve tool'
    : q.kind === 'confirm' ? 'Confirm'
    : 'Your input needed';

  const lines: string[] = [];
  // A sub-agent's request names it beside the heading.
  const heading = q.askedBy ? `${badge} · ${q.askedBy}` : badge;
  lines.push(q.kind === 'approval' ? cWarn(heading) : cPB(heading));
  lines.push(cPB(q.prompt));
  if (q.detail) lines.push(cM(q.detail));
  // approval danger summary: tool + args.
  if (q.kind === 'approval' && (q.tool || q.args)) {
    if (q.tool) lines.push(cWarn(`tool: ${q.tool}`));
    const a = summariseArgs(q.args);
    if (a) lines.push(cM(a));
  }
  if (options.length > 0) lines.push('');

  for (let i = 0; i < options.length; i++) {
    const opt = options[i]!;
    const sel = !typing && i === index;
    const mark = q.multi ? (picked.has(opt) ? '[x] ' : '[ ] ') : '';
    lines.push(sel
      ? `${cP('▌ ')}${cPB(mark + opt)}`
      : `  ${mark}${opt}`);
  }

  if (q.allow_custom) {
    lines.push('');
    const prefix = options.length ? 'or type: ' : 'type: ';
    const cursor = typing ? '█' : '';
    lines.push(typing
      ? `${cP('▌ ')}${cM(prefix)}${paint(colors.text)(custom)}${cP(cursor)}`
      : `  ${cM(prefix)}${cM('(start typing)')}`);
  }

  lines.push('');
  const footer = q.multi
    ? 'space toggle · ↑↓ move · enter submit · esc reject'
    : q.allow_custom
    ? '↑↓ choose · type for free text · enter submit · esc reject'
    : '↑↓ choose · enter submit · esc reject';
  lines.push(cM(footer));

  return (
    <Box
      flexDirection="column"
      borderStyle="round"
      borderColor={q.kind === 'approval' ? colors.warning : colors.primary}
      paddingX={1}
      marginBottom={1}
      width={panelWidth}
      flexShrink={0}
    >
      <RawAnsi lines={lines} width={innerWidth} />
    </Box>
  );
}

/** Router entry — renders the queue head. kind="form" goes to the
 *  multi-field FormPicker; ask/confirm/approval to QuestionPicker. */
export function buildQuestionPicker(
  client: BackendClient,
  decision: PendingDecision | undefined,
  onResolve: (id: string) => void,
): React.ReactElement | null {
  if (!decision) return null;
  if (decision.kind === 'form') {
    return (
      <FormPicker
        key={decision.id}
        client={client}
        decision={decision}
        onResolve={onResolve}
      />
    );
  }
  if (decision.kind === 'ask_many') {
    return (
      <MultiAskPicker
        key={decision.id}
        client={client}
        decision={decision}
        onResolve={onResolve}
      />
    );
  }
  return (
    <QuestionPicker
      key={decision.id}
      client={client}
      decision={decision}
      onResolve={onResolve}
    />
  );
}

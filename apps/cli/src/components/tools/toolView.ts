/**
 * What a tool call looks like in the transcript, as plain data.
 *
 * Every tool gets one header line — status glyph, bold name, the
 * argument that says what it acted on — and under a `⎿` gutter either a
 * quantified summary ("Read 52 lines", "Found 8 files", "Added 5 lines,
 * removed 2 lines") or its output, folded to a few lines with an
 * accurate "… +N lines (ctrl+o to expand)". Claude Code's per-tool
 * renderers set the summaries; Codex sets the head-plus-count fold;
 * opencode the split between one-line tools (read, search) and block
 * tools (bash, edit, agent, todo). See
 * docs/reference/design/cli/tui-upgrade.html.
 */
import type { ToolCall, TodoItem } from '../Turn.js';
import { changeSummary, lineDiff, patchRows, type DiffRow } from './diff.js';

export type Tone = 'text' | 'muted' | 'error' | 'add' | 'remove' | 'active' | 'done';

export type ViewLine =
  | { kind: 'text'; text: string; tone: Tone; bold?: boolean; strike?: boolean }
  | { kind: 'diff'; row: DiffRow; numberWidth: number };

export interface ToolView {
  /** Display name, as the tool is called. */
  name: string;
  /** What it acted on: a path, a command, a pattern. */
  arg?: string;
  lines: ViewLine[];
}

/** Lines a folded result keeps (Claude Code's three). */
export const FOLD_LINES = 3;
/** Rows a folded diff keeps. */
export const FOLD_DIFF_ROWS = 12;

/** The kind of renderer a tool name gets; other providers' spellings
 *  (Claude Code's `Bash`, `Edit`, `TodoWrite`, `Task`) map onto ours. */
export type ToolKind =
  | 'bash' | 'read' | 'edit' | 'write' | 'patch' | 'search' | 'list'
  | 'agent' | 'todo' | 'generic';

export function toolKind(tool: string): ToolKind {
  const name = tool.toLowerCase();
  if (name === 'bash' || name === 'shell' || name === 'exec_command' || name === 'execute_code') return 'bash';
  if (name === 'read' || name === 'notebookread') return 'read';
  if (name === 'edit' || name === 'multiedit' || name === 'str_replace') return 'edit';
  if (name === 'write') return 'write';
  if (name === 'apply_patch') return 'patch';
  if (name === 'grep' || name === 'glob' || name === 'search') return 'search';
  if (name === 'list' || name === 'ls') return 'list';
  if (name === 'agent' || name === 'task') return 'agent';
  if (name.startsWith('todo')) return 'todo';
  return 'generic';
}

export function parseArgs(input?: string): Record<string, unknown> {
  if (!input) return {};
  try {
    const value = JSON.parse(input);
    return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
  } catch {
    return {};
  }
}

const str = (v: unknown): string | undefined => (typeof v === 'string' && v ? v : undefined);

const outputLines = (text: string | undefined): string[] => {
  if (!text) return [];
  const lines = text.replace(/\s+$/, '').split('\n');
  return lines.length === 1 && lines[0] === '' ? [] : lines;
};

const firstLine = (text: string): string => text.split('\n', 1)[0] ?? '';

function argOf(kind: ToolKind, args: Record<string, unknown>, raw?: string): string | undefined {
  switch (kind) {
    case 'bash': return str(args.command) ?? str(args.cmd) ?? str(args.code);
    case 'read':
    case 'edit':
    case 'write': return str(args.file_path) ?? str(args.path) ?? str(args.notebook_path);
    case 'patch': {
      const patch = str(args.patch) ?? str(args.input) ?? '';
      const files = [...patch.matchAll(/^\*\*\* (?:Add|Update|Delete) File: (.+)$/gm)].map((m) => m[1]!.trim());
      return files.length ? files.join(', ') : undefined;
    }
    case 'search': {
      const pattern = str(args.pattern) ?? str(args.query);
      const where = str(args.path);
      return pattern ? (where ? `"${pattern}" in ${where}` : `"${pattern}"`) : undefined;
    }
    case 'list': return str(args.path);
    case 'agent': return str(args.description) ?? (str(args.prompt) ? firstLine(str(args.prompt)!) : undefined);
    case 'todo': return str(args.subject) ?? (str(args.todo_id) ? `#${str(args.todo_id)}` : undefined);
    default: {
      const first = Object.values(args).find((v) => typeof v === 'string' && v) as string | undefined;
      if (first) return firstLine(first);
      return raw && raw !== '{}' ? raw : undefined;
    }
  }
}

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`;

/** Output folded to FOLD_LINES with a count of the rest. */
function folded(
  lines: string[],
  expanded: boolean,
  expandKey: string,
  tone: Tone,
  total?: number,
): ViewLine[] {
  const all = total && total > lines.length ? total : lines.length;
  // One hidden line costs the same row as the note about it.
  const keep = expanded || all <= FOLD_LINES + 1 ? lines.length : FOLD_LINES;
  const out: ViewLine[] = lines.slice(0, keep).map((text) => ({ kind: 'text', text, tone }));
  const hidden = all - keep;
  if (hidden > 0) {
    const hint = expanded || !expandKey ? '' : ` (${expandKey} to expand)`;
    out.push({ kind: 'text', text: `… +${plural(hidden, 'line')}${hint}`, tone: 'muted' });
  }
  return out;
}

const summary = (text: string, tone: Tone = 'muted'): ViewLine => ({ kind: 'text', text, tone });

function diffLines(rows: DiffRow[], expanded: boolean, expandKey: string): ViewLine[] {
  const numbers = rows.flatMap((r) => (r.kind === 'gap' ? [] : [r.oldNo ?? 0, r.newNo ?? 0]));
  const numberWidth = Math.max(0, ...numbers.map((n) => (n ? String(n).length : 0)));
  const keep = expanded || rows.length <= FOLD_DIFF_ROWS + 1 ? rows.length : FOLD_DIFF_ROWS;
  const out: ViewLine[] = rows.slice(0, keep).map((row) => ({ kind: 'diff', row, numberWidth }));
  if (rows.length > keep) {
    const hint = expandKey ? ` (${expandKey} to expand)` : '';
    out.push(summary(`… +${plural(rows.length - keep, 'line')}${hint}`));
  }
  return out;
}

const TODO_MARK: Record<string, string> = { completed: '☒', in_progress: '◼', pending: '☐' };

function boardLines(board: TodoItem[]): ViewLine[] {
  return board.map((item) => ({
    kind: 'text' as const,
    text: `${TODO_MARK[item.status] ?? '☐'} ${item.subject}`,
    tone: item.status === 'completed' ? 'muted' as const : item.status === 'in_progress' ? 'active' as const : 'text' as const,
    bold: item.status === 'in_progress',
    strike: item.status === 'completed',
  }));
}

/** Todos written by a Claude-Code-style `TodoWrite` call's own arguments. */
function boardFromArgs(args: Record<string, unknown>): TodoItem[] | undefined {
  if (!Array.isArray(args.todos)) return undefined;
  return args.todos.flatMap((t) => {
    if (!t || typeof t !== 'object') return [];
    const row = t as Record<string, unknown>;
    const subject = str(row.content) ?? str(row.subject) ?? str(row.title);
    return subject ? [{ subject, status: str(row.status) ?? 'pending' }] : [];
  });
}

export function formatDuration(ms: number): string {
  const s = Math.max(0, Math.round(ms / 1000));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  return `${m}m ${s % 60}s`;
}

export function toolView(call: ToolCall, opts: { expanded?: boolean; expandKey?: string } = {}): ToolView {
  const expanded = !!opts.expanded;
  const expandKey = opts.expandKey ?? '';
  const kind = toolKind(call.tool);
  const args = parseArgs(call.input);
  // An input that is not JSON (an older frame, a provider's own summary)
  // is the argument as it stands.
  const plainInput = call.input && !call.input.trim().startsWith('{') ? firstLine(call.input) : undefined;
  const arg = argOf(kind, args, call.input) ?? plainInput;
  const view: ToolView = { name: call.tool, arg, lines: [] };
  const result = call.result;
  const lines = outputLines(result);

  if (call.status === 'error') {
    view.lines = folded(lines.length ? lines : ['failed'], expanded, expandKey, 'error', call.totalLines);
    return view;
  }

  switch (kind) {
    case 'bash': {
      if (call.status === 'running') {
        view.lines = lines.length ? folded(lines, expanded, expandKey, 'muted') : [summary('Running…')];
      } else {
        view.lines = lines.length
          ? folded(lines, expanded, expandKey, 'muted', call.totalLines)
          : [summary('(no output)')];
      }
      return view;
    }
    case 'read': {
      if (call.status === 'running') { view.lines = [summary('Reading…')]; return view; }
      const numbered = lines.filter((l) => /^\s*\d+\t/.test(l));
      view.lines = [summary(numbered.length ? `Read ${plural(numbered.length, 'line')}` : firstLine(result ?? '') || 'Read')];
      if (expanded) view.lines.push(...lines.map((text) => ({ kind: 'text' as const, text, tone: 'muted' as const })));
      return view;
    }
    case 'search':
    case 'list': {
      if (call.status === 'running') { view.lines = [summary('Searching…')]; return view; }
      const hits = lines.filter((l) => l && l !== 'No matches');
      const contentMode = str(args.output_mode) === 'content';
      const noun = kind === 'list' ? 'entry' : contentMode ? 'match' : 'file';
      const count = noun === 'entry' ? `${hits.length} ${hits.length === 1 ? 'entry' : 'entries'}` : plural(hits.length, noun);
      view.lines = [summary(hits.length ? `${kind === 'list' ? 'Listed' : 'Found'} ${count}` : 'No matches')];
      if (expanded) view.lines.push(...hits.map((text) => ({ kind: 'text' as const, text, tone: 'muted' as const })));
      return view;
    }
    case 'edit': {
      const before = str(args.old_string) ?? str(args.old_str) ?? '';
      const after = str(args.new_string) ?? str(args.new_str) ?? '';
      if (call.status === 'running') { view.lines = [summary('Editing…')]; return view; }
      if (result && /^error/i.test(result.trim())) {
        view.lines = folded(lines, expanded, expandKey, 'error');
        return view;
      }
      const diff = lineDiff(before, after, call.startLine ?? 1);
      if (call.startLine === undefined) {
        // Where the edit sits in the file is unknown: no numbers beat wrong ones.
        diff.rows = diff.rows.map((r) => (r.kind === 'gap' ? r : { ...r, oldNo: undefined, newNo: undefined }));
      }
      view.lines = [summary(changeSummary(diff.added, diff.removed)), ...diffLines(diff.rows, expanded, expandKey)];
      return view;
    }
    case 'patch': {
      const patch = str(args.patch) ?? str(args.input) ?? '';
      if (call.status === 'running') { view.lines = [summary('Applying patch…')]; return view; }
      const diff = patchRows(patch);
      view.lines = [summary(changeSummary(diff.added, diff.removed)), ...diffLines(diff.rows, expanded, expandKey)];
      return view;
    }
    case 'write': {
      if (call.status === 'running') { view.lines = [summary('Writing…')]; return view; }
      const content = outputLines(str(args.content));
      view.lines = [summary(content.length ? `Wrote ${plural(content.length, 'line')}` : firstLine(result ?? '') || 'Wrote')];
      const rows: DiffRow[] = content.map((text, i) => ({ kind: 'add', text, newNo: i + 1 }));
      const keep = expanded || rows.length <= FOLD_LINES + 1 ? rows.length : FOLD_LINES;
      const width = String(rows.length).length;
      view.lines.push(...rows.slice(0, keep).map((row) => ({ kind: 'diff' as const, row, numberWidth: width })));
      if (rows.length > keep) {
        view.lines.push(summary(`… +${plural(rows.length - keep, 'line')}${expandKey ? ` (${expandKey} to expand)` : ''}`));
      }
      return view;
    }
    case 'agent': {
      const elapsed = call.startedAt !== undefined && call.endedAt !== undefined ? formatDuration(call.endedAt - call.startedAt) : undefined;
      if (call.status === 'running') {
        view.lines = [summary('Running…', 'active')];
        return view;
      }
      if (args.run_in_background === true) {
        view.lines = [summary('Started in the background'), ...folded(lines, expanded, expandKey, 'muted').slice(0, expanded ? undefined : 1)];
        return view;
      }
      view.lines = [summary(elapsed ? `Done (${elapsed})` : 'Done', 'done'), ...folded(lines, expanded, expandKey, 'muted', call.totalLines)];
      return view;
    }
    case 'todo': {
      const board = call.board ?? boardFromArgs(args);
      if (board && board.length) {
        view.lines = boardLines(board);
        return view;
      }
      view.lines = call.status === 'running' ? [summary('Updating…')] : folded(lines, expanded, expandKey, 'muted');
      return view;
    }
    default: {
      if (call.status === 'running') { view.lines = []; return view; }
      view.lines = folded(lines, expanded, expandKey, 'muted', call.totalLines);
      return view;
    }
  }
}

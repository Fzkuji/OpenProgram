/**
 * Line diff for edit results: which lines an edit removed and added,
 * with line numbers and three lines of context, the shape Claude Code's
 * StructuredDiff and Codex's diff_render draw.
 */

export type DiffRow =
  | { kind: 'context' | 'add' | 'remove'; text: string; oldNo?: number; newNo?: number }
  | { kind: 'gap' };

export interface LineDiff {
  rows: DiffRow[];
  added: number;
  removed: number;
}

const splitLines = (text: string): string[] => {
  if (text === '') return [];
  const lines = text.split('\n');
  if (lines[lines.length - 1] === '') lines.pop();
  return lines;
};

/** Longest-common-subsequence edit script; above the cell cap the whole
 *  block reads as replaced, which is still a correct diff. */
function editScript(a: string[], b: string[]): Array<'=' | '-' | '+'> {
  const n = a.length;
  const m = b.length;
  if (n * m > 4_000_000) {
    return [...Array(n).fill('-'), ...Array(m).fill('+')];
  }
  const table: Uint32Array[] = Array.from({ length: n + 1 }, () => new Uint32Array(m + 1));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      table[i]![j] = a[i] === b[j]
        ? table[i + 1]![j + 1]! + 1
        : Math.max(table[i + 1]![j]!, table[i]![j + 1]!);
    }
  }
  const ops: Array<'=' | '-' | '+'> = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) { ops.push('='); i++; j++; }
    else if (table[i + 1]![j]! >= table[i]![j + 1]!) { ops.push('-'); i++; }
    else { ops.push('+'); j++; }
  }
  while (i++ < n) ops.push('-');
  while (j++ < m) ops.push('+');
  return ops;
}

/**
 * Diff `before` against `after`. `startLine` is the file line the
 * first line of `before` sits on (1 when unknown).
 */
export function lineDiff(before: string, after: string, startLine = 1, context = 3): LineDiff {
  const a = splitLines(before);
  const b = splitLines(after);
  const ops = editScript(a, b);
  const all: Array<Exclude<DiffRow, { kind: 'gap' }>> = [];
  let i = 0;
  let j = 0;
  let added = 0;
  let removed = 0;
  for (const op of ops) {
    if (op === '=') {
      all.push({ kind: 'context', text: a[i]!, oldNo: startLine + i, newNo: startLine + j });
      i++; j++;
    } else if (op === '-') {
      all.push({ kind: 'remove', text: a[i]!, oldNo: startLine + i });
      i++; removed++;
    } else {
      all.push({ kind: 'add', text: b[j]!, newNo: startLine + j });
      j++; added++;
    }
  }
  // Keep `context` unchanged lines around each change; fold the rest.
  const keep = new Array(all.length).fill(false);
  all.forEach((row, index) => {
    if (row.kind === 'context') return;
    for (let k = Math.max(0, index - context); k <= Math.min(all.length - 1, index + context); k++) keep[k] = true;
  });
  const rows: DiffRow[] = [];
  let folded = false;
  all.forEach((row, index) => {
    if (keep[index]) {
      rows.push(row);
      folded = false;
    } else if (!folded && rows.length > 0) {
      rows.push({ kind: 'gap' });
      folded = true;
    }
  });
  if (rows[rows.length - 1]?.kind === 'gap') rows.pop();
  return { rows, added, removed };
}

/**
 * Rows of a unified-diff or `*** Begin Patch` body, coloured by their
 * first character. Line numbers come from `@@ -a,b +c,d @@` headers
 * when the patch has them.
 */
export function patchRows(patch: string): LineDiff {
  const rows: DiffRow[] = [];
  let added = 0;
  let removed = 0;
  let oldNo: number | undefined;
  let newNo: number | undefined;
  for (const line of patch.split('\n')) {
    const hunk = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(line);
    if (hunk) {
      if (rows.length) rows.push({ kind: 'gap' });
      oldNo = Number(hunk[1]);
      newNo = Number(hunk[2]);
      continue;
    }
    if (/^(\*\*\*|---|\+\+\+|diff |index |@@)/.test(line)) continue;
    if (line.startsWith('+')) {
      rows.push({ kind: 'add', text: line.slice(1), newNo });
      if (newNo !== undefined) newNo++;
      added++;
    } else if (line.startsWith('-')) {
      rows.push({ kind: 'remove', text: line.slice(1), oldNo });
      if (oldNo !== undefined) oldNo++;
      removed++;
    } else if (line.startsWith(' ')) {
      rows.push({ kind: 'context', text: line.slice(1), oldNo, newNo });
      if (oldNo !== undefined) oldNo++;
      if (newNo !== undefined) newNo++;
    }
  }
  return { rows, added, removed };
}

export function changeSummary(added: number, removed: number): string {
  const part = (n: number, verb: string) => `${verb} ${n} line${n === 1 ? '' : 's'}`;
  if (added && removed) return `${part(added, 'Added')}, ${part(removed, 'removed')}`;
  if (added) return part(added, 'Added');
  if (removed) return part(removed, 'Removed');
  return 'No changes';
}

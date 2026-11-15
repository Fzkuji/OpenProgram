import React from 'react';
import { describe, expect, it } from 'vitest';

import { render } from './renderToFrame.js';
import { changeSummary, lineDiff, patchRows } from '../src/components/tools/diff.js';
import { toolKind, toolView, type ViewLine } from '../src/components/tools/toolView.js';
import { ToolExpansionContext, ToolRow } from '../src/components/tools/ToolRow.js';
import type { ToolCall, TurnBlock } from '../src/components/Turn.js';
import {
  attachTodoBoard,
  completeToolBlocks,
  editStartLine,
  todoBoard,
} from '../src/screens/repl/wsHandlers/streamingHelpers.js';

const stripAnsi = (s: string): string => s.replace(/\x1b\[[0-9;?]*[A-Za-z]/g, '');
const texts = (lines: ViewLine[]) => lines.map((l) => (l.kind === 'text' ? l.text : `${l.row.kind}`));
const call = (over: Partial<ToolCall>): ToolCall => ({ id: 't', tool: 'bash', status: 'done', ...over });
const many = (n: number) => Array.from({ length: n }, (_, i) => `line ${i + 1}`).join('\n');

describe('line diff', () => {
  it('numbers changes and keeps three lines of context', () => {
    const before = many(12);
    const after = before.replace('line 6', 'line six');
    const diff = lineDiff(before, after, 40);
    expect(diff.added).toBe(1);
    expect(diff.removed).toBe(1);
    const shown = diff.rows.map((r) => (r.kind === 'gap' ? '⋮' : `${r.kind}:${r.text}:${r.oldNo ?? ''}/${r.newNo ?? ''}`));
    expect(shown).toEqual([
      'context:line 3:42/42', 'context:line 4:43/43', 'context:line 5:44/44',
      'remove:line 6:45/', 'add:line six:/45',
      'context:line 7:46/46', 'context:line 8:47/47', 'context:line 9:48/48',
    ]);
  });

  it('folds unchanged runs between hunks', () => {
    const before = many(20);
    const after = before.replace('line 2\n', 'line two\n').replace('line 19', 'line nineteen');
    expect(lineDiff(before, after).rows.some((r) => r.kind === 'gap')).toBe(true);
  });

  it('reads unified and apply_patch bodies', () => {
    const rows = patchRows('*** Begin Patch\n*** Update File: a.py\n@@ -3,2 +3,2 @@\n keep\n-old\n+new\n*** End Patch');
    expect(rows.added).toBe(1);
    expect(rows.removed).toBe(1);
    expect(rows.rows[1]).toMatchObject({ kind: 'remove', text: 'old', oldNo: 4 });
  });

  it('summarises counts in words', () => {
    expect(changeSummary(5, 2)).toBe('Added 5 lines, removed 2 lines');
    expect(changeSummary(1, 0)).toBe('Added 1 line');
    expect(changeSummary(0, 0)).toBe('No changes');
  });
});

describe('tool views', () => {
  it('maps other providers’ tool names onto ours', () => {
    expect(toolKind('Bash')).toBe('bash');
    expect(toolKind('MultiEdit')).toBe('edit');
    expect(toolKind('TodoWrite')).toBe('todo');
    expect(toolKind('Task')).toBe('agent');
    expect(toolKind('web_fetch')).toBe('generic');
  });

  it('bash folds to three lines with an accurate count and the expand key', () => {
    const view = toolView(call({ input: '{"command":"seq 10"}', result: many(10) }), { expandKey: 'ctrl+o' });
    expect(view.arg).toBe('seq 10');
    expect(texts(view.lines)).toEqual(['line 1', 'line 2', 'line 3', '… +7 lines (ctrl+o to expand)']);
    // The server previewed 10 of 400 lines: the count is the whole output.
    const long = toolView(call({ input: '{"command":"x"}', result: many(10), totalLines: 400 }), { expandKey: 'ctrl+o' });
    expect(texts(long.lines).at(-1)).toBe('… +397 lines (ctrl+o to expand)');
    // One hidden line is shown instead of a note about it.
    expect(texts(toolView(call({ result: many(4) })).lines)).toEqual(['line 1', 'line 2', 'line 3', 'line 4']);
    expect(texts(toolView(call({ result: many(10) }), { expanded: true }).lines)).toHaveLength(10);
  });

  it('read and search show one quantified line until expanded', () => {
    const read = toolView(call({ tool: 'read', input: '{"file_path":"/a/b.py"}', result: '# b.py (lines 1-3 of 3)\n     1\tx\n     2\ty\n     3\tz' }));
    expect(read.arg).toBe('/a/b.py');
    expect(texts(read.lines)).toEqual(['Read 3 lines']);
    const grep = toolView(call({ tool: 'grep', input: '{"pattern":"foo","path":"/src"}', result: '/src/a.ts\n/src/b.ts' }));
    expect(grep.arg).toBe('"foo" in /src');
    expect(texts(grep.lines)).toEqual(['Found 2 files']);
    expect(texts(toolView(call({ tool: 'grep', input: '{"pattern":"foo","output_mode":"content"}', result: 'a:1:foo' })).lines)).toEqual(['Found 1 match']);
    expect(texts(toolView(call({ tool: 'glob', input: '{"pattern":"*.md"}', result: 'No matches' })).lines)).toEqual(['No matches']);
    expect(toolView(call({ tool: 'grep', input: '{"pattern":"foo"}', result: '/a\n/b' }), { expanded: true }).lines).toHaveLength(3);
  });

  it('edit shows a counted, numbered diff when the line is known', () => {
    const input = JSON.stringify({ file_path: '/f.py', old_string: 'a\nb\n', new_string: 'a\nB\nc\n' });
    const view = toolView(call({ tool: 'edit', input, result: 'ok', startLine: 10 }));
    expect(texts(view.lines)).toEqual(['Added 2 lines, removed 1 line', 'context', 'remove', 'add', 'add']);
    const first = view.lines[1]!;
    expect(first.kind === 'diff' && first.row.kind !== 'gap' && first.row.oldNo).toBe(10);
    // Unknown position: no numbers rather than wrong ones.
    const bare = toolView(call({ tool: 'edit', input, result: 'ok' }));
    expect(bare.lines.every((l) => l.kind !== 'diff' || l.numberWidth === 0)).toBe(true);
    // A refused edit reads as its error.
    const failed = toolView(call({ tool: 'edit', input, result: 'Error: file not found: /f.py' }));
    expect(failed.lines[0]).toMatchObject({ tone: 'error' });
  });

  it('write shows the head of the new file', () => {
    const view = toolView(call({ tool: 'write', input: JSON.stringify({ file_path: '/n.txt', content: many(8) }) }), { expandKey: 'ctrl+o' });
    expect(texts(view.lines)).toEqual(['Wrote 8 lines', 'add', 'add', 'add', '… +5 lines (ctrl+o to expand)']);
  });

  it('a sub-agent says how long it ran and the head of its reply', () => {
    const running = toolView(call({ tool: 'agent', status: 'running', input: '{"description":"Survey","prompt":"go"}' }));
    expect(running.arg).toBe('Survey');
    expect(texts(running.lines)).toEqual(['Running…']);
    const done = toolView(call({ tool: 'agent', input: '{"description":"Survey"}', result: 'found it', startedAt: 0, endedAt: 130_000 }));
    expect(texts(done.lines)).toEqual(['Done (2m 10s)', 'found it']);
    const bg = toolView(call({ tool: 'agent', input: '{"description":"S","run_in_background":true}', result: 'execution exec_1' }));
    expect(texts(bg.lines)[0]).toBe('Started in the background');
  });

  it('todo calls draw the board', () => {
    const view = toolView(call({
      tool: 'todo_update',
      input: '{"todo_id":"2","status":"in_progress"}',
      result: 'Todo #2 updated (status)',
      board: [
        { subject: 'Read code', status: 'completed' },
        { subject: 'Write tests', status: 'in_progress' },
        { subject: 'Ship', status: 'pending' },
      ],
    }));
    expect(texts(view.lines)).toEqual(['☒ Read code', '◼ Write tests', '☐ Ship']);
    expect(view.lines[0]).toMatchObject({ strike: true });
    expect(view.lines[1]).toMatchObject({ bold: true });
    // Claude Code's TodoWrite carries the board in its own arguments.
    const write = toolView(call({ tool: 'TodoWrite', input: JSON.stringify({ todos: [{ content: 'A', status: 'pending' }] }) }));
    expect(texts(write.lines)).toEqual(['☐ A']);
  });

  it('a failed call shows its error, and plain inputs stay the argument', () => {
    const view = toolView(call({ status: 'error', input: 'ls /nope', result: 'No such file' }));
    expect(view.arg).toBe('ls /nope');
    expect(view.lines[0]).toMatchObject({ text: 'No such file', tone: 'error' });
  });
});

describe('ToolRow', () => {
  it('draws the header, the gutter and the folded body', () => {
    const { lastFrame } = render(
      <ToolRow call={call({ tool: 'bash', input: '{"command":"seq 10"}', result: many(10) })} />,
    );
    const out = stripAnsi(lastFrame() ?? '');
    expect(out).toContain('● bash(seq 10)');
    expect(out).toContain('⎿ line 1');
    expect(out).toContain('… +7 lines (ctrl+o to expand)');
  });

  it('shows everything while expanded', () => {
    const { lastFrame } = render(
      <ToolExpansionContext.Provider value>
        <ToolRow call={call({ tool: 'bash', input: '{"command":"seq 10"}', result: many(10) })} />
      </ToolExpansionContext.Provider>,
    );
    const out = stripAnsi(lastFrame() ?? '');
    expect(out).toContain('line 10');
    expect(out).not.toContain('to expand');
  });

  it('draws diff rows with numbers and signs', () => {
    const input = JSON.stringify({ file_path: '/f.py', old_string: 'old', new_string: 'new' });
    const { lastFrame } = render(<ToolRow call={call({ tool: 'edit', input, result: 'ok', startLine: 7 })} />);
    const out = stripAnsi(lastFrame() ?? '');
    expect(out).toContain('edit(/f.py)');
    expect(out).toContain('Added 1 line, removed 1 line');
    expect(out).toMatch(/7 - old/);
    expect(out).toMatch(/7 \+ new/);
  });
});

describe('streaming tool results', () => {
  const running = (tool: string, callId?: string, input?: string): TurnBlock => ({
    kind: 'tool', call: { id: `${tool}-${callId}`, tool, status: 'running', callId, input, startedAt: 1 },
  });

  it('match a result to its call by id, so same-name calls finish correctly', () => {
    const blocks = [running('bash', 'a'), running('bash', 'b')];
    const out = completeToolBlocks(blocks, { tool: 'bash', tool_call_id: 'a', result: 'first', full_output: { total_lines: 900 } }, 5);
    expect(out[0]).toMatchObject({ call: { status: 'done', result: 'first', endedAt: 5, totalLines: 900 } });
    expect(out[1]).toMatchObject({ call: { status: 'running' } });
    // Without an id the newest running call of that name takes it.
    const legacy = completeToolBlocks(blocks, { tool: 'bash', result: 'x', is_error: true });
    expect(legacy[1]).toMatchObject({ call: { status: 'error' } });
  });

  it('locate an edit in its file', () => {
    const input = JSON.stringify({ file_path: '/f', new_string: 'needle' });
    expect(editStartLine(input, () => 'a\nb\nneedle\n')).toBe(3);
    expect(editStartLine(input, () => 'needle needle')).toBeUndefined();
    expect(editStartLine(input, () => { throw new Error('remote'); })).toBeUndefined();
    const out = completeToolBlocks([running('edit', 'e', input)], { tool: 'edit', tool_call_id: 'e', result: 'ok' }, 2, () => 12);
    expect(out[0]).toMatchObject({ call: { startLine: 12 } });
  });

  it('put the saved board on the newest todo call', () => {
    const board = todoBoard([{ subject: 'A', status: 'pending' }, { nope: 1 }, { subject: 'B' }]);
    expect(board).toEqual([{ subject: 'A', status: 'pending' }, { subject: 'B', status: 'pending' }]);
    const blocks: TurnBlock[] = [running('todo_create', '1'), { kind: 'text', text: 'hi' }, running('todo_update', '2')];
    const out = attachTodoBoard(blocks, board);
    expect(out[2]).toMatchObject({ call: { board } });
    expect(out[0]).toBe(blocks[0]);
  });
});

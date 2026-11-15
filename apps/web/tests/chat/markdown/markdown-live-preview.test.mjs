import assert from 'node:assert/strict';
import test from 'node:test';
import { EditorSelection, EditorState } from '@codemirror/state';
import { composerMarkdown, livePreviewRanges } from '../../../lib/chat/markdown/markdown-live-preview.ts';

const text = '# Title\nsay **bold** and `code` ~~gone~~\n- item\n> quote\n```py\nx=1\n```';
const at = (caret) => livePreviewRanges(EditorState.create({
  doc: text, selection: EditorSelection.cursor(caret), extensions: [composerMarkdown],
}));
const slice = (r) => text.slice(r.from, r.to);

test('formatting is styled and markers are hidden away from the caret', () => {
  const r = at(text.length);
  const marks = r.marks.map((m) => `${m.kind}:${slice(m)}`);
  assert.ok(marks.includes('strong:**bold**'));
  assert.ok(marks.includes('code:`code`'));
  assert.ok(marks.includes('strike:~~gone~~'));
  assert.deepEqual(r.lines.filter((l) => l.kind === 'h1').map((l) => l.from), [0]);
  assert.ok(r.lines.some((l) => l.kind === 'quote'));
  assert.equal(r.lines.filter((l) => l.kind === 'fence').length, 3);
  const hidden = r.hidden.map(slice);
  assert.ok(hidden.includes('# '));
  assert.equal(hidden.filter((h) => h === '**').length, 2);
  assert.equal(hidden.filter((h) => h === '`').length, 2);
});

test('markers reappear while the caret is inside the construct', () => {
  const insideBold = text.indexOf('bold') + 1;
  const hidden = at(insideBold).hidden.map(slice);
  assert.equal(hidden.filter((h) => h === '**').length, 0);
  assert.ok(hidden.includes('# '), 'other constructs stay rendered');
  assert.ok(!at(2).hidden.map(slice).includes('# '), 'heading marker shows when editing the heading');
});

test('paste tokens and finished @file mentions become chips', async () => {
  const { inlineChips } = await import('../../../lib/chat/markdown/markdown-live-preview.ts');
  const src = 'see @src/app/page.tsx and [Pasted #2 +401 lines] then @still-typing';
  const chips = inlineChips(src);
  assert.deepEqual(chips.map((c) => [c.kind, src.slice(c.from, c.to), c.label]), [
    ['file', '@src/app/page.tsx', '…/app/page.tsx'],
    ['paste', '[Pasted #2 +401 lines]', 'Pasted · 401 lines'],
  ]);
});

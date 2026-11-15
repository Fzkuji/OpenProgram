import assert from 'node:assert/strict';
import test from 'node:test';
import {
  collapsePastedSpans, elementsForBody, pasteSentinel, readPastedElements,
} from '../../../lib/chat/send/paste-elements.ts';

const pasted = 'line\n'.repeat(400);
const body = `compare this:\n${pasted}\nthanks`;
const start = body.indexOf(pasted);
const elements = elementsForBody(body, [{ start, end: start + pasted.length, label: 'Pasted · 401 lines' }]);

test('spans survive a rewritten attachment prefix because they count from the end', () => {
  const stored = '[attachment: a.txt (txt, 1 KB) @json "/long/server/path/a.txt"]\n\n' + body;
  const { content, spans } = collapsePastedSpans(stored, readPastedElements(elements));
  assert.equal(spans.length, 1);
  assert.equal(spans[0].text, pasted);
  assert.ok(content.endsWith(`compare this:\n${pasteSentinel(0)}\nthanks`));
});

test('a span whose text no longer matches is ignored', () => {
  const { content, spans } = collapsePastedSpans(body.replace('line', 'LINE'), elements);
  assert.equal(spans.length, 0);
  assert.equal(content, body.replace('line', 'LINE'));
});

test('untrusted records are validated', () => {
  assert.deepEqual(readPastedElements([{ tail_start: 1, tail_end: 5, label: 'x', head: '' }, 'junk']), []);
  assert.deepEqual(readPastedElements('nope'), []);
});

test('delimited spans are measured after later rewrites of the body', async () => {
  const { PASTE_OPEN, PASTE_CLOSE, takePastedSpans } = await import('../../../components/chat/composer/paste/paste-store.ts');
  // A later rewrite (an @path expansion) lengthened text before the span.
  const rewritten = `see <file a.txt>…</file> and ${PASTE_OPEN}one\ntwo${PASTE_CLOSE} end`;
  const { text, spans } = takePastedSpans(rewritten);
  assert.equal(text, 'see <file a.txt>…</file> and one\ntwo end');
  assert.deepEqual(spans.map((s) => [text.slice(s.start, s.end), s.label]), [['one\ntwo', 'Pasted · 2 lines']]);
});

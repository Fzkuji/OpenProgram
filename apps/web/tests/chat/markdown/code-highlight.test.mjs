import assert from 'node:assert/strict';
import test from 'node:test';
import { highlightEscapedCode } from '../../../lib/chat/markdown/code-highlight.ts';

test('known languages gain hljs spans and keep text escaped', () => {
  const out = highlightEscapedCode('if a &lt; b:\n    print(&quot;&lt;x&gt;&quot;)', 'python');
  assert.match(out, /<span class="hljs-keyword">if<\/span>/);
  assert.match(out, /&lt;x&gt;/);
  assert.doesNotMatch(out, /<x>/);
});

test('unknown or missing languages are returned unchanged', () => {
  assert.equal(highlightEscapedCode('a &lt; b', 'brainfuck'), 'a &lt; b');
  assert.equal(highlightEscapedCode('a &lt; b', ''), 'a &lt; b');
});

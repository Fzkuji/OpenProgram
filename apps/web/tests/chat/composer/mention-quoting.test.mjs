import assert from 'node:assert/strict';
import test from 'node:test';
import { expandAtMentions, findAtToken, MAX_MENTION_RESOLVES } from '../../../components/chat/composer/attach/mention/at-mention.ts';
import { mentionInsert } from '../../../components/chat/composer/attach/mention/mention-sources.ts';
import { PASTE_CLOSE, PASTE_OPEN } from '../../../components/chat/composer/paste/paste-store.ts';

async function withResolver(fn) {
  const original = globalThis.fetch;
  const asked = [];
  globalThis.fetch = async (url) => {
    const path = new URL(url, 'http://worker').searchParams.get('path');
    asked.push(path);
    return { ok: true, json: async () => ({ path: `/work/${path}`, size: 1024 }) };
  };
  try { await fn(asked); } finally { globalThis.fetch = original; }
}

test('a path with spaces is inserted quoted and resolves unquoted', async () => {
  assert.equal(mentionInsert({ path: 'docs/My Notes.md', is_dir: false }), '@"docs/My Notes.md" ');
  assert.equal(mentionInsert({ path: 'docs/My Dir', is_dir: true }), '@"docs/My Dir/');
  assert.equal(mentionInsert({ path: 'docs/plain.md', is_dir: false }), '@docs/plain.md ');
  await withResolver(async (asked) => {
    const out = await expandAtMentions('read @"docs/My Notes.md" please', null);
    assert.deepEqual(asked, ['docs/My Notes.md']);
    assert.equal(out.text, 'read [attachment: My Notes.md (md, 1 KB) @ /work/docs/My Notes.md] please');
  });
});

test('an open quoted mention keeps the menu on its partial', () => {
  const draft = 'see @"docs/My Dir/No';
  assert.deepEqual(findAtToken(draft, draft.length), { start: 4, partial: 'docs/My Dir/No' });
  assert.deepEqual(findAtToken('@"docs', 6), { start: 0, partial: 'docs' });
  assert.equal(findAtToken('@"docs/a b.md" ', 15), null);
  assert.equal(findAtToken('@"docs/a b.md"', 14), null);
  assert.deepEqual(findAtToken('x @src/ma', 9), { start: 2, partial: 'src/ma' });
});

test('pasted blocks and code fences are not scanned for mentions', async () => {
  await withResolver(async (asked) => {
    const pasted = `${PASTE_OPEN}class A {\n  @Override\n  void run() {}\n}\n@main.py${PASTE_CLOSE}`;
    const fence = '```python\n@dataclass\nclass B: pass\n```';
    const text = `look at @main.py\n${pasted}\n${fence}\nafter fence @main.py`;
    const out = await expandAtMentions(text, null);
    assert.deepEqual(asked, ['main.py']);
    assert.ok(out.text.includes(`${PASTE_OPEN}class A {\n  @Override\n  void run() {}\n}\n@main.py${PASTE_CLOSE}`));
    assert.ok(out.text.includes('```python\n@dataclass\n'));
    assert.equal(out.text.split('[attachment: main.py').length - 1, 2);
  });
});

test('one message looks up at most a fixed number of paths', async () => {
  await withResolver(async (asked) => {
    const tokens = Array.from({ length: MAX_MENTION_RESOLVES + 5 }, (_, i) => `@f${i}.txt`).join(' ');
    const out = await expandAtMentions(tokens, null);
    assert.equal(asked.length, MAX_MENTION_RESOLVES);
    assert.ok(out.text.endsWith(`@f${MAX_MENTION_RESOLVES + 4}.txt`));
    assert.equal(out.missing.length, 0);
  });
});

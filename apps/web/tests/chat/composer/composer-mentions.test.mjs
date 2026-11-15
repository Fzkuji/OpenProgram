import assert from 'node:assert/strict';
import test from 'node:test';
import { matchApps, matchTabs, mentionInsert, mentionKey } from '../../../components/chat/composer/attach/mention/mention-sources.ts';
import { onComposerFiles, queueComposerFiles, takeComposerFiles } from '../../../lib/chat/composer-inbox.ts';
import { regionFileName } from '../../../lib/browser/capture/region-shot.ts';

const tabs = [
  { id: 'web-1', kind: 'web', title: 'Pull requests · OpenProgram', url: 'https://github.com/FuzzyKuji/OpenProgram/pulls' },
  { id: 'web-2', kind: 'web', title: '', url: 'http://localhost:18100/docs' },
  { id: 'web-3', kind: 'web', title: 'New tab', url: 'openprogram://newtab' },
  { id: 's-1', kind: 'session', title: 'Chat', url: 'https://example.com' },
];
const apps = [
  { name: 'Calculator', bundle_id: 'com.apple.calculator', icon: 'data:image/png;base64,AA' },
  { name: '微信', bundle_id: 'com.tencent.xinWeChat', icon: '' },
];

test('an @ lists open web pages and running apps that match, and stays out of paths', () => {
  assert.deepEqual(matchTabs(tabs, '').map((tab) => [tab.tabId, tab.title]),
    [['web-1', 'Pull requests · OpenProgram'], ['web-2', 'localhost:18100']]);
  assert.deepEqual(matchTabs(tabs, 'GITHUB').map((tab) => tab.tabId), ['web-1']);
  assert.deepEqual(matchApps(apps, 'calc').map((app) => app.bundle_id), ['com.apple.calculator']);
  assert.deepEqual(matchApps(apps, 'wechat').map((app) => app.name), ['微信']);
  assert.deepEqual(matchTabs(tabs, 'src/'), []);
  assert.deepEqual(matchApps(apps, 'src/app'), []);
});

test('a pick writes the exact page or app into the draft', () => {
  const [tab] = matchTabs(tabs, 'pull');
  assert.equal(mentionInsert(tab), '[Pull requests · OpenProgram](https://github.com/FuzzyKuji/OpenProgram/pulls) ');
  assert.equal(mentionInsert({ kind: 'tab', tabId: 'x', title: 'a [b] c', url: 'https://x.test/' }), '[a b c](https://x.test/) ');
  assert.equal(mentionInsert(matchApps(apps, 'calc')[0]), 'Calculator (com.apple.calculator) ');
  assert.equal(mentionInsert({ path: 'src', is_dir: true }), '@src/');
  assert.equal(mentionInsert({ path: 'a.ts', is_dir: false }), '@a.ts ');
  assert.notEqual(mentionKey(tab), mentionKey({ path: tab.tabId, is_dir: false }));
});

test('files sent to a conversation wait until its composer takes them', () => {
  let heard = 0;
  const stop = onComposerFiles(() => { heard += 1; });
  const file = { name: 'region.png' };
  queueComposerFiles('chat-a', [file]);
  queueComposerFiles('chat-a', []);
  stop();
  queueComposerFiles('chat-a', [file]);
  assert.equal(heard, 1);
  assert.deepEqual(takeComposerFiles('chat-b'), []);
  assert.deepEqual(takeComposerFiles('chat-a'), [file, file]);
  assert.deepEqual(takeComposerFiles('chat-a'), []);
});

test('a region screenshot is named after its site and size', () => {
  assert.equal(regionFileName({ url: 'https://www.github.com/x', width: 300.4, height: 62 }), 'region-github.com-300x62.png');
  assert.equal(regionFileName({ url: 'not a url', width: 10, height: 10 }), 'region-page-10x10.png');
});

test('an @ mention resolves in the chat it belongs to', async () => {
  const { expandAtMentions } = await import('../../../components/chat/composer/attach/mention/at-mention.ts');
  const originalFetch = globalThis.fetch;
  const asked = [];
  globalThis.fetch = async (url) => {
    asked.push(new URL(url, 'http://worker').searchParams);
    return { ok: true, json: async () => ({ path: '/work/src/main.py', size: 2048 }) };
  };
  try {
    const out = await expandAtMentions('read @src/main.py', { sessionId: 'local_draft', projectId: 'p1' });
    assert.equal(asked[0].get('path'), 'src/main.py');
    assert.equal(asked[0].get('session_id'), 'local_draft');
    assert.equal(asked[0].get('project_id'), 'p1');
    assert.equal(asked[0].get('root'), null);
    assert.match(out.text, /@ \/work\/src\/main\.py\]$/);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

import assert from 'node:assert/strict';
import test from 'node:test';
import { matchApps, matchTabs, matchWindows, mentionInsert, mentionKey } from '../../../../components/chat/composer/attach/mention/mention-sources.ts';
import { onComposerFiles, queueComposerFiles, takeComposerFiles } from '../../../../lib/chat/composer-inbox.ts';
import { regionFileName } from '../../../../lib/browser/capture/region-shot.ts';

const pages = [
  { id: 'web-1', title: 'Pull requests · OpenProgram', url: 'https://github.com/Fzkuji/OpenProgram/pulls' },
  { id: 'web-2', title: '', url: 'http://localhost:18100/docs' },
  ...Array.from({ length: 6 }, (_, i) => ({ id: `more-${i}`, title: `Result ${i}`, url: `https://r${i}.test/` })),
];
const apps = [
  { name: 'Calculator', bundle_id: 'com.apple.calculator', icon: 'data:image/png;base64,AA' },
  { name: '微信', bundle_id: 'com.tencent.xinWeChat', icon: '' },
];

test('an @ lists every page of the conversation and running apps that match, and stays out of paths', () => {
  assert.equal(matchTabs(pages, '').length, 8);
  assert.deepEqual(matchTabs(pages, '').slice(0, 2).map((tab) => [tab.tabId, tab.title]),
    [['web-1', 'Pull requests · OpenProgram'], ['web-2', 'localhost:18100']]);
  assert.deepEqual(matchTabs(pages, 'GITHUB').map((tab) => tab.tabId), ['web-1']);
  assert.deepEqual(matchApps(apps, 'calc').map((app) => app.bundle_id), ['com.apple.calculator']);
  assert.deepEqual(matchApps(apps, 'wechat').map((app) => app.name), ['微信']);
  assert.deepEqual(matchTabs(pages, 'src/'), []);
  assert.deepEqual(matchApps(apps, 'src/app'), []);
});

test('a pick writes the exact page or app into the draft', () => {
  const [tab] = matchTabs(pages, 'pull');
  assert.equal(mentionInsert(tab), '[Pull requests · OpenProgram](https://github.com/Fzkuji/OpenProgram/pulls) ');
  assert.equal(mentionInsert({ kind: 'tab', tabId: 'x', title: 'a [b] c', url: 'https://x.test/' }), '[a b c](https://x.test/) ');
  assert.equal(mentionInsert(matchApps(apps, 'calc')[0]), 'Calculator (com.apple.calculator) ');
  assert.equal(mentionInsert({ path: 'src', is_dir: true }), '@src/');
  assert.equal(mentionInsert({ path: 'a.ts', is_dir: false }), '@a.ts ');
  assert.notEqual(mentionKey(tab), mentionKey({ path: tab.tabId, is_dir: false }));
});

test('an @ hands a window shown in a tab to the agent by its window id', () => {
  const tabs = [
    { id: 'w1', kind: 'window', title: 'Budget.xlsx', nativeWindowId: 4242, nativeWindowApp: 'Excel' },
    { id: 'w2', kind: 'window', title: 'Window' },
    { id: 't1', kind: 'web', title: 'Excel help', url: 'https://x.test/' },
  ];
  const found = matchWindows(tabs, 'excel');
  assert.deepEqual(found.map((item) => item.windowId), [4242]);
  assert.deepEqual(matchWindows(tabs, 'budget').map((item) => item.tabId), ['w1']);
  assert.deepEqual(matchWindows(tabs, 'src/app'), []);
  assert.equal(mentionInsert(found[0]), 'Excel window "Budget.xlsx" (target "app", app "Excel", window_id 4242) ');
  assert.equal(mentionKey(found[0]), 'window:4242');
  assert.equal(mentionInsert({ kind: 'window', tabId: 'x', app: 'Notes', title: 'Notes', windowId: 7 }), 'Notes window (target "app", app "Notes", window_id 7) ');
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
  // A new chat has no key yet; its files wait under null.
  queueComposerFiles(null, [file]);
  assert.deepEqual(takeComposerFiles('chat-a'), []);
  assert.deepEqual(takeComposerFiles(null), [file]);
});

test('a region screenshot is named after its site and size', () => {
  assert.equal(regionFileName({ url: 'https://www.github.com/x', width: 300.4, height: 62 }), 'region-github.com-300x62.png');
  assert.equal(regionFileName({ url: 'not a url', width: 10, height: 10 }), 'region-page-10x10.png');
});

test('an @ mention resolves in the chat it belongs to', async () => {
  const { expandAtMentions } = await import('../../../../components/chat/composer/attach/mention/at-mention.ts');
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

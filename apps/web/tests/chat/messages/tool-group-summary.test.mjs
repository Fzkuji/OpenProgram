import assert from 'node:assert/strict';
import test from 'node:test';
import {
  LINE_DIFF_CAP,
  lineDiff,
  parsePatch,
  summarizeToolGroup,
} from '../../../components/chat/messages/execution/tool-group-summary.ts';

const en = (english) => english;
const zh = (_english, chinese) => chinese;
let seq = 0;
const tool = (name, args, extra = {}) => ({
  type: 'tool', tool: name, tool_call_id: `c${++seq}`, input: JSON.stringify(args), ...extra,
});
const lines = (n) => Array.from({ length: n }, (_, i) => `line ${i}`).join('\n') + '\n';

test('a command then a new file reads in order, with the created file named and counted', () => {
  const blocks = [
    { type: 'thinking', text: 'plan' },
    tool('bash', { command: 'ls' }),
    tool('write', { file_path: '/p/pill-shadow.html', content: lines(16) }),
  ];
  const summary = summarizeToolGroup(blocks, { text: en });
  assert.equal(summary.label, 'Ran a command, created pill-shadow.html');
  assert.deepEqual([summary.added, summary.removed, summary.failed], [16, 0, 0]);
  assert.equal(summarizeToolGroup(blocks, { text: zh }).label, '运行了 1 条命令，创建了 pill-shadow.html');
});

test('buckets merge by kind at their first position; one target is named, more are counted', () => {
  const blocks = [
    tool('read', { file_path: '/p/a.ts' }),
    tool('read', { file_path: '/p/b.ts' }),
    tool('read', { file_path: '/p/a.ts' }),
    tool('grep', { pattern: 'tl-toggle' }),
    tool('read', { file_path: '/p/c.ts' }),
    tool('edit', { file_path: '/p/src/execution-strip.tsx', old_string: 'a\nb\nc\nd', new_string: 'a\nB\nc\nd\ne' }),
  ];
  const summary = summarizeToolGroup(blocks, { text: en });
  assert.equal(summary.label, 'Read 3 files, searched for "tl-toggle", edited execution-strip.tsx');
  assert.deepEqual([summary.added, summary.removed], [2, 1]);
  assert.equal(summarizeToolGroup(blocks.slice(0, 1), { text: en }).label, 'Read a.ts');
  assert.equal(summarizeToolGroup(blocks.slice(0, 1), { text: en }).added, null);
});

test('plurals in both languages', () => {
  const blocks = [
    tool('bash', { command: 'a' }), tool('bash', { command: 'b' }), tool('bash', { command: 'c' }),
    tool('web_search', { query: 'x' }), tool('web_search', { query: 'y' }),
    tool('web_fetch', { url: 'https://docs.anthropic.com/en/api' }),
    tool('grep', { pattern: 'a' }), tool('glob', { pattern: '*.ts' }),
  ];
  assert.equal(summarizeToolGroup(blocks, { text: en }).label,
    'Ran 3 commands, searched the web 2 times, fetched docs.anthropic.com, ran 2 searches');
  assert.equal(summarizeToolGroup(blocks, { text: zh }).label,
    '运行了 3 条命令，搜索了 2 次网页，抓取了 docs.anthropic.com，搜索了 2 次');
});

test('a long search pattern is quoted and cut to 24 characters', () => {
  const label = summarizeToolGroup([tool('grep', { pattern: 'abcdefghijklmnopqrstuvwxyz0123' })], { text: en }).label;
  assert.equal(label, 'Searched for "abcdefghijklmnopqrstuvwx…"');
});

test('the call in progress moves to the end in the present tense', () => {
  const run = tool('bash', { command: 'npm test' });
  const blocks = [run, tool('read', { file_path: '/p/a.ts' }), tool('read', { file_path: '/p/b.ts' })];
  const options = { active: true, runningIds: new Set([run.tool_call_id]) };
  assert.equal(summarizeToolGroup(blocks, { text: en, ...options }).label, 'Read 2 files, running a command…');
  assert.equal(summarizeToolGroup(blocks, { text: zh, ...options }).label, '读取了 2 个文件，正在运行命令…');
  // Not active: everything settles to the past tense in first-occurrence order.
  assert.equal(summarizeToolGroup(blocks, { text: en, runningIds: options.runningIds }).label,
    'Ran a command, read 2 files');
  const edit = tool('edit', { file_path: '/p/turn-files-chips.tsx', old_string: 'a', new_string: 'b' });
  assert.equal(summarizeToolGroup([edit], { text: en, active: true, runningIds: new Set([edit.tool_call_id]) }).label,
    'Editing turn-files-chips.tsx…');
});

test('thinking appears only when the group has nothing else', () => {
  const thinking = [{ type: 'thinking', text: 'hmm' }];
  assert.equal(summarizeToolGroup(thinking, { text: en }).label, 'Thought');
  assert.equal(summarizeToolGroup(thinking, { text: en, active: true }).label, 'Thinking…');
  assert.equal(summarizeToolGroup(thinking, { text: zh, active: true }).label, '思考中…');
});

test('failures are counted, cancellations are not, and failed writes stay out of the badge', () => {
  const blocks = [
    tool('edit', { file_path: '/p/app.tsx', old_string: 'x\ny\nz', new_string: 'X\nY\nZ' }, { result: 'Edited /p/app.tsx (1 replacement)' }),
    tool('bash', { command: 'a' }, { is_error: true }),
    tool('bash', { command: 'b' }, { is_error: true, outcome: 'cancelled' }),
    tool('edit', { file_path: '/p/other.tsx', old_string: 'a', new_string: 'b' }, { is_error: true }),
  ];
  const summary = summarizeToolGroup(blocks, { text: en });
  assert.equal(summary.label, 'Edited 2 files, ran 2 commands');
  assert.equal(summary.failed, 2);
  assert.deepEqual([summary.added, summary.removed], [3, 3]);
});

test('edit counts scale with the replacement count from the result', () => {
  const summary = summarizeToolGroup([
    tool('edit', { file_path: '/p/a.ts', old_string: 'foo', new_string: 'bar', replace_all: true },
      { result: 'Edited /p/a.ts (4 replacements)' }),
  ], { text: en });
  assert.deepEqual([summary.added, summary.removed], [4, 4]);
});

test('a write over a file the turn already touched is an edit with an unknown count until turnFiles settles it', () => {
  const read = tool('read', { file_path: '/p/a.ts' });
  const write = tool('write', { file_path: '/p/a.ts', content: lines(5) });
  const turn = [read, { type: 'text', text: 'ok' }, write];
  const streaming = summarizeToolGroup([write], { text: en, turnBlocks: turn });
  assert.equal(streaming.label, 'Edited a.ts');
  assert.equal(streaming.added, null);
  const turnFiles = { version: 2, file_count: 1, added: 5, removed: 2,
    files: [{ path: '/p/a.ts', op: 'modify', added: 5, removed: 2 }] };
  const settled = summarizeToolGroup([write], { text: en, turnBlocks: turn, turnFiles });
  assert.deepEqual([settled.added, settled.removed], [5, 2]);
  // turnFiles says modify even with no earlier block: still an edit.
  assert.equal(summarizeToolGroup([write], { text: en, turnFiles }).label, 'Edited a.ts');
});

test('turnFiles numbers are not used when another group also wrote the file', () => {
  const first = tool('edit', { file_path: '/p/a.ts', old_string: 'a', new_string: 'b' });
  const second = tool('edit', { file_path: '/p/a.ts', old_string: 'c', new_string: 'd\ne' });
  const turn = [first, { type: 'text', text: 'then' }, second];
  const turnFiles = { version: 2, file_count: 1, added: 3, removed: 2,
    files: [{ path: '/p/a.ts', op: 'modify', added: 3, removed: 2 }] };
  const summary = summarizeToolGroup([second], { text: en, turnBlocks: turn, turnFiles });
  assert.deepEqual([summary.added, summary.removed], [2, 1]);
});

test('apply_patch sections become created, edited and deleted files with exact counts', () => {
  const patch = [
    '*** Begin Patch',
    '*** Add File: /p/new.css', '+a', '+b',
    '*** Update File: /p/app.ts', '@@ ctx', ' keep', '-old', '+new', '+more',
    '*** End Patch',
  ].join('\n');
  assert.deepEqual(parsePatch(patch).map((s) => [s.op, s.added, s.removed]), [['add', 2, 0], ['update', 2, 1]]);
  const summary = summarizeToolGroup([tool('apply_patch', { patch })], { text: en });
  assert.equal(summary.label, 'Created new.css, edited app.ts');
  assert.deepEqual([summary.added, summary.removed], [4, 1]);
  const removal = summarizeToolGroup([tool('apply_patch', { patch: '*** Begin Patch\n*** Delete File: /p/old.css\n*** End Patch' })], { text: en });
  assert.equal(removal.label, 'Deleted old.css');
  assert.equal(removal.added, null);
});

test('unknown and MCP tools are named once, counted when several', () => {
  assert.equal(summarizeToolGroup([tool('linear_search', { q: 1 }), tool('linear_search', { q: 2 }), tool('bash', { command: 'x' })], { text: en }).label,
    'Used linear_search 2 times, ran a command');
  assert.equal(summarizeToolGroup([tool('a_tool', {}), tool('b_tool', {}), tool('mcp_call', {})], { text: en }).label,
    'Used 3 tools');
  assert.equal(summarizeToolGroup([tool('a_tool', {}), tool('b_tool', {})], { text: zh }).label, '使用了 a_tool 和 b_tool');
  assert.equal(summarizeToolGroup([tool('a_tool', {}), tool('b_tool', {})], { text: en }).label, 'Used a_tool and b_tool');
});

test('browser and memory read as their own verbs, not a chain of "used"', () => {
  const blocks = [tool('a_tool', {}), tool('b_tool', {}), tool('web_use', {}), tool('memory_search', {})];
  assert.equal(summarizeToolGroup(blocks, { text: en }).label, 'Used a_tool and b_tool, operated the browser, accessed memory');
  assert.equal(summarizeToolGroup(blocks, { text: zh }).label, '使用了 a_tool 和 b_tool，操作了浏览器，读写了记忆');
});

test('sub-agents use their spawn labels, including legacy groups with no tool blocks', () => {
  assert.equal(summarizeToolGroup([], { text: en, spawnNames: ['Reviewer'] }).label, 'Ran sub-agent Reviewer');
  assert.equal(summarizeToolGroup([], { text: en, spawnNames: ['A', 'B'] }).label, 'Ran 2 sub-agents');
  assert.equal(summarizeToolGroup([tool('agent', { description: 'x' })], { text: en }).label, 'Ran a sub-agent');
  assert.equal(summarizeToolGroup([], { text: en }).label, 'Execution');
});

test('messages to an agent count that agent once', () => {
  const blocks = [
    tool('agent', { description: 'calc' }),
    tool('send_message', { to: 's1:h1', message: 'add one' }),
    tool('send_message', { to: 's1:h1', message: '', notify_when_idle: true }),
  ];
  const spawned = { text: en, spawnNames: ['calc'], spawnHeads: ['h1'], spawnStates: ['completed'] };
  assert.equal(summarizeToolGroup(blocks, spawned).label, 'Ran sub-agent calc · done');
  assert.equal(summarizeToolGroup([tool('send_message', { to: 's1:h9', message: 'hi' })], { text: en }).label,
    'Ran a sub-agent');
  assert.equal(summarizeToolGroup([tool('send_message', { to: 's1:h1' }), tool('send_message', { to: 's1:h2' })], { text: en }).label,
    'Ran 2 sub-agents');
});

test('another conversation is a peer, counted once and not as a sub-agent', () => {
  const blocks = [
    tool('send_message', { to: 'p2:HEAD', message: 'hi' }, { result: '[send_message error] target not found' }),
    tool('send_message', { to: 'p2:a9', message: 'hi' }),
    tool('send_message', { to: 'p2:a9', message: '', notify_when_idle: true }),
  ];
  assert.equal(summarizeToolGroup(blocks, { text: en, sessionId: 'p1' }).label, 'Messaged another conversation');
  assert.equal(summarizeToolGroup(blocks, { text: zh, sessionId: 'p1' }).label, '给另一个会话发了消息');
  assert.equal(summarizeToolGroup([tool('send_message', { to: 'p1:a0', message: 'hi' })], { text: en, sessionId: 'p1' }).label,
    'Ran a sub-agent');
});

test('spawned agents say where they stand, in place of a separate card', () => {
  const one = (state) => summarizeToolGroup([], { text: en, spawnNames: ['scout'], spawnStates: [state] }).label;
  assert.equal(one('completed'), 'Ran sub-agent scout · done');
  assert.equal(one('running'), 'Running sub-agent scout…');
  assert.equal(one('errored'), 'Ran sub-agent scout · failed');
  assert.equal(one('cancelled'), 'Ran sub-agent scout · cancelled');
  assert.equal(
    summarizeToolGroup([], { text: zh, spawnNames: ['scout'], spawnStates: ['completed'] }).label,
    '运行了子代理 scout · 已完成');
  assert.equal(
    summarizeToolGroup([], { text: en, spawnNames: ['A', 'B', 'C'], spawnStates: ['completed', 'completed', 'running'] }).label,
    'Ran 3 sub-agents · 2 done, 1 running');
});

test('the line diff finds the changed lines and falls back to plain counts above the cap', () => {
  assert.deepEqual(lineDiff('a\nb\nc\n', 'a\nx\nc\nd\n'), { added: 2, removed: 1 });
  assert.deepEqual(lineDiff('', 'a\nb'), { added: 2, removed: 0 });
  assert.deepEqual(lineDiff('same', 'same'), { added: 0, removed: 0 });
  const big = lines(LINE_DIFF_CAP + 1);
  const bigger = 'head\n' + big.split('\n').reverse().join('\n');
  const result = lineDiff(big, bigger);
  assert.equal(result.removed, LINE_DIFF_CAP + 1);
});

test('public computer_use calls read as operating the app; the GUI Agent capability stays a GUI call', () => {
  const calls = [
    tool('computer_use', { action: 'get_app_state', app: 'Calculator' }),
    tool('computer_use', { action: 'click', app: 'Calculator', element_index: 20 }),
  ];
  assert.equal(summarizeToolGroup(calls, { text: en }).label, 'Operated Calculator');
  assert.equal(summarizeToolGroup(calls, { text: zh }).label, '操作了 Calculator');
  const two = [...calls, tool('computer_use', { action: 'get_app_state', app: 'Notes' })];
  assert.equal(summarizeToolGroup(two, { text: en }).label, 'Operated 2 apps');
  assert.equal(summarizeToolGroup([tool('computer_use', { task: 'inspect', app_name: 'TextEdit' })], { text: en }).label, 'Ran a GUI call');
});

test('computer_use_agent groups by its target: apps, built-in browser Pages, a new agent', () => {
  const apps = [
    tool('computer_use_agent', { target: 'app', args: { app: 'Calculator' } }),
    tool('computer_use_agent', { target: 'app', args: { app: 'Calculator' }, actions: [{ action: 'click', args: { element_index: 3 } }] }),
  ];
  assert.equal(summarizeToolGroup(apps, { text: en }).label, 'Operated Calculator');
  const pages = [tool('computer_use_agent', { target: 'browser', action: 'observe' })];
  assert.equal(summarizeToolGroup(pages, { text: en }).label, summarizeToolGroup([tool('web_use', {})], { text: en }).label);
  const fresh = [tool('computer_use_agent', { target: 'app', context: 'fresh', brief: 'Sum column B', args: { app: 'Numbers' } })];
  assert.equal(summarizeToolGroup(fresh, { text: en }).label,
    summarizeToolGroup([tool('agent', { prompt: 'x' })], { text: en }).label);
});

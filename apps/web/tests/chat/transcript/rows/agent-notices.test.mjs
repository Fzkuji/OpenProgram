import assert from 'node:assert/strict';
import test from 'node:test';
import { noticeDetail, readNotice, readReceipt } from '../../../../lib/chat/transcript/agent-notices.ts';

// Text and items as agent/job/notices.py writes them.
const content = [
  'Background agents you started have finished. This is an automated notice, not a message from the user.',
  '',
  'Agent "scout" finished (job s1).',
  'Task: research X',
  'Its report is attached above.',
  '',
  'Agent "frontend" finished (job r1).',
  'Task: [message from p1:a1 (main conversation)] …',
  'Reply from branch p2:hb:',
  'Settings page done.',
  '',
  'Chat page left.',
  'To continue with it, use send_message(to="p2:hb").',
  '',
  "Carry on with the user's request from here: tell the user what they found.",
].join('\n');
const items = readNotice([
  { kind: 'report', name: 'scout', failed: false, address: 'p1:h1', line: 'Agent "scout" finished (job s1).' },
  { kind: 'reply', name: 'frontend', failed: false, address: 'p2:hb', line: 'Agent "frontend" finished (job r1).' },
  { kind: 'bogus', line: 'x' },
]);

test('unknown notice items are dropped', () => {
  assert.deepEqual(items.map((it) => it.kind), ['report', 'reply']);
  assert.equal(readNotice('nope'), undefined);
});

test('a report row opens on its task, a reply row on the whole reply', () => {
  assert.equal(noticeDetail(content, items, 0), 'research X');
  assert.equal(noticeDetail(content, items, 1), 'Settings page done.\n\nChat page left.');
});

test('a delivered message names its sender', () => {
  const r = readReceipt('[message from p1:a1 (main conversation)] Your final reply in this turn is returned to the sender automatically when the turn ends; answer there, not with send_message.\n\nhow far along?');
  assert.deepEqual(r, { kind: 'message', address: 'p1:a1', from: 'main conversation', body: 'how far along?' });
  const old = readReceipt('[task from p1:a1] This is a tracked job.\n\ndo it');
  assert.equal(old.from, '');
  assert.equal(old.body, 'do it');
  assert.equal(readReceipt('hello'), null);
});

test('a stored hand-off between conversations names the conversation that sent it', () => {
  const sent = readReceipt('📨 Message from “Butler” (conv_8a1f):\n\nCheck the deploy.\n\n(Another of your conversations handed this to you. Handle it here; your final answer goes back to it on its own.)');
  assert.deepEqual(sent, { kind: 'message', address: 'conv_8a1f', from: 'Butler', body: 'Check the deploy.' });
  const back = readReceipt('↩️ Reply from “Butler” (conv_8a1f) to the message you sent it:\n\nYes, 3 files.');
  assert.deepEqual(back, { kind: 'reply', address: 'conv_8a1f', from: 'Butler', body: 'Yes, 3 files.' });
  assert.equal(readReceipt('I saw 📨 Message from “Butler” (x): earlier'), null);
});

// The same notice since every result comes back in an <agent> block
// (runtime/jobs/agent_result.py render), with a stalled agent among them.
const blocked = [
  'Background agents you started have finished. This is an automated notice, not a message from the user.',
  '',
  'Agent "scout" finished (job s1).',
  'Task: research X',
  '<agent name="scout" id="s1" status="completed">',
  '(its report is attached above)',
  'usage: 41 s · 12 tool calls · 38k tokens · $0.021',
  '</agent>',
  '',
  'Agent "frontend" finished (job r1).',
  'Task: [message from p1:a1 (main conversation)] …',
  'Reply from branch p2:hb:',
  '<agent name="frontend" id="r1" status="completed">',
  'Settings page done.',
  '',
  'Chat page left.',
  'files: worktree /tmp/wt, 2 changed (a.py, b.py)',
  'usage: 2 min · 3 tool calls · 9.1k tokens · cost unknown',
  '</agent>',
  'To continue with it, use send_message(to="p2:hb").',
  '',
  'Agent "slow" has had no activity for 10 minutes and is marked stalled; it is still running (job t1).',
  'Task: index the repo',
  '<agent name="slow" id="t1" status="stalled">',
  '(no reply yet) Wait for it, steer it with send_message, or stop it with close_agent("t1").',
  'usage: 12 min · 40 tool calls · 120k tokens · $0.300',
  '</agent>',
  '',
  "Carry on with the user's request from here: tell the user what they found.",
].join('\n');
const blockedItems = readNotice([
  { kind: 'report', name: 'scout', failed: false, status: 'completed', address: 'p1:h1', line: 'Agent "scout" finished (job s1).' },
  { kind: 'reply', name: 'frontend', failed: false, status: 'completed', address: 'p2:hb', line: 'Agent "frontend" finished (job r1).' },
  { kind: 'stalled', name: 'slow', failed: false, status: 'stalled', address: 'p1:h3', line: 'Agent "slow" has had no activity for 10 minutes and is marked stalled; it is still running (job t1).' },
]);

test('a stalled item draws a row and opens on its task', () => {
  assert.deepEqual(blockedItems.map((it) => it.kind), ['report', 'reply', 'stalled']);
  assert.equal(noticeDetail(blocked, blockedItems, 2), 'index the repo');
});

test('a row opens on the reply inside the result block, without its tags, files or usage', () => {
  assert.equal(noticeDetail(blocked, blockedItems, 0), 'research X');
  assert.equal(noticeDetail(blocked, blockedItems, 1), 'Settings page done.\n\nChat page left.');
});

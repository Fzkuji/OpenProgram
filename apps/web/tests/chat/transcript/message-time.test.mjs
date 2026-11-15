import assert from 'node:assert/strict';
import test from 'node:test';
import { formatMessageTime } from '../../../lib/chat/transcript/message-time.ts';

const now = new Date(2026, 9, 19, 16, 30);
const clock = (date) => date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

test('a message from today shows only its time', () => {
  const sent = new Date(2026, 9, 19, 9, 5);
  assert.equal(formatMessageTime(sent, now), clock(sent));
});

test('an earlier day shows its date before the time', () => {
  const sent = new Date(2026, 9, 16, 21, 39);
  const label = formatMessageTime(sent, now);
  assert.ok(label.endsWith(clock(sent)));
  assert.ok(label.startsWith(sent.toLocaleDateString([], { month: 'short', day: 'numeric' })));
  assert.ok(!label.includes('2026'));
});

test('another year also shows the year', () => {
  const sent = new Date(2025, 11, 31, 23, 0);
  assert.ok(formatMessageTime(sent, now).includes('2025'));
});

import assert from 'node:assert/strict';
import test from 'node:test';
import { executionMessageIds, pendingExecutionReplayRequests } from '../../lib/net/execution-message-recovery.ts';
test('canonical replay display restores message ownership without input', () => {
  assert.deepEqual(executionMessageIds({display:{user_message_id:'u',assistant_message_id:'a'}}), ['u','a']);
  assert.deepEqual(executionMessageIds({display:{assistant_message_id:'a'}},{user_message_id:'wrong'}), ['a']);
  assert.deepEqual(executionMessageIds({}, {user_message_id:'legacy'}), ['legacy']);
  assert.deepEqual(executionMessageIds({display:{assistant_message_id:17}}), []);
});
test('pending wait recovery requests canonical snapshots once per execution without message guesses', () => {
  assert.deepEqual(pendingExecutionReplayRequests([{execution_id:'e'},{execution_id:'e'},{execution_id:''},{execution_id:'f'}]), [
    {action:'execution.replay',execution_id:'e',after_sequence:0},
    {action:'execution.replay',execution_id:'f',after_sequence:0},
  ]);
});

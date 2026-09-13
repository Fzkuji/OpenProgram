import assert from 'node:assert/strict';
import test from 'node:test';
import { activityBranches } from '../../lib/execution/activity-branches.ts';
const run = (id, parent = null) => ({ execution_id: id, parent_execution_id: parent });
const branch = (id, ids) => ({ branch_id: id, execution_ids: ids });
test('one conversation branch retains multiple turns and programs without multiplying rows', () => {
  const result = activityBranches([run('last'), run('first'), run('job', 'first')], [branch('tip', ['first', 'last'])]);
  assert.equal(result.groups.length, 1);
  assert.deepEqual(result.groups[0].executions.map(x => x.execution_id), ['last', 'first', 'job']);
  assert.deepEqual(result.ungrouped, []);
});
test('real forks remain separate even with shared history; missing association is not a branch', () => {
  const result = activityBranches([run('shared'), run('a'), run('b'), run('unknown')], [branch('a', ['shared', 'a']), branch('b', ['shared', 'b'])]);
  assert.equal(result.groups.length, 2);
  assert.deepEqual(result.ungrouped.map(x => x.execution_id), ['unknown']);
});

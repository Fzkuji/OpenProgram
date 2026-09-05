import test from 'node:test';
import assert from 'node:assert/strict';
import { permissionSnapshotPatch } from '../lib/session-store/permission-state.ts';

test('confirmed modes reject stale hydration and invalid payloads', () => {
  const current = { permission_version: 4 };
  assert.equal(permissionSnapshotPatch(current, { mode: 'ask', version: 3 }), null);
  assert.equal(permissionSnapshotPatch(current, { mode: 'unknown', version: 5 }), null);
  assert.equal(permissionSnapshotPatch(current, { mode: 'bypass', version: -1 }), null);
  assert.deepEqual(permissionSnapshotPatch(current, { mode: 'bypass', version: 5 }), {
    permission_mode: 'bypass', effective_permission: 'bypass', permission_version: 5,
  });
});
test('legacy and draft sessions start at version zero', () => {
  assert.equal(permissionSnapshotPatch({}, { mode: 'ask', version: 0 }).permission_version, 0);
});

import assert from 'node:assert/strict';
import test from 'node:test';
import { systemAccessRequired } from '../components/chat/messages/system-access-result.ts';
test('only structured blocked results expose known system capabilities', () => {
 const value={status:'infeasible',reason_code:'system_access_required',system_access:[{id:'screen_recording',status:'not_granted'},{id:'accessibility',status:'granted'},{id:'arbitrary',status:'not_granted'}]};
 assert.deepEqual(systemAccessRequired(JSON.stringify(value)),['screen_recording']);
 assert.deepEqual(systemAccessRequired({...value,status:'succeeded'}),[]);
 assert.deepEqual(systemAccessRequired({status:'infeasible',summary:'please open settings'}),[]);
 assert.deepEqual(systemAccessRequired('not json'),[]);
});

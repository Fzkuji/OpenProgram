import test from 'node:test';
import assert from 'node:assert/strict';
import { systemAccessAction as next } from '../lib/system-access-action.ts';
test('live recovery requests each missing capability once then resumes', () => {
 const sent = new Set();
 assert.deepEqual(next(true,true,true,true,false,false,['screen_recording','accessibility'],sent), {type:'request',id:'screen_recording'});
 sent.add('screen_recording');
 assert.equal(next(true,true,true,true,false,false,['screen_recording','accessibility'],sent),null);
 assert.deepEqual(next(true,true,true,true,false,false,['accessibility'],sent), {type:'request',id:'accessibility'});
 sent.add('accessibility');
 assert.deepEqual(next(true,true,true,true,false,false,[],sent),{type:'resume'});
 assert.equal(next(true,true,true,true,false,true,[],sent),null);
});
test('history, remote, hidden, unchecked and pending recovery never resumes', () => {
 for(const flags of [[false,true,true,true,false,false],[true,false,true,true,false,false],[true,true,false,true,false,false],[true,true,true,false,false,false],[true,true,true,true,true,false]])
  assert.equal(next(...flags,[],new Set()),null);
});

import test from 'node:test';
import assert from 'node:assert/strict';
import { systemAccessAction as next } from '../lib/system-access-action.ts';
test('live recovery requests native access once and never resumes a task', () => {
 const sent = new Set();
 assert.deepEqual(next(true,true,true,true,false,['screen_recording','accessibility'],sent), {type:'request',id:'screen_recording'});
 sent.add('screen_recording');
 assert.equal(next(true,true,true,true,false,['screen_recording','accessibility'],sent),null);
 assert.deepEqual(next(true,true,true,true,false,['accessibility'],sent), {type:'request',id:'accessibility'});
 sent.add('accessibility');
 assert.equal(next(true,true,true,true,false,[],sent),null);
});
test('history, remote, hidden, unchecked and pending recovery never resumes', () => {
 for(const flags of [[false,true,true,true,false],[true,false,true,true,false],[true,true,false,true,false],[true,true,true,false,false],[true,true,true,true,true]])
  assert.equal(next(...flags,[],new Set()),null);
});

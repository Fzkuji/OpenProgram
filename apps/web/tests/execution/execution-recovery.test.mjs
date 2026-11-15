import assert from 'node:assert/strict';
import test from 'node:test';
import { createExecutionRecovery } from '../../lib/net/execution/execution-recovery.ts';

function harness() {
  const sent = [], jobs = new Map(); let id = 0;
  const recovery = createExecutionRecovery({
    send: request => sent.push(request),
    schedule: (callback, delay) => { jobs.set(++id, {callback, delay}); return id; },
    cancel: id => jobs.delete(id),
  });
  const tick = (delay = 0) => {
    for (const [id, job] of [...jobs]) if (job.delay === delay) { jobs.delete(id); job.callback(); }
  };
  return { recovery, sent, jobs, tick };
}

test('recovery waits for visible history and sends one request at a time', () => {
  const h = harness();
  for(let i=0;i<1000;i++)h.recovery.request(`e${i}`, 3);
  h.tick(); assert.equal(h.sent.length, 0);
  h.recovery.start(); h.tick();
  assert.deepEqual(h.sent, [{action:'execution.replay',execution_id:'e0',after_sequence:3,snapshot_only:true}]);
  h.tick(); assert.equal(h.sent.length, 1);
  h.recovery.complete('e0'); h.tick(); assert.equal(h.sent.length, 2);
  h.recovery.dispose(); assert.equal(h.jobs.size, 0);
});
test('pending requests coalesce at earliest required cursor', () => {
  const h=harness(); h.recovery.request('e',5);h.recovery.request('e',0);h.recovery.request('e',7);
  h.recovery.start();h.tick();assert.equal(h.sent[0].after_sequence,0);
  h.recovery.request('e',0);h.recovery.complete('e');h.tick();assert.equal(h.sent.length,1);
});
test('a newer gap while recovery is in flight gets one follow-up', () => {
  const h=harness();h.recovery.start();h.recovery.request('e',2);h.tick();
  h.recovery.request('e',7);h.recovery.request('e',5);h.recovery.complete('e');h.tick();
  assert.equal(h.sent.length,2);assert.equal(h.sent[1].after_sequence,5);
});
test('terminal recovery removes redundant queued requests', () => {
  const h=harness();h.recovery.start();h.recovery.request('e',2);h.tick();
  h.recovery.request('e',5);h.recovery.complete('e',true);h.tick();assert.equal(h.sent.length,1);
});
test('timeout, send failure and disconnect release owned work', () => {
  const h=harness();h.recovery.start();h.recovery.request('a',0);h.recovery.request('b',0);h.tick();
  h.tick(15000);h.tick();assert.equal(h.sent.length,2);
  h.recovery.dispose();h.recovery.request('c',0);h.tick();assert.equal(h.jobs.size,0);
  let callback;const broken=createExecutionRecovery({send:()=>{throw Error('closed')},schedule:fn=>{callback=fn;return 1},cancel:()=>{}});
  broken.start();broken.request('x',0);assert.doesNotThrow(()=>callback());broken.dispose();
});

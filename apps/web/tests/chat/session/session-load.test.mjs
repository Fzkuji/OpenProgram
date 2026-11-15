import assert from 'node:assert/strict';
import test from 'node:test';
import { createSessionLoads } from '../../../lib/net/ws/session-load.ts';
function fixture(t) {
  t.mock.timers.enable({apis:['setTimeout']});
  const sent=[], states=[]; let sequence=0;
  const owner=createSessionLoads({send:r=>sent.push(r),status:(...s)=>states.push(s),requestId:()=>String(++sequence)});
  t.after(()=>{owner.dispose();t.mock.timers.reset();});
  const request=(id,invalidate=false,extra={})=>owner.request({action:'load_session',session_id:id,...extra},invalidate);
  const reply=(i)=>{const accepted=owner.accept({id:sent[i].session_id,request_id:sent[i].request_id});return accepted&&{...accepted,context:{id:sent[i].session_id}};};
  return {owner,sent,states,request,reply};
}
test('A/B/A and split-pane reads coalesce per session and retain response ownership',t=>{
 const f=fixture(t);f.request('a');f.request('b');f.request('a');
 assert.equal(f.sent.length,2);assert.equal(f.reply(1).context.id,'b');assert.equal(f.reply(0).context.id,'a');assert.equal(f.reply(0),false);
});
test('mutation invalidations coalesce into one replacement with latest options',t=>{
 const f=fixture(t);f.request('a');f.request('a',true,{graph:true});f.request('a',true,{graph:false});
 const first=f.reply(0);assert.equal(first.context.id,'a');assert.equal(first.next.graph,false);assert.equal(f.sent.length,1);
 f.owner.request(first.next);assert.equal(f.sent.length,2);assert.equal(f.sent[1].graph,false);
 assert.equal(f.reply(0),false);assert.equal(f.reply(1).context.id,'a');
});
test('timeout releases UI and late replies cannot settle a retry',t=>{
 const f=fixture(t);f.request('a');t.mock.timers.tick(15000);assert.deepEqual(f.states.at(-1),['a','error']);
 assert.equal(f.reply(0),false);f.request('a');assert.equal(f.reply(0),false);assert.equal(f.reply(1).context.id,'a');
});
test('closed owner rejects replies and cleans up every pending timer',t=>{
 const f=fixture(t);f.request('a');f.request('b');f.owner.dispose();
 assert.deepEqual(f.states.slice(-2),[['a','disconnected'],['b','disconnected']]);assert.equal(f.reply(0),false);
 t.mock.timers.tick(60000);assert.equal(f.states.length,4);
});
test('correlated errors release only their request',t=>{
 const f=fixture(t);f.request('a');f.request('b');assert.equal(f.owner.error({request_id:'1'}),true);
 assert.deepEqual(f.states.at(-1),['a','error']);assert.equal(f.reply(1).context.id,'b');
});
test('repair and page reads of a view run one at a time, and a newer one replaces the waiting one',async t=>{
 const f=fixture(t);const log=[];let release;
 const first=f.owner.exclusive('a',()=>new Promise(resolve=>{log.push('first');release=resolve;}),'skipped');
 const second=f.owner.exclusive('a',async()=>{log.push('second');return 'second';},'skipped');
 const third=f.owner.exclusive('a',async()=>{log.push('third');return 'third';},'skipped');
 const other=f.owner.exclusive('b',async()=>{log.push('other');return 'other';},'skipped');
 assert.equal(await other,'other');assert.equal(await second,'skipped');
 release('first');assert.equal(await first,'first');assert.equal(await third,'third');
 assert.deepEqual(log,['first','other','third']);
});

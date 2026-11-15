import assert from 'node:assert/strict';
import test from 'node:test';
import { startHistoryAutoload } from '../../../lib/chat/history/history-autoload.ts';

function fixture(t, metrics = {}) {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const old = new Map();
  const put = (key, value) => { old.set(key, Object.getOwnPropertyDescriptor(globalThis,key)); Object.defineProperty(globalThis,key,{value,configurable:true,writable:true}); };
  const frames = new Map(); let frameId=0;
  const doc=Object.assign(new EventTarget(),{visibilityState:'visible'});
  put('document',doc);put('window',new EventTarget());
  put('requestAnimationFrame',fn=>{frames.set(++frameId,fn);return frameId;});
  put('cancelAnimationFrame',id=>frames.delete(id));
  let resize;
  put('ResizeObserver',class { constructor(fn){resize=fn;} observe(){} disconnect(){resize=null;} });
  const area=Object.assign(new EventTarget(),{clientHeight:500,scrollTop:4000,...metrics});
  let page={before:'older',head_id:'head',generation:1,loading:false,error:false};
  const listeners=new Set();const requests=[];
  const update=patch=>{page={...page,...patch};for(const fn of listeners)fn();};
  const stop=startHistoryAutoload(area,{
    read:()=>page,subscribe:fn=>{listeners.add(fn);return()=>listeners.delete(fn);},
    load:(direction)=>new Promise((resolve,reject)=>{requests.push({resolve,reject,direction});}),
  });
  const frame=()=>{const callbacks=[...frames.values()];frames.clear();callbacks.forEach(fn=>fn());};
  const settle=async(patch={},reject=false)=>{update(patch); const req=requests.at(-1); if(reject)req.reject(new Error('offline'));else req.resolve(); await Promise.resolve(); await Promise.resolve(); await Promise.resolve();};
  t.after(()=>{stop();t.mock.timers.reset();for(const[key,desc]of old){if(desc)Object.defineProperty(globalThis,key,desc);else delete globalThis[key];}});
  return {area,doc,frame,requests,settle,update,stop,listeners,resize:()=>resize?.()};
}

test('prefetches near the top, serializes requests and stops at exhausted history',async t=>{
  const f=fixture(t);f.frame();assert.equal(f.requests.length,0);
  f.area.scrollTop=100;f.area.dispatchEvent(new Event('scroll'));f.frame();
  assert.equal(f.requests.length,1);
  for(let i=0;i<5;i++){f.area.dispatchEvent(new Event('scroll'));f.frame();}
  assert.equal(f.requests.length,1);
  // Simulate the preserved viewport moving down after a prepend.
  f.area.scrollTop=5000;await f.settle({before:'much-older'});f.frame();
  assert.equal(f.requests.length,1);
  f.area.scrollTop=0;f.area.dispatchEvent(new Event('scroll'));f.frame();
  assert.equal(f.requests.length,2);
  await f.settle({before:null});f.frame();f.resize();f.frame();
  assert.equal(f.requests.length,2);
});

test('failed and nonadvancing requests retry with backoff without clicks',async t=>{
  const f=fixture(t);f.area.scrollTop=0;f.frame();
  await f.settle({error:true},true);
  f.area.dispatchEvent(new Event('scroll'));f.frame();assert.equal(f.requests.length,1);
  t.mock.timers.tick(999);f.frame();assert.equal(f.requests.length,1);
  t.mock.timers.tick(1);f.frame();assert.equal(f.requests.length,2);
  await f.settle({error:false}); // Same cursor: prevent an unbounded request loop.
  t.mock.timers.tick(1999);f.frame();assert.equal(f.requests.length,2);
  t.mock.timers.tick(1);f.frame();assert.equal(f.requests.length,3);
  await f.settle({before:null});f.frame();assert.equal(f.requests.length,3);
});

test('hidden views do not fetch and cleanup prevents completion from scheduling work',async t=>{
  const f=fixture(t);f.area.scrollTop=0;f.area.clientHeight=0;f.frame();
  assert.equal(f.requests.length,0);
  f.area.clientHeight=500;f.doc.visibilityState='hidden';f.resize();f.frame();
  assert.equal(f.requests.length,0);
  f.doc.visibilityState='visible';f.doc.dispatchEvent(new Event('visibilitychange'));f.frame();
  assert.equal(f.requests.length,1);
  f.stop();await f.settle({error:true});t.mock.timers.tick(60000);f.frame();
  assert.equal(f.requests.length,1);assert.equal(f.listeners.size,0);
});

test('a concurrent latest load does not back off older prefetch',async t=>{
  const f=fixture(t);f.area.scrollTop=0;f.frame();
  assert.equal(f.requests.length,1);
  await f.settle({loading:true});
  f.frame();
  f.update({loading:false});
  f.area.dispatchEvent(new Event('scroll'));f.frame();
  assert.equal(f.requests.length,2);
  await f.settle({before:null});f.frame();
});

test('a new session generation clears old retry delays',async t=>{
  const f=fixture(t);f.area.scrollTop=0;f.frame();await f.settle({error:true});
  f.update({generation:2,error:false,before:'new-history'});f.frame();
  assert.equal(f.requests.length,2);
  await f.settle({before:null});f.frame();
});

 test('connectivity recovery clears backoff and cleanup removes the listener',async t=>{
  const f=fixture(t);f.area.scrollTop=0;f.frame();await f.settle({error:true});
  window.dispatchEvent(new Event('op:browser-connection'));f.frame();
  assert.equal(f.requests.length,2);
  await f.settle({error:true});
  window.dispatchEvent(new Event('online'));f.frame();
  assert.equal(f.requests.length,3);
  f.stop();await f.settle({error:true});
  window.dispatchEvent(new Event('op:browser-connection'));f.frame();
  assert.equal(f.requests.length,3);
 });

 test('loading transitions retain exponential failure backoff', async t => {
  const f=fixture(t); f.area.scrollTop=0; f.frame();
  f.update({loading:true}); f.frame();
  await f.settle({loading:false,error:true});
  t.mock.timers.tick(1000); f.frame();
  assert.equal(f.requests.length,2);
  f.update({loading:true}); f.frame();
  await f.settle({loading:false,error:true});
  t.mock.timers.tick(1000); f.frame();
  assert.equal(f.requests.length,2,'second failure must wait two seconds');
  t.mock.timers.tick(1000); f.frame(); assert.equal(f.requests.length,3);
  await f.settle({loading:false,before:null});
 });

test('failed snapshot renewal retries with backoff instead of stopping for good', async t => {
 const f=fixture(t);f.area.scrollTop=0;f.frame();
 await f.settle({error:true,renewalRequired:true});
 f.area.dispatchEvent(new Event('scroll'));f.frame();
 assert.equal(f.requests.length,1,'a failed renewal waits for the backoff');
 t.mock.timers.tick(1000);f.frame();
 assert.equal(f.requests.length,2,'the data layer gets another chance to renew');
 await f.settle({error:false,renewalRequired:false,before:'renewed'});f.frame();
 assert.equal(f.requests.length,3);await f.settle({before:null});
});

 test('positive latest placement does not extend opposite older lookahead',t=>{
  const f=fixture(t,{scrollHeight:7444});f.area.scrollTop=6944;f.frame();
  assert.equal(f.requests.length,0);
 });
 test('negative movement does not extend opposite newer lookahead',t=>{
  const f=fixture(t,{scrollHeight:10000});f.update({before:null,after:'newer'});
  f.area.scrollTop=2000;f.frame();assert.equal(f.requests.length,0);
 });
 test('directional lookahead retains early bidirectional paging',t=>{
  const f=fixture(t,{scrollHeight:10000});f.update({after:'newer'});
  f.area.scrollTop=2000;f.frame();assert.equal(f.requests[0].direction,'older');
 });
 test('positive lookahead reaches newer before the fixed edge threshold',t=>{
  const f=fixture(t,{scrollHeight:8500});f.update({after:'newer'});
  f.area.scrollTop=6000;f.frame();assert.equal(f.requests[0].direction,'newer');
 });
 test('short content still fills without movement',t=>{
  const f=fixture(t,{scrollHeight:300,scrollTop:0});f.frame();
  assert.equal(f.requests[0].direction,'older');
 });

test('virtualized height shrink at latest is not upward reading velocity',t=>{
 const f=fixture(t,{scrollHeight:8244,scrollTop:7744});
 f.area.scrollHeight=7444;f.area.scrollTop=6944;f.frame();
 assert.equal(f.requests.length,0);
});

test('a resize checks in the observer, after layout, and never again in a frame', t => {
 const f=fixture(t,{scrollHeight:10000});f.frame();
 // Reads of the area's measures, by the phase they happen in.
 const reads={observer:0,frame:0};let phase='frame';
 const height=f.area.scrollHeight;
 Object.defineProperty(f.area,'scrollHeight',{configurable:true,get(){reads[phase]+=1;return height;}});
 // A pane sliding: the area resizes in every frame, and a scroll queued a frame first.
 f.area.dispatchEvent(new Event('scroll'));
 for(let i=0;i<5;i+=1){phase='observer';f.resize();phase='frame';f.frame();}
 assert.ok(reads.observer>0,'measured in the observer');
 assert.equal(reads.frame,0,'no frame callback lays the page out');
 assert.equal(f.requests.length,0,'far from both edges: nothing to load');
 // Resized so that the top comes within reach: it loads, as from a frame before.
 f.area.scrollTop=100;phase='observer';f.resize();
 assert.equal(f.requests.length,1);assert.equal(f.requests[0].direction,'older');
});

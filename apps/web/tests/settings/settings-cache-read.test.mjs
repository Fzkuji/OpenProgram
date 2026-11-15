import assert from 'node:assert/strict';
import test from 'node:test';
import {cachedFetch,readCachedSettings,invalidate} from '../../lib/prefs/settings-cache.ts';
test('synchronous reads only expose successful fresh responses and respect invalidation',async()=>{
 const originalFetch=globalThis.fetch, originalNow=Date.now;
 let now=1000,calls=0;Date.now=()=>now;
 globalThis.fetch=async()=>({ok:true,json:async()=>({value:++calls})});
 try {
  assert.equal(readCachedSettings('/cache-test'),undefined);
  const data=await cachedFetch('/cache-test');
  assert.equal(readCachedSettings('/cache-test'),data);
  now+=30000;
  assert.equal(readCachedSettings('/cache-test'),undefined);
  await cachedFetch('/cache-test');
  assert.equal(calls,2);
  invalidate('/cache-test');
  assert.equal(readCachedSettings('/cache-test'),undefined);
 }finally{globalThis.fetch=originalFetch;Date.now=originalNow;invalidate('/cache-test');}
});
test('a failed read carries what the server said went wrong',async()=>{
 const originalFetch=globalThis.fetch;
 globalThis.fetch=async()=>({ok:false,status:500,json:async()=>({error:'OSError: catalogue unreadable'})});
 try {
  await assert.rejects(cachedFetch('/cache-error'),/OSError: catalogue unreadable/);
  globalThis.fetch=async()=>({ok:false,status:401,json:async()=>{throw new SyntaxError('not json');}});
  await assert.rejects(cachedFetch('/cache-error'),/\/cache-error → 401/);
 }finally{globalThis.fetch=originalFetch;invalidate('/cache-error');}
});

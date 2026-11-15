import assert from 'node:assert/strict';
import test from 'node:test';

async function fixture(t) {
  const previous = {Image:globalThis.Image, document:globalThis.document};
  const requests=[];
  class Image {
    naturalWidth=16; naturalHeight=16;
    set src(value) { this.url=value; requests.push(this); }
    removeAttribute() {this.cancelled=true;}
  }
  globalThis.Image=Image;
  globalThis.document={createElement:()=>({getContext:()=>({drawImage(){},getImageData:()=>({data:new Uint8Array([0,0,0,255])})})})};
  t.mock.timers.enable({apis:['Date','setTimeout'],now:0});
  t.after(()=>{globalThis.Image=previous.Image;globalThis.document=previous.document;t.mock.timers.reset();});
  const cache=await import(`../../lib/browser/favicon-cache.ts?case=${encodeURIComponent(t.name)}`);
  return {...cache,requests};
}

test('favicon requests coalesce and decoded successes are immediately reusable',async t=>{
  const c=await fixture(t);
  const first=c.loadFavicon('https://icon.test/a.png');
  const second=c.loadFavicon('https://icon.test/a.png');
  assert.equal(c.requests.length,1);
  assert.equal(c.requests[0].referrerPolicy,'no-referrer');
  c.requests[0].onload();
  const icon=await first;
  assert.ok(icon);
  assert.equal(await second,icon);
  assert.equal(c.cachedFavicon('https://icon.test/a.png'),icon);
  assert.equal(await c.loadFavicon('https://icon.test/a.png'),icon);
  assert.equal(c.requests.length,1);
  t.mock.timers.tick(30*60_000);
  const reload=c.loadFavicon('https://icon.test/a.png');
  assert.equal(c.requests.length,2);
  c.requests[1].onload();await reload;
});

test('failures are reused briefly, changed sources recover, and timeouts cancel',async t=>{
  const c=await fixture(t);
  const bad=c.loadFavicon('https://icon.test/bad.png');
  c.requests[0].onerror();assert.equal(await bad,null);
  assert.equal(await c.loadFavicon('https://icon.test/bad.png'),null);
  assert.equal(c.requests.length,1);
  const fallback=c.loadFavicon('https://icon.test/bad.png','https://icon.test/good.png');
  await Promise.resolve();
  c.requests[1].onload();assert.ok(await fallback);
  t.mock.timers.tick(5*60_000);
  const retry=c.loadFavicon('https://icon.test/bad.png');
  assert.equal(c.requests.length,3);
  t.mock.timers.tick(5000);
  assert.equal(await retry,null);
  assert.equal(c.requests[2].cancelled,true);
  assert.equal(c.requests[2].onload,null);
  assert.equal(await c.loadFavicon('file:///private/icon.png'),null);
  assert.equal(await c.loadFavicon('https://user:password@icon.test/'),null);
  assert.equal(c.requests.length,3);
});

test('capacity evicts old entries and cancels pending image loads',async t=>{
  const c=await fixture(t);
  const pending=c.loadFavicon('https://icon.test/pending.png');
  for(let i=0;i<256;i++) {
    const ready=c.loadFavicon(`https://icon.test/${i}.png`);
    c.requests.at(-1).onload();await ready;
  }
  assert.equal(await pending,null);
  assert.equal(c.requests[0].cancelled,true);
  assert.equal(c.cachedFavicon('https://icon.test/pending.png'),undefined);
  const again=c.loadFavicon('https://icon.test/pending.png');
  c.requests.at(-1).onload();assert.ok(await again);
});

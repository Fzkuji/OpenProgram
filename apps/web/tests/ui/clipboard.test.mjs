import test from 'node:test';
import assert from 'node:assert/strict';
import { registerHooks } from 'node:module';
import { existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
registerHooks({resolve(specifier,context,next){
  if(specifier.startsWith('@/')) return next(new URL('../../'+specifier.slice(2)+'.ts',import.meta.url).href,context);
  if(specifier.startsWith('.') && !/\.[a-z]+$/.test(specifier)) {
    const url=new URL(specifier+'.ts',context.parentURL);
    if(existsSync(fileURLToPath(url)))return {url:url.href,shortCircuit:true};
  }
  return next(specifier,context);
}});
const {copyText}=await import('../../lib/clipboard.ts');
const {registerClipboardIpc}=await import('../../../desktop/main/web-views/clipboard.js');

test('native menu copy succeeds without browser activation', async()=>{
  let copied; globalThis.window={openprogramDesktop:{writeClipboardText:async text=>{copied=text;}}};
  assert.equal(await copyText('http://127.0.0.1:18100/s/one'),true);
  assert.equal(copied,'http://127.0.0.1:18100/s/one');
});

test('denied browser clipboard uses selection fallback and restores focus',async()=>{
  globalThis.window={};let selected=false,removed=false,focused=false;
  Object.defineProperty(globalThis,'navigator',{configurable:true,value:{clipboard:{writeText:async()=>{throw Error('NotAllowedError');}}}});
  globalThis.document={activeElement:{focus(){focused=true;}},createElement(){return {style:{},focus(){},select(){selected=true;},remove(){removed=true;}};},body:{appendChild(){}},execCommand:()=>true};
  assert.equal(await copyText('link'),true);
  assert.ok(selected && removed && focused);
  document.execCommand=()=>false;
  assert.equal(await copyText('link'),false);
});

test('desktop clipboard rejects foreign frames and invalid payloads',()=>{
  let handler,copied;
  registerClipboardIpc({handle(name,fn){assert.equal(name,'clipboard:write-text');handler=fn;}},{writeText(text){copied=text;}},event=>event.trusted);
  assert.throws(()=>handler({},'link'),/Unauthorized/);
  assert.throws(()=>handler({trusted:true},{}),/Invalid/);
  assert.throws(()=>handler({trusted:true},'x'.repeat(1_000_001)),/Invalid/);
  handler({trusted:true},'link');assert.equal(copied,'link');
});

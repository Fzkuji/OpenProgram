import assert from 'node:assert/strict';
import test, { after } from 'node:test';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { build } from 'esbuild';
const dir = await mkdtemp(join(tmpdir(), 'op-function-retry-'));
after(() => rm(dir, {recursive:true,force:true}));
const file = join(dir,'retry.mjs');
await build({entryPoints:['lib/runtime-bridge/function-retry.ts'],bundle:true,platform:'node',format:'esm',outfile:file});
const {startFunctionRetry, settleFunctionRetry} = await import(pathToFileURL(file));
function begin(id, send=()=>true) {
 const calls=[];
 startFunctionRetry({sessionId:'s',requestId:id,apply:()=>calls.push('pending'),restore:()=>calls.push('restore'),settled:()=>false,send,toast:m=>calls.push(m),timeoutMessage:'timeout'});
 return calls;
}
test('accepted retry waits for function without a second timeout', t=>{
 t.mock.timers.enable({apis:['setTimeout','setInterval']});
 const calls=begin('accepted');
 assert.equal(settleFunctionRetry('s','accepted'),true);
 t.mock.timers.tick(20000); assert.deepEqual(calls,['pending']);
});
test('rejection restores matching card once and cancels timeout',t=>{
 t.mock.timers.enable({apis:['setTimeout','setInterval']});
 const calls=begin('rejected');
 assert.equal(settleFunctionRetry('other','rejected','wrong'),false);
 assert.deepEqual(calls,['pending']);
 settleFunctionRetry('s','rejected','tool missing');
 settleFunctionRetry('s','rejected','duplicate');
 t.mock.timers.tick(20000); assert.deepEqual(calls,['pending','restore','tool missing']);
});
test('disconnected send restores immediately',()=>{
 assert.deepEqual(begin('offline',()=>false),['pending','restore','Connection unavailable — retry was not sent.']);
});
test('unconfirmed retry times out once and never resubmits',t=>{
 t.mock.timers.enable({apis:['setTimeout','setInterval']});
 let sent=0; const calls=begin('lost',()=>{sent++;return true});
 t.mock.timers.tick(20000);
 assert.equal(sent,1); assert.deepEqual(calls,['pending','restore','timeout']);
 assert.equal(settleFunctionRetry('s','lost'),false);
});

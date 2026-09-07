import {readFileSync} from 'node:fs';
import {registerHooks,createRequire} from 'node:module';
import assert from 'node:assert/strict';
import test from 'node:test';
import {fileURLToPath,pathToFileURL} from 'node:url';
const repo=fileURLToPath(new URL('../../../',import.meta.url));
const require=createRequire(repo+'/apps/web/package.json');
const ts=require('typescript');
const target=repo+'/apps/web/lib/net/use-ws.ts';
const code=ts.transpileModule(readFileSync(target,'utf8'),{compilerOptions:{module:ts.ModuleKind.ESNext}}).outputText;
const mocks=new Map();
for(const m of code.matchAll(/import\s*\{([^}]+)\}\s*from\s*["']([^"']+)["']/g)) {
 const names=m[1].split(',').map(x=>x.trim()).filter(Boolean);
 mocks.set(m[2],(mocks.get(m[2])??'')+names.map(n=>`export const ${n} = globalThis.probeValues[${JSON.stringify(n)}] ?? (()=>{});`).join('\n'));
}
mocks.set('@/lib/session-store','export const useSessionStore = globalThis.probeValues.useSessionStore');
let cleanup, socket;
const state={sandbox:true};
globalThis.probeValues={
 useEffect:fn=>{cleanup=fn()},
 useSessionStore:{getState:()=>({setComposerSettings:(patch,id)=>{Object.assign(state,patch);void id}})},
 recordExecutionCursor:()=>({}),consumeCommandErrorFrame:()=>false,
 runtimeState:{},setSocket:s=>{socket=s},waitForOwnerAuthBootstrap:async()=>{},
};
globalThis.location={protocol:'http:',host:'unused'};
globalThis.WebSocket=class {static OPEN=1;readyState=1;send(){}close(){}};
registerHooks({resolve(s,c,next){if(mocks.has(s))return{url:'data:text/javascript,'+encodeURIComponent(mocks.get(s)),shortCircuit:true};return next(s,c)},load(url,c,next){if(url===pathToFileURL(target).href)return{format:'module',source:code,shortCircuit:true};return next(url,c)}});
test('global Sandbox updates accept mutations but ignore correlated read/write replies',async()=>{
 const {useWS}=await import(pathToFileURL(target).href);useWS();
 await new Promise(resolve=>setImmediate(resolve));
 const reply=(id,value)=>socket.onmessage({data:JSON.stringify({type:'sandbox_changed',data:{session_id:'A',action:'set_sandbox',...(id?{request_id:id}:{}),sandbox:value}})});
 try {
  reply(null,false);await new Promise(resolve=>setImmediate(resolve));assert.equal(state.sandbox,false);
  reply('old-read',true);await new Promise(resolve=>setImmediate(resolve));assert.equal(state.sandbox,false);
  reply('old-write',true);await new Promise(resolve=>setImmediate(resolve));assert.equal(state.sandbox,false);
 } finally {cleanup();}
});

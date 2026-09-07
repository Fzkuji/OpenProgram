import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync, existsSync } from 'node:fs';
import { registerHooks } from 'node:module';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';
import { parseHTML } from 'linkedom';
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { useStore } from 'zustand';
const rootURL = new URL('../', import.meta.url);
const Scope = React.createContext(null);
globalThis.searchScopeHook = selector => useStore(React.useContext(Scope), selector);
registerHooks({
 resolve(s,c,next) {
  const mocks = {
   '@/lib/session-store/session-scope': 'export const useSessionScope = s => globalThis.searchScopeHook(s)',
   '@/lib/i18n': 'export const useTranslation = () => globalThis.searchTranslation',
   '@/lib/prefs/settings-cache': 'export const cachedFetch = async () => globalThis.searchCatalog; export const invalidate = () => {}',
   '@/components/ui/search-input': 'export const SearchInput = () => null',
   './owner-auth-bootstrap.ts': 'export const waitForOwnerAuthBootstrap = async () => {}',
   '@/lib/runtime-bridge/state': 'export const getSocket = () => globalThis.searchSocket',
  };
  if (s.endsWith('.module.css')) return {url:'data:text/javascript,export default {}',shortCircuit:true};
  if(c.parentURL?.endsWith('/settings/search-providers/index.tsx') && ['./detail','./item'].includes(s)) {
   return {url:'data:text/javascript,'+encodeURIComponent(s==='./detail'?'export const SearchProviderDetail = props => {globalThis.searchDetail = props; return null}':'export const SearchProviderItem = () => null'),shortCircuit:true};
  }
  if (mocks[s]) return {url:'data:text/javascript,'+encodeURIComponent(mocks[s]),shortCircuit:true};
  const base=s.startsWith('@/')?new URL(s.slice(2),rootURL).href:s.startsWith('.')&&!/\.[a-z]+$/i.test(s)?new URL(s,c.parentURL).href:null;
  if(base) for(const suffix of ['.ts','.tsx','/index.ts']) if(existsSync(fileURLToPath(base+suffix)))return {url:base+suffix,shortCircuit:true};
  return next(s,c);
 },
 load(url,c,next) {
  if(url.endsWith('.tsx'))return {format:'module',shortCircuit:true,source:ts.transpileModule(readFileSync(fileURLToPath(url),'utf8'),{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.ESNext}}).outputText};
  return next(url,c);
 }
});
const {window}=parseHTML('<!doctype html><html><body></body></html>');
Object.assign(globalThis,{window,document:window.document,IS_REACT_ACT_ENVIRONMENT:true,WebSocket:{OPEN:1}});
globalThis.fetch=async()=>Response.json({profiles:{research:['web_search']}});
const {getSessionStore,dropSessionStore,DEFAULT_SCOPE_SETTINGS}=await import('../lib/session-store/session-scope-registry.ts');
const {useToolsToggles}=await import('../components/chat/composer/controls/use-tools-toggles.ts');
const {useToolProfiles}=await import('../components/chat/composer/controls/use-tool-profiles.ts');
const {useSandboxToggle}=await import('../components/chat/composer/controls/use-sandbox-toggle.ts');
async function fixture(fn) {
 const host=document.createElement('div');document.body.append(host);const root=createRoot(host);
 const stores=Object.fromEntries(['A','B'].map(id=>[id,getSessionStore(id,{draft:'',settings:{...DEFAULT_SCOPE_SETTINGS,sandbox:id==='A'},running:null})]));
 const hooks={};const listeners=new Map();const frames=[];
 globalThis.searchSocket={readyState:1,send:s=>frames.push(JSON.parse(s)),addEventListener:(n,f)=>{if(!listeners.has(n))listeners.set(n,new Set());listeners.get(n).add(f)},removeEventListener:(n,f)=>listeners.get(n)?.delete(f)};
 const reply=(frame,sandbox)=>{for(const f of [...listeners.get('message')||[]])f({data:JSON.stringify({type:'sandbox_changed',data:{session_id:frame.session_id,action:frame.action,request_id:frame.request_id,sandbox,sandbox_available:true}})});};
 function Probe({id,persisted}) {hooks[id]={...useToolsToggles(),...useToolProfiles(id),...useSandboxToggle(id,persisted)};return null;}
 const render=async (ids,persisted=true)=>act(async()=>root.render(React.createElement(React.Fragment,null,...ids.map((id,i)=>React.createElement(Scope.Provider,{key:i,value:stores[id]},React.createElement(Probe,{id,persisted}))))));
 try {await fn({hooks,stores,frames,reply,render});}finally{await act(async()=>root.unmount());host.remove();dropSessionStore('A');dropSessionStore('B');}
}
test('Tools and Search follow the current scope when both sessions have equal values',()=>fixture(async({hooks,stores,render,frames,reply})=>{
 await render(['A']);await act(async()=>reply(frames.at(-1),true));await render(['B']);await act(async()=>reply(frames.at(-1),false));
 await act(async()=>{hooks.B.toggleWebSearch();hooks.B.toggleTools();});
 assert.equal(stores.A.getState().settings.webSearch,false);assert.equal(stores.A.getState().settings.tools,true);
 assert.equal(stores.B.getState().settings.webSearch,true);assert.equal(stores.B.getState().settings.tools,false);
}));
test('profile selection follows its scope across switches and remounts',()=>fixture(async({hooks,render,frames,reply})=>{
 await render(['A']);await act(async()=>reply(frames.at(-1),true));await act(async()=>hooks.A.switchProfile('research'));
 await render(['B']);await act(async()=>reply(frames.at(-1),false));assert.equal(hooks.B.activeProfile,'__agent__');
 await render(['A']);await act(async()=>reply(frames.at(-1),true));assert.equal(hooks.A.activeProfile,'research');
 await render([]);await render(['A']);await act(async()=>reply(frames.at(-1),true));assert.equal(hooks.A.activeProfile,'research');
}));
test('concurrent Sandbox changes accept only their own replies',()=>fixture(async({hooks,render,frames,reply})=>{
 await render(['A','B']);await act(async()=>{reply(frames[0],true);reply(frames[1],false);});
 await act(async()=>{hooks.A.toggleSandbox();hooks.B.toggleSandbox();});const writes=frames.filter(f=>'sandbox_enabled'in f);
 await act(async()=>reply(writes[0],false));await act(async()=>reply(writes[1],true));
 assert.equal(hooks.A.sandbox,false);assert.equal(hooks.B.sandbox,true);
}));

test('late Sandbox read cannot overwrite a confirmed mutation',()=>fixture(async({hooks,render,frames,reply})=>{
 await render(['A']);const read=frames[0];await act(async()=>hooks.A.toggleSandbox());const write=frames.at(-1);
 await act(async()=>reply(write,false));await act(async()=>reply(read,true));assert.equal(hooks.A.sandbox,false);
}));
test('Sandbox disconnect retains the confirmed selection',()=>fixture(async({hooks,render,frames,reply})=>{
 await render(['A']);await act(async()=>reply(frames[0],true));globalThis.searchSocket.readyState=3;
 await act(async()=>hooks.A.toggleSandbox());assert.equal(hooks.A.sandbox,true);assert.match(hooks.A.sandboxReason,/Could not update/);
}));
test('provisional draft keeps its local Sandbox choice without an inherited read',()=>fixture(async({hooks,render,frames})=>{
 await render(['B'],false);assert.equal(frames.length,0);assert.equal(hooks.B.sandbox,false);
}));
test('failed default-provider save keeps the prior selection and shows an error',async()=>{
 globalThis.searchTranslation={t:x=>x,text:x=>x};
 globalThis.searchCatalog={providers:[{id:'tavily',name:'Tavily',available:true,priority:100},{id:'exa',name:'Exa',available:true,priority:90}],default:'tavily'};
 globalThis.fetch=async()=>Response.json({error:'test failure'},{status:500});
 const {SearchProvidersSection}=await import('../components/settings/search-providers/index.tsx');
 const host=document.createElement('div');document.body.append(host);const root=createRoot(host);
 try {
  await act(async()=>root.render(React.createElement(SearchProvidersSection)));
  await act(async()=>globalThis.searchDetail.onSetDefault('exa'));
  assert.equal(globalThis.searchDetail.defaultId,'tavily');assert.ok(host.querySelector('[role="alert"]'));
 } finally {await act(async()=>root.unmount());host.remove();}
});

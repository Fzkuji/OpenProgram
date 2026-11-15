import assert from 'node:assert/strict';
import test from 'node:test';
import {existsSync,readFileSync} from 'node:fs';
import {registerHooks,createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const root = new URL('../../', import.meta.url);
const require=createRequire(new URL('tests/quality-probe.mjs',root));
const ts=require('typescript');
const {parseHTML}=require('linkedom');
registerHooks({resolve(specifier,context,next){
 if(specifier==='next/navigation')return {url:'data:text/javascript,export const usePathname=()=>"/chat"; export const useRouter=()=>({push(){}});',shortCircuit:true};
 if(specifier==='@/lib/external-libs')return {url:'data:text/javascript,export const externalLibsReady=async()=>{};',shortCircuit:true};
 if(specifier.endsWith('.module.css'))return {url:'data:text/javascript,export default {};',shortCircuit:true};
 const base=specifier.startsWith('@/')?new URL(specifier.slice(2),root).href:specifier.startsWith('.')&&!/\.[a-z]+$/i.test(specifier)?new URL(specifier,context.parentURL).href:null;
 if(base)for(const suffix of ['.ts','.tsx','/index.ts','/index.tsx'])if(existsSync(fileURLToPath(base+suffix)))return {url:base+suffix,shortCircuit:true};
 return next(specifier,context.parentURL===import.meta.url?{...context,parentURL:new URL('tests/quality-probe.mjs',root).href}:context);
},load(url,context,next){if(url.endsWith('.tsx'))return {format:'module',shortCircuit:true,source:ts.transpileModule(readFileSync(fileURLToPath(url),'utf8'),{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText};return next(url,context);}});
const {window}=parseHTML('<html><body></body></html>');
Object.assign(globalThis,{window,document:window.document,Event:window.Event,CustomEvent:window.CustomEvent,IS_REACT_ACT_ENVIRONMENT:true});
const values=new Map();
globalThis.localStorage={getItem:k=>values.get(k)??null,setItem:(k,v)=>values.set(k,String(v)),removeItem:k=>values.delete(k)};
globalThis.sessionStorage=globalThis.localStorage;
window.location={pathname:'/chat',hash:'',search:'',protocol:'http:',host:'127.0.0.1:18100'};globalThis.location=window.location;
window.history={state:null,pushState(){},replaceState(){}};globalThis.history=window.history;
window.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
globalThis.requestAnimationFrame=fn=>setTimeout(fn,0);globalThis.cancelAnimationFrame=clearTimeout;
globalThis.fetch=async()=>Response.json({});window.alert=message=>{throw new Error(message)};
const {act,createElement}=await import('react');
const {createRoot}=await import('react-dom/client');
const {useTabLifecycle}=await import(new URL('components/center-tabs/tab-actions/use-tab-lifecycle.ts',root));
const {useCenterTabs}=await import(new URL('lib/tabs/center-tabs-store.ts',root));
const {useSessionStore}=await import(new URL('lib/session-store/index.ts',root));
const {getOrCreateDocumentController}=await import(new URL('lib/files/document/document-controller.ts',root));
const {setDraftStoreAdapterForTests}=await import(new URL('lib/files/drafts/file-drafts.ts',root));
const {MemoryDraftStore}=await import(new URL('lib/files/drafts/file-draft-store.ts',root));
setDraftStoreAdapterForTests(new MemoryDraftStore());
test('closing one preview keeps the shared document editable until the last owner closes', async()=>{
const noop=()=>{};let lifecycle;
function Receiver(){const activeId=useCenterTabs(s=>s.activeId);lifecycle=useTabLifecycle({activeId,cancelDrag:noop,setFocusedTabId:noop,freezeWidthsForMouseClose:noop,releaseFrozenWidths:noop});return createElement('div');}
useSessionStore.setState({currentSessionId:null,conversations:{}});
useCenterTabs.setState({tabs:[{id:'s:a',kind:'session',sessionId:'a',title:'A'},{id:'s:b',kind:'session',sessionId:'b',title:'B'}],groups:[],activeId:'s:a'});
assert.equal(useCenterTabs.getState().openFilePreview('a',{projectId:'p',path:'report.md'},true),true);
useCenterTabs.setState({activeId:'s:b'});
assert.equal(useCenterTabs.getState().openFilePreview('b',{projectId:'p',path:'report.md'},true),true);
const files=useCenterTabs.getState().tabs.filter(t=>t.kind==='file');assert.equal(files.length,2);
const store={get:async()=>null,delete:async()=>{},put:async()=>1};
let disk='original';
const first=getOrCreateDocumentController({projectId:'p',path:'report.md',draftStore:store,fetchImpl:async(_url,init)=>{
 if(init?.method==='PUT'){disk=await init.body.text();return Response.json({ok:true,status:'committed',revision:'b'.repeat(64)});}
 return new Response(disk,{headers:{'x-document-revision':'b'.repeat(64),'x-document-version':'v2'}});
}});
await first.hydrate({bytes:'original',revision:'a'.repeat(64)});
const second=getOrCreateDocumentController({projectId:'p',path:'report.md',draftStore:store});assert.equal(first,second);
const retained=second.subscribe(()=>{}); // Other visited pane remains mounted.
const host=document.createElement('div');document.body.append(host);const view=createRoot(host);
await act(async()=>view.render(createElement(Receiver)));
await act(async()=>lifecycle.onTabsClose({stopPropagation(){}},[files[0]]));
await act(async()=>lifecycle.finishClose(files[0]));
try {
 assert.ok(useCenterTabs.getState().tabs.some(tab=>tab.id===files[1].id));
 assert.notEqual(second.getState().status,'closed');
 second.update('continued edit in remaining tab');
 assert.equal(await second.currentDraft().text(),'continued edit in remaining tab');
 await second.flush();await second.refresh();
 assert.equal(await second.getState().snapshot.bytes.text(),'continued edit in remaining tab');
 second.update('last owner edit');
 await act(async()=>lifecycle.onTabsClose({stopPropagation(){}},[files[1]]));
 assert.equal(disk,'last owner edit');
 assert.equal(second.getState().status,'closed');
 await act(async()=>lifecycle.finishClose(files[1]));
} finally {await act(async()=>view.unmount());host.remove();retained();}
});

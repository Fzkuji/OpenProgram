/** Closing a pane of a split is instant: it leaves the layout in the
 *  click's frame, nothing animates or freezes the panes, and releasing its
 *  document runs after the tab has gone. Only unsaved edits hold a close. */
import assert from 'node:assert/strict';
import test from 'node:test';
import {existsSync,readFileSync} from 'node:fs';
import {registerHooks,createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const root = new URL('../../../', import.meta.url);
const require=createRequire(new URL('tests/quality-probe.mjs',root));
const ts=require('typescript');
const {parseHTML}=require('linkedom');
registerHooks({resolve(specifier,context,next){
 if(specifier==='next/navigation')return {url:'data:text/javascript,export const usePathname=()=>"/chat"; export const useRouter=()=>({push(){}});',shortCircuit:true};
 if(specifier==='@/lib/external-libs')return {url:'data:text/javascript,export const externalLibsReady=async()=>{};',shortCircuit:true};
 if(specifier.endsWith('.module.css'))return {url:'data:text/javascript,export default new Proxy({}, {get: (_t, k) => String(k)});',shortCircuit:true};
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
window.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});globalThis.matchMedia=window.matchMedia;
globalThis.requestAnimationFrame=fn=>setTimeout(fn,0);globalThis.cancelAnimationFrame=clearTimeout;
globalThis.fetch=async()=>Response.json({});window.alert=message=>{throw new Error(message)};
const {act,createElement}=await import('react');
const {createRoot}=await import('react-dom/client');
const {useTabLifecycle}=await import(new URL('components/center-tabs/tab-actions/use-tab-lifecycle.ts',root));
const {CanvasControls}=await import(new URL('components/center-tabs/canvas/canvas-controls.tsx',root));
const {useCenterTabs}=await import(new URL('lib/tabs/center-tabs-store.ts',root));
const {findCenterTabGroup}=await import(new URL('lib/tabs/center-tab-groups.ts',root));
const {leaves}=await import(new URL('lib/tabs/canvas/canvas-layout.ts',root));
const {canvasPanesMoving}=await import(new URL('lib/tabs/canvas/canvas-motion.ts',root));
const {useSessionStore}=await import(new URL('lib/session-store/index.ts',root));
const {getOrCreateDocumentController}=await import(new URL('lib/files/document/document-controller.ts',root));
const {setDraftStoreAdapterForTests}=await import(new URL('lib/files/drafts/file-drafts.ts',root));
const {MemoryDraftStore}=await import(new URL('lib/files/drafts/file-draft-store.ts',root));
setDraftStoreAdapterForTests(new MemoryDraftStore());

const noop=()=>{};
const draftStore={get:async()=>null,delete:async()=>{},put:async()=>1};

/** A chat with a preview of `path` beside it, and the lifecycle hook mounted. */
async function previewSplit(path){
  useSessionStore.setState({currentSessionId:null,conversations:{}});
  useCenterTabs.setState({tabs:[{id:'s:a',kind:'session',sessionId:'a',title:'A'}],groups:[],activeId:'s:a'});
  assert.equal(useCenterTabs.getState().openFilePreview('a',{projectId:'p',path},true),true);
  const preview=useCenterTabs.getState().tabs.find(t=>t.kind==='file');
  let lifecycle;
  function Receiver(){const activeId=useCenterTabs(s=>s.activeId);lifecycle=useTabLifecycle({activeId,cancelDrag:noop,setFocusedTabId:noop,freezeWidthsForMouseClose:noop,releaseFrozenWidths:noop});return createElement('div');}
  const host=document.createElement('div');document.body.append(host);const view=createRoot(host);
  await act(async()=>view.render(createElement(Receiver)));
  return {preview,lifecycle:()=>lifecycle,done:async()=>{await act(async()=>view.unmount());host.remove();}};
}
const open=(id)=>useCenterTabs.getState().tabs.some(tab=>tab.id===id);

test('a saved preview leaves the split at once and its document is released after', async()=>{
  const split=await previewSplit('saved.md');
  const controller=getOrCreateDocumentController({projectId:'p',path:'saved.md',draftStore,fetchImpl:async()=>new Response('saved',{headers:{'x-document-revision':'a'.repeat(64)}})});
  await controller.hydrate({bytes:'saved',revision:'a'.repeat(64)});
  const order=[];let finish;
  const close=controller.close.bind(controller);
  controller.close=()=>{order.push(open(split.preview.id)?'released while open':'released after');return new Promise(resolve=>{finish=()=>resolve(close());});};
  try{
    let closing;
    act(()=>{closing=split.lifecycle().onTabsClose({stopPropagation(){}},[split.preview]);});
    assert.equal(open(split.preview.id),false,'the tab is gone in the same call, before any save or release');
    assert.equal(findCenterTabGroup(useCenterTabs.getState().groups,'s:a'),undefined,'the chat is on its own again');
    await act(async()=>{await closing;await new Promise(resolve=>setTimeout(resolve,0));});
    assert.deepEqual(order,['released after']);
    await act(async()=>{finish();await new Promise(resolve=>setTimeout(resolve,0));});
    assert.equal(controller.getState().status,'closed');
  }finally{await split.done();}
});

test('unsaved edits are saved before the tab goes, and a failed save keeps it open', async()=>{
  const split=await previewSplit('draft.md');
  let disk='original',fail=true;
  const controller=getOrCreateDocumentController({projectId:'p',path:'draft.md',draftStore,fetchImpl:async(_url,init)=>{
    if(init?.method==='PUT'){if(fail)return new Response('down',{status:503});disk=await init.body.text();return Response.json({ok:true,status:'committed',revision:'b'.repeat(64)});}
    return new Response(disk,{headers:{'x-document-revision':'b'.repeat(64),'x-document-version':'v2'}});
  }});
  await controller.hydrate({bytes:'original',revision:'a'.repeat(64)});
  const retained=controller.subscribe(noop);
  try{
    controller.update('edited');
    await act(async()=>split.lifecycle().onTabsClose({stopPropagation(){}},[split.preview]));
    assert.equal(open(split.preview.id),true,'a save that failed keeps the tab open');
    fail=false;
    await act(async()=>split.lifecycle().onTabsClose({stopPropagation(){}},[split.preview]));
    assert.equal(disk,'edited','the edit was saved before the tab went');
    assert.equal(open(split.preview.id),false);
  }finally{retained();await split.done();}
});

test('red and yellow take a pane off the canvas in the click, without animating or freezing the panes', async()=>{
  for(const light of ['minimize','close']){
    useCenterTabs.setState({tabs:[{id:'s:a',kind:'session',sessionId:'a',title:'A'}],groups:[],activeId:'s:a'});
    useCenterTabs.getState().openPagePreview('a',{url:'https://example.com/',title:'Example'});
    const group=findCenterTabGroup(useCenterTabs.getState().groups,'s:a');
    assert.equal(leaves(group.canvas.root).length,2);
    const web=group.memberIds.find(id=>id!=='s:a');
    let closed=[];
    const onClose=event=>{closed=event.detail.tabIds;useCenterTabs.getState().closeTab(event.detail.tabIds[0]);};
    window.addEventListener('op-close-center-tabs',onClose);
    const canvasRoot=document.createElement('div');canvasRoot.setAttribute('data-canvas-root','');document.body.append(canvasRoot);
    const view=createRoot(canvasRoot);
    function Controls(){const groups=useCenterTabs(s=>s.groups);const g=findCenterTabGroup(groups,'s:a');return g?.canvas?createElement(CanvasControls,{layout:g.canvas,targetId:'s:a'}):null;}
    try{
      await act(async()=>view.render(createElement(Controls)));
      const button=canvasRoot.querySelector(`[data-canvas-content="${web}"] [data-pane-light="${light}"]`);
      act(()=>{button.dispatchEvent(new window.Event('click',{bubbles:true}));});
      const after=findCenterTabGroup(useCenterTabs.getState().groups,'s:a');
      assert.equal(after,undefined,`${light}: the chat is alone in the same frame`);
      assert.equal(canvasPanesMoving(),false,`${light}: no capture, so no web pane freezes to a snapshot`);
      assert.equal(document.querySelector('[data-canvas-ghost]'),null,`${light}: no ghost plays out`);
      if(light==='close')assert.deepEqual(closed,[web]);
      else assert.equal(open(web),true,'yellow keeps the tab in the strip');
    }finally{window.removeEventListener('op-close-center-tabs',onClose);await act(async()=>view.unmount());canvasRoot.remove();}
  }
});

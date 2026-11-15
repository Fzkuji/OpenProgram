import assert from 'node:assert/strict';
import test from "node:test";
import { join, dirname } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';
import { parseHTML } from 'linkedom';

import { scratchDir } from "../scratch-dir.mjs";

const web = dirname(fileURLToPath(new URL('../../package.json', import.meta.url)));
const dir = scratchDir(web, "tab-pointer-test");
await build({absWorkingDir:web, stdin:{contents:`export { startPaneDrag } from './lib/tabs/canvas/canvas-drag'; export { useTabPointerDrag } from './components/center-tabs/tab-actions/use-tab-pointer-drag'; export { useCenterTabs } from './lib/tabs/center-tabs-store'; export { useWindowSnapshots } from './lib/tabs/canvas/window-snapshots'; export { useSessionStore } from './lib/session-store';`,resolveDir:web},bundle:true,format:'esm',platform:'node',packages:'external',outfile:join(dir,'test.mjs'),tsconfig:join(web,'tsconfig.json'),plugins:[{name:'services',setup(b){
 b.onResolve({filter:/desktop-bridge$/},()=>({path:'bridge',namespace:'stub'}));
 b.onResolve({filter:/\/i18n$/},()=>({path:'i18n',namespace:'stub'}));
 b.onResolve({filter:/net\/fetch-client/},()=>({path:'fetch',namespace:'stub'}));
 b.onLoad({filter:/.*/,namespace:'stub'},({path})=>({contents:path==='bridge'?`export const desktopBridge=()=>globalThis.bridge; export const buildTransferPayload=()=>({});`:path==='i18n'?`export const useTranslation=()=>({text:(en)=>en}); export const translateText=(en)=>en;`:`export const jsonFetch=(...args)=>globalThis.attachFetch(...args);`}));
}}]});
const {window}=parseHTML('<html><body></body></html>');
Object.assign(globalThis,{window,document:window.document,HTMLElement:window.HTMLElement,Element:window.Element,CustomEvent:window.CustomEvent,IS_REACT_ACT_ENVIRONMENT:true,requestAnimationFrame:cb=>cb()});
Object.defineProperty(globalThis,'localStorage',{value:{getItem:()=>null,setItem(){},removeItem(){}},configurable:true});
globalThis.CSS={escape:s=>s.replaceAll(':','\\:')};
window.location={pathname:'/s/a'};
window.getComputedStyle=()=>({paddingLeft:'0',paddingRight:'0'});
globalThis.getComputedStyle=window.getComputedStyle;
const {createElement:h,act}=await import('react');
const {createRoot}=await import('react-dom/client');
const {startPaneDrag,useTabPointerDrag,useCenterTabs,useSessionStore,useWindowSnapshots}=await import(pathToFileURL(join(dir,'test.mjs')));

function assertResourcesCue() {
 const cue=document.querySelector('.canvas-drop-preview[data-drop-target="resources"]');
 assert.ok(cue,'Resources shows the same visible drop preview as canvas docking');
 assert.match(cue.textContent,/Release to add to Resources/);
 return cue;
}

for (const pageCount of [1, 0, 2]) {
 test(`compound tab with ${pageCount} webpage(s) ${pageCount===1?'attaches its webpage':'does not choose a Resources attachment'}`,async()=>{
  const calls=[];
  globalThis.bridge={windowId:'main',tabTransfer:{prepare:()=> 'group-token',cancel:async token=>{calls.push(['cancel',token]);return true;},windowAtCursor:async()=>null,detach:async token=>{calls.push(['detach',token]);return 'detached';}}};
  globalThis.attachFetch=async(path,options)=>{const body=JSON.parse(options.body);calls.push(['attach',path,body]);return {items:[{id:'assoc',resource_id:'page',source:'browser',kind:'web',conversation_session_id:'a',tab_id:body.tab_id,status:'open'}]};};
  const members=[{id:'s:a',kind:'session',sessionId:'a',title:'A'},...Array.from({length:pageCount},(_,i)=>({id:`w:${i}`,kind:'web',url:`https://page${i}.test`,title:`Page ${i}`}))];
  if(!pageCount)members.push({id:'s:b',kind:'session',sessionId:'b',title:'B'});
  const tabs=[...members,{id:'s:other',kind:'session',sessionId:'other',title:'Other'}];
  const ids=members.map(tab=>tab.id);
  const group={id:'g',memberIds:ids,visibleIds:ids,focusedId:ids[0]};
  const groups=[group];
  useCenterTabs.setState({tabs,groups,activeId:ids[0]});
  const host=document.createElement('div');document.body.append(host);
  const target=document.createElement('div');target.dataset.resourceDropSession='a';
  target.getBoundingClientRect=()=>({left:700,right:900,top:100,bottom:500,width:200,height:400});document.body.append(target);
  const root=createRoot(host);const strip={current:null};const flow={current:null};let hook;
  function Harness(){hook=useTabPointerDrag({stripRef:strip,tabsFlowRef:flow,releaseFrozenWidths(){},onTabClick(){},suppressedClickRef:{current:null},tabMenuRef:{current:null},applyDrop:()=>false,setDragAnnouncement(){}});return h('div',{ref:element=>{strip.current=element;flow.current=element}},h('div',{'data-tab-id':ids[0],onPointerDown:event=>hook.onTabPointerDown({kind:'group',tabIds:ids,sourceGroup:group},event)},'Compound'),h('div',{'data-tab-id':'s:other'},'Other'));}
  await act(async()=>root.render(h(Harness)));
  const element=host.querySelector('[data-tab-id="s:a"]');
  const rect={left:0,right:800,top:0,bottom:40,width:800,height:40};strip.current.getBoundingClientRect=()=>rect;strip.current.getClientRects=()=>[rect];
  for(const [i,el] of [...flow.current.children].entries()){el.getBoundingClientRect=()=>({...rect,left:i*200,right:(i+1)*200,width:200});el.setPointerCapture=()=>{};el.releasePointerCapture=()=>{};}
  async function fire(type,x=100,y=20){await act(async()=>{const event=new window.Event(type,{bubbles:true,cancelable:true});Object.assign(event,{button:0,buttons:type==='pointerup'?0:1,pointerId:19,clientX:x,clientY:y,screenX:x,screenY:y});element.dispatchEvent(event);});}
  try {
   await fire('pointerdown');await fire('pointermove',750,150);
   if(pageCount===1){
    assertResourcesCue();assert.equal(target.getAttribute('data-resource-drop-over'),'true');assert.equal(hook.detaching,false,'a supported compound tab must not detach over Resources');
    await fire('pointerup',750,150);
    assert.deepEqual(calls,[['cancel','group-token'],['attach','/api/session/a/resources/attach-web',{window_id:'main',tab_id:'w:0'}]],'cancel the group transfer before attaching the webpage, even when the first member is a conversation');
   }else{
    assert.equal(document.querySelector('[data-drop-target="resources"]'),null);assert.equal(target.hasAttribute('data-resource-drop-over'),false,'unsupported group must not advertise a Resources drop');
    await fire('pointercancel');assert.deepEqual(calls,[['cancel','group-token']],'do not select an arbitrary webpage or attach the conversation');
   }
   assert.deepEqual(useCenterTabs.getState().tabs,tabs);assert.deepEqual(useCenterTabs.getState().groups,groups);assert.equal(useCenterTabs.getState().activeId,'s:a','attachment preserves the existing canvas');
   assert.equal(document.querySelector('[data-drop-target="resources"]'),null);
  } finally {await fire('pointercancel');await act(async()=>root.unmount());host.remove();target.remove();}
 });
}

test('pointer press preserves current conversation; release activates and drag/cancel suppress clicks',async()=>{
 let hook; const consumed={current:null}; let activated=0; let detached=0; let posts=0;
 globalThis.bridge={windowId:'main',tabTransfer:{prepare:()=> 'token',cancel:async()=>true,windowAtCursor:async()=>null,detach:async()=>{detached++;return 'new';}}};
 globalThis.attachFetch=async()=>{posts++;return {items:[{id:'assoc',resource_id:'page',source:'browser',kind:'web',session_id:'a',conversation_session_id:'a',tab_id:'w:b',title:'B',target:'https://b.test',status:'open'}]};};
 const tabs=[{id:'s:a',kind:'session',sessionId:'a',title:'A'},{id:'w:b',kind:'web',url:'https://b.test',title:'B'}];
 useCenterTabs.setState({tabs,groups:[],activeId:'s:a'});
 const host=document.createElement('div');document.body.append(host);
 const target=document.createElement('div');
 const root=createRoot(host);const strip={current:null};const flow={current:null};
 function Harness(){hook=useTabPointerDrag({stripRef:strip,tabsFlowRef:flow,releaseFrozenWidths(){},onTabClick(tab){activated++;useCenterTabs.getState().setActive(tab.id);},suppressedClickRef:consumed,tabMenuRef:{current:null},applyDrop:()=>false,setDragAnnouncement(){}});return h('div',{ref:e=>{strip.current=e;flow.current=e}},tabs.map(tab=>h('div',{key:tab.id,'data-tab-id':tab.id,onPointerDown:e=>hook.onTabPointerDown({kind:'tab',tabIds:[tab.id]},e),onClick:()=>{if(consumed.current===tab.id){consumed.current=null;return;}activated++;useCenterTabs.getState().setActive(tab.id);}},tab.title)));}
 await act(async()=>root.render(h(Harness)));
 const el=host.querySelector('[data-tab-id="w:b"]');
 const rect={left:0,right:800,top:0,bottom:40,width:800,height:40};strip.current.getBoundingClientRect=()=>rect;strip.current.getClientRects=()=>[rect];
 for(const [i,e] of [...flow.current.children].entries()){e.getBoundingClientRect=()=>({...rect,left:i*150,right:(i+1)*150,width:150});e.setPointerCapture=()=>{};e.releasePointerCapture=()=>{};}
 async function fire(type,x=200,y=20){await act(async()=>{const e=new window.Event(type,{bubbles:true,cancelable:true});Object.assign(e,{button:0,buttons:type==='pointerup'?0:1,pointerId:1,clientX:x,clientY:y,screenX:x,screenY:y});el.dispatchEvent(e);});}
 try{
 await fire('pointerdown');assert.equal(useCenterTabs.getState().activeId,'s:a','press must not navigate');assert.equal(activated,0);assert.equal(el.getAttribute('data-pointer-pressed'),'true');
 await fire('pointerup');await fire('click');assert.equal(useCenterTabs.getState().activeId,'w:b');assert.equal(activated,1);
 useCenterTabs.setState({activeId:'s:a'});
 await fire('pointerdown');await fire('pointermove',210,150);
 const preview=document.querySelector('[data-tab-drag-preview]');
 assert.ok(preview, 'leaving strip must show a floating tab');
 assert.equal(preview.parentElement,document.body,'preview escapes clipping ancestors');
 assert.equal(preview.textContent,'B');
 assert.equal(preview.style.position,'fixed');
 assert.equal(preview.style.transform,'translate(10px, 130px)');
 assert.equal(el.style.visibility,'hidden','only one tab copy is visible');
 assert.equal(document.querySelectorAll('[data-tab-id="w:b"]').length,1);
 await fire('pointermove',220,20);
 assert.equal(document.querySelector('[data-tab-drag-preview]'),null,'return to strip removes overlay');
 assert.equal(el.style.visibility,'');
 await fire('pointermove',210,150);await fire('pointercancel');
 assert.equal(document.querySelector('[data-tab-drag-preview]'),null,'cancel removes overlay');
 assert.equal(el.style.visibility,'');
 // With a picture of the tab's content, leaving the strip opens it into a small window.
 const proto=window.HTMLElement.prototype;
 const stubbed=!proto.animate;
 if(stubbed){proto.animate=()=>({finished:Promise.resolve(),cancel(){}});proto.getAnimations=()=>[];}
 useWindowSnapshots.setState({shots:{'w:b':'data:image/jpeg;base64,AA'}});
 await fire('pointerdown');await fire('pointermove',210,150);
 const mini=document.querySelector('[data-tab-drag-preview]');
 assert.equal(mini.style.width,'240px','the tab opens into a 240px window');
 assert.equal(mini.querySelector('img')?.getAttribute('src'),'data:image/jpeg;base64,AA','showing the last picture of its content');
 assert.equal(mini.textContent,'B','its tab stays on top');
 await fire('pointermove',220,20);await act(async()=>{await new Promise(resolve=>setTimeout(resolve,0));});
 assert.equal(document.querySelector('[data-tab-drag-preview]'),null,'back in the strip it folds into the tab again');
 assert.equal(el.style.visibility,'');
 await fire('pointercancel');
 useWindowSnapshots.setState({shots:{}});
 if(stubbed){delete proto.animate;delete proto.getAnimations;}
 await fire('pointerdown');await fire('pointermove',210,150);assert.equal(useCenterTabs.getState().activeId,'s:a');assert.equal(hook.detaching,true);assert.equal(el.getAttribute('data-detach-intent'),'');assert.equal(detached,0);await fire('pointerup',210,150);await fire('click');assert.equal(detached,1);assert.equal(document.querySelector('[data-tab-drag-preview]'),null,'release removes overlay');assert.equal(useCenterTabs.getState().activeId,'s:a');
 await fire('pointerdown');await fire('pointercancel');await fire('click');assert.equal(useCenterTabs.getState().activeId,'s:a');assert.equal(posts,0);
 target.dataset.resourceDropSession='a';target.getBoundingClientRect=()=>({left:700,right:800,top:100,bottom:500,width:100,height:400});document.body.append(target);
 await fire('pointerdown');await fire('pointermove',750,150);assert.equal(target.getAttribute('data-resource-drop-over'),'true');assertResourcesCue();assert.equal(posts,0);assert.equal(hook.detaching,false);
 await fire('pointermove',600,150);assert.equal(document.querySelector('[data-drop-target="resources"]'),null,'leaving Resources removes the tab drop preview');
 await fire('pointermove',750,150);assertResourcesCue();await fire('pointerup',750,150);await fire('click');assert.equal(posts,1);assert.equal(detached,1);assert.equal(useCenterTabs.getState().activeId,'s:a');assert.equal(target.hasAttribute('data-resource-drop-over'),false);assert.equal(document.querySelector('[data-drop-target="resources"]'),null,'release removes the Resources preview');
 globalThis.attachFetch=async()=>{posts++;throw new Error('offline');};
 await fire('pointerdown');await fire('pointermove',750,150);await fire('pointerup',750,150);await fire('click');assert.equal(posts,2);assert.equal(detached,1);assert.equal(useCenterTabs.getState().activeId,'s:a');assert.match(hook.resourceDropError,/Could not attach/);
 await fire('pointerdown');await fire('pointermove',750,150);assertResourcesCue();await fire('pointercancel');await fire('click');assert.equal(posts,2);assert.equal(target.hasAttribute('data-resource-drop-over'),false);assert.equal(document.querySelector('[data-drop-target="resources"]'),null,'cancel removes the tab Resources preview');
 target.remove();
 await fire('pointerdown');await fire('pointerup');await fire('click');assert.equal(useCenterTabs.getState().activeId,'w:b','next genuine click still activates');
 }finally{await fire('pointercancel');await act(async()=>root.unmount());host.remove();target.remove();}
});


test('pane grip drags a full tab label rather than the three dots',()=>{
 globalThis.bridge=null;
 const grip=document.createElement('div');grip.innerHTML='<i></i><i></i><i></i><span data-pane-drag-label hidden style="display:none"><svg></svg><span>Google</span></span>';
 document.body.append(grip);
 const strip=document.createElement('div');strip.setAttribute('role','tablist');
 strip.getBoundingClientRect=()=>({left:50,top:0,right:800,bottom:40,width:750,height:40});
 document.body.append(strip);
 grip.setPointerCapture=()=>{};grip.hasPointerCapture=()=>false;
 const fire=(type,x,y)=>{const e=new window.Event(type);Object.assign(e,{pointerId:7,clientX:x,clientY:y});window.dispatchEvent(e);};
 try {
  startPaneDrag({button:0,preventDefault(){},target:grip,currentTarget:grip,pointerId:7,clientX:100,clientY:20},'w:b',grip.querySelector('[data-pane-drag-label]'));
  fire('pointermove',102,21);
  assert.equal(document.querySelector('[data-pane-drag-preview]'),null);
  fire('pointermove',350,240);
  const preview=document.querySelector('[data-pane-drag-preview]');
  assert.ok(preview);assert.equal(preview.parentElement,document.body);
  assert.equal(preview.textContent,'Google');assert.ok(preview.querySelector('svg'));
  assert.equal(preview.querySelectorAll('i').length,0);
  assert.equal(preview.hasAttribute('hidden'),false);assert.equal(preview.style.width,'220px');
  assert.equal(preview.style.left,'240px');assert.equal(preview.style.top,'224px');
  assert.ok(grip.hasAttribute('data-pane-dragging'),'the dragged pane hides its grip through an attribute, not inline style');
  fire('pointermove',350,20);
  const cue=document.querySelector('.canvas-drop-preview[data-drop-target="tab-strip"]');
  assert.ok(cue,'returning a pane to the strip shows a docking cue');
  assert.equal(cue.style.left,'54px');assert.equal(cue.style.width,'742px');
  fire('pointermove',20,20);
  assert.equal(document.querySelector('.canvas-drop-preview'),null,'outside strip horizontal bounds clears cue');
  fire('pointermove',350,20);
  assert.ok(document.querySelector('.canvas-drop-preview'));
  fire('pointercancel',350,240);
  assert.equal(document.querySelector('.canvas-drop-preview'),null,'cancel clears cue');
  assert.equal(document.querySelector('[data-pane-drag-preview]'),null);
  assert.equal(grip.hasAttribute('data-pane-dragging'),false);
 } finally {grip.remove();strip.remove();}
});


test('a pane drag whose release the page never saw ends on the next buttonless move',()=>{
 globalThis.bridge=null;
 const pane=document.createElement('div');pane.setAttribute('data-canvas-pane','p');
 const grip=document.createElement('div');grip.innerHTML='<span data-pane-drag-label hidden>Doc</span>';pane.append(grip);document.body.append(pane);
 grip.setPointerCapture=()=>{};grip.hasPointerCapture=()=>false;
 const fire=(type,x,y,buttons=1)=>{const e=new window.Event(type);Object.assign(e,{pointerId:9,clientX:x,clientY:y,buttons,pointerType:'mouse'});window.dispatchEvent(e);};
 try {
  startPaneDrag({button:0,preventDefault(){},target:grip,currentTarget:grip,pointerId:9,clientX:100,clientY:100},'w:x',grip.firstElementChild);
  fire('pointermove',300,300);
  assert.ok(pane.hasAttribute('data-pane-dragging'));
  assert.ok(document.documentElement.hasAttribute('data-canvas-dragging'));
  fire('pointermove',310,310,0);
  assert.equal(pane.hasAttribute('data-pane-dragging'),false,'the grip comes back');
  assert.equal(document.documentElement.hasAttribute('data-canvas-dragging'),false,'iframes and drag regions are released');
  assert.equal(document.querySelector('[data-pane-drag-preview]'),null,'no floating label is left behind');
  // A later drag clears any pane a broken drag left marked.
  const other=document.createElement('div');other.setAttribute('data-pane-dragging','');document.body.append(other);
  startPaneDrag({button:0,preventDefault(){},target:grip,currentTarget:grip,pointerId:9,clientX:100,clientY:100},'w:x',grip.firstElementChild);
  assert.equal(other.hasAttribute('data-pane-dragging'),false);
  fire('pointercancel',0,0);other.remove();
 } finally {pane.remove();}
});

test('pane web drag highlights and attaches to the Resources panel conversation',async()=>{
 let posted;
 const toasts=[];const onToast=event=>toasts.push(event.detail);window.addEventListener('op:toast',onToast);
 globalThis.innerWidth=1200;globalThis.innerHeight=800;
 globalThis.bridge={windowId:'main',tabTransfer:{prepare:()=> 'token',cancel:async()=>true}};
 globalThis.attachFetch=async(path,options)=>{posted={path,body:JSON.parse(options.body)};return {items:[{id:'assoc',resource_id:'page',source:'browser',kind:'web',conversation_session_id:'b',tab_id:'w:c',status:'open'}]};};
 const ids=['s:a','s:b','w:c'];
 useCenterTabs.setState({tabs:[{id:ids[0],kind:'session',sessionId:'a',title:'A'},{id:ids[1],kind:'session',sessionId:'b',title:'B'},{id:ids[2],kind:'web',url:'https://example.test',title:'Web'}],activeId:ids[2],groups:[{id:'g',memberIds:ids,visibleIds:ids,focusedId:ids[2]}]});
 useSessionStore.setState({rightDock:{view:'activity',open:false}});
 const target=document.createElement('div');target.dataset.resourceDropSession='b';
 target.getBoundingClientRect=()=>({left:700,right:900,top:100,bottom:500,width:200,height:400});document.body.append(target);
 const grip=document.createElement('div');grip.innerHTML='<span data-pane-drag-label>Web</span>';document.body.append(grip);
 grip.setPointerCapture=()=>{};grip.hasPointerCapture=()=>false;
 const start=()=>startPaneDrag({button:0,preventDefault(){},target:grip,currentTarget:grip,pointerId:7,clientX:300,clientY:100},'w:c',grip.firstElementChild);
 const fire=async(type,x=750,y=150)=>{const e=new window.Event(type);Object.assign(e,{pointerId:7,clientX:x,clientY:y});window.dispatchEvent(e);await new Promise(resolve=>setImmediate(resolve));};
 try {
  start();await fire('pointermove');
  assert.equal(target.getAttribute('data-resource-drop-over'),'true','pane drag uses the same Resources feedback as a tab drag');
  const cue=assertResourcesCue();assert.equal(cue.style.left,'700px');assert.equal(cue.style.width,'200px');
  await fire('pointermove',600,150);assert.equal(target.hasAttribute('data-resource-drop-over'),false);assert.equal(document.querySelector('[data-drop-target="resources"]'),null,'leaving Resources removes the pane drop preview');
  await fire('pointermove');assertResourcesCue();await fire('pointercancel');assert.equal(target.hasAttribute('data-resource-drop-over'),false);assert.equal(document.querySelector('[data-drop-target="resources"]'),null,'cancel removes the pane Resources preview');assert.equal(posted,undefined);assert.equal(toasts.length,0);
  start();await fire('pointermove');await fire('pointerup');
  assert.equal(posted.path,'/api/session/b/resources/attach-web','drop goes to the shown chat, not the first grouped chat');
  assert.deepEqual(posted.body,{window_id:'main',tab_id:'w:c'});
  assert.equal(target.hasAttribute('data-resource-drop-over'),false);assert.equal(document.querySelector('[data-pane-drag-preview]'),null);assert.equal(document.querySelector('[data-drop-target="resources"]'),null);
  assert.match(toasts.at(-1)?.message??'',/added to Resources/,'successful pane drop gives visible confirmation');
  assert.deepEqual(useSessionStore.getState().rightDock,{view:'resources',open:true},'successful drop reveals Resources even while the webpage is active');
 } finally {await fire('pointercancel');window.removeEventListener('op:toast',onToast);grip.remove();target.remove();}
});

for (const failure of ['attach rejected','transfer cancellation rejected']) {
 test(`pane Resources drop reports ${failure} and preserves the existing layout`,async()=>{
  let posts=0;
  const toasts=[];const onToast=event=>toasts.push(event.detail);window.addEventListener('op:toast',onToast);
  globalThis.innerWidth=1200;globalThis.innerHeight=800;
  globalThis.bridge={windowId:'main',tabTransfer:{prepare:()=> 'token',cancel:async()=>failure!=='transfer cancellation rejected'}};
  globalThis.attachFetch=async()=>{posts++;throw new Error('offline');};
  const tabs=[{id:'s:a',kind:'session',sessionId:'a',title:'A'},{id:'w:b',kind:'web',url:'https://b.test',title:'B'}];
  const groups=[{id:'g',memberIds:['s:a','w:b'],visibleIds:['s:a','w:b'],focusedId:'w:b'}];
  useCenterTabs.setState({tabs,groups,activeId:'w:b'});
  useSessionStore.setState({rightDock:{view:'activity',open:false}});
  const target=document.createElement('div');target.dataset.resourceDropSession='a';target.getBoundingClientRect=()=>({left:700,right:900,top:100,bottom:500,width:200,height:400});document.body.append(target);
  const grip=document.createElement('div');grip.innerHTML='<span data-pane-drag-label>B</span>';document.body.append(grip);grip.setPointerCapture=()=>{};grip.hasPointerCapture=()=>false;
  const fire=async(type)=>{const event=new window.Event(type);Object.assign(event,{pointerId:11,clientX:750,clientY:150});window.dispatchEvent(event);await new Promise(resolve=>setImmediate(resolve));};
  try {
   startPaneDrag({button:0,preventDefault(){},target:grip,currentTarget:grip,pointerId:11,clientX:300,clientY:100},'w:b',grip.firstElementChild);
   await fire('pointermove');await fire('pointerup');
   assert.match(toasts.at(-1)?.message??'',/Could not attach/,'failure is visible rather than an unhandled event promise');
   assert.equal(posts,failure==='attach rejected'?1:0,'attachment waits for a successful native transfer cancellation');
   assert.deepEqual(useCenterTabs.getState().tabs,tabs);assert.deepEqual(useCenterTabs.getState().groups,groups);assert.equal(useCenterTabs.getState().activeId,'w:b');
   assert.deepEqual(useSessionStore.getState().rightDock,{view:'activity',open:false});
   assert.equal(document.querySelector('[data-drop-target="resources"]'),null);assert.equal(document.querySelector('[data-pane-drag-preview]'),null);assert.equal(target.hasAttribute('data-resource-drop-over'),false);
  } finally {await fire('pointercancel');window.removeEventListener('op:toast',onToast);grip.remove();target.remove();}
 });
}

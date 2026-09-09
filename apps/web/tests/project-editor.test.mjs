import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { registerHooks } from 'node:module';
import ts from 'typescript';
import { parseHTML } from 'linkedom';
const webRoot = new URL('../', import.meta.url);
registerHooks({
  resolve(specifier, context, next) {
    const base = specifier.startsWith('@/') ? new URL(specifier.slice(2),webRoot).href : specifier.startsWith('.') && !/\.[a-z]+$/i.test(specifier) ? new URL(specifier,context.parentURL).href : null;
    if(base) for(const suffix of ['.ts','.tsx','/index.ts','/index.tsx']) if(existsSync(fileURLToPath(base+suffix))) return {url:base+suffix,shortCircuit:true};
    return next(specifier,context);
  },
  load(url,context,next) {
    let source;
    if(url.endsWith(".module.css")) source='export default new Proxy({}, {get:(_, key)=>String(key)});';
    if(url.endsWith('/lib/i18n/index.ts')) source='export const useTranslation=()=>({text:(en)=>en});';
    if(url.endsWith('/components/ui/dialog.tsx')) source='export const Dialog=({open,children})=>open?children:null; export const DialogContent=({children})=>children; export const DialogHeader=DialogContent; export const DialogTitle=DialogContent; export const DialogDescription=DialogContent; export const DialogFooter=DialogContent;';
    if(url.endsWith('/components/ui/button.tsx')) source='import {createElement as h} from "react"; export const Button=({variant,...props})=>h("button",props);';
    if(url.endsWith('/components/ui/folder-picker.tsx')) source='export const useFolderPicker=()=>({pickFolder:async()=>"/extra",folderPickerDialog:null,manualOpen:false});';
    if(url.endsWith('/lib/session-store/session-scope.tsx')) source='export const useOptionalScopedSessionId=()=>globalThis.projectScopedSession ?? null;';
    if(url.endsWith('/lib/runtime-bridge/ui.ts')) source='export const closeAllPopovers=()=>{};';
    if(url.endsWith('/components/ui/popover.tsx')) source='export const Popover=({children})=>children;export const PopoverTrigger=Popover;export const PopoverContent=()=>null;';
    if(url.endsWith('/components/ui/tooltip.tsx')) source='export const HoverTip=({children})=>children;';
    if(url.endsWith('/lib/session-store/index.ts')) source='import {useSyncExternalStore} from "react"; export const useSessionStore=selector=>useSyncExternalStore(fn=>{globalThis.projectStoreListeners.add(fn);return()=>globalThis.projectStoreListeners.delete(fn);},()=>selector(globalThis.projectStore));useSessionStore.getState=()=>globalThis.projectStore;';
    if(url.endsWith('/lib/net/ws-request.ts')) source='export const wsRequest=(...args)=>globalThis.projectRequest(...args);';
    if(url.includes('/@radix-ui/react-dropdown-menu/')) source=`import {createElement as h,createContext,useContext,useState,cloneElement} from "react";
      const C=createContext(null);
      export const Root=({children,open,onOpenChange})=>{const [own,setOwn]=useState(false);return h(C.Provider,{value:{open:open??own,set:onOpenChange??setOwn}},children);};
      export const Trigger=({children})=>{const c=useContext(C);return cloneElement(children,{onPointerDown:e=>{children.props.onPointerDown?.(e);c.set(!c.open);}});};
      export const Content=({children})=>useContext(C).open?h("div",{role:"menu"},children):null;
      export const Item=({children,onSelect})=>{const c=useContext(C);return h("button",{onClick:()=>{onSelect?.();c.set(false);}},children);};
      export const Portal=({children})=>children;export const Sub=Portal;export const SubContent=Portal;
      export const SubTrigger=({children})=>h("span",null,children);export const Separator=()=>h("hr");`;
    if(source) return {format:'module',source,shortCircuit:true};
    if(url.endsWith('.tsx')) return {format:'module',shortCircuit:true,source:ts.transpileModule(readFileSync(fileURLToPath(url),'utf8'),{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText};
    return next(url,context);
  },
});
const {window}=parseHTML('<html><body></body></html>');
globalThis.window=window;globalThis.document=window.document;document.oninput=null;
globalThis.Event=window.Event;globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const {createElement:h,act}=await import('react');
const {createRoot}=await import('react-dom/client');
const {ProjectEditor}=await import('../components/sidebar/project-editor.tsx');

test('project editor saves selected icon and folders, retains input after a failed save, and cancels without writing',async()=>{
  const host=document.createElement('div');document.body.append(host);const root=createRoot(host);
  const calls=[];let saved;let closes=0;let reject=true;
  globalThis.projectRequest=async(...args)=>{calls.push(args);return reject?{ok:false,error:'disk unavailable'}:{ok:true,project:{id:'p',path:'/main',...args[1].patch}};};
  await act(async()=>root.render(h(ProjectEditor,{project:{id:'p',name:'Original',path:'/main'},onSaved:p=>saved=p,onClose:()=>closes++})));
  for (const input of host.querySelectorAll("input")) input.type="text"; // Browser default absent in linkedom.
  async function click(label){const el=[...host.querySelectorAll('button')].find(b=>b.textContent===label);assert.ok(el,label);await act(async()=>el.dispatchEvent(new Event('click',{bubbles:true})));}
  async function type(el,value){Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el),'value').set.call(el,value);await act(async()=>el.dispatchEvent(new Event('input',{bubbles:true})));}
  await type(host.querySelector('input'),'Renamed');
  await type(host.querySelector('textarea'),'Research notes');
  await click('🔬');await click('Add folder');
  await act(async()=>host.querySelector('form').dispatchEvent(new Event('submit',{bubbles:true,cancelable:true})));
  assert.equal(calls[0][0],'update_project');assert.equal(calls[0][2],'project_updated');
  assert.deepEqual(calls[0][1],{project_id:'p',patch:{name:'Renamed',icon:'🔬',description:'Research notes',source_folders:['/extra']}});
  assert.match(host.textContent,/disk unavailable/);assert.equal(closes,0);assert.equal(host.querySelector('input').value,'Renamed');
  reject=false;
  await act(async()=>host.querySelector('form').dispatchEvent(new Event('submit',{bubbles:true,cancelable:true})));
  assert.equal(closes,1);assert.equal(saved.icon,'🔬');assert.deepEqual(saved.source_folders,['/extra']);
  await click('Cancel');assert.equal(calls.length,2);assert.equal(closes,2);
  await act(async()=>root.unmount());host.remove();
});


test('project menus open, pin, edit, create and rename sections without altering project membership',async()=>{
  const values=new Map();window.localStorage={getItem:k=>values.get(k)??null,setItem:(k,v)=>values.set(k,v)};
  const {ProjectMenu,ProjectSectionHeading}=await import('../components/sidebar/project-menu.tsx');
  const {getRecentsView}=await import('../lib/prefs/recents-view.ts');
  const host=document.createElement('div');document.body.append(host);const root=createRoot(host);let opened=0,newChats=0;
  const project={id:'p',name:'Project',path:'/main'};
  await act(async()=>root.render(h(ProjectMenu,{project,onOpen:()=>opened++,onNewSession:()=>newChats++,onSaved:()=>{},children:trigger=>h('div',{'data-header':true},'Project',trigger)})));
  async function context(){await act(async()=>host.querySelector('[data-header]').dispatchEvent(new Event('contextmenu',{bubbles:true,cancelable:true})));}
  async function click(label){const el=[...host.querySelectorAll('button')].find(b=>b.textContent===label);assert.ok(el,label+host.textContent);await act(async()=>el.dispatchEvent(new Event('click',{bubbles:true})));}
  await context();assert.equal(host.querySelector('[aria-haspopup="menu"]').getAttribute("data-state"),"open");assert.ok(host.querySelector('[role="menu"]'));await click('Open project');assert.equal(opened,1);assert.equal(host.querySelector('[aria-haspopup="menu"]').getAttribute('data-state'),'closed');
  await context();await click('New chat');assert.equal(newChats,1);
  await context();await click('Pin');assert.deepEqual(getRecentsView().pinnedProjects,['p']);
  await context();await click('Unpin');assert.deepEqual(getRecentsView().pinnedProjects,[]);
  await context();await click('Edit project');assert.ok(host.querySelector('form'));await click('Cancel');
  await context();await click('New section…');
  const input=host.querySelector('input');input.type='text';Object.getOwnPropertyDescriptor(Object.getPrototypeOf(input),'value').set.call(input,'Research');
  await act(async()=>input.dispatchEvent(new Event('input',{bubbles:true})));
  await act(async()=>host.querySelector('form').dispatchEvent(new Event('submit',{bubbles:true,cancelable:true})));
  assert.deepEqual(getRecentsView().projectSectionNames,['Research']);assert.equal(getRecentsView().projectSections.p,'Research');
  let folds=0;
  await act(async()=>root.render(h(ProjectSectionHeading,{section:'Research',collapsed:false,onToggle:()=>folds++})));
  const heading=host.querySelector('[role=button]');assert.equal(heading.getAttribute('aria-expanded'),'true');
  await act(async()=>heading.dispatchEvent(new Event('click',{bubbles:true})));assert.equal(folds,1);
  await act(async()=>host.querySelector('button').dispatchEvent(new Event('pointerdown',{bubbles:true})));
  await click('Rename section');assert.equal(folds,1,'section options must not toggle its list');
  const rename=host.querySelector('input');rename.type='text';Object.getOwnPropertyDescriptor(Object.getPrototypeOf(rename),'value').set.call(rename,'Experiments');
  await act(async()=>rename.dispatchEvent(new Event('input',{bubbles:true})));
  await act(async()=>host.querySelector('form').dispatchEvent(new Event('submit',{bubbles:true,cancelable:true})));
  assert.deepEqual(getRecentsView().projectSectionNames,['Experiments']);assert.equal(getRecentsView().projectSections.p,'Experiments');
  await act(async()=>root.render(h(ProjectSectionHeading,{section:'Experiments',collapsed:false,onToggle:()=>{}})));
  await act(async()=>host.querySelector('button').dispatchEvent(new Event('pointerdown',{bubbles:true})));
  await click('Remove section');assert.deepEqual(getRecentsView().projectSectionNames,[]);assert.equal(getRecentsView().projectSections.p,undefined);
  await act(async()=>root.unmount());host.remove();
});


test('draft source chips follow the selected project until the user sets an override',async()=>{
  const listeners=new Set();globalThis.projectStoreListeners=listeners;
  const state={currentSessionId:null,activeChatKey:'draft',conversations:{},pendingProjectsByChat:{draft:'a'},additionalWorkingDirsBySession:{},setAdditionalWorkingDirs:(key,dirs)=>{state.additionalWorkingDirsBySession={...state.additionalWorkingDirsBySession,[key]:dirs};for(const fn of listeners)fn();}};
  globalThis.projectStore=state;
  globalThis.projectRequest=async()=>({projects:[{id:'a',name:'A',path:'/a',source_folders:['/a-extra']},{id:'b',name:'B',path:'/b',source_folders:['/b-extra']},{id:'none',name:'None',path:'/none',source_folders:[]}],current_project_id:null,session_id:null});
  const {WorkingDirChips}=await import('../components/chat/top-bar/working-dir-chips.tsx');
  const host=document.createElement('div');document.body.append(host);const root=createRoot(host);
  await act(async()=>root.render(h(WorkingDirChips)));
  const dirs=()=>[...host.querySelectorAll('.workdir-badge[title]')].map(el=>el.getAttribute('title'));
  const select=async id=>act(async()=>{state.pendingProjectsByChat={draft:id};for(const fn of listeners)fn();});
  assert.deepEqual(dirs(),['/a-extra']);assert.equal(state.additionalWorkingDirsBySession.draft,undefined);
  await select('b');assert.deepEqual(dirs(),['/b-extra']);
  await select('none');assert.deepEqual(dirs(),[]);
  await select('a');
  await act(async()=>host.querySelector('[aria-label="Remove folder"]').dispatchEvent(new Event('click',{bubbles:true})));
  assert.deepEqual(state.additionalWorkingDirsBySession.draft,[]);
  await select('b');assert.deepEqual(dirs(),[]);
  await act(async()=>state.setAdditionalWorkingDirs('draft',['/custom']));
  await select('a');assert.deepEqual(dirs(),['/custom']);
  await act(async()=>root.unmount());host.remove();assert.equal(listeners.size,0);
});


test('acknowledged local-prefixed chats are real sessions for project directory writes',async()=>{
  globalThis.projectStoreListeners=new Set();
  globalThis.projectStore={currentSessionId:null,activeChatKey:'local_chat',conversations:{}};
  globalThis.projectScopedSession='local_chat';
  const {useBoundChat}=await import('../components/chat/top-bar/bound-chat.tsx');
  let bound;
  function Probe(){bound=useBoundChat();return null;}
  const host=document.createElement('div');document.body.append(host);const root=createRoot(host);
  await act(async()=>root.render(h(Probe)));
  assert.deepEqual(bound,{sessionId:null,chatKey:'local_chat'});
  await act(async()=>{globalThis.projectStore.conversations={local_chat:{id:'local_chat'}};for(const fn of globalThis.projectStoreListeners)fn();});
  assert.deepEqual(bound,{sessionId:'local_chat',chatKey:'local_chat'});
  const writes=[];
  globalThis.projectStore.additionalWorkingDirsBySession={local_chat:['/custom']};
  globalThis.projectStore.pendingProjectsByChat={};
  globalThis.projectStore.setAdditionalWorkingDirs=(key,dirs)=>{globalThis.projectStore.additionalWorkingDirsBySession={...globalThis.projectStore.additionalWorkingDirsBySession,[key]:dirs};for(const fn of globalThis.projectStoreListeners)fn();};
  globalThis.projectRequest=async(action,payload)=>{writes.push([action,payload]);return action==='list_projects'?{projects:[],current_project_id:null,session_id:'local_chat'}:{session_id:'local_chat',dirs:[]};};
  const {WorkingDirChips}=await import('../components/chat/top-bar/working-dir-chips.tsx');
  await act(async()=>root.render(h(WorkingDirChips)));
  await act(async()=>host.querySelector('[aria-label="Remove folder"]').dispatchEvent(new Event('click',{bubbles:true})));
  assert.ok(writes.some(([action,payload])=>action==='set_working_dirs'&&payload.session_id==='local_chat'&&payload.dirs.length===0));
  await act(async()=>root.unmount());host.remove();globalThis.projectScopedSession=null;
});

test('project operations confirm requests, retain errors, and close only after success',async()=>{
  const {ProjectOperationDialog}=await import('../components/sidebar/project-operation-dialog.tsx');
  const host=document.createElement('div');document.body.append(host);const root=createRoot(host);
  let closed=0;const saved=[];const calls=[];let fail=true;
  globalThis.projectRequest=async(...args)=>{calls.push(args);return fail?{ok:false,error:'disk failure'}:{ok:true,project_id:'p',project:{id:'p',name:'P',path:'/main',hidden:true}};};
  const props={project:{id:'p',name:'P',path:'/main'},onClose:()=>closed++,onSaved:p=>saved.push(p)};
  await act(async()=>root.render(h(ProjectOperationDialog,{...props,operation:'remove_project'})));
  assert.equal(calls.length,0);
  await act(async()=>host.querySelector('form').dispatchEvent(new Event('submit',{bubbles:true,cancelable:true})));
  assert.equal(calls[0][0],'remove_project');assert.equal(closed,0);assert.equal(host.querySelector('[role="alert"]').textContent,'disk failure');
  fail=false;
  await act(async()=>host.querySelector('form').dispatchEvent(new Event('submit',{bubbles:true,cancelable:true})));
  assert.equal(closed,1);assert.equal(saved[0].hidden,true);
  await act(async()=>root.render(h(ProjectOperationDialog,{...props,key:'worktree',operation:'create_project_worktree'})));
  const inputs=host.querySelectorAll('input');
  for(const [i,value] of ['/new worktree','codex/new-worktree'].entries()){
    inputs[i].type='text';Object.getOwnPropertyDescriptor(Object.getPrototypeOf(inputs[i]),'value').set.call(inputs[i],value);
    await act(async()=>inputs[i].dispatchEvent(new Event('input',{bubbles:true})));
  }
  await act(async()=>host.querySelector('form').dispatchEvent(new Event('submit',{bubbles:true,cancelable:true})));
  assert.equal(calls.at(-1)[0],'create_project_worktree');
  assert.deepEqual(calls.at(-1)[1],{project_id:'p',path:'/new worktree',branch:'codex/new-worktree'});
  await act(async()=>root.render(h(ProjectOperationDialog,{...props,key:'archive',operation:'archive_project_chats'})));
  await act(async()=>host.querySelector('form').dispatchEvent(new Event('submit',{bubbles:true,cancelable:true})));
  assert.equal(calls.at(-1)[0],'archive_project_chats');
  await act(async()=>root.unmount());host.remove();
});

test('project menu reaches reveal, worktree, archive, and remove dialogs',async()=>{
  const {ProjectMenu}=await import('../components/sidebar/project-menu.tsx');
  const host=document.createElement('div');document.body.append(host);const root=createRoot(host);const calls=[];
  globalThis.projectRequest=async(...args)=>{calls.push(args);return {ok:true};};
  await act(async()=>root.render(h(ProjectMenu,{project:{id:'p',name:'P',path:'/main'},onOpen:()=>{},onNewSession:()=>{},onSaved:()=>{},children:trigger=>h('div',{'data-header':true},trigger)})));
  async function context(){await act(async()=>host.querySelector('[data-header]').dispatchEvent(new Event('contextmenu',{bubbles:true,cancelable:true})));}
  async function click(label){const button=[...host.querySelectorAll('button')].find(b=>b.textContent===label);assert.ok(button,label);await act(async()=>button.dispatchEvent(new Event('click',{bubbles:true})));}
  await context();await click('Reveal in file manager');assert.equal(calls[0][0],'project_file_reveal');assert.deepEqual(calls[0][1],{project_id:'p',path:''});
  for(const label of ['Create permanent worktree','Archive chats','Remove project']){
    await context();await click(label);assert.ok(host.querySelector('form'),label);await click('Cancel');assert.equal(calls.length,1,'cancel must not mutate');
  }
  await act(async()=>root.unmount());host.remove();
});

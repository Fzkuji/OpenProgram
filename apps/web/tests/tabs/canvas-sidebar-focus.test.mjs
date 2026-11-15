import assert from 'node:assert/strict';
import test from "node:test";
import { join, dirname } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';
import { parseHTML } from 'linkedom';

import { scratchDir } from "../scratch-dir.mjs";

const web = dirname(fileURLToPath(new URL('../../package.json', import.meta.url)));
const dir = scratchDir(web, "sidebar-focus");
const stubs = {
  'next/navigation': 'export const usePathname=()=>"/s/a";',
  '@/lib/i18n': 'export const useTranslation=()=>({t:x=>x,text:x=>x});',
  '@/lib/net/ws/ws-request': 'export const wsRequest=(...args)=>globalThis.sidebarRequest(...args);',
  '../session-resources/session-resources-panel': 'import React from "react"; export const SessionResourcesPanel=({sessionId})=>React.createElement("output",{"data-resources":sessionId},sessionId);',
  '../../net/fetch-client.ts': 'export const jsonFetch=(...args)=>globalThis.sidebarResourceFetch(...args);',
  './context-commit-timeline': 'export const ContextCommitTimeline=()=>null;',
  '../animated-icons': 'import {forwardRef} from "react"; const Icon=forwardRef(()=>null); export const ActivityIcon=Icon,BoxIcon=Icon,FolderOpenIcon=Icon,PanelLeftCloseIcon=Icon,PanelLeftOpenIcon=Icon;',
  '../files/tree/file-tree': 'import React from "react"; export const FileTree=({projectId})=>React.createElement("output",{"data-project":projectId},projectId);',
  './running/running-panel': 'import React from "react"; export const RunningPanel=({sessionId})=>React.createElement("output",{"data-activity":sessionId},sessionId);',
  '../layout/use-resizable-rail': 'export const useResizableRail=({open})=>({style:{},shellProps:{},resizeHandleProps:{},closedSettled:!open});',
  './detail-panel': 'export const DetailPanel=()=>null,SessionViewSwitch=()=>null,VIEW_CONTEXT="context",VIEW_DETAIL="detail";',
};
await build({ absWorkingDir:web, stdin:{contents:`export {RightSidebar} from './components/right-sidebar/right-sidebar'; export {useCenterTabs} from './lib/tabs/center-tabs-store'; export {useSessionStore} from './lib/session-store';`,resolveDir:web},bundle:true,format:'esm',platform:'node',packages:'external',jsx:'automatic',outfile:join(dir,'test.mjs'),tsconfig:join(web,'tsconfig.json'),loader:{'.css':'empty'},plugins:[{name:'fixtures',setup(b){ b.onResolve({filter:/.*/},a=>a.path in stubs ? {path:a.path,namespace:'fixture'}:null);b.onLoad({filter:/.*/,namespace:'fixture'},a=>({contents:stubs[a.path],resolveDir:web})); }}] });
const {window}=parseHTML('<html><body></body></html>');
Object.assign(globalThis,{window,document:window.document,HTMLElement:window.HTMLElement,IS_REACT_ACT_ENVIRONMENT:true});
globalThis.localStorage={getItem:()=>null,setItem(){},removeItem(){}};
window.location={pathname:'/s/a'};
window.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
const {act,createElement}=await import('react');
const {createRoot}=await import('react-dom/client');
globalThis.sidebarResourceFetch=async()=>({items:[]});
const {RightSidebar,useCenterTabs,useSessionStore}=await import(pathToFileURL(join(dir,'test.mjs')));

test('canvas focus switches activity and project without waiting for the legacy route',async()=>{
  const requests=[];
  globalThis.sidebarRequest=(action,args,_type,_match,_timeout,options)=>new Promise(resolve=>requests.push({action,args,options,resolve}));
  const a={id:'s:a',kind:'session',sessionId:'a',title:'A'};
  const b={id:'s:b',kind:'session',sessionId:'b',title:'B'};
  useCenterTabs.setState({tabs:[a,b],activeId:a.id,groups:[{id:'g',memberIds:[a.id,b.id],visibleIds:[a.id,b.id],focusedId:a.id,canvas:{focusedPaneId:'pa',root:{kind:'split',id:'row',dir:'row',sizes:[0.5,0.5],children:[{kind:'pane',id:'pa',content:a.id},{kind:'pane',id:'pb',content:b.id}]}}}]});
  useSessionStore.setState({currentSessionId:'a',activeChatKey:'a'});
  const host=document.createElement('div');document.body.append(host);const root=createRoot(host);
  const reply=(request,id)=>request.resolve({session_id:request.args.session_id,current_project_id:id,projects:[{id,path:`/${id}`,name:id}]});
  try {
    await act(async()=>root.render(createElement(RightSidebar)));
    assert.equal(host.querySelector('[data-activity]').getAttribute('data-activity'),'a');
    await act(async()=>reply(requests.at(-1),'project-a'));
    assert.equal(host.querySelector('[data-project]').getAttribute('data-project'),'project-a');
    await act(async()=>useCenterTabs.getState().setActive(b.id));
    assert.equal(useSessionStore.getState().currentSessionId,'a','legacy session deliberately stays unchanged');
    assert.equal(host.querySelector('[data-activity]').getAttribute('data-activity'),'b');
    assert.equal(host.querySelector('[data-project]'),null,'do not show the previous conversation project');
    assert.equal(requests.at(-1).args.session_id,'b');
    const pendingB=requests.at(-1);
    await act(async()=>useCenterTabs.getState().setActive(a.id));
    await act(async()=>{reply(pendingB,'project-b');reply(requests.at(-1),'project-a');});
    assert.equal(host.querySelector('[data-activity]').getAttribute('data-activity'),'a');
    assert.equal(host.querySelector('[data-project]').getAttribute('data-project'),'project-a','late B response must not replace A');
  } finally {await act(async()=>root.unmount());host.remove();}
});


test('web pane keeps the last visible chat for Resources and its drop target',async()=>{
  const snapshots=[];
  globalThis.sidebarResourceFetch=async path=>{snapshots.push(path);return {items:[]};};
  globalThis.sidebarRequest=()=>new Promise(()=>{});
  const a={id:'s:a',kind:'session',sessionId:'a',title:'A'};
  const b={id:'s:b',kind:'session',sessionId:'b',title:'B'};
  const web={id:'w:c',kind:'web',url:'https://example.test',title:'Web'};
  const ids=[a.id,b.id,web.id];
  const group={id:'g',memberIds:ids,visibleIds:ids,focusedId:a.id,canvas:{focusedPaneId:'pa',root:{kind:'split',id:'row',dir:'row',sizes:[1/3,1/3,1/3],children:ids.map((id,i)=>({kind:'pane',id:'p'+i,content:id}))}}};
  useCenterTabs.setState({tabs:[a,b,web],activeId:a.id,groups:[group]});
  useSessionStore.setState({currentSessionId:'a',activeChatKey:'a',rightDock:{open:true,view:'resources'}});
  const host=document.createElement('div');document.body.append(host);const root=createRoot(host);
  const owner=()=>host.querySelector('[data-resources]')?.getAttribute('data-resources') ?? null;
  try {
    await act(async()=>root.render(createElement(RightSidebar)));
    await act(async()=>useCenterTabs.getState().setActive(web.id));
    assert.equal(owner(),'a','web pane must retain the visible conversation');
    assert.equal(snapshots.at(-1),'/api/session/a/resources','resource polling follows the shown chat');
    assert.equal(host.querySelector('#sessionResourcesPanel').dataset.resourceDropSession,'a');
    await act(async()=>useCenterTabs.getState().setActive(b.id));
    assert.equal(owner(),'b','clicking another chat switches the panel');
    await act(async()=>useCenterTabs.getState().setActive(web.id));
    assert.equal(owner(),'b','web focus must not select the first chat or stale route');
    assert.equal(snapshots.at(-1),'/api/session/b/resources','legacy currentSessionId must not drive resource polling');
    assert.equal(host.querySelector('#sessionResourcesPanel').dataset.resourceDropSession,'b');
    await act(async()=>useCenterTabs.setState({groups:[]}));
    assert.equal(owner(),null,'standalone unowned webpage must not inherit an unrelated session');
    assert.equal(host.querySelector('#sessionResourcesPanel').hasAttribute('data-resource-drop-session'),false);
    await act(async()=>useCenterTabs.setState({groups:[{...group,visibleIds:[a.id,web.id],canvas:{focusedPaneId:'p0',root:{kind:'split',id:'row',dir:'row',sizes:[0.5,0.5],children:[{kind:'pane',id:'p0',content:a.id},{kind:'pane',id:'p2',content:web.id}]}}}]}));
    assert.equal(owner(),'a','hidden last chat cannot own the visible panel');
  } finally {await act(async()=>root.unmount());host.remove();}
});

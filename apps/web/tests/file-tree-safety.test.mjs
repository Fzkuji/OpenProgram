import assert from 'node:assert/strict';
import test from 'node:test';
import { build } from 'esbuild';
import { createRequire } from 'node:module';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { parseHTML } from 'linkedom';
const require = createRequire(import.meta.url);
const mocks = {
  '@/lib/i18n': 'export const useTranslation=()=>({text:en=>en});',
  '@/lib/net/ws-request': `export const wsRequest=(action,payload)=>globalThis.host.query(action,payload);
    export const idempotencyKeyFor=()=>"key"; export class MutationRegistryCapacityError extends Error {}
    export const reconcileWsMutation=()=>{}; export const wsMutationRequest=(_key,run)=>run();`,
  '@/lib/session-store': 'export const useSessionStore={getState:()=>({currentSessionId:null})};',
  '@/lib/navigate': 'export const navigate=()=>{};',
  '@/lib/state/center-tabs-store': `const state={tabs:[],activeId:null,openFileTab:()=>{},closeTab:()=>{}};
    export const useCenterTabs=Object.assign(fn=>fn(state),{getState:()=>state});`,
  '@/lib/state/file-drafts': 'export const hasDocumentDraftsForPath=async()=>false;',
  '@/lib/state/files-shared': `export const hasDocumentDraftsForPath=async()=>false;
    export const loadFileDraftsForPath=async()=>globalThis.host.drafts;
    export const clearFileDraftsForPath=async()=>{globalThis.host.drafts=[];return {ok:true}};
    export const fileResponseMatchesOwner=()=>true; export const invalidateFileRead=()=>{};
    export const noteFileMtime=()=>{}; export const runServerRenameWithDrafts=async(_p,_a,_b,run)=>({ok:(await run()).status==='ready'});`,
  '@/components/sidebar/use-sidebar-menu': `export const useSidebarMenu=()=>({open:false,close:()=>{},show:(_event,items)=>{
    const item=items.find(x=>x.id===globalThis.host.menu); globalThis.host.menuDisabled=item?.disabled;
    if(item && !item.disabled)item.onSelect?.();
  }});`,
  '@/components/sidebar/sessions-list/confirm-dialog': `import React from 'react';
    export const ConfirmDialog=({onConfirm})=><button data-confirm onClick={onConfirm}>Confirm</button>;`,
  './pierre-file-tree': `import React,{forwardRef,useImperativeHandle} from 'react';
    const Tree=forwardRef(({entries=[],matches=[],onContextMenu},ref)=>{
      useImperativeHandle(ref,()=>({getScrollState:()=>null,scrollToTop:()=>{},reveal:()=>true}));
      return <div>{(entries.length?entries:matches).map(e=><button key={e.path} data-entry={e.path}
        onContextMenu={event=>onContextMenu(event,e.path,e.type)}>{e.path}</button>)}</div>;
    }); export const PierreFileTree=Tree; export const PierreSearchTree=Tree;`,
  './explorer-header': `import React from 'react'; export const copyText=async()=>true;
    export const ExplorerHeader=({actions,onQueryChange})=><div>{actions}<button data-search onClick={()=>onQueryChange('apple')}>Search apple</button></div>;`,
  './file-management': `import React from 'react'; export const FileBreadcrumb=()=>null; export const FileDetails=()=>null;
    export const FileSortMenu=()=>null; export const useFileSort=()=>['name:asc:folders:hidden:ignored',()=>{}];
    export const invalidateFolderSizes=()=>{};`,
};
const bundle=await build({stdin:{contents:'export {FileTree} from "./components/files/file-tree";',resolveDir:fileURLToPath(new URL('../',import.meta.url)),loader:'tsx'},bundle:true,write:false,format:'cjs',platform:'node',jsx:'automatic',plugins:[{name:'host',setup(b){
  b.onResolve({filter:/.*/},({path})=>path in mocks?{path,namespace:'mock'}:undefined);
  b.onLoad({filter:/.*/,namespace:'mock'},({path})=>({contents:mocks[path],loader:'tsx'}));
  b.onResolve({filter:/^react(?:-dom)?(?:\/.*)?$/},({path})=>({path:require.resolve(path),external:true}));
  b.onLoad({filter:/\.css$/},()=>({contents:'export default {};',loader:'js'}));
}}]});
const temporary=mkdtempSync(join(tmpdir(),'op-tree-safety-'));
let FileTree;
try{const output=join(temporary,'tree.cjs');writeFileSync(output,bundle.outputFiles[0].text);({FileTree}=require(output));}finally{rmSync(temporary,{recursive:true,force:true});}
async function mounted(run){
  const {window}=parseHTML('<html><body><div id="root"></div></body></html>');
  const saved={};for(const key of ['window','document','ResizeObserver','CustomEvent','IS_REACT_ACT_ENVIRONMENT','host'])saved[key]=globalThis[key];
  Object.assign(globalThis,{window,document:window.document,CustomEvent:window.CustomEvent,ResizeObserver:class{observe(){}disconnect(){}},IS_REACT_ACT_ENVIRONMENT:true});
  const requests=[];
  const host=globalThis.host={drafts:[],menu:'',query:async(action,payload)=>{
    requests.push({action,...payload});
    if(action==='list_projects')return {projects:[{id:'A',path:'/A'},{id:'B',path:'/B'}]};
    if(action==='project_file_tree')return {...payload,entries:[{name:'apple.txt',type:'file',size:3,mtime:1},{name:'dest',type:'dir',size:0,mtime:1}]};
    if(action==='project_file_search')return {...payload,results:[{path:'apple.txt',name:'apple.txt',type:'file',size:3,mtime:1}]};
    return {...payload,status:'ready',ok:true};
  }};
  window.alert=()=>{};window.prompt=()=> 'export';
  const root=createRoot(window.document.getElementById('root'));
  const render=async projectId=>act(async()=>root.render(React.createElement(FileTree,{projectId})));
  const click=async selector=>act(async()=>{const element=document.querySelector(selector);assert.ok(element,selector);element.dispatchEvent(new window.Event('click',{bubbles:true}));});
  const menu=async(path,action)=>{host.menu=action;await act(async()=>document.querySelector(`[data-entry="${path}"]`).dispatchEvent(new window.Event('contextmenu',{bubbles:true})));};
  try{await render('A');await run({host,requests,render,click,menu,window});}finally{await act(async()=>root.unmount());Object.assign(globalThis,saved);}
}
for(const op of ['copy','cut'])test(`${op} in project A cannot paste the same relative path in B`,()=>mounted(async({render,menu,host,requests})=>{
  await menu('apple.txt',op);await render('B');await menu('dest','paste');
  assert.equal(host.menuDisabled,true);
  assert.equal(requests.filter(x=>['project_file_copy','project_file_rename'].includes(x.action)).length,0);
  await render('A');await menu('dest','paste');
  assert.equal(host.menuDisabled,false);
  assert.ok(requests.some(x=>x.action===`project_file_${op==='cut'?'rename':'copy'}`&&x.project_id==='A'&&x.new_path==='dest/apple.txt'));
}));
test('refresh and file-change events repeat an unchanged active search',()=>mounted(async({click,requests,window})=>{
  // The production debounce is driven to its callback; no wall-clock sleeps.
  const original=globalThis.setTimeout;
  globalThis.setTimeout=(callback,delay,...args)=>delay===200?original(callback,0,...args):original(callback,delay,...args);
  const settle=()=>act(async()=>new Promise(resolve=>original(resolve,0)));
  try{
    await click('[data-search]');await settle();
    assert.equal(requests.filter(x=>x.action==='project_file_search').length,1);
    await click('[aria-label="Refresh"]');await settle();
    assert.equal(requests.filter(x=>x.action==='project_file_search').length,2);
    await act(async()=>window.dispatchEvent(new window.CustomEvent('project-files-changed',{detail:{project_id:'A'}})));await settle();
    assert.equal(requests.filter(x=>x.action==='project_file_search').length,3);
    assert.ok(document.querySelector('[data-entry="apple.txt"]'));
  }finally{globalThis.setTimeout=original;}
}));
test('export before deletion retains the durable draft when download has no completion receipt',()=>mounted(async({host,menu,click,requests})=>{
  host.drafts=[{path:'apple.txt',draft:{draft:'unsaved content',baselineMtime:1}}];
  await menu('apple.txt','delete');await click('[data-confirm]');
  assert.ok(requests.some(x=>x.action==='project_file_delete'));
  assert.equal(host.drafts[0].draft.draft,'unsaved content');
}));

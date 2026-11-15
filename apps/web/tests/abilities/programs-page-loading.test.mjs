import assert from "node:assert/strict";
import test from "node:test";
import { join, dirname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";
import { parseHTML } from "linkedom";

import { scratchDir } from "../scratch-dir.mjs";

const web = dirname(fileURLToPath(new URL('../../package.json', import.meta.url)));
const temp = scratchDir(web, "programs-load-test");
await build({
  absWorkingDir: web, entryPoints: ['components/programs/programs-page.tsx'], outfile: join(temp,'page.mjs'),
  bundle: true, packages: 'external', format: 'esm', platform: 'node', jsx: 'automatic',
  loader: { '.css': 'empty' }, plugins: [{ name: 'services', setup(b) {
    b.onResolve({ filter: /(?:net\/fetch-client|net\/applications$|lib\/i18n|runtime-bridge\/state|abilities\/functions-store|net\/application-list|use-application-launcher|center-tabs-store|^next\/navigation$|^next\/link$|pierre-file-tree$)/ }, a => ({ path: a.path, namespace: 'mock' }));
    b.onLoad({ filter: /.*/, namespace: 'mock' }, a => ({ resolveDir: web, contents:
      a.path.includes('fetch-client') ? 'export const jsonFetch = (...args) => globalThis.programFetch(...args);'
      : a.path.endsWith('net/applications') ? 'export const applicationRequest = async (...args) => { (globalThis.applicationCalls??=[]).push(args); return {}; };'
      : a.path.includes('application-list') ? 'export const useApplicationList=()=>globalThis.applicationList||{applications:[],builtin:[],loaded:true}; export const refreshApplications=async()=>[];'
      : a.path.includes('use-application-launcher') ? 'export const useApplicationLauncher=()=>({launch:async(app)=>{(globalThis.launched??=[]).push(app.id)},error:"",dialog:null});'
      : a.path.includes('center-tabs-store') ? 'export const useCenterTabs={getState:()=>({openBuiltinTab:(page)=>{(globalThis.openedTabs??=[]).push(page)}})};'
      : a.path.includes('i18n') ? 'const text=(en)=>en; const t=(key)=>key; export const useTranslation=()=>({text,t});'
      : a.path.includes('functions-store') ? 'const state=(globalThis.functionsState={meta:{favorites:[],folders:{},icons:{}},setMeta(m){this.meta=m}});export const useFunctions={getState:()=>state};'
      : a.path.includes('runtime-bridge') ? 'export const runtimeState=(globalThis.runtimeStateMock={});'
      // The Files tree (tests/abilities/programs-page-tree covers the real one):
      // a row per entry that selects it when clicked.
      : a.path.endsWith('pierre-file-tree') ? 'import {createElement,forwardRef} from "react"; const Tree=forwardRef(({entries=[],matches=[],onSelect},ref)=>createElement("div",{ref},(entries.length?entries:matches).map(e=>createElement("button",{key:e.path,"data-entry":e.path,onClick:()=>onSelect(e.path,e.type)},e.path)))); export const PierreFileTree=Tree; export const PierreSearchTree=Tree;'
      : a.path === 'next/link' ? 'import {createElement} from "react"; export default function Link(props){return createElement("a",props);}'
      : 'export const useRouter=()=>({push(href){(globalThis.routerPushes??=[]).push(href)}}); export const usePathname=()=>"/programs";'
    }));
  } }],
});
const { window, document } = parseHTML('<html><body></body></html>');
Object.assign(globalThis,{window,document,HTMLElement:window.HTMLElement,Node:window.Node,IS_REACT_ACT_ENVIRONMENT:true});
globalThis.getComputedStyle=()=>({getPropertyValue:()=>'',display:'block'});
globalThis.requestAnimationFrame=fn=>setTimeout(fn,0);
globalThis.cancelAnimationFrame=clearTimeout;
const intervals = new Set();
window.setInterval = fn => { intervals.add(fn); return fn; };
window.clearInterval = fn => intervals.delete(fn);
const { createElement, act } = await import('react');
const { createRoot } = await import('react-dom/client');
const { ProgramsPage } = await import(pathToFileURL(join(temp,'page.mjs')));
const row = name => ({name,path:`applications/${name}`,kind:'folder',program_kind:'application',origin:'installed',has_children:false,logic_path:`applications/${name}`,callable_name:`${name}_run`});
const requests=[];
// The explorer's current revision; `explorerEmpty` serves an empty catalog.
let explorerRevision='1';
let explorerEmpty=false;
const postedMeta=[];
globalThis.fetch = async (url, init={}) => {
  if (url === '/api/programs/meta' && init.method === 'POST') postedMeta.push(JSON.parse(init.body));
  return new Response('{}');
};
let block=false;
const waiting=[];
globalThis.programFetch = async url => {
  requests.push(url);
  if (block && !url.includes('/meta')) await new Promise(resolve=>waiting.push(resolve));
  if (url.includes('/meta')) return {favorites:[],folders:{},icons:{}};
  const u=new URL(url,'http://local');
  if (u.searchParams.get('revision') === explorerRevision) return {unchanged:true,revision:explorerRevision};
  if (u.pathname.endsWith('/explorer') && explorerEmpty) return {revision:explorerRevision,entries:[],default_selection:null};
  if (u.pathname.endsWith('/explorer')) return {revision:explorerRevision,entries: u.searchParams.get('path') ? [row('alpha'),row('beta')] : [{name:'applications',path:'applications',kind:'folder',program_kind:null,has_children:true}],default_selection:'applications/alpha'};
  const path=u.searchParams.get('path');
  return {revision:'1',root:path,nodes:[{id:path,name:path.split('/').at(-1),path,program_kind:'application',depth:0}],edges:[]};
};
const rowOf = (host, path) => host.querySelector(`[data-entry="${path}"]`);
async function settle(){ for(let i=0;i<8;i++) await act(async()=>{await Promise.resolve();}); }
const buttonNamed = (host, label) => [...host.querySelectorAll('button')].find(button => button.getAttribute('aria-label') === label || button.textContent.trim() === label);

test('an empty catalog says how to add a Program', async()=>{
  explorerEmpty=true; explorerRevision='0';
  const host=document.createElement('div'); document.body.append(host);
  const root=createRoot(host);
  try {
    await act(async()=>root.render(createElement(ProgramsPage)));
    await settle();
    assert.match(host.textContent,/No Programs found/);
    assert.match(host.textContent,/Install an application or add a Tool\./);
  } finally { await act(async()=>root.unmount()); host.remove(); explorerEmpty=false; explorerRevision='1'; }
});

test('warm Programs remount renders selection immediately while background checks are pending', async()=>{
  const host=document.createElement('div'); document.body.append(host);
  let root=createRoot(host);
  try {
    await act(async()=>root.render(createElement(ProgramsPage)));
    await settle();
    const beta=rowOf(host,'applications/beta');
    assert.ok(beta);
    await act(async()=>beta.dispatchEvent(new window.Event('click',{bubbles:true})));
    await settle();
    assert.match(host.querySelector('h2').textContent,/beta/);
    await act(async()=>root.unmount());
    assert.equal(intervals.size,0);
    block=true;
    root=createRoot(host);
    await act(async()=>root.render(createElement(ProgramsPage)));
    assert.match(host.querySelector('h2').textContent,/beta/);
    assert.ok(rowOf(host,'applications/beta'));
    assert.doesNotMatch(host.textContent,/Loading call logic|Loading Programs/);
    block=false;
    await act(async()=>{for(const resolve of waiting.splice(0))resolve();});
    await settle();
    assert.match(requests.at(-1),/revision=1/);
    assert.equal(requests.some(url=>url==='/api/programs'),false);
  } finally { block=false;for(const resolve of waiting.splice(0))resolve();await act(async()=>root.unmount());host.remove(); }
});

test('the favourite toggle reports its state, saves the meta and updates the store; Use opens the run URL', async()=>{
  const host=document.createElement('div'); document.body.append(host);
  const root=createRoot(host);
  try {
    await act(async()=>root.render(createElement(ProgramsPage)));
    await settle();
    assert.match(host.querySelector('h2').textContent,/beta/);
    const favorite=buttonNamed(host,'Favorite');
    assert.ok(favorite);
    assert.equal(favorite.getAttribute('aria-pressed'),'false');
    await act(async()=>favorite.dispatchEvent(new window.Event('click',{bubbles:true})));
    await settle();
    const unfavorite=buttonNamed(host,'Unfavorite');
    assert.ok(unfavorite);
    assert.equal(unfavorite.getAttribute('aria-pressed'),'true');
    assert.deepEqual(postedMeta.at(-1).favorites,['beta_run']);
    assert.deepEqual(globalThis.functionsState.meta.favorites,['beta_run']);
    assert.deepEqual(globalThis.runtimeStateMock.programsMeta.favorites,['beta_run']);

    await act(async()=>unfavorite.dispatchEvent(new window.Event('click',{bubbles:true})));
    await settle();
    assert.equal(buttonNamed(host,'Favorite').getAttribute('aria-pressed'),'false');
    assert.deepEqual(postedMeta.at(-1).favorites,[]);

    await act(async()=>buttonNamed(host,'Use in chat').dispatchEvent(new window.Event('click',{bubbles:true})));
    assert.equal(globalThis.routerPushes.at(-1),'/chat?run=beta_run&cat=application');
  } finally { await act(async()=>root.unmount()); host.remove(); }
});

import assert from 'node:assert/strict';
import test from 'node:test';
import {join,dirname} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {build} from 'esbuild';
import {parseHTML} from 'linkedom';

import { scratchDir } from "../../scratch-dir.mjs";

const web=dirname(fileURLToPath(new URL('../../../package.json',import.meta.url)));
const dir = scratchDir(web, "chat-activation");
const output=join(dir,'hook.mjs');
await build({absWorkingDir:web,entryPoints:['components/chat/messages/list/use-chat-area-stick.ts'],outfile:output,bundle:true,packages:'external',platform:'node',format:'esm',jsx:'automatic',plugins:[{name:'services',setup(b){
 b.onResolve({filter:/^@\//},a=>a.path.endsWith('/chat-scroll')?null:{path:a.path,namespace:'service'});
 b.onLoad({filter:/.*/,namespace:'service'},a=>({contents:
 a.path.includes('session-store')?'export const useSessionStore=f=>f(globalThis.chatState);':
 a.path.includes('session-history-loader')?'export const loadSessionHistoryWindow=(...args)=>globalThis.loadLatest(...args);':
 a.path.includes('session-history')?'export const useSessionHistory=Object.assign(f=>f(globalThis.historyState),{getState:()=>globalThis.historyState});':
 a.path.includes('history-viewport')?'export const saveHistoryAnchor=()=>{},setFollowLock=()=>{};':
 'export const typesetMath=()=>{};'}));
}}]});
const parsed=parseHTML('<html><body><div id="root"></div><div id="area"><div id="messages"></div></div></body></html>');
globalThis.HTMLElement=parsed.window.HTMLElement;globalThis.window=parsed.window;globalThis.document=parsed.document;globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const saved=new Map();window.sessionStorage={getItem:k=>saved.get(k)??null,setItem:(k,v)=>saved.set(k,v)};
globalThis.getComputedStyle=()=>({paddingBottom:'0',getPropertyValue:()=>"0"});
let resize;globalThis.ResizeObserver=class{constructor(cb){resize=cb;}observe(){}disconnect(){}};
globalThis.chatState={currentSessionId:'a',welcomeVisible:false};globalThis.historyState={pages:{}};
const {act,createElement}=await import('react');const {createRoot}=await import('react-dom/client');
const {useChatAreaStick}=await import(pathToFileURL(output));
const area=document.querySelector('#area'),column=document.querySelector('#messages');let height=1500,top=0;
Object.defineProperties(area,{scrollHeight:{get:()=>height},clientHeight:{get:()=>500},scrollTop:{get:()=>top,set:v=>{top=Math.max(0,Math.min(v,height-500));}}});
const options={sessionId:'a',areaRef:{current:area},columnRef:{current:column}};
function View({id,visible=true,seed='seed'}){options.sessionId=id;useChatAreaStick(id,seed,visible,options);return null;}
test('activation reaches latest through delayed file growth and manual input cancels following',async()=>{
 const root=createRoot(document.querySelector('#root'));const render=async(id,visible=true)=>act(async()=>root.render(createElement(View,{id,visible})));
 await render('a');assert.equal(top,1000);await render('b');height=1600;await act(async()=>resize());assert.equal(top,1100);
 await render('a');assert.equal(top,1100,'a conversation left at bottom follows its current bottom');
 height=1900;await act(async()=>resize());assert.equal(top,1400,'delayed growth stays at bottom after returning');
 await act(async()=>{area.dispatchEvent(new window.Event('wheel'));top=500;area.dispatchEvent(new window.Event('scroll'));});
 height=2100;await act(async()=>resize());assert.equal(top,500,'manual history reading remains fixed');
 await render('b');await render('a');assert.equal(top,500,'returning restores manually detached reading');
 height=2300;await act(async()=>resize());assert.equal(top,500,'delayed growth does not move a detached reader');
 await render('a',false);await render('a');assert.equal(top,500,'reactivated opened chat preserves reading position');
 await act(async()=>root.unmount());
});
test('older-window activation requests latest and ignores outgoing completion',async()=>{
 const root=createRoot(document.querySelector('#root'));let complete;
 globalThis.historyState={pages:{old:{after:'cursor',snapshot:'old',loading:false}}};
 globalThis.loadLatest=async(sid,direction,_anchor,{isCurrent})=>{assert.equal(sid,'old');assert.equal(direction,'latest');return new Promise(resolve=>{complete=()=>{if(isCurrent())globalThis.historyState.pages.old={snapshot:'latest'};resolve(true);};});};
 await act(async()=>root.render(createElement(View,{id:'old'})));assert.ok(complete,'activation requests latest');
 await act(async()=>root.render(createElement(View,{id:'other'})));const incoming=top;
 await act(async()=>complete());assert.equal(top,incoming,'stale completion cannot move incoming view');
 await act(async()=>root.unmount());
});

test('a window read on protocol 3 activates at latest the same way',async()=>{
 const root=createRoot(document.querySelector('#root'));let complete;
 globalThis.historyState={pages:{oldv3:{after:'cursor',revision:1,loading:false}}};
 globalThis.loadLatest=async(sid,direction,_anchor,{isCurrent})=>{assert.equal(sid,'oldv3');assert.equal(direction,'latest');return new Promise(resolve=>{complete=()=>{if(isCurrent())globalThis.historyState.pages.oldv3={revision:2};resolve(true);};});};
 await act(async()=>root.render(createElement(View,{id:'oldv3'})));assert.ok(complete,'activation requests latest');
 await act(async()=>root.render(createElement(View,{id:'other'})));const incoming=top;
 await act(async()=>complete());assert.equal(top,incoming,'stale completion cannot move incoming view');
 await act(async()=>root.unmount());
});

test('saved latest intent survives remount and a temporarily shorter transcript',async()=>{
 saved.set('chatScrollByKey',JSON.stringify({persisted:{top:3000,atBottom:true},legacy:240}));
 height=800;top=0;globalThis.historyState={pages:{}};
 const root=createRoot(document.querySelector('#root'));
 await act(async()=>root.render(createElement(View,{id:'persisted'})));assert.equal(top,300);
 height=4100;await act(async()=>resize());assert.equal(top,3600,'restored latest follows final layout instead of clamped old pixels');
 await act(async()=>root.render(createElement(View,{id:'legacy'})));assert.equal(top,3600,'legacy positions without freshness information open at latest');
 await act(async()=>root.unmount());
});

test('recent returns restore reading; one-hour absence and long app blur show latest',async()=>{
 const realNow=Date.now;let now=1900000000000;Date.now=()=>now;
 const root=createRoot(document.querySelector('#root'));
 const render=async id=>act(async()=>root.render(createElement(View,{id})));
 try {
  height=2500;top=0;globalThis.historyState={pages:{}};
  await render('timed');
  await act(async()=>{area.dispatchEvent(new window.Event('wheel'));top=300;area.dispatchEvent(new window.Event('scroll'));});
  now+=3*3600000;await act(async()=>resize());assert.equal(top,300,'active reading has no time-based jump');
  await render('timed-other');now+=3600000-1;await render('timed');assert.equal(top,300,'expiry starts when leaving, not the last scroll');
  await render('timed-other');now+=3600000;await render('timed');assert.equal(top,2000,'one-hour absence opens latest');
  await act(async()=>{area.dispatchEvent(new window.Event('wheel'));top=400;area.dispatchEvent(new window.Event('scroll'));});
  await act(async()=>window.dispatchEvent(new window.Event('blur')));now+=3600000-1;
  await act(async()=>window.dispatchEvent(new window.Event('focus')));assert.equal(top,400,'short app absence preserves reading');
  await act(async()=>window.dispatchEvent(new window.Event('blur')));now+=3600000;
  await act(async()=>window.dispatchEvent(new window.Event('focus')));assert.equal(top,2000,'long app absence follows latest');
 } finally {await act(async()=>root.unmount());Date.now=realNow;}
});

test('cold activation keeps latest intent when asynchronous transcript arrives after initial resize',async()=>{
 const root=createRoot(document.querySelector('#root'));
 height=500;top=0;globalThis.historyState={pages:{}};
 await act(async()=>root.render(createElement(View,{id:'cold-load',seed:null})));
 await act(async()=>resize());
 height=9500;
 await act(async()=>root.render(createElement(View,{id:'cold-load',seed:'loaded-last-message'})));
 await act(async()=>resize());
 assert.equal(top,9000,'loaded transcript must retain first-activation latest intent');
 height=10000;await act(async()=>resize());
 assert.equal(top,9500,'subsequent markdown growth keeps following without an initial suppression window');
 await act(async()=>root.unmount());
});

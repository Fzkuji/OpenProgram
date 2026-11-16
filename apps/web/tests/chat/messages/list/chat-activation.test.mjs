import assert from 'node:assert/strict';
import test from 'node:test';
import {join,dirname} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {build} from 'esbuild';
import {parseHTML} from 'linkedom';

import { scratchDir } from "../../../scratch-dir.mjs";

const web=dirname(fileURLToPath(new URL('../../../../package.json',import.meta.url)));
const dir = scratchDir(web, "chat-activation");
const output=join(dir,'hook.mjs');
await build({absWorkingDir:web,entryPoints:['components/chat/messages/list/use-chat-area-stick.ts'],outfile:output,bundle:true,packages:'external',platform:'node',format:'esm',jsx:'automatic',plugins:[{name:'services',setup(b){
 b.onResolve({filter:/^@\//},a=>a.path.endsWith('/chat-scroll')?null:{path:a.path,namespace:'service'});
 b.onLoad({filter:/.*/,namespace:'service'},a=>({contents:
 a.path.includes('session-store')?'export const useSessionStore=f=>f(globalThis.chatState);':
 a.path.includes('session-history-loader')?'export const loadSessionHistoryWindow=(...args)=>globalThis.loadLatest(...args);':
 a.path.includes('session-history')?'export const useSessionHistory=Object.assign(f=>f(globalThis.historyState),{getState:()=>globalThis.historyState});':
 a.path.includes('history-viewport')?'export const setFollowLock=()=>{};':
 'export const typesetMath=()=>{};'}));
}}]});
const parsed=parseHTML('<html><body><div id="root"></div><div id="area"><div id="messages"></div></div></body></html>');
globalThis.HTMLElement=parsed.window.HTMLElement;globalThis.window=parsed.window;globalThis.document=parsed.document;globalThis.IS_REACT_ACT_ENVIRONMENT=true;
globalThis.getComputedStyle=()=>({paddingBottom:'0',getPropertyValue:()=>"0"});
let resize;const observed=new Set();globalThis.ResizeObserver=class{constructor(cb){resize=cb;}observe(el){observed.add(el);}disconnect(){observed.clear();}};
globalThis.chatState={currentSessionId:'a',welcomeVisible:false};globalThis.historyState={pages:{}};
const {act,createElement}=await import('react');const {createRoot}=await import('react-dom/client');
const {useChatAreaStick}=await import(pathToFileURL(output));
const area=document.querySelector('#area'),column=document.querySelector('#messages');let height=1500,top=0,client=500;
Object.defineProperties(area,{scrollHeight:{get:()=>height},clientHeight:{get:()=>client},scrollTop:{get:()=>top,set:v=>{top=Math.max(0,Math.min(v,height-client));}}});
const options={sessionId:'a',areaRef:{current:area},columnRef:{current:column}};
let stick;function View({id,visible=true,seed='seed'}){options.sessionId=id;stick=useChatAreaStick(id,seed,visible,options);return null;}
const wheel=deltaY=>Object.assign(new window.Event('wheel'),{deltaY});
const scrollTo=async(next,deltaY)=>act(async()=>{if(deltaY!==undefined)area.dispatchEvent(wheel(deltaY));top=next;area.dispatchEvent(new window.Event('scroll'));});
test('every opening lands at the latest, whatever position the reader left',async()=>{
 const root=createRoot(document.querySelector('#root'));const render=async(id,visible=true)=>act(async()=>root.render(createElement(View,{id,visible})));
 await render('a');assert.equal(top,1000);await render('b');height=1600;await act(async()=>resize());assert.equal(top,1100);
 await render('a');assert.equal(top,1100,'a conversation left at the latest opens at its current latest');
 height=1900;await act(async()=>resize());assert.equal(top,1400,'delayed growth stays at the latest after returning');
 await scrollTo(500,-100);
 height=2100;await act(async()=>resize());assert.equal(top,500,'a reader in history is not moved by growth');
 await render('b');await render('a');assert.equal(top,1600,'returning lands at the latest, not at the place left');
 assert.equal(stick.showJump,false);assert.equal(stick.detached,false);
 height=2300;await act(async()=>resize());assert.equal(top,1800,'and follows later growth');
 await scrollTo(500,-100);
 await render('a',false);await render('a');assert.equal(top,1800,'a conversation shown again lands at the latest too');
 await act(async()=>root.unmount());
});
test('growth, a page of older rows and wheel inertia never stop following or show Jump',async()=>{
 const root=createRoot(document.querySelector('#root'));const render=async id=>act(async()=>root.render(createElement(View,{id})));
 height=2500;top=0;globalThis.historyState={pages:{}};
 await render('follow');assert.equal(top,2000);assert.equal(stick.showJump,false);
 height=2600;await act(async()=>resize());assert.equal(top,2100);assert.equal(stick.showJump,false);
 await act(async()=>{area.dispatchEvent(wheel(0));area.dispatchEvent(wheel(1));});
 height=2900;await act(async()=>resize());assert.equal(top,2400,'a resting finger and downward inertia leave the view following');
 globalThis.historyState={pages:{follow:{loading:true}}};await render('follow');
 assert.equal(stick.showJump,false,'an older page loading is not the reader leaving the latest');
 height=3900;globalThis.historyState={pages:{follow:{loading:false}}};await render('follow');
 assert.equal(top,3400,'older rows inserted above keep the view at the latest');assert.equal(stick.showJump,false);
 await scrollTo(3300,-100);
 assert.equal(stick.detached,true,'moving up stops following');
 assert.equal(stick.showJump,false,'100 px above the latest is too near for Jump');
 height=4100;await act(async()=>resize());assert.equal(top,3300,'a reader who moved up is left alone');
 assert.equal(stick.showJump,true,'300 px above the latest shows Jump');
 await scrollTo(3550);
 assert.equal(stick.showJump,false,'Jump hides near the latest');
 assert.equal(stick.detached,true,'50 px short of the bottom is not pulled to it');assert.equal(top,3550);
 await scrollTo(3600);
 assert.equal(stick.detached,false,'reaching the bottom follows again');
 await act(async()=>root.unmount());
});
test('a view taken up to a message after landing is not pulled back by late layout',async()=>{
 const root=createRoot(document.querySelector('#root'));
 height=3000;top=0;globalThis.historyState={pages:{}};
 await act(async()=>root.render(createElement(View,{id:'target'})));assert.equal(top,2500);
 await scrollTo(400);
 height=3200;await act(async()=>resize());assert.equal(top,400,'the placement that follows a landing does not undo the move');
 assert.equal(stick.showJump,true);
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

test('an opening follows a transcript that is short at first',async()=>{
 height=800;top=0;globalThis.historyState={pages:{}};
 const root=createRoot(document.querySelector('#root'));
 await act(async()=>root.render(createElement(View,{id:'short-first'})));assert.equal(top,300);
 height=4100;await act(async()=>resize());assert.equal(top,3600,'the final layout is followed');
 await act(async()=>root.unmount());
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

test('a window made shorter keeps the latest above the composer, and a reader in history gets Jump',async()=>{
 const root=createRoot(document.querySelector('#root'));
 height=3000;top=0;client=800;globalThis.historyState={pages:{}};
 await act(async()=>root.render(createElement(View,{id:'shrink'})));assert.equal(top,2200);
 assert.ok(observed.has(area),'the scroll area itself is observed, so a height-only change runs pin');
 // Only the window height changes: the column keeps its box, scrollTop stays.
 client=600;await act(async()=>resize());
 assert.equal(top,2400,'the view snaps to the new latest instead of sliding under the composer');
 client=300;await act(async()=>resize());assert.equal(top,2700);
 assert.equal(stick.showJump,false);
 client=800;await scrollTo(1500,-100);assert.equal(stick.detached,true);
 client=500;await act(async()=>resize());
 assert.equal(top,1500,'a reader in history is left where they are');
 assert.equal(stick.showJump,true,'and the button reflects the new distance');
 client=500;await act(async()=>root.unmount());
});

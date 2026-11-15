import assert from 'node:assert/strict';
import test, { after } from 'node:test';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';

import { scratchDir } from "../../scratch-dir.mjs";

const root = dirname(fileURLToPath(new URL('../../../package.json', import.meta.url)));
const dir = scratchDir(root, "history-test");
await build({absWorkingDir:root,stdin:{contents:`
export {loadOlderSessionHistory,loadSessionData} from './lib/runtime-bridge/session/conversations';
export {seedTurnWindow,loadSessionHistoryWindow,registerHistoryViewport,retrySessionHistory} from './lib/runtime-bridge/session/session-history-loader';
export {runtimeState,setSocket} from './lib/runtime-bridge/state';
export {useSessionStore,selectRow} from './lib/session-store';
export {createHistoryFragmentDecoder} from './lib/net/ws/history-fragments';
export {holdsHistoryWindow,registerSessionHistory,updateSessionHistory,useSessionHistory} from './lib/chat/history/session-history';
export {peekTakeLatest,lastSettledTakeLatest} from './lib/chat/history/chat-scroll';
export {setFollowLock,readHistoryAnchor,saveHistoryAnchor} from './lib/chat/history/history-viewport';
`,resolveDir:root},bundle:true,format:'esm',platform:'node',packages:'external',tsconfig:join(root,'tsconfig.json'),outfile:join(dir,'test.mjs')});
const fragment=(decoder,data)=>decoder.receive(JSON.stringify({type:'history_fragment',data}));
const {readHistoryAnchor,saveHistoryAnchor,seedTurnWindow,peekTakeLatest,lastSettledTakeLatest,setFollowLock,registerHistoryViewport,retrySessionHistory,loadSessionHistoryWindow,loadOlderSessionHistory,loadSessionData,runtimeState,setSocket,useSessionStore,selectRow,createHistoryFragmentDecoder,registerSessionHistory,updateSessionHistory,useSessionHistory,holdsHistoryWindow}=await import(pathToFileURL(join(dir,'test.mjs')));
test('large Unicode response is dispatched only when all ordered fragments arrive',()=>{
 const decoder=createHistoryFragmentDecoder();
 const original=JSON.stringify({type:'session_loaded',data:{id:'s',messages:['中文😀'.repeat(500000)]}});
 const parts=[];for(let i=0;i<original.length;i+=16384)parts.push(original.slice(i,i+16384));
 for(let i=0;i<parts.length;i++) {
  const result=fragment(decoder,{id:'x',index:i,text:parts[i],final:i===parts.length-1});
  assert.deepEqual(result,i===parts.length-1?[original]:[]);
 }
});
test('socket replacement discards partial history and rejects out-of-order continuation',()=>{
 const decoder=createHistoryFragmentDecoder();
 fragment(decoder,{id:'x',index:0,text:'old',final:false});decoder.clear();
 assert.throws(()=>fragment(decoder,{id:'x',index:1,text:'tail',final:true}));
 assert.deepEqual(fragment(decoder,{id:'fresh',index:0,text:'new',final:true}),['new']);
});
test('old history response cannot change a reloaded or different conversation',()=>{
 registerSessionHistory('a',{head_id:'head-a',before:'old-a'});
 const generation=useSessionHistory.getState().pages.a.generation;
 registerSessionHistory('b',{head_id:'head-b',before:'old-b'});
 registerSessionHistory('a',{head_id:'new-head',before:'new-old'});
 assert.equal(updateSessionHistory('a',generation,{before:null}),false);
 assert.equal(useSessionHistory.getState().pages.a.before,'new-old');
 assert.equal(useSessionHistory.getState().pages.b.before,'old-b');
});

function turnSocket(){
 class Socket extends EventTarget { static OPEN=1;readyState=1;sent=[];send(text){const m=JSON.parse(text);if(m.action!=='close_view')this.sent.push(m);} }
 globalThis.WebSocket=Socket;globalThis.window ??= new EventTarget();
 const ws=new Socket();setSocket(ws);
 const reply=(data)=>ws.dispatchEvent(new MessageEvent('message',{data:JSON.stringify({type:'session_history_page',data:{action:'load_session',request_id:ws.sent.at(-1).request_id,...data}})}));
 return {ws,reply};
}
const turn=(id,fields={})=>({id,rows:[{id,role:'assistant',rowKind:'assistant',content:`${id} from the page`,status:'done',...fields}]});
const turnWindow=(start,end,total,extra={})=>({head_id:'head',revision:4,start,end,total,before:start?`t${start}`:null,after:end<total?`t${end-1}`:null,...extra});
function openTurns(id,turns,window,rows=turns.flatMap(t=>t.rows)){
 runtimeState.currentSessionId='other';runtimeState.conversations[id]={id};
 useSessionStore.getState().applyRead(id,rows);seedTurnWindow(id,turns,window);registerSessionHistory(id,window);
}
test('an older page lands before the held rows, and a page answering an earlier read is dropped',async()=>{
 const {ws,reply}=turnSocket();const id='older-first';
 openTurns(id,[turn('t10')],turnWindow(10,11,20,{before:'t10'}));
 const load=loadOlderSessionHistory(id);
 reply({id,turns:[turn('t9')],window:turnWindow(9,10,20)});
 assert.equal(await load,undefined);
 assert.deepEqual(useSessionStore.getState().messageOrder[id],['t9','t10']);
 assert.equal(runtimeState.currentSessionId,'other');
 const stale=loadOlderSessionHistory(id);
 registerSessionHistory(id,turnWindow(10,11,20,{before:'t10',revision:9}));
 reply({id,turns:[turn('stale')],window:turnWindow(8,9,20)});
 await stale;
 assert.equal(selectRow(id,'stale')(useSessionStore.getState()),undefined);
 assert.equal(useSessionHistory.getState().pages[id].revision,9);
 setSocket(null);
});

test('a first read completes before interleaved terminal updates are applied',()=>{
 runtimeState.currentSessionId='other';
 const id='fragment-live';
 loadSessionData({id,turns:[turn('running-row',{content:'initial',status:'running'})],window:turnWindow(0,1,1)});
 const decoder=createHistoryFragmentDecoder();
 const read=JSON.stringify({type:'session_loaded',data:{id,turns:[turn('running-row',{content:'read-old',status:'running'})],window:turnWindow(0,1,1)}});
 const cut=Math.floor(read.length/2);
 assert.deepEqual(fragment(decoder,{id:'read',index:0,text:read.slice(0,cut),final:false}),[]);
 assert.deepEqual(decoder.receive(JSON.stringify({type:'terminal',data:'terminal-result'})),[]);
 for(const wire of fragment(decoder,{id:'read',index:1,text:read.slice(cut),final:true})) {
  const message=JSON.parse(wire);
  if(message.type==='session_loaded')loadSessionData(message.data);
  else useSessionStore.getState().applyRead(id,[{id:'running-row',role:'assistant',rowKind:'assistant',content:message.data,status:'done'}]);
 }
 assert.equal(selectRow(id,'running-row')(useSessionStore.getState()).content,'terminal-result');
});
test('interleaved snapshots retain first-fragment order and clear deferred notifications on disconnect',()=>{
 const decoder=createHistoryFragmentDecoder();
 assert.deepEqual(fragment(decoder,{id:'a',index:0,text:'A',final:false}),[]);
 assert.deepEqual(fragment(decoder,{id:'b',index:0,text:'B',final:true}),[]);
 assert.deepEqual(decoder.receive('"notification"'),[]);
 assert.deepEqual(fragment(decoder,{id:'a',index:1,text:'!',final:true}),['A!','B','"notification"']);
 fragment(decoder,{id:'c',index:0,text:'old',final:false});decoder.receive('"old-live"');decoder.clear();
 assert.deepEqual(decoder.receive('"fresh"'),['"fresh"']);
});

test('visiting many chats keeps at most four windows; removing a chat releases its window',t=>{
 const storageDescriptor=Object.getOwnPropertyDescriptor(globalThis,'localStorage');
 Object.defineProperty(globalThis,'localStorage',{configurable:true,value:{getItem:()=>null,setItem:()=>{},removeItem:()=>{}}});
 t.after(()=>storageDescriptor?Object.defineProperty(globalThis,'localStorage',storageDescriptor):delete globalThis.localStorage);
 for(let i=0;i<10;i++)openTurns(`cache-${i}`,[turn(`row-${i}`)],turnWindow(0,1,1));
 assert.ok(Object.entries(useSessionStore.getState().messageOrder).filter(([id,ids])=>id.startsWith('cache-')&&ids.length).length<=4);
 useSessionStore.getState().removeConversation('cache-9');
 assert.equal(useSessionHistory.getState().pages['cache-9'],undefined);
});

test('latest waits for a pending page instead of dropping the navigation intent',async()=>{
 const {ws,reply}=turnSocket();const id='queued';
 openTurns(id,[turn('t50')],turnWindow(50,51,1000,{before:'t50',after:'t50'}));
 const older=loadOlderSessionHistory(id);const latest=loadSessionHistoryWindow(id,'latest');
 assert.equal(ws.sent.length,1);
 reply({id,turns:[turn('t49')],window:turnWindow(49,50,1000)});
 await older;await new Promise(resolve=>setImmediate(resolve));
 assert.equal(ws.sent.length,2);assert.equal(ws.sent[1].history_latest,true);
 reply({id,turns:[turn('t999')],window:turnWindow(999,1000,1000)});
 await latest;assert.deepEqual(useSessionStore.getState().messageOrder[id],['t999']);setSocket(null);
});

test('a newest page nobody asked for hands its placement to the stick hook as a take-latest note',async t=>{
 const {ws,reply}=turnSocket();t.after(()=>setSocket(null));
 const id='renewed-latest';
 openTurns(id,[turn('t50')],turnWindow(50,51,1000,{before:'t50',after:'t50'}));
 // A viewport with no readable row: the retry has no anchor and asks for the newest page.
 const area={scrollTop:2000,scrollHeight:5000,isConnected:true,dispatchEvent(){},getBoundingClientRect:()=>({top:0}),querySelectorAll:()=>[]};
 const release=registerHistoryViewport(id,area,id);t.after(release);
 const before=peekTakeLatest(id,id)?.generation ?? 0;
 let retry=retrySessionHistory(id);
 assert.equal(ws.sent.at(-1).history_latest,true);
 reply({id,turns:[turn('t999')],window:turnWindow(999,1000,1000)});
 assert.equal(await retry,true);await new Promise(resolve=>setImmediate(resolve));
 const note=peekTakeLatest(id,id);
 assert.ok(note && note.generation>before,'the hook is told to take the latest');
 assert.ok(note.generation>lastSettledTakeLatest(id,id),'the note is still waiting to be applied');
 assert.equal(area.scrollTop,2000,'the loader itself pins nothing');
 // A Jump or a send holds the follow lock and places the view itself: no second note.
 setFollowLock(id,true);t.after(()=>setFollowLock(id,false));
 retry=retrySessionHistory(id);
 reply({id,turns:[turn('t999')],window:turnWindow(999,1000,1000)});
 assert.equal(await retry,true);await new Promise(resolve=>setImmediate(resolve));
 assert.equal(peekTakeLatest(id,id).generation,note.generation);
});

test('a protocol 3 page reply takes the page\'s rows, keeps rows outside the page and adds no row at the tail',async()=>{
 const {ws,reply}=turnSocket();const id='v3-page';
 openTurns(id,[turn('t10')],turnWindow(10,11,20,{before:'t10',after:'t10'}));
 const load=loadSessionHistoryWindow(id,'older');
 assert.equal(ws.sent.at(-1).history_before,'t10');assert.equal(ws.sent.at(-1).history_snapshot,undefined);assert.equal(ws.sent.at(-1).history_head,undefined);
 // Something appended and streaming after the request, neither in the page.
 const held=selectRow(id,'t10')(useSessionStore.getState());
 useSessionStore.getState().applyRead(id,[held,{id:'open',role:'assistant',content:'streaming',status:'streaming'}]);
 reply({id,turns:[turn('t8'),turn('open',{content:'stream',status:'running'}),turn('t9')],window:turnWindow(7,10,20)});
 assert.equal(await load,true);
 const state=useSessionStore.getState();
 assert.deepEqual(state.messageOrder[id],['t8','open','t9','t10']);
 // The server holds the session's updates while it sends the page, so the page's row is current.
 assert.equal(selectRow(id,'open')(state).content,'stream');
 assert.equal(selectRow(id,'t10')(state).content,held.content,'a held row outside the page is untouched');
 assert.equal(useSessionHistory.getState().pages[id].revision,4);
 setSocket(null);
});
test('a protocol 3 page replaces a row that did not change during the read',async()=>{
 const {reply}=turnSocket();const id='v3-fresh';
 openTurns(id,[turn('t10'),turn('t11',{content:'old',status:'running'})],turnWindow(10,12,20));
 const load=loadSessionHistoryWindow(id,'around','t11');
 reply({id,turns:[turn('t10'),turn('t11',{content:'new',status:'done'})],window:turnWindow(10,12,20)});
 assert.equal(await load,true);
 assert.equal(selectRow(id,'t11')(useSessionStore.getState()).content,'new');
 setSocket(null);
});
test('a page that touches nothing held is dropped and the window stays',async()=>{
 const {reply}=turnSocket();const id='v3-gap';
 openTurns(id,[turn('t10')],turnWindow(10,11,20,{before:'t10'}));
 const load=loadSessionHistoryWindow(id,'older');
 reply({id,turns:[turn('t2')],window:turnWindow(2,3,20)});
 assert.equal(await load,false);
 assert.deepEqual(useSessionStore.getState().messageOrder[id],['t10']);
 assert.equal(useSessionHistory.getState().pages[id].loading,false);
 setSocket(null);
});
test('an unknown cursor does one first read instead of retrying the same cursor',async()=>{
 const {ws,reply}=turnSocket();const id='v3-cursor';
 openTurns(id,[turn('t10')],turnWindow(10,11,20,{before:'t10'}));
 const load=loadSessionHistoryWindow(id,'around','gone');
 const before=ws.sent.length;
 reply({id,error_code:'cursor_not_on_branch'});
 assert.equal(await load,false);
 assert.equal(ws.sent.length,before+1);
 assert.equal(ws.sent.at(-1).action,'load_session');assert.equal(ws.sent.at(-1).history_around,undefined);
 setSocket(null);
});
test('a reading position on a row the branch no longer has is forgotten, so the next read does not ask for it again',async t=>{
 const kept=new Map();const descriptor=Object.getOwnPropertyDescriptor(globalThis,'sessionStorage');
 Object.defineProperty(globalThis,'sessionStorage',{configurable:true,value:{getItem:k=>kept.get(k)??null,setItem:(k,v)=>kept.set(k,v)}});
 t.after(()=>descriptor?Object.defineProperty(globalThis,'sessionStorage',descriptor):delete globalThis.sessionStorage);
 const {ws,reply}=turnSocket();const id='v3-anchor-gone';
 openTurns(id,[turn('t10')],turnWindow(10,11,20,{before:'t10'}));
 saveHistoryAnchor(id,{id:'gone',offset:12,head:'old-head'});saveHistoryAnchor('other',{id:'kept',offset:0});
 const load=loadSessionHistoryWindow(id,'around','gone');
 reply({id,error_code:'cursor_not_on_branch'});
 assert.equal(await load,false);
 assert.equal(ws.sent.at(-1).action,'load_session');assert.equal(ws.sent.at(-1).history_around,undefined);
 assert.equal(readHistoryAnchor(id),null,'the row is not on this branch: nothing restores to it');
 assert.equal(readHistoryAnchor('other').id,'kept');
 saveHistoryAnchor(id,{id:'t10',offset:0});
 const older=loadSessionHistoryWindow(id,'around','elsewhere');
 reply({id,error_code:'cursor_not_on_branch'});
 assert.equal(await older,false);
 assert.equal(readHistoryAnchor(id).id,'t10','a position on another row stays');
 setSocket(null);
});
test('a first read answered in turns seeds the window and holds no raw rows',()=>{
 const {ws}=turnSocket();const id='v3-open';runtimeState.currentSessionId='other';
 loadSessionData({id,turns:[turn('t1')],window:turnWindow(1,2,2),graph:[],head_id:'t1'});
 assert.deepEqual(useSessionStore.getState().messageOrder[id],['t1']);
 assert.equal(runtimeState.conversations[id].messages,undefined);assert.equal(runtimeState.conversations[id].turns,undefined);
 assert.equal(holdsHistoryWindow(useSessionHistory.getState().pages[id]),true);
 assert.equal(holdsHistoryWindow(undefined),false);
 assert.equal(ws.sent.length,0);
 setSocket(null);
});
test('a chat trimmed from the client closes its view on the server',t=>{
 const storageDescriptor=Object.getOwnPropertyDescriptor(globalThis,'localStorage');
 Object.defineProperty(globalThis,'localStorage',{configurable:true,value:{getItem:()=>null,setItem:()=>{},removeItem:()=>{}}});
 t.after(()=>storageDescriptor?Object.defineProperty(globalThis,'localStorage',storageDescriptor):delete globalThis.localStorage);
 const closed=[];
 class Socket extends EventTarget {static OPEN=1;readyState=1;send(text){const m=JSON.parse(text);if(m.action==='close_view')closed.push(m.view);}}
 globalThis.WebSocket=Socket;setSocket(new Socket());t.after(()=>setSocket(null));runtimeState.currentSessionId='other';
 for(let i=0;i<10;i++)loadSessionData({id:`v3-close-${i}`,turns:[turn(`close-${i}`)],window:turnWindow(0,1,1),graph:[],head_id:`close-${i}`});
 assert.ok(closed.includes('v3-close-0'));
});
test('leftover rows are no window, and trimming a chat forgets its window',t=>{
 const storageDescriptor=Object.getOwnPropertyDescriptor(globalThis,'localStorage');
 Object.defineProperty(globalThis,'localStorage',{configurable:true,value:{getItem:()=>null,setItem:()=>{},removeItem:()=>{}}});
 t.after(()=>storageDescriptor?Object.defineProperty(globalThis,'localStorage',storageDescriptor):delete globalThis.localStorage);
 useSessionStore.getState().applyRead('v3-leftover',[{id:'x',role:'assistant',content:'tail',status:'running'}]);
 assert.equal(holdsHistoryWindow(useSessionHistory.getState().pages['v3-leftover']),false);
 for(let i=0;i<10;i++)openTurns(`v3-trim-${i}`,[turn(`row-${i}`)],turnWindow(0,1,1));
 const left=Object.keys(useSessionHistory.getState().pages).filter(id=>id.startsWith('v3-trim-'));
 assert.ok(left.length<=4,'a trimmed chat reads again on its next open');
});

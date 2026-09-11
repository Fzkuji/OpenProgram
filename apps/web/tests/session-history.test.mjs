import assert from 'node:assert/strict';
import test, { after } from 'node:test';
import { mkdtemp, rm } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';
const root = dirname(fileURLToPath(new URL('../package.json', import.meta.url)));
const dir = await mkdtemp(join(root, '.history-test-'));
after(() => rm(dir, { recursive: true, force: true }));
await build({absWorkingDir:root,stdin:{contents:`
export {loadOlderSessionHistory,loadSessionData} from './lib/runtime-bridge/conversations';
export {runtimeState,setSocket} from './lib/runtime-bridge/state';
export {useSessionStore} from './lib/session-store';
export {createHistoryFragmentDecoder} from './lib/net/history-fragments';
export {registerSessionHistory,updateSessionHistory,useSessionHistory} from './lib/state/session-history';
`,resolveDir:root},bundle:true,format:'esm',platform:'node',packages:'external',tsconfig:join(root,'tsconfig.json'),outfile:join(dir,'test.mjs')});
const {loadOlderSessionHistory,loadSessionData,runtimeState,setSocket,useSessionStore,createHistoryFragmentDecoder,registerSessionHistory,updateSessionHistory,useSessionHistory}=await import(pathToFileURL(join(dir,'test.mjs')));
test('large Unicode response is dispatched only when all ordered fragments arrive',()=>{
 const decoder=createHistoryFragmentDecoder();
 const original=JSON.stringify({type:'session_loaded',data:{id:'s',messages:['中文😀'.repeat(500000)]}});
 const parts=[];for(let i=0;i<original.length;i+=16384)parts.push(original.slice(i,i+16384));
 for(let i=0;i<parts.length;i++) {
  const result=decoder.accept({id:'x',index:i,text:parts[i],final:i===parts.length-1});
  assert.equal(result,i===parts.length-1?original:null);
 }
});
test('socket replacement discards partial history and rejects out-of-order continuation',()=>{
 const decoder=createHistoryFragmentDecoder();
 decoder.accept({id:'x',index:0,text:'old',final:false});decoder.clear();
 assert.throws(()=>decoder.accept({id:'x',index:1,text:'tail',final:true}));
 assert.equal(decoder.accept({id:'fresh',index:0,text:'new',final:true}),'new');
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

test('public older-page load preserves live rows and rejects a page after reload',async()=>{
 class Socket extends EventTarget {
  static OPEN=1;readyState=1;sent=[];
  send(text){this.sent.push(JSON.parse(text));}
 }
 globalThis.WebSocket=Socket;
 globalThis.window ??= new EventTarget();
 const ws=new Socket();setSocket(ws);
 runtimeState.currentSessionId='other';
 runtimeState.conversations.s={id:'s',messages:[{id:'live',role:'assistant',content:'old'}]};
 useSessionStore.getState().setMessages('s',[{id:'live',role:'assistant',content:'streamed newest',status:'running'}]);
 registerSessionHistory('s',{head_id:'head',before:'live'});
 const load=loadOlderSessionHistory('s');
 const request=ws.sent.at(-1);
 ws.dispatchEvent(new MessageEvent('message',{data:JSON.stringify({type:'session_history_page',data:{
  id:'s',action:'load_session',request_id:request.request_id,
  messages:[{id:'old',role:'user',content:'earlier'}],history:{head_id:'head',before:null},
 }})}));
 await load;
 assert.deepEqual(useSessionStore.getState().messageOrder.s,['old','live']);
 assert.equal(useSessionStore.getState().messagesById.live.content,'streamed newest');
 assert.equal(runtimeState.currentSessionId,'other');
 registerSessionHistory('s',{head_id:'head',before:'old'});
 const stale=loadOlderSessionHistory('s');const staleRequest=ws.sent.at(-1);
 registerSessionHistory('s',{head_id:'different',before:'new-before'});
 ws.dispatchEvent(new MessageEvent('message',{data:JSON.stringify({type:'session_history_page',data:{
  id:'s',action:'load_session',request_id:staleRequest.request_id,
  messages:[{id:'stale',role:'user',content:'wrong'}],history:{head_id:'head',before:null},
 }})}));
 await stale;
 assert.equal(useSessionStore.getState().messagesById.stale,undefined);
 assert.equal(useSessionHistory.getState().pages.s.before,'new-before');
 setSocket(null);
});

test('historical compaction loads full content without graph placeholders',async()=>{
 class Socket extends EventTarget {static OPEN=1;readyState=1;sent=[];send(t){this.sent.push(JSON.parse(t));}}
 globalThis.WebSocket=Socket; const ws=new Socket();setSocket(ws);
 runtimeState.currentSessionId='other';
 loadSessionData({id:'compacted',messages:[{id:'new',role:'user',content:'latest'}],history:{head_id:'new',before:'new'},graph:[{id:'sum',covers_ids:['old'],preview:'short preview',summarised_count:1}]});
 const pending=loadOlderSessionHistory('compacted');const request=ws.sent.at(-1);
 ws.dispatchEvent(new MessageEvent('message',{data:JSON.stringify({type:'session_history_page',data:{id:'compacted',action:'load_session',request_id:request.request_id,messages:[{id:'sum_card',role:'system',kind:'compaction',slot:'card',content:'FULL SUMMARY CONTENT',covers_ids:['old']}],history:{head_id:'new',before:null}}})}));
 await pending;
 assert.equal(runtimeState.conversations.compacted.messages.find(m=>m.id==='sum_card').content,'FULL SUMMARY CONTENT');
 setSocket(null);
});

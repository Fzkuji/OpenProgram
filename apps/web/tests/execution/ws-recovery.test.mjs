/** Exercise the production WebSocket hook; mock the browser transport and UI
 * side effects, keeping cursor, recovery and update ordering code real. */
import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { registerHooks } from 'node:module';
import { decideExecutionUpdateOrder } from '../../lib/net/execution/execution-update-order.ts';

const hookURL = new URL('../../lib/net/ws/use-ws.ts', import.meta.url).href;
const exportsByModule = new Map();
const source = readFileSync(new URL(hookURL), 'utf8');
for (const match of source.matchAll(/import\s+(?!type\b)\{([^}]+)\}\s+from\s+["']([^"']+)["']/g)) {
  exportsByModule.set(match[2], [...(exportsByModule.get(match[2])??[]), ...match[1].split(',').map(s=>s.trim()).filter(s=>s && !s.startsWith('type '))]);
}
exportsByModule.set('@/lib/chat/send/send-queue', ['useSendQueue','reconcileAfterSessionLoad']);
// Lazily imported by frame handlers; stubbed like the static imports.
exportsByModule.set('@/lib/desktop/attention', ['reportExecutionStatus','reportAttention','reportNotifiesDesktop']);
exportsByModule.set('@/lib/chat/conversation-work', ['notifyConversationWorkChanged']);
exportsByModule.set('@/lib/chat/transcript/todo-plan', ['useTodoStore']);
const real = new Set(['@/lib/runtime-bridge/session/session-load','../execution/execution-recovery','./history-fragments','../execution/execution-message-recovery','@/lib/net/execution/execution-cursor']);
const hooks=registerHooks({resolve(specifier, context, next) {
  // Recovery has no unsaved composer settings; keep the production read owner.
  if(context.parentURL?.endsWith('/runtime-bridge/session/session-load.ts') && specifier==='@/lib/agents/branch-settings') return {url:'data:text/javascript,export const pendingBranchSettings=()=>undefined;',shortCircuit:true};
  if(context.parentURL?.endsWith('/runtime-bridge/session/session-load.ts') && specifier==='@/lib/net/ws/text-frames') return {url:'data:text/javascript,export const flushTextFrames=()=>{};',shortCircuit:true};
  if(specifier==='@/lib/net/ws/session-load') return {url:new URL('../../lib/net/ws/session-load.ts',import.meta.url).href,shortCircuit:true};
  if(context.parentURL?.endsWith('/runtime-bridge/session/session-load.ts') && (specifier==='../state'||specifier==='@/lib/session-store')) return {url:'data:text/javascript,'+encodeURIComponent(specifier==='../state'?'export const runtimeState=globalThis.__wsFixture.exports.runtimeState; export const getSocket=()=>runtimeState.ws;':'export const useSessionStore=globalThis.__wsFixture.exports.useSessionStore;'),shortCircuit:true};
  if(context.parentURL===hookURL && real.has(specifier)) {
    const url=specifier.startsWith('@/')?new URL(`../../${specifier.slice(2)}.ts`,import.meta.url).href:new URL(`${specifier}.ts`,hookURL).href;
    return {url,shortCircuit:true};
  }
  if(context.parentURL===hookURL && exportsByModule.has(specifier)) {
    const names=exportsByModule.get(specifier);
    return {url:'data:text/javascript,'+encodeURIComponent(names.map(n=>`export const ${n}=globalThis.__wsFixture.exports[${JSON.stringify(n)}] ?? (()=>undefined);`).join('\n')),shortCircuit:true};
  }
  return next(specifier,context);
}});
const jobs = new Map();let timerId=0;
const realSetTimeout=globalThis.setTimeout, realClearTimeout=globalThis.clearTimeout;
const realSetInterval=globalThis.setInterval, realClearInterval=globalThis.clearInterval;
const state={currentSessionId:'s',_optimisticCancels:{},conversations:{}};
const orders={};
const store={messageOrder:{},setTranscriptReadStatus(){},runningTasks:{},rowsByView:{},pendingDecisions:[],composerSettingsBySession:{},
  acceptExecutionUpdate(id,seq,status,sid,ids){const d=decideExecutionUpdateOrder(orders[id],seq,status,sid,ids);if(d.next)orders[id]=d.next;return d.accepted;},
  setRunningTaskFor(sid,task){store.runningTasks[sid]=task;},setAdditionalWorkingDirs(){},setComposerSettings(){},dequeueDecision(){},enqueueDecision(){},
};
let cleanup;const loaded=[];const connections=[];
const attentionRequests=[];let inDesktopApp=false;
globalThis.__wsFixture={exports:{
  useEffect:fn=>{cleanup=fn();}, runtimeState:state, setSocket:s=>{state.ws=s;},
  useSessionStore:{getState:()=>store}, useSendQueue:{getState:()=>({queues:{}})},
  waitForOwnerAuthBootstrap:async()=>{},loadProgramsMeta:async()=>{},
  loadSessionData:d=>loaded.push(d.id), getQueryClient:()=>null,
  consumeCommandErrorFrame:()=>false,interfaceWindowId:()=> 'test-window',
  reportAttention:event=>{attentionRequests.push(event);return inDesktopApp;},reportNotifiesDesktop:frame=>frame.notify_desktop,
}};
const values=new Map([['openprogram.runtime.execution-cursors.v1',JSON.stringify(Array.from({length:20},(_,i)=>({execution_id:`old-${i}`,next_sequence:2,snapshot_status_version:1})))]]);
globalThis.window=new EventTarget();window.sessionStorage={getItem:k=>values.get(k)??null,setItem:(k,v)=>values.set(k,v)};
globalThis.location={protocol:'http:',host:'test.invalid'};
globalThis.document={visibilityState:'visible'};
globalThis.fetch=async()=>({status:200,ok:true,json:async()=>({questions:[]})});
class Socket extends EventTarget {
  static OPEN=1;readyState=1;sent=[];
  constructor(){super();connections.push(this);}
  send(raw){this.sent.push(JSON.parse(raw));}
  close(){this.readyState=3;this.onclose?.();}
  receive(frame){if(frame.type==='session_loaded' && !frame.data.request_id)frame.data.request_id=this.sent.findLast(r=>r.action==='load_session'&&r.session_id===frame.data.id)?.request_id;this.onmessage?.({data:JSON.stringify(frame)});}
}
globalThis.WebSocket=Socket;
const { useWS }=await import(hookURL);
const flush=async()=>{for(let i=0;i<12;i++)await Promise.resolve();};
// A frame handler's lazy import takes real turns of the event loop.
const settle=async()=>{for(let i=0;i<5;i++)await new Promise(resolve=>setImmediate(resolve));};
const tick=async(delay=0)=>{for(const[id,job]of [...jobs])if(job.delay===delay){jobs.delete(id);job.callback();}await flush();};
const replay=(id,status='completed',sequence=2)=>({type:'execution.replay',execution_id:id,data:{snapshot:{execution_id:id,session_id:'s',status,event_sequence:sequence},event_cursor:{execution_id:id,next_sequence:sequence+1,snapshot_status_version:sequence}}});

// First: the lazily imported stubs resolve only while the hooks are registered.
test('the desktop App answers a report it took; a browser or a muted report does not',async()=>{
  useWS();await flush();const socket=connections.at(-1);
  const report=(id,notify_desktop)=>({type:'always_on_report',data:{session_id:'s',agent_name:'Scout',kind:'finding',notify_desktop,report:{id,session_id:'s',title:'Found'}}});
  const answers=()=>socket.sent.filter(r=>r.action==='reminder_shown').map(r=>r.id);
  try {
    socket.receive(report('in-browser',true));await settle();
    assert.deepEqual(answers(),[],'a browser has no notification to claim, so the worker must post it');
    assert.equal(attentionRequests.at(-1).key,'always-on-report:in-browser');
    inDesktopApp=true;
    socket.receive(report('in-app',true));await settle();
    assert.deepEqual(answers(),['in-app']);
    const before=attentionRequests.length;
    socket.receive(report('muted',false));await settle();
    assert.equal(attentionRequests.length,before);
    assert.deepEqual(answers(),['in-app']);
  } finally {
    cleanup();inDesktopApp=false;
  }
});

test('mounted socket prioritizes history, never reloads on a terminal replay, and releases connection work',async()=>{
  globalThis.setTimeout=(callback,delay)=>{jobs.set(++timerId,{callback,delay});return timerId;};
  globalThis.clearTimeout=id=>jobs.delete(id);
  globalThis.setInterval=()=>++timerId;globalThis.clearInterval=()=>{};
  try {
    useWS();await flush();const first=connections.at(-1);first.onopen();await flush();await tick();
    assert.equal(first.sent.filter(r=>r.action==='load_session').length,1);
    assert.equal(first.sent.some(r=>r.action==='execution.replay'),false);
    first.receive({type:'session_loaded',data:{id:'s',messages:[],run_active:false}});await tick();
    assert.equal(first.sent.filter(r=>r.action==='execution.replay').length,1);
    assert.equal(first.sent.find(r=>r.action==='execution.replay').snapshot_only,true);
    for(let i=0;i<20;i++){first.receive(replay(`old-${i}`));await tick();}
    assert.equal(first.sent.filter(r=>r.action==='load_session').length,1,'twenty old completions must not reload transcript');
    assert.deepEqual(loaded,['s']);
    // A task observed running can finish while disconnected: its slot goes and its row comes as an update.
    store.runningTasks.s={execution_id:'was-live'};
    first.receive(replay('was-live'));await flush();
    assert.equal(first.sent.filter(r=>r.action==='load_session').length,1);
    first.close();await tick(2000);const second=connections.at(-1);assert.notEqual(second,first);
    second.onopen();second.receive({type:'session_loaded',data:{id:'s',messages:[]}});await tick();
    assert.equal(second.sent.some(r=>r.action==='execution.replay'),false,'terminal cursors must not replay again');
    const count=loaded.length;first.receive({type:'session_loaded',data:{id:'obsolete'}});assert.equal(loaded.length,count);
    store.runningTasks.background={execution_id:'gap-terminal'};
    second.receive({type:'execution.updated',execution:{execution_id:'gap-terminal',session_id:'background',status:'running',event_sequence:1},event_cursor:{execution_id:'gap-terminal',next_sequence:2,snapshot_status_version:1}});await flush();
    second.receive({type:'execution.updated',execution:{execution_id:'gap-terminal',session_id:'background',status:'completed',event_sequence:4},event_cursor:{execution_id:'gap-terminal',next_sequence:5,snapshot_status_version:4}});
    assert.ok(store.runningTasks.background,'gap terminal is held back until recovery');
    second.close();await tick(2000);const third=connections.at(-1);third.onopen();third.receive({type:'session_loaded',data:{id:'s',messages:[]}});await tick();
    assert.equal(third.sent.some(r=>r.action==='execution.replay'&&r.execution_id==='gap-terminal'),true,'a terminal frame not delivered to the store must remain recoverable after disconnect');
    cleanup();await tick();assert.equal(jobs.size,0);
  } finally {
    cleanup?.();globalThis.setTimeout=realSetTimeout;globalThis.clearTimeout=realClearTimeout;
    globalThis.setInterval=realSetInterval;globalThis.clearInterval=realClearInterval;hooks.deregister();
  }
});

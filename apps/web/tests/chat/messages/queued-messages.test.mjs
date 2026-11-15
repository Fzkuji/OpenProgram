import assert from 'node:assert/strict';
import test from "node:test";
import { join, dirname } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';
import { parseHTML } from 'linkedom';

import { scratchDir } from "../../scratch-dir.mjs";

const web = dirname(fileURLToPath(new URL('../../../package.json', import.meta.url)));
const dir = scratchDir(web, "queue-ui-test");
await build({absWorkingDir:web, stdin:{contents:`export { QueuedMessages } from './components/chat/messages/editing/queued-messages'; export { PendingSteers } from './components/chat/messages/bubbles/pending-steers'; export { useSendQueue, registerChatSender, settleDeliveredSteer } from './lib/chat/send/send-queue'; export { queuedMessagePayload } from './lib/chat/send/queued-attachments';`,resolveDir:web},bundle:true,format:'esm',platform:'node',packages:'external',outfile:join(dir,'test.mjs'),jsx:'automatic',tsconfig:join(web,'tsconfig.json'),plugins:[{name:'ports',setup(b){
 b.onResolve({filter:/^react$/},()=>({path:'react',external:true}));
 b.onResolve({filter:/\.module\.css$/},()=>({path:'css',namespace:'stub'}));
 b.onResolve({filter:/desktop-bridge$/},()=>({path:'bridge',namespace:'stub'}));
 b.onResolve({filter:/\/i18n$/},()=>({path:'i18n',namespace:'stub'}));
 b.onResolve({filter:/\/session-store$/},()=>({path:'session',namespace:'stub'}));
 b.onResolve({filter:/lazy-document-window$/},()=>({path:'viewer',namespace:'stub'}));
 b.onResolve({filter:/\/user-bubble$/},()=>({path:'bubble',namespace:'stub'}));
 b.onLoad({filter:/.*/,namespace:'stub'},({path})=>({contents:({
 css:`export default new Proxy({}, {get:(_,key)=>String(key)});`,
 bridge:`export const desktopBridge=()=>({getPathForFile:file=>'/fixture/'+file.name});`,
 i18n:`export const useTranslation=()=>({text:(en)=>en});`,
 session:`export const useSessionStore=selector=>selector(globalThis.sessions); useSessionStore.getState=()=>globalThis.sessions;`,
 bubble:`import {createElement} from 'react'; export const UserBubble=({msg})=>createElement('div',{'data-status':msg.status},msg.content);`,
 viewer:`import {createElement} from 'react'; export const DocumentWindow=props=>createElement('div',{'data-document-path':props.path,'data-document-session':props.sessionId},'Document preview');`,
 })[path]}));
}}]});
const {window}=parseHTML('<html><body></body></html>');
Object.assign(globalThis,{window,document:window.document,HTMLElement:window.HTMLElement,Element:window.Element,CustomEvent:window.CustomEvent,IS_REACT_ACT_ENVIRONMENT:true,requestAnimationFrame:cb=>cb()});
Object.defineProperty(globalThis,'localStorage',{value:{getItem:()=>null,setItem(){},removeItem(){}},configurable:true});
globalThis.sessions={activeChatKey:'A',currentSessionId:'A',wsStatus:'open',runningTasks:{A:{msg_id:'busy',reply_id:'busy_reply'}}};
window.location={pathname:'/s/A'};
const {createElement:h,act}=await import('react');
const {createRoot}=await import('react-dom/client');
const {QueuedMessages,PendingSteers,useSendQueue,registerChatSender,settleDeliveredSteer,queuedMessagePayload}=await import(pathToFileURL(join(dir,'test.mjs')));

test('queued bubble previews attachments and edits files without changing order or another draft',async()=>{
 const doc={id:'old',filename:'old.txt',ext:'txt',sizeBytes:3,content:'old',sourcePath:'/fixture/old.txt',order:0};
 const settings={thinking:'medium',webSearchEnabled:false,background:false};
 const id=useSendQueue.getState().enqueue('A',{...settings,text:'first',docs:[doc]});
 const second=useSendQueue.getState().enqueue('A',{...settings,text:'second'});
 useSendQueue.getState().enqueue('B',{...settings,text:'other session'});
 const host=document.createElement('div');document.body.append(host);const root=createRoot(host);
 const click=async el=>{assert.ok(el);await act(async()=>el.dispatchEvent(new window.Event('click',{bubbles:true})));};
 const button=name=>[...host.querySelectorAll('button')].find(el=>el.getAttribute('aria-label')===name || el.textContent===name);
 const sent=[];registerChatSender(args=>{sent.push(args);return true;});
 try {
 await act(async()=>root.render(h(QueuedMessages,{sessionId:'A'})));
 assert.equal(host.querySelectorAll('[data-queued-message]').length,2);
 assert.doesNotMatch(host.textContent,/other session/);
 // Rows are one line; clicking the text expands the message and its files.
 assert.equal(host.querySelector('[data-attachment-preview]'),null);
 await click(host.querySelector('[data-queued-message] button[aria-expanded]'));
 globalThis.sessions.currentSessionId='B'; // Preview remains local even while a peer has focus.
 await click(host.querySelector('[data-attachment-preview]'));
 assert.equal(Boolean(document.querySelector('[data-document-path]')),false, 'unsent native files must not request backend path access');
 assert.match(document.querySelector('[role="dialog"]').textContent,/old/);
 await click(document.querySelector('[aria-label="Close preview"]'));
 assert.equal(document.querySelector('[role="dialog"]'),null);
 await click(button('Edit queued message'));
 assert.equal(useSendQueue.getState().queues.A[0].editing,true);
 await click(host.querySelector('[data-attachment-remove]'));
 // The queue snapshot remains unchanged until Save.
 assert.equal(useSendQueue.getState().queues.A[0].docs[0].filename,'old.txt');
 const input=host.querySelector('input[type="file"]');
 Object.defineProperty(input,'files',{value:[{name:'new.txt',type:'text/plain',size:3,text:async()=> 'new'}],configurable:true});
 await act(async()=>input.dispatchEvent(new window.Event('change',{bubbles:true})));
 assert.match(host.textContent,/new.txt/);
 await click(button('Save'));
 assert.equal(useSendQueue.getState().queues.A[0].id,id);
 assert.equal(useSendQueue.getState().queues.A[0].docs[0].filename,'new.txt');
 assert.equal(useSendQueue.getState().queues.A[1].id,second);
 await click(button('Edit queued message'));
 await click(host.querySelector('[data-attachment-remove]'));
 await click(button('Cancel'));
 assert.equal(useSendQueue.getState().queues.A[0].docs[0].filename,'new.txt');
 // Native PDF bytes are preview-only and are snapshotted before drain.
 const pdfBytes=Buffer.from('%PDF-1.4\nfixture').toString('base64');
 globalThis.FileReader=class { readAsDataURL(){this.result='data:application/pdf;base64,'+pdfBytes;queueMicrotask(()=>this.onload());} };
 await click(button('Edit queued message'));
 const pdfInput=host.querySelector('input[type="file"]');
 Object.defineProperty(pdfInput,'files',{value:[{name:'local.pdf',type:'application/pdf',size:17}],configurable:true});
 await act(async()=>pdfInput.dispatchEvent(new window.Event('change',{bubbles:true})));
 await click(button('Save'));
 const queued=useSendQueue.getState().queues.A[0];
 assert.equal(queued.docs[1].dataB64,pdfBytes);
 assert.equal(queued.docs[1].loading,false);
 assert.equal(queuedMessagePayload(queued).attachments.length,0,'native PDF remains a path reference');
 await click([...host.querySelectorAll('[data-attachment-preview]')].find(el=>el.textContent.includes('local.pdf')));
 assert.match(document.querySelector('iframe')?.getAttribute('src') || '',/^blob:/);
 assert.equal(Boolean(document.querySelector('[data-document-path]')),false);
 await click(document.querySelector('[aria-label="Close preview"]'));
 await click(button('Remove from queue'));
 assert.equal(useSendQueue.getState().queues.A[0].id,second);
 assert.equal(useSendQueue.getState().queues.B[0].text,'other session');
 assert.equal(sent.length,0);
 await click(button('1 queued message'));
 assert.equal(host.querySelectorAll('[data-queued-message]').length,0);
 } finally {await act(async()=>root.unmount());host.remove();}
});


test('a row going into this turn leaves the card; a failed one comes back', async()=>{
 useSendQueue.setState({queues:{}});
 const id=useSendQueue.getState().enqueue('A',{text:'supplement',thinking:'medium',webSearchEnabled:false,background:false});
 const host=document.createElement('div');document.body.append(host);const root=createRoot(host);
 const patch=async data=>act(async()=>useSendQueue.getState().setSteering('A',id,data));
 const row=()=>host.querySelector(`[data-queued-message="${id}"]`);
 try {
 await act(async()=>root.render(h(QueuedMessages,{sessionId:'A'})));
 assert.ok(row(),'a Next turn row is in the card');
 await patch({injecting:true});
 assert.equal(row(),null,'the transcript shows it while the steer request is prepared');
 await patch({steerCommand:{command_id:'same'},injecting:false});
 assert.equal(row(),null,'and while its receipt is awaited');
 await patch({steerError:'unconfirmed'});
 assert.ok(row(),'an unconfirmed delivery is back in the card');
 const remove=[...row().querySelectorAll('button')].find(b=>b.getAttribute('aria-label')==='Remove from queue');
 assert.equal(remove.disabled,false,'and can be taken back');
 } finally {await act(async()=>root.unmount());host.remove();useSendQueue.setState({queues:{}});}
});

test('only rows for the next turn are listed and Clear all keeps rows still being delivered', async()=>{
 useSendQueue.setState({queues:{}});
 const settings={thinking:'medium',webSearchEnabled:false,background:false};
 const steering=useSendQueue.getState().enqueue('A',{...settings,text:'add this now'});
 useSendQueue.getState().enqueue('A',{...settings,text:'next one\nsecond line'});
 useSendQueue.getState().setSteering('A',steering,{injecting:true});
 const host=document.createElement('div');document.body.append(host);const root=createRoot(host);
 const sent=[];registerChatSender(args=>{sent.push(args);return true;});
 try {
 await act(async()=>root.render(h(QueuedMessages,{sessionId:'A'})));
 const rows=[...host.querySelectorAll('[data-queued-message]')];
 assert.equal(rows.length,1);
 assert.equal(rows[0].querySelector('[data-tone]').textContent,'Next turn');
 assert.equal(rows[0].querySelector('button[aria-expanded]').textContent,'next one');
 assert.match(host.textContent,/1 queued message/);
 assert.doesNotMatch(host.textContent,/adding to this turn/);
 const clear=[...host.querySelectorAll('button')].find(b=>b.textContent==='Clear all');
 await act(async()=>clear.dispatchEvent(new window.Event('click',{bubbles:true})));
 assert.deepEqual(useSendQueue.getState().queues.A.map(row=>row.id),[steering]);
 assert.equal(sent.length,0);
 } finally {await act(async()=>root.unmount());host.remove();useSendQueue.setState({queues:{}});}
});

test('a message going into this turn shows at the end of the running reply until the turn writes it', async()=>{
 useSendQueue.setState({queues:{}});
 const settings={thinking:'medium',webSearchEnabled:false,background:false};
 const steering=useSendQueue.getState().enqueue('A',{...settings,text:'the DMIT server was released'});
 useSendQueue.getState().enqueue('A',{...settings,text:'for later'});
 useSendQueue.getState().setSteering('A',steering,{steerCommand:{command_id:'cmd-1'}});
 const host=document.createElement('div');document.body.append(host);const root=createRoot(host);
 try {
 await act(async()=>root.render(h(PendingSteers,{sessionId:'A',replyId:'busy_reply'})));
 const bubbles=[...host.querySelectorAll('[data-pending-steer]')];
 assert.deepEqual(bubbles.map(el=>el.dataset.pendingSteer),[steering]);
 assert.equal(bubbles[0].querySelector('[data-status]').dataset.status,'pending');
 assert.match(bubbles[0].textContent,/the DMIT server was released/);
 await act(async()=>root.render(h(PendingSteers,{sessionId:'A',replyId:'another-reply'})));
 assert.equal(host.querySelector('[data-pending-steer]'),null,'only the reply that owns the turn shows it');
 await act(async()=>root.render(h(PendingSteers,{sessionId:'A',replyId:'busy_reply'})));
 await act(async()=>settleDeliveredSteer('A','cmd-1'));
 assert.equal(host.querySelector('[data-pending-steer]'),null,'the written message takes its place');
 assert.deepEqual(useSendQueue.getState().queues.A.map(row=>row.text),['for later']);
 } finally {await act(async()=>root.unmount());host.remove();useSendQueue.setState({queues:{}});}
});

test('rows reorder by keyboard, and rows being delivered or edited keep their slot', async()=>{
 useSendQueue.setState({queues:{}});
 const settings={thinking:'medium',webSearchEnabled:false,background:false};
 const [a,b,c,d]=['a','b','c','d'].map(t=>useSendQueue.getState().enqueue('A',{...settings,text:t}));
 const order=()=>useSendQueue.getState().queues.A.map(row=>row.id);
 // A drag hands over the whole visual order; the store keeps it.
 useSendQueue.getState().reorder('A',[c,a,b,d]);
 assert.deepEqual(order(),[c,a,b,d]);
 // Unknown ids are ignored and rows left out keep their relative order.
 useSendQueue.getState().reorder('A',['gone',d]);
 assert.deepEqual(order(),[d,c,a,b]);
 // A row being added to the turn holds its slot whatever the drag says.
 useSendQueue.getState().setSteering('A',c,{injecting:true});
 useSendQueue.getState().reorder('A',[c,b,a,d]);
 assert.deepEqual(order(),[b,c,a,d]);
 const host=document.createElement('div');document.body.append(host);const root=createRoot(host);
 const key=async(id,k)=>{const el=host.querySelector(`[data-queued-message="${id}"] button[aria-expanded]`);
  await act(async()=>el.dispatchEvent(Object.assign(new window.Event('keydown',{bubbles:true}),{key:k,altKey:true})));};
 try {
 await act(async()=>root.render(h(QueuedMessages,{sessionId:'A'})));
 // c is on its way into the turn: the transcript shows it, not the card.
 assert.deepEqual([...host.querySelectorAll('[data-queued-message]')].map(el=>el.dataset.queuedMessage),[b,a,d]);
 // Alt+↓ on b steps over the pinned row c.
 await key(b,'ArrowDown');
 assert.deepEqual(order(),[a,c,b,d]);
 await key(d,'ArrowUp');
 assert.deepEqual(order(),[a,c,d,b]);
 await key(a,'ArrowUp');
 assert.deepEqual(order(),[a,c,d,b], 'the first row cannot move up');
 } finally {await act(async()=>root.unmount());host.remove();useSendQueue.setState({queues:{}});}
});

test('rows show their enqueue time and offer Add to this turn', async()=>{
 useSendQueue.setState({queues:{}});
 const settings={thinking:'medium',webSearchEnabled:false,background:false};
 const waiting=useSendQueue.getState().enqueue('A',{...settings,text:'add me'});
 useSendQueue.getState().enqueue('A',{...settings,text:'later'});
 useSendQueue.getState().setSteering('A',waiting,{injecting:true});
 const queuedAt=useSendQueue.getState().queues.A[1].queuedAt;
 const host=document.createElement('div');document.body.append(host);const root=createRoot(host);
 try {
 await act(async()=>root.render(h(QueuedMessages,{sessionId:'A'})));
 const rows=host.querySelectorAll('[data-queued-message]');
 assert.equal(rows.length,1);
 assert.equal(rows[0].querySelector('.message-timestamp').textContent,new Date(queuedAt).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'}));
 const labels=row=>[...row.querySelectorAll('button[aria-label]')].map(b=>b.getAttribute('aria-label'));
 assert.ok(labels(rows[0]).includes('Add to this turn'));
 } finally {await act(async()=>root.unmount());host.remove();useSendQueue.setState({queues:{}});}
});

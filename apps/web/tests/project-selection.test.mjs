import assert from 'node:assert/strict';
import test from 'node:test';
import {parseHTML} from 'linkedom';
import {createElement as h,act} from 'react';
import {createRoot} from 'react-dom/client';
import {useProjectSelection} from '../components/sidebar/sessions-list/use-project-selection.ts';
const {window}=parseHTML('<html><body></body></html>');
globalThis.window=window;globalThis.document=window.document;globalThis.IS_REACT_ACT_ENVIRONMENT=true;

test('whole-row selection follows real and draft contexts without reviving stale selections',async()=>{
 const projects=[{id:'home',name:'Home',path:'/home',is_default:true},{id:'a',name:'A',path:'/a',is_default:false,session_ids:['chat-a']},{id:'b',name:'B',path:'/b',is_default:false,session_ids:['chat-b']}];
 const host=document.createElement('div');document.body.append(host);const root=createRoot(host);let selection;
 function Harness({current=null,chat=null,pending,registry=projects}){selection=useProjectSelection(registry,current,chat,pending);return h('div',null,selection.selectedProjectId);}
 const render=async props=>act(async()=>root.render(h(Harness,props)));
 await render({current:'chat-a',chat:'chat-a'});assert.equal(host.textContent,'a');
 await act(async()=>selection.selectProject('b'));assert.equal(host.textContent,'b');
 await render({current:'chat-b',chat:'chat-b'});assert.equal(host.textContent,'b');
 await render({current:'chat-a',chat:'chat-a'});assert.equal(host.textContent,'a');
 // setCurrentDraft leaves currentSessionId null: the distinct chat key owns pending choices.
 await render({chat:'local_draft-a',pending:'a'});assert.equal(host.textContent,'a');
 await act(async()=>selection.selectProject('home'));assert.equal(host.textContent,'home');
 await render({chat:'local_draft-b',pending:'b'});assert.equal(host.textContent,'b');
 await render({chat:'local_draft-a',pending:'a'});assert.equal(host.textContent,'a');
 await render({chat:'local_draft-a',pending:'b'});assert.equal(host.textContent,'b');
 await act(async()=>selection.selectProject('a'));await render({chat:'local_draft-a',pending:'b'});assert.equal(host.textContent,'a');
 await act(async()=>selection.clearProjectSelection());assert.equal(host.textContent,'b');
 await render({current:'chat-a',chat:'chat-a'});await act(async()=>selection.selectProject('a'));
 const moved=projects.map(p=>({...p,session_ids:p.id==='b'?['chat-a','chat-b']:[]}));
 await render({current:'chat-a',chat:'chat-a',registry:moved});assert.equal(host.textContent,'b');
 await render({current:'chat-a',chat:'chat-a'});assert.equal(host.textContent,'a');
 await act(async()=>root.unmount());host.remove();
});

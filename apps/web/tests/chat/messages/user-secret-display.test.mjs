import assert from 'node:assert/strict';
import test from 'node:test';
import {registerHooks} from 'node:module';
import {readFileSync,existsSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import ts from 'typescript';
import {act,createElement} from 'react';
import {createRoot} from 'react-dom/client';
import {parseHTML} from 'linkedom';
const rootUrl=new URL('../../../',import.meta.url);
const mocks={
 '@/lib/session-store':'export const useSessionStore=s=>s({currentSessionId:"A"});',
 '@/lib/i18n':'export const useTranslation=()=>({text:en=>en});',
 '@/components/avatar':'export const Avatar=()=>null;',
 '@/lib/prefs/user-profile':'export const useUserProfile=()=>({name:"User",avatar:{}});',
 './message-actions':'export const MessageActions=()=>null;',
 './use-avatar-align':'export const useAvatarAlign=()=>({containerRef:{current:null},avatarTop:0});',
 '../attachments/user-attachments':'export const AttachmentChips=()=>null;export const parseAttachments=text=>({attachments:[],text,recordings:[]});',
 '../../recording/recording-card':'export const RecordingCard=()=>null;',
 '../../rewind/rewind-context-menu':'export const RewindContextMenu=({children})=>children;',
 '../editing/message-editor':'export const MessageEditor=()=>null;',
 '@/components/ui/button':'export const buttonVariants=()=>"btn";',
 '@/components/solar-icons':'export const SolarIcon=()=>null;',
 '../list/transcript-view-scope':'export const useRowSessionId=own=>own??"A";',
 './markdown':'export const renderUserMarkdown=s=>s.replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;");',
};
registerHooks({resolve(s,c,next){
 if(Object.hasOwn(mocks,s))return {url:'data:text/javascript,'+encodeURIComponent(mocks[s]),shortCircuit:true};
 if(s.startsWith('@/')){const base=new URL(s.slice(2),rootUrl);for(const suffix of ['.ts','.tsx'])if(existsSync(fileURLToPath(base)+suffix))return {url:base.href+suffix,shortCircuit:true};}
 return next(s,c);
},load(url,c,next){if(url.endsWith('/user-bubble.tsx'))return {format:'module',source:ts.transpileModule(readFileSync(new URL(url),'utf8'),{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText,shortCircuit:true};return next(url,c);}});
const {document,window}=parseHTML('<html><body></body></html>');Object.assign(globalThis,{document,window,IS_REACT_ACT_ENVIRONMENT:true});
const {UserBubble}=await import('../../../components/chat/messages/bubbles/user-bubble.tsx');
const {elementsForBody}=await import('../../../lib/chat/send/paste-elements.ts');
const a='ghp_'+'A'.repeat(36),b='ghp_'+'B'.repeat(36);
async function mount(msg){const host=document.createElement('div');document.body.append(host);const root=createRoot(host);let sid='A';const render=()=>root.render(createElement(UserBubble,{msg,sessionIdOverride:sid}));await act(render);return {host,async click(selector){const button=host.querySelector(selector);assert.ok(button);await act(()=>button.dispatchEvent(new window.Event('click',{bubbles:true})));},async update(next,session=sid){msg=next;sid=session;await act(render);},async close(){await act(()=>root.unmount());host.remove();}};}
test('long private-key paste preview masks before truncating',async()=>{const key='-----BEGIN PRIVATE KEY-----\n'+'SYNTHETICPRIVATEBODY'.repeat(40)+'\n-----END PRIVATE KEY-----';const msg={id:'m',role:'user',content:key,pastedElements:elementsForBody(key,[{start:0,end:key.length,label:'Pasted text'}])};const v=await mount(msg);try{assert.ok(!v.host.querySelector('.user-paste-chip').title.includes('SYNTHETICPRIVATEBODY'));assert.equal(msg.content,key);}finally{await v.close();}});
test('expanding an earlier paste never reveals its secret by index reuse',async()=>{const content=a+' then '+b;const msg={id:'m',role:'user',content,pastedElements:elementsForBody(content,[{start:0,end:a.length,label:'Pasted text'}])};const v=await mount(msg);try{await v.click('.user-secret-toggle');assert.ok(v.host.textContent.includes(b));await v.click('.user-paste-chip');assert.ok(!v.host.textContent.includes(a));}finally{await v.close();}});
test('content and conversation changes hide earlier revealed values',async()=>{const msg={id:'m',role:'user',content:a};const v=await mount(msg);try{await v.click('.user-secret-toggle');assert.ok(v.host.textContent.includes(a));await v.update({...msg,content:b});assert.ok(!v.host.textContent.includes(b));await v.click('.user-secret-toggle');assert.ok(v.host.textContent.includes(b));await v.update({...msg,content:b},'B');assert.ok(!v.host.textContent.includes(b));await v.click('.user-secret-toggle');assert.ok(v.host.textContent.includes(b));await v.click('.user-secret-toggle');assert.ok(!v.host.textContent.includes(b));}finally{await v.close();}});

test('returning to previous content or message never restores a reveal', async () => {
  const msg = { id: 'first', role: 'user', content: a };
  const v = await mount(msg);
  try {
    await v.click('.user-secret-toggle');
    await v.update({ ...msg, content: b });
    await v.update(msg);
    assert.ok(!v.host.textContent.includes(a));
    await v.click('.user-secret-toggle');
    await v.update({ ...msg, id: 'second' });
    assert.ok(!v.host.textContent.includes(a));
    await v.update(msg);
    assert.ok(!v.host.textContent.includes(a));
  } finally {
    await v.close();
  }
});

test('a send waiting for its ACK is faded; one that never got it says Not sent and offers Retry', async () => {
  const v = await mount({ id: 'pending_1', role: 'user', content: 'hello', status: 'pending' });
  try {
    assert.ok(v.host.querySelector('.message.user.is-pending'));
    assert.equal(v.host.querySelector('.user-not-sent'), null);
    await v.update({ id: 'pending_1', role: 'user', content: 'hello', status: 'error', errorReason: 'not_sent' });
    assert.equal(v.host.querySelector('.is-pending'), null);
    assert.match(v.host.querySelector('.user-not-sent').textContent, /Not sent\s*Retry/);
    await v.update({ id: 'pending_1', role: 'user', content: 'hello', status: 'done' });
    assert.equal(v.host.querySelector('.user-not-sent'), null);
  } finally { await v.close(); }
});

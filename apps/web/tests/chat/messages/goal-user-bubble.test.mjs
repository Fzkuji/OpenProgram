// The message that sets a Goal is one bubble: the Goal label opens its
// first line and the objective follows on that line. The label is drawn
// from the row's startsGoal mark, never from the text, and is not part of
// the message. The bubble renders with the real renderUserMarkdown, whose
// output is a `.md-rendered` grid: a label beside that grid could never
// share its first line, which is how the label once sat on a line alone.
import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import {installLoader,webRoot} from '../../_support/load.mjs';
import {installDom} from '../../_support/dom.mjs';

installLoader({stubs:{
  '@/lib/session-store':'export const useSessionStore=s=>s({currentSessionId:"A"});',
  '@/lib/i18n':'export const useTranslation=()=>({text:(en,zh)=>globalThis.__lang==="zh"?zh:en});',
  '@/lib/prefs/user-profile':'export const useUserProfile=()=>({name:"User",avatar:{}});',
  './message-actions':'export const MessageActions=({msg,onEdit})=>{globalThis.__actions={msg,onEdit};return null;};',
  '../attachments/user-attachments':'export const AttachmentChips=()=>null;export const parseAttachments=text=>({attachments:[],text,recordings:[]});',
  '../../recording/recording-card':'export const RecordingCard=()=>null;',
  '../../rewind/rewind-context-menu':'export const RewindContextMenu=({children})=>children;',
  '../editing/message-editor':'import {createElement} from "react";export const MessageEditor=({msg})=>createElement("textarea",{className:"editor",defaultValue:msg.content});',
  '@/components/ui/button':'export const buttonVariants=()=>"btn";',
  '@/components/solar-icons':'import {createElement} from "react";export const SolarIcon=({name,size})=>createElement("span",{"data-icon":name,"data-size":String(size)});',
  '../list/transcript-view-scope':'export const useRowSessionId=own=>own??"A";',
}});
installDom();
const {act,createElement}=await import('react');
const {createRoot}=await import('react-dom/client');
const {UserBubble,splitLeadParagraph}=await import('../../../components/chat/messages/bubbles/user-bubble.tsx');
const {renderUserMarkdown}=await import('../../../components/chat/messages/bubbles/markdown.ts');

async function mount(msg){
  const host=document.createElement('div');document.body.append(host);
  const root=createRoot(host);
  await act(()=>root.render(createElement(UserBubble,{msg,sessionIdOverride:'A'})));
  return {host,async close(){await act(()=>root.unmount());host.remove();}};
}

const objective='Ship the release notes for 2.4 and check every link';
const text=el=>[...el.querySelectorAll('.user-md')].map(n=>n.textContent).join('');

test('the rendered markdown is a .md-rendered wrapper, the structure the label must live with',()=>{
  const html=renderUserMarkdown(objective);
  assert.ok(html.startsWith('<span class="md-rendered"><p>'),html);
});

test('the Goal label and the objective share one paragraph',async()=>{
  const v=await mount({id:'g',role:'user',rowKind:'user',content:objective,startsGoal:true});
  try{
    const content=v.host.querySelector('.message-content');
    assert.equal(content.querySelectorAll('.user-goal').length,1);
    const lead=content.querySelector('.user-goal > p.user-goal-lead');
    assert.ok(lead,'the opening paragraph holds the label');
    const label=lead.firstElementChild;
    assert.equal(label.className,'user-goal-label');
    assert.equal(label.querySelector('[data-icon]').dataset.icon,'target');
    assert.equal(label.querySelector('[data-icon]').dataset.size,'16');
    assert.equal(label.textContent,'Goal');
    assert.equal(label.getAttribute('title'),null);
    // The objective is inline in that same paragraph: no block, no grid in between.
    const body=label.nextElementSibling;
    assert.equal(body.tagName,'SPAN');
    assert.equal(body.textContent,objective);
    assert.equal(lead.querySelector('.md-rendered, p, div'),null);
    assert.equal(lead.parentElement.querySelectorAll('.md-rendered').length,0);
    assert.equal(text(content),objective);
  }finally{await v.close();}
});

test('blocks after the opening paragraph follow as in any message',async()=>{
  const src='First line of the goal\n\nSecond paragraph\n\n- one\n- two\n\n```\nx = 1\n```';
  const v=await mount({id:'g',role:'user',rowKind:'user',content:src,startsGoal:true});
  try{
    const goal=v.host.querySelector('.user-goal');
    assert.equal(goal.querySelector('.user-goal-lead > .user-md').textContent,'First line of the goal');
    const rest=goal.querySelector('.user-goal-lead + .user-md > .md-rendered');
    assert.ok(rest,'the rest is the usual markdown grid');
    const kids=[...rest.children].map(el=>el.tagName.toLowerCase()+(el.classList.contains('md-code')?'.md-code':''));
    assert.equal(kids[0],'p');
    assert.equal(kids[1],'ul');
    assert.ok(rest.querySelector('.md-code'),kids.join());
    assert.equal(rest.firstElementChild.textContent,'Second paragraph');
  }finally{await v.close();}
});

test('a goal that opens with a list keeps the label on its own line above the list',async()=>{
  const v=await mount({id:'g',role:'user',rowKind:'user',content:'- one\n- two',startsGoal:true});
  try{
    const lead=v.host.querySelector('.user-goal-lead');
    assert.equal(lead.textContent,'Goal');
    assert.equal(lead.nextElementSibling.querySelector('.md-rendered > ul').textContent.replace(/\s+/g,''),'onetwo');
  }finally{await v.close();}
});

test('splitLeadParagraph lifts only the opening paragraph',()=>{
  const md='<span class="md-rendered">';
  assert.deepEqual(splitLeadParagraph(md+'<p>a <b>b</b></p></span>'),{lead:'a <b>b</b>',rest:''});
  assert.deepEqual(splitLeadParagraph(md+'<p>a</p>\n<p>b</p></span>'),{lead:'a',rest:md+'<p>b</p></span>'});
  assert.deepEqual(splitLeadParagraph(md+'<ul><li>a</li></ul></span>'),{lead:null,rest:md+'<ul><li>a</li></ul></span>'});
  assert.deepEqual(splitLeadParagraph('plain &amp; escaped'),{lead:'plain &amp; escaped',rest:''});
});

test('the label speaks the interface language',async()=>{
  globalThis.__lang='zh';
  const v=await mount({id:'g',role:'user',rowKind:'user',content:objective,startsGoal:true});
  try{assert.equal(v.host.querySelector('.user-goal-label').textContent,'目标');}
  finally{globalThis.__lang=undefined;await v.close();}
});

test('a message without the mark has no label, even when its text names a goal',async()=>{
  const v=await mount({id:'p',role:'user',rowKind:'user',content:'Goal: '+objective});
  try{
    assert.equal(v.host.querySelector('.user-goal'),null);
    assert.equal(v.host.querySelector('.user-goal-label'),null);
    assert.ok(v.host.querySelector('.user-md > .md-rendered > p'));
    assert.equal(v.host.querySelector('.user-md').textContent.trim(),'Goal: '+objective);
  }finally{await v.close();}
});

test('copy and edit see the objective alone',async()=>{
  const msg={id:'g',role:'user',rowKind:'user',content:objective,startsGoal:true};
  const v=await mount(msg);
  try{
    assert.equal(globalThis.__actions.msg.content,objective);
    await act(()=>globalThis.__actions.onEdit());
    assert.equal(v.host.querySelector('.user-goal-label'),null);
    assert.equal(v.host.querySelector('.editor').value??v.host.querySelector('.editor').getAttribute('value'),objective);
  }finally{await v.close();}
});

test('the label keeps to theme tokens; the rest sits a paragraph and a grid gap below',()=>{
  const css=readFileSync(new URL('app/styles/chat/messages/bubbles.css',webRoot),'utf8');
  const rule=css.match(/\.message\.user \.user-goal-label \{([^}]*)\}/)?.[1]??'';
  assert.match(rule,/color: var\(--accent-blue\)/);
  assert.match(rule,/font-weight: 500/);
  assert.doesNotMatch(rule,/font-size|#[0-9a-f]{3}|display/i);
  assert.match(css,/\.message\.user \.user-goal-lead \{ margin: 0; \}/);
  assert.match(css,/\.message\.user \.user-goal-lead \+ \.user-md \{ margin-top: calc\(8px \+ var\(--reply-gap\)\); \}/);
  // Nothing may turn the markdown grid itself inline: that selector never matched.
  assert.doesNotMatch(css,/\.user-goal > \.user-md/);
});

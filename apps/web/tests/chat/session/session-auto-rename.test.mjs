import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync, existsSync } from 'node:fs';
import { registerHooks } from 'node:module';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';
import { parseHTML } from 'linkedom';
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
const rootURL = new URL('../../../', import.meta.url);
const messages = [];
globalThis.renameToasts = messages;
registerHooks({
  resolve(s, c, next) {
    const mocks = {
      '@/lib/i18n': 'export const translateText = en => en; export const useTranslation = () => ({t: k => k})',
      '@/lib/format-utils/toast': 'export const showToast = m => globalThis.renameToasts.push(m)',
      '@/lib/runtime-bridge/state': 'export const getSocket = () => globalThis.renameSocket',
    };
    if (mocks[s]) return {url: 'data:text/javascript,'+encodeURIComponent(mocks[s]), shortCircuit:true};
    const base = s.startsWith('@/') ? new URL(s.slice(2),rootURL).href : s.startsWith('.') && !/\.[a-z]+$/i.test(s) ? new URL(s,c.parentURL).href : null;
    if (base) for (const suffix of ['.ts','.tsx','/index.ts']) if (existsSync(fileURLToPath(base+suffix))) return {url:base+suffix,shortCircuit:true};
    return next(s,c);
  },
  load(url,c,next) {
    if (url.endsWith('.tsx')) return {format:'module',shortCircuit:true,source:ts.transpileModule(readFileSync(fileURLToPath(url),'utf8'),{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.ESNext}}).outputText};
    return next(url,c);
  },
});
const {window} = parseHTML('<!doctype html><html><body></body></html>');
Object.assign(globalThis,{window,document:window.document,IS_REACT_ACT_ENVIRONMENT:true,WebSocket:{OPEN:1},
  // Radix's menu measures and observes its popper; linkedom has neither.
  getComputedStyle:()=>({animationName:'none',display:'block'}),
  ResizeObserver:class{observe(){}unobserve(){}disconnect(){}},
  Event:window.Event,CustomEvent:window.CustomEvent});
const {autoRenameSession} = await import('../../../lib/chat/session-auto-rename.ts');
const {ConvMenu} = await import('../../../components/sidebar/sessions/conv-menu.tsx');
const {DropdownMenu} = await import('@radix-ui/react-dropdown-menu').then(m=>({DropdownMenu:m.Root}));
function socket() {
  messages.length=0;
  const listeners=new Map(), frames=[];
  globalThis.renameSocket={readyState:1,send:s=>frames.push(JSON.parse(s)),addEventListener:(n,f)=>{if(!listeners.has(n))listeners.set(n,new Set());listeners.get(n).add(f)},removeEventListener:(n,f)=>listeners.get(n)?.delete(f)};
  return {frames,reply(frame,status){for(const f of [...listeners.get('message')||[]]) f({data:JSON.stringify({type:'session_rename_result',data:{...frame,status}})});}, close(){for(const f of [...listeners.get('close')||[]])f();}};
}
test('menu exposes Auto rename directly after Rename and invokes its own action',async()=>{
  let automatic=0,manual=0,closed=0;
  const host=document.createElement('div'); document.body.append(host); const root=createRoot(host);
  const noop=()=>{};
  try {
    // The menu's Radix root lives in the row (ConvItem); selecting an item closes it through onOpenChange.
    await act(async()=>root.render(React.createElement(DropdownMenu,{open:true,modal:false,onOpenChange:open=>{if(!open)closed++;}},React.createElement(ConvMenu,{conv:{id:'A'},groups:[],onRename:()=>manual++,onAutoRename:()=>automatic++,onClose:noop,onTogglePin:noop,onToggleArchive:noop,onMoveToGroup:noop,onNewGroup:noop,onCopyLink:noop,onExport:noop,onDelete:noop}))));
    const buttons=[...document.querySelectorAll('[role=menuitem]')];
    assert.match(buttons[0].textContent,/sidebar.rename/);
    assert.match(buttons[1].textContent,/sidebar.auto_rename/);
    await act(async()=>buttons[1].click());
    assert.equal(automatic,1);assert.equal(manual,0);assert.equal(closed,1);
  } finally {await act(async()=>root.unmount());host.remove();}
});
test('deduplicates clicks and correlates responses across sessions',async()=>{
  const s=socket();const a=autoRenameSession('A');await autoRenameSession('A');const b=autoRenameSession('B');
  assert.equal(s.frames.length,2);assert.equal(s.frames[0].title,undefined);
  s.reply({...s.frames[0],request_id:'wrong'},'ok');assert.equal(messages.filter(m=>m==='Conversation renamed').length,0);
  s.reply(s.frames[1],'failed');await b;s.reply(s.frames[0],'ok');await a;
  assert.equal(messages.filter(m=>m==='Conversation renamed').length,1);
  assert.ok(messages.some(m=>m.includes('previous title is unchanged')));
});
test('disconnect reports uncertainty and releases pending action',async()=>{
  const s=socket();const pending=autoRenameSession('A');s.close();await pending;
  assert.match(messages.at(-1),/not confirmed.*Reload the page/);
  const again=autoRenameSession('A');assert.equal(s.frames.length,2);s.reply(s.frames[1],'superseded');await again;
  assert.match(messages.at(-1),/was not applied/);
});

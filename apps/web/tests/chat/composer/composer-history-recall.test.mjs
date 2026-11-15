import assert from 'node:assert/strict';
import { registerHooks } from 'node:module';
import test from 'node:test';
import { act, createElement } from 'react';
import { createRoot } from 'react-dom/client';
import { parseHTML } from 'linkedom';
const host = globalThis.__historyProbe = { currentSessionId: 'A', messageOrder: { A: ['a'], B: ['b'] }, rowsById: { a: {role:'user',content:'old A'}, b:{role:'user',content:'old B'} } };
registerHooks({resolve(s,c,next){
 if(s==='@/lib/session-store') return {url:'data:text/javascript,'+encodeURIComponent('export const useSessionStore = select => select(globalThis.__historyProbe); export const selectViewRows = () => state => state.rowsById;'),shortCircuit:true};
 return next(s,c);
}});
const {document,window}=parseHTML('<html><body></body></html>');
Object.assign(globalThis,{document,window,IS_REACT_ACT_ENVIRONMENT:true});
const frames=new Map();let frame=0;
globalThis.requestAnimationFrame=cb=>{frames.set(++frame,cb);return frame;};
globalThis.cancelAnimationFrame=id=>frames.delete(id);
const {useHistoryRecall,recallText,caretOnFirstVisualLine}=await import('../../../components/chat/composer/input/use-history-recall.ts');
const {useComposerKeyDown}=await import('../../../components/chat/composer/input/use-composer-keydown.ts');
async function mount(draft='unfinished draft'){
 let hook,keydown,input=draft,bound=null,current='A';host.currentSessionId='A';
 const container=document.createElement('div');document.body.append(container);const root=createRoot(container);
 const ta={selectionStart:0,selectionEnd:0,setSelectionRange(){}};
 function Probe(){hook=useHistoryRecall(bound,current,input);keydown=useComposerKeyDown({input,setInput:v=>{input=v;},historyRecall:hook,fileMention:{},slash:{visible:false}});return null;}
 const render=()=>root.render(createElement(Probe));await act(render);
 return {get input(){return input;},get index(){return hook.historyIndex;},ta,
  async key(key,extra={}){await act(()=>{keydown({key,nativeEvent:{},currentTarget:ta,preventDefault(){},...extra});render();});},
  async edit(value){await act(()=>{input=value;render();});},
  async scope(b,c){bound=b;current=c;host.currentSessionId=c;await act(render);},
  async close(){await act(()=>root.unmount());container.remove();frames.clear();}
 };
}
test('Up then Down restores the exact unsent draft',async()=>{const v=await mount('  unfinished\n草稿  ');try{await v.key('ArrowUp');assert.equal(v.input,'old A');await v.key('ArrowDown');assert.equal(v.input,'  unfinished\n草稿  ');}finally{await v.close();}});
test('editing recalled text starts a new draft-preserving cycle',async()=>{const v=await mount();try{await v.key('ArrowUp');await v.edit('revised old A');await v.key('ArrowUp');await v.key('ArrowDown');assert.equal(v.input,'revised old A');}finally{await v.close();}});
test('a bound composer ignores another pane focus but resets on its own owner change',async()=>{const v=await mount();try{await v.scope('A','A');await v.key('ArrowUp');await v.scope('A','B');assert.equal(v.index,0);await v.key('ArrowDown');assert.equal(v.input,'unfinished draft');await v.key('ArrowUp');await v.scope('B','B');assert.equal(v.index,-1);}finally{await v.close();}});
test('modifier arrows and selected text do not replace editor content',async()=>{const v=await mount();try{await v.key('ArrowUp',{ctrlKey:true});assert.equal(v.input,'unfinished draft');v.ta.selectionEnd=4;await v.key('ArrowUp');assert.equal(v.input,'unfinished draft');}finally{await v.close();}});

test('selected recalled text and Ctrl+Down preserve editing',async()=>{const v=await mount();try{await v.key('ArrowUp');await v.key('ArrowDown',{ctrlKey:true});assert.equal(v.input,'old A');v.ta.selectionEnd=3;await v.key('ArrowDown');assert.equal(v.input,'old A');}finally{await v.close();}});
test('stale caret frames cannot move a new draft or survive owner change',async()=>{const v=await mount();try{let moves=0;v.ta.setSelectionRange=()=>moves++;await v.key('ArrowUp');await v.edit('new draft');for(const cb of frames.values())cb();assert.equal(moves,0);frames.clear();await v.key('ArrowUp');await v.scope('B','B');assert.equal(frames.size,0);await v.scope('A','A');assert.equal(v.index,-1);}finally{await v.close();}});

test('editing then undoing to recalled text cannot reactivate an old cycle',async()=>{const v=await mount();try{await v.key('ArrowUp');await v.edit('changed');await v.edit('old A');assert.equal(v.index,-1);await v.key('ArrowDown');assert.equal(v.input,'old A');await v.key('ArrowUp');await v.key('ArrowDown');assert.equal(v.input,'old A');}finally{await v.close();}});

test('recall engages only on the first visual row of a wrapped paragraph',()=>{
 const rows=pos=>({top:pos<40?0:20,bottom:pos<40?20:40});
 const value='x'.repeat(80);
 assert.equal(caretOnFirstVisualLine({selectionStart:10,coordsAt:rows},value),true);
 assert.equal(caretOnFirstVisualLine({selectionStart:60,coordsAt:rows},value),false);
 assert.equal(caretOnFirstVisualLine({selectionStart:3},'abc\ndef'),true);
 assert.equal(caretOnFirstVisualLine({selectionStart:5},'abc\ndef'),false);
});
test('a recalled message leaves out the attachment markers added at send',()=>{
 assert.equal(recallText('[attachment: a.txt (txt, 1 KB) @json "/w/a.txt"]\n\nsummarise this'),'summarise this');
 assert.equal(recallText('  plain  '),'plain');
});

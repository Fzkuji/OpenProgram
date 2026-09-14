import assert from 'node:assert/strict';
import test from 'node:test';
import {HistoryWindow,HISTORY_WINDOW_PAGES,HISTORY_WINDOW_BYTES} from '../../lib/chat/history-window.ts';
const total=300000;
function page(start, size=50, text='content') {
 const end=Math.min(total,start+size);
 return {messages:Array.from({length:end-start},(_,i)=>({id:`m${start+i}`,content:text})),history:{snapshot:'s',head_id:'head',start,end,total,before:start?`m${start}`:null,after:end<total?`m${end-1}`:null}};
}
const put=(window,p,d,anchor)=>window.add(p.messages,p.history,d,anchor);
test('300000-message source traverses both directions with bounded data and exact cursors',()=>{
 const window=new HistoryWindow();
 for(let start=total-50;start>=0;start-=50){
  put(window,page(start),'older');
  assert.ok(window.pageCount<=HISTORY_WINDOW_PAGES);
  assert.ok(window.messages.length<=300);
  assert.equal(window.history.start,start);
  assert.ok(window.bytes<=HISTORY_WINDOW_BYTES);
 }
 for(let start=window.history.end;start<total;start+=50)put(window,page(start),'newer');
 assert.equal(window.history.end,total);assert.equal(window.history.after,null);
 assert.equal(window.pageCount,HISTORY_WINDOW_PAGES);
});
test('eviction preserves the reader anchor and snapshot replacement releases stale pages',()=>{
 const window=new HistoryWindow();
 for(let start=0;start<300;start+=50)put(window,page(start),'newer');
 put(window,page(300),'newer','m0');
 assert.equal(window.history.start,0);assert.equal(window.messages.some(m=>m.id==='m0'),true);
 const replacement=page(500);replacement.history.snapshot='new';put(window,replacement,'around');
 assert.equal(window.pageCount,1);assert.equal(window.history.start,500);
});
test('byte eviction keeps indivisible oversized turns readable',()=>{
 const window=new HistoryWindow();
 for(let start=0;start<4;start++)put(window,page(start,1,'中'.repeat(1000000)),'newer');
 assert.ok(window.bytes<=HISTORY_WINDOW_BYTES);assert.equal(window.pageCount,2);
 put(window,page(4,1,'x'.repeat(HISTORY_WINDOW_BYTES+1)),'newer');
 assert.equal(window.pageCount,1);assert.equal(window.messages[0].id,'m4');
});

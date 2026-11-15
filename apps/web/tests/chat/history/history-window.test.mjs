import assert from 'node:assert/strict';
import test from 'node:test';
import {HistoryWindow,HISTORY_WINDOW_PAGES,HISTORY_WINDOW_BYTES} from '../../../lib/chat/history/history-window.ts';
const total=300000;
const turnsFrom=(start,size=25,text='content')=>{
 const end=Math.min(total,start+size);
 return {turns:Array.from({length:end-start},(_,i)=>({id:`t${start+i}`,rows:[{id:`t${start+i}`,content:text}]})),
  window:{head_id:'head',revision:7,start,end,total,before:start?`t${start}`:null,after:end<total?`t${end-1}`:null}};
};
const place=(w,p,d,anchor)=>w.addTurns(p.turns,p.window,d,anchor);
test('300000 one-row turns traverse both directions with bounded data and exact cursors',()=>{
 const window=new HistoryWindow();place(window,turnsFrom(total-25),'latest');
 for(let start=total-50;start>=0;start-=25){
  assert.equal(place(window,turnsFrom(start),'older'),true);
  assert.ok(window.pageCount<=HISTORY_WINDOW_PAGES);assert.equal(window.history.start,start);assert.ok(window.rowIds.length<=150);
 }
 assert.equal(window.history.before,null);
 for(let start=window.history.end;start<total;start+=25)assert.equal(place(window,turnsFrom(start),'newer'),true);
 assert.equal(window.history.end,total);assert.equal(window.history.after,null);
});
test('a page is placed by position and one that touches nothing held is dropped',()=>{
 const window=new HistoryWindow();place(window,turnsFrom(100),'latest');
 assert.equal(place(window,turnsFrom(500),'newer'),false);assert.deepEqual([window.history.start,window.history.end],[100,125]);
 assert.equal(place(window,turnsFrom(75),'older'),true);assert.deepEqual([window.history.start,window.history.end],[75,125]);
 assert.equal(window.rowIds[0],'t75');assert.equal(window.rowIds.at(-1),'t124');
});
test('an overlapping page adds only the turns not held',()=>{
 const window=new HistoryWindow();place(window,turnsFrom(100),'latest');place(window,turnsFrom(90),'older');
 assert.equal(window.rowIds.length,35);assert.equal(new Set(window.rowIds).size,35);assert.equal(window.history.start,90);
});
test('a page reply never moves the revision; replacing the window sets it',()=>{
 const window=new HistoryWindow();const first=turnsFrom(100);first.window.revision=3;place(window,first,'latest');
 const older=turnsFrom(75);older.window.revision=9;place(window,older,'older');assert.equal(window.history.revision,3);
 const replaced=turnsFrom(500);replaced.window.revision=11;place(window,replaced,'around');assert.equal(window.history.revision,11);assert.equal(window.pageCount,1);
});
test('eviction drops whole pages of turns and keeps the reader anchor',()=>{
 const window=new HistoryWindow();place(window,turnsFrom(0),'latest');
 for(let start=25;start<=25*HISTORY_WINDOW_PAGES;start+=25)place(window,turnsFrom(start),'newer','t0');
 assert.equal(window.history.start,0);assert.equal(window.pageCount,HISTORY_WINDOW_PAGES);assert.ok(window.rowIds.includes('t0'));
});
test('byte eviction keeps indivisible oversized turns readable',()=>{
 const window=new HistoryWindow();place(window,turnsFrom(0,1,'中'.repeat(1000000)),'latest');
 for(let start=1;start<4;start++)place(window,turnsFrom(start,1,'中'.repeat(1000000)),'newer');
 assert.ok(window.bytes<=HISTORY_WINDOW_BYTES);assert.equal(window.pageCount,2);
 place(window,turnsFrom(4,1,'x'.repeat(HISTORY_WINDOW_BYTES+1)),'newer');
 assert.equal(window.pageCount,1);assert.equal(window.rowIds[0],'t4');
});

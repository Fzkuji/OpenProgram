"""Large virtual source, bounded real transcript, delayed bidirectional transport."""
from pathlib import Path
import subprocess
import pytest
ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.browser


def test_large_history_fast_scroll_eviction_restore_and_latest(tmp_path):
    from playwright.sync_api import sync_playwright, expect
    entry = r'''
import React from 'react';import {createRoot} from 'react-dom/client';
import {MessageList} from './components/chat/messages/message-list';
import {useSessionStore} from './lib/session-store';
import {registerSessionHistory,useSessionHistory} from './lib/chat/session-history';
import {seedHistoryWindow,loadSessionHistoryWindow} from './lib/runtime-bridge/session-history-loader';
import {runtimeState,setSocket} from './lib/runtime-bridge/state';
import {heightsFor} from './lib/chat/message-window';
import {readHistoryAnchor} from './lib/chat/history-viewport';
const total=300000;
const rows=(start,end)=>Array.from({length:end-start},(_,i)=>({id:`m${start+i}`,role:'user',content:`Message ${start+i}`,status:'completed'}));
function result(start){const end=Math.min(total,start+50);return {messages:rows(start,end),history:{snapshot:'snapshot',head_id:'m299999',start,end,total,before:start?`m${start}`:null,after:end<total?`m${end-1}`:null}};}
class Socket extends EventTarget{static OPEN=1;readyState=1;send(wire){const req=JSON.parse(wire);if(req.action!=='load_session')return;
 if(req.history_latest)window.latestRequests++;
 window.requests++;window.pending++;window.maxPending=Math.max(window.maxPending,window.pending);
 const index=v=>Number(v.slice(1));
 const start=req.history_before?Math.max(0,index(req.history_before)-50):req.history_after?index(req.history_after)+1:req.history_around?Math.max(0,index(req.history_around)-25):total-50;
 setTimeout(()=>{window.pending--;this.dispatchEvent(new MessageEvent('message',{data:JSON.stringify({type:'session_history_page',data:{id:'large',action:'load_session',request_id:req.request_id,...result(start)}})}));},120);
}}
window.WebSocket=Socket;window.latestRequests=0;window.requests=0;window.pending=0;window.maxPending=0;setSocket(new Socket());
runtimeState.currentSessionId='large';const initial=result(total-50);runtimeState.conversations.large={id:'large',messages:initial.messages};
useSessionStore.setState({currentSessionId:'large',activeChatKey:'large'});useSessionStore.getState().setMessages('large',initial.messages);
seedHistoryWindow('large',initial.messages,initial.history);registerSessionHistory('large',initial.history);
window.seek=id=>loadSessionHistoryWindow('large','around',id);
window.stats=()=>({count:useSessionStore.getState().messageOrder.large.length,heights:heightsFor('large').size,...useSessionHistory.getState().pages.large});
window.live=()=>{useSessionStore.getState().setMessages('large',[...useSessionStore.getState().messageOrder.large.map(id=>useSessionStore.getState().messagesById[id]),{id:'live',role:'assistant',status:'streaming',content:'Still generating'}]);};
window.anchor=()=>readHistoryAnchor('large');
window.reopen=()=>{const initial=result(total-50);runtimeState.conversations.large.messages=initial.messages;seedHistoryWindow('large',initial.messages,initial.history);registerSessionHistory('large',initial.history);useSessionStore.getState().setMessages('large',initial.messages);};
window.remount=()=>{root.unmount();root=createRoot(document.getElementById('messages-mount'));root.render(<MessageList/>);};
let root=createRoot(document.getElementById('messages-mount'));root.render(<MessageList/>);
'''
    bundle = tmp_path / 'large.js'
    subprocess.run(['node','-e',"require('esbuild').buildSync({stdin:{contents:process.argv[3],resolveDir:process.argv[1],loader:'tsx'},bundle:true,format:'iife',platform:'browser',jsx:'automatic',loader:{'.css':'empty'},outfile:process.argv[2],tsconfig:process.argv[1]+'/tsconfig.json'});",str(ROOT/'apps/web'),str(bundle),entry],cwd=ROOT,check=True,capture_output=True)
    shell=tmp_path/'large.html'
    shell.write_text('''<!doctype html><style>#chatArea{height:500px;width:800px;overflow:auto;overflow-anchor:none}#chatMessages{padding:24px 0}.message{min-height:120px;box-sizing:border-box}.message-header,.message-actions-footer{display:none}</style><div id="chatView"><div id="chatArea"><div id="chatMessages"><div id="messages-mount"></div></div></div></div>''')
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page();page.goto(shell.as_uri());page.add_script_tag(path=str(bundle))
            expect(page.locator('[data-msg-slot="m299999"] .message')).to_be_attached()
            # A tail window can still have an older request in flight when Jump is clicked.
            for _ in range(5):
                previous=page.evaluate('window.stats().start')
                page.evaluate("document.getElementById('chatArea').scrollTop=0")
                page.wait_for_function('(previous)=>window.stats().start<previous',arg=previous)
            assert page.evaluate('window.stats().after') is None
            page.evaluate("document.getElementById('chatArea').scrollTop=0")
            page.wait_for_function('window.pending===1 || window.stats().after')
            latest_before=page.evaluate('window.latestRequests')
            needed_latest=page.evaluate('!!(window.stats().after || window.stats().loading || window.pending)')
            page.get_by_role('button',name='Jump to latest',exact=True).click()
            page.wait_for_function('window.stats().end===300000 && window.pending===0')
            if needed_latest:
                assert page.evaluate('window.latestRequests')>latest_before
            expect(page.locator('[data-msg-slot="m299999"] .message')).to_be_attached()
            expect(page.get_by_role('button',name='Jump to latest',exact=True)).to_have_count(0)
            page.evaluate("window.seek('m150000')")
            expect(page.locator('[data-msg-slot="m150000"] .message')).to_be_attached()
            for _ in range(10):
                previous=page.evaluate('window.stats().start')
                page.evaluate("const a=document.getElementById('chatArea');a.scrollTop=0;a.dispatchEvent(new Event('scroll'))")
                page.wait_for_function('(previous)=>window.stats().start<previous',arg=previous)
                assert page.evaluate('window.stats().count')<=300
                assert page.locator('[data-msg-slot]').count()<=300
                assert page.evaluate('window.stats().heights')<=300
            page.evaluate('window.live()')
            for _ in range(8):
                previous=page.evaluate('window.stats().end')
                page.evaluate("const a=document.getElementById('chatArea');a.scrollTop=a.scrollHeight")
                page.wait_for_function('(previous)=>window.stats().end>previous',arg=previous)
                assert page.evaluate('window.stats().count')<=301
                expect(page.get_by_text('Still generating',exact=True)).to_be_attached()
            # Rapid reversals while a delayed response is pending must stay serial.
            page.evaluate("document.getElementById('chatArea').scrollTop=0")
            page.wait_for_function('window.pending===1')
            page.evaluate("const a=document.getElementById('chatArea');a.scrollTop=1200;a.dispatchEvent(new Event('scroll'));a.scrollTop=600;a.dispatchEvent(new Event('scroll'))")
            page.wait_for_function('window.pending===0 && !window.stats().loading')
            assert page.evaluate('window.maxPending')==1
            assert page.evaluate('window.stats().count')<=301
            # Reopening starts from a fresh recent window and restores the same message anchor.
            page.evaluate("document.getElementById('chatArea').scrollTop=2400")
            page.wait_for_function("window.anchor() && window.anchor().id!=='m299999'")
            anchor=page.evaluate('window.anchor()')
            page.evaluate('window.reopen()')
            page.wait_for_function('(id)=>!!document.querySelector(`[data-msg-slot="${id}"] .message`)',arg=anchor['id'])
            offset=page.locator(f'[data-msg-slot="{anchor["id"]}"]').evaluate('(e)=>e.getBoundingClientRect().top-document.getElementById("chatArea").getBoundingClientRect().top')
            assert abs(offset-anchor['offset'])<3
            # A user-visible jump fetches latest directly rather than traversing all intermediate pages.
            page.evaluate("document.getElementById('chatArea').scrollTop=0")
            page.wait_for_function('window.pending===1')
            page.get_by_role('button',name='Jump to latest',exact=True).click()
            page.wait_for_function('window.stats().end===300000')
            expect(page.locator('[data-msg-slot="m299999"] .message')).to_be_attached()
            print(page.evaluate('({sourceMessages:300000,...window.stats(),maxPending:window.maxPending,requests:window.requests})'))
        finally:
            browser.close()

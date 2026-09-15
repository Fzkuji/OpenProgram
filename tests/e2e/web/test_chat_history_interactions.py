"""History interaction contracts through production hooks, loader and split pane."""
from pathlib import Path
import subprocess
import pytest

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.browser


def test_history_jump_failure_cancellation_and_restore(tmp_path):
    from playwright.sync_api import sync_playwright, expect
    entry = r'''
import React from 'react';import {createRoot} from 'react-dom/client';
import {useChatAreaStick} from './components/chat/messages/use-chat-area-stick';
import {useHistoryWindow} from './components/chat/messages/use-history-window';
import {useSessionStore} from './lib/session-store';
import {useSessionHistory,registerSessionHistory} from './lib/chat/session-history';
import {seedHistoryWindow} from './lib/runtime-bridge/session-history-loader';
import {runtimeState,setSocket} from './lib/runtime-bridge/state';
import {saveHistoryAnchor,readHistoryAnchor} from './lib/chat/history-viewport';
import {PeerSessionPane} from './components/chat/peer-session-pane';
import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
const queryClient=new QueryClient({defaultOptions:{queries:{retry:false}}});
function result(id,start=200){let end=start+50;return {messages:Array.from({length:50},(_,i)=>({id:`${id}-${start+i}`,role:'user',content:`Message ${start+i}`,status:'completed'})),history:{snapshot:id,head_id:`${id}-499`,before:`${id}-${start}`,after:end<500?`${id}-${end-1}`:null,start,end,total:500}};}
class Socket extends EventTarget{static OPEN=1;readyState=1;send(wire){let req=JSON.parse(wire);if(req.action==='load_session')window.requests.push(req);}}
window.requests=[];window.WebSocket=Socket;const socket=new Socket();setSocket(socket);
window.seed=(id,start)=>{const r=result(id,start);runtimeState.conversations[id]={id,messages:r.messages};seedHistoryWindow(id,r.messages,r.history);registerSessionHistory(id,r.history);useSessionStore.getState().setMessages(id,r.messages);};
window.reply=(req,fail=false)=>{let start=req.history_latest?450:req.history_around?Number(req.history_around.split('-').at(-1))-25:req.history_before?Number(req.history_before.split('-').at(-1))-50:Number(req.history_after.split('-').at(-1))+1;socket.dispatchEvent(new MessageEvent('message',{data:JSON.stringify({type:'session_history_page',data:{id:fail?'wrong':req.session_id,action:'load_session',request_id:req.request_id,...result(req.session_id,start)}})}));};
window.pageState=id=>useSessionHistory.getState().pages[id];
window.save=(id,n)=>saveHistoryAnchor(id,{id:`${id}-${n}`,offset:0});window.anchor=id=>readHistoryAnchor(id);
runtimeState.currentSessionId='main';useSessionStore.setState({currentSessionId:'main',activeChatKey:'main'});window.seed('main',200);
function Main(){const ids=useSessionStore(s=>s.messageOrder.main??[]);const {detached,jumpToLatest}=useChatAreaStick('main',ids.at(-1)??null,true);useHistoryWindow('main',true);return <><div id="chatArea" tabIndex={0}><div id="chatMessages">{ids.map(id=><div data-msg-id={id} className="row" key={id}>{id}</div>)}</div></div>{detached&&<button onClick={jumpToLatest}>Jump</button>}</>;}
let root=createRoot(document.getElementById('mount'));root.render(<Main/>);
window.unmount=()=>root.unmount();window.mount=()=>{root=createRoot(document.getElementById('mount'));root.render(<Main/>);};
window.remount=()=>{window.unmount();window.mount();};
window.peers=()=>{root.unmount();window.seed('left',200);window.seed('right',300);root=createRoot(document.getElementById('mount'));root.render(<QueryClientProvider client={queryClient}><div style={{display:'flex',height:500}}><PeerSessionPane tabId="left" sessionId="left" title="Left"/><PeerSessionPane tabId="right" sessionId="right" title="Right"/></div></QueryClientProvider>);};
'''
    bundle = tmp_path / 'interactions.js'
    subprocess.run(['node','-e',"require('esbuild').buildSync({stdin:{contents:process.argv[3],resolveDir:process.argv[1],loader:'tsx'},bundle:true,format:'iife',platform:'browser',jsx:'automatic',loader:{'.css':'empty'},outfile:process.argv[2],tsconfig:process.argv[1]+'/tsconfig.json'});",str(ROOT/'apps/web'),str(bundle),entry],cwd=ROOT,check=True,capture_output=True)
    shell=tmp_path/'interactions.html'
    shell.write_text('<!doctype html><style>#chatArea{height:500px;overflow:auto;overflow-anchor:none}.row,.message{height:120px}button{position:relative;z-index:100}.peer-session-composer-host{display:none}</style><div id="mount"></div>')
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page();page.on('pageerror',lambda error: print(f'BROWSER ERROR: {error}'));page.goto(shell.as_uri());page.add_script_tag(path=str(bundle))
            # A window bottom with newer history remains detached from latest.
            page.wait_for_function("document.getElementById('chatArea')?.scrollTop>0")
            expect(page.get_by_role('button',name='Jump',exact=True)).to_be_visible()
            # Settle the automatic newer request, then start a deliberately failed jump.
            page.wait_for_function('window.requests.length>0')
            page.evaluate('window.requests.splice(0).forEach(r=>window.reply(r,true))')
            page.get_by_role('button',name='Jump',exact=True).click()
            page.wait_for_function('window.requests.some(r=>r.history_latest)')
            page.evaluate('window.requests.splice(0).forEach(r=>window.reply(r,true))')
            page.wait_for_function("!window.pageState('main').loading")
            expect(page.get_by_role('button',name='Jump',exact=True)).to_be_visible()
            # Repeated clicks produce one intent. Keyboard input cancels it before response.
            page.get_by_role('button',name='Jump',exact=True).click(click_count=2)
            page.wait_for_function('window.requests.some(r=>r.history_latest)')
            assert page.evaluate('window.requests.filter(r=>r.history_latest).length') == 1
            page.locator('#chatArea').focus();page.keyboard.press('Home')
            page.evaluate('window.requests.splice(0).forEach(r=>window.reply(r))')
            page.wait_for_function("!window.pageState('main').loading")
            assert page.evaluate("window.pageState('main').start") == 200
            # Unmount before the queued scroll frame still persists the actual reading position.
            page.evaluate("const a=document.getElementById('chatArea');a.scrollTop=1800;a.dispatchEvent(new Event('scroll'));window.unmount()")
            page.evaluate("new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))")
            assert page.evaluate("window.anchor('main')?.id") == 'main-215'
            page.evaluate('window.mount()')
            page.wait_for_function("Math.abs(document.getElementById('chatArea').scrollTop-1800)<2")
            # Interrupted saved-position restore must not replace the user's current window.
            page.evaluate("window.save('main',50);window.seed('main',450);window.remount()")
            page.wait_for_function('window.requests.some(r=>r.history_around)')
            page.locator('#chatArea').dispatch_event('wheel',{'deltaY':-100})
            page.evaluate('window.requests.splice(0).forEach(r=>window.reply(r))')
            page.wait_for_function("!window.pageState('main').loading")
            assert page.evaluate("window.pageState('main').start") == 450
            # Real split panes each expose a jump and only change their own window.
            page.evaluate('window.peers()')
            panes=page.locator('.peer-session-pane')
            expect(panes).to_have_count(2)
            expect(panes.nth(0).get_by_role('button',name='Jump to latest',exact=True)).to_be_visible()
            expect(panes.nth(1).get_by_role('button',name='Jump to latest',exact=True)).to_be_visible()
            page.evaluate('window.requests.splice(0).forEach(r=>window.reply(r,true))')
            panes.nth(0).get_by_role('button',name='Jump to latest',exact=True).click()
            page.wait_for_function("window.requests.some(r=>r.history_latest&&r.session_id==='left')")
            page.evaluate("window.requests.splice(0).forEach(r=>window.reply(r,r.session_id!=='left'))")
            page.wait_for_function("window.pageState('left').end===500")
            assert page.evaluate("window.pageState('right').end") == 350
        finally:
            browser.close()

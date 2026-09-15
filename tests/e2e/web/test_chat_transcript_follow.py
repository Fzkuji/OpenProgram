"""Transcript follow public entries through production hooks and loader."""
from pathlib import Path
import subprocess
import pytest

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.browser


def test_send_jump_peer_and_loader_follow(tmp_path):
    from playwright.sync_api import sync_playwright, expect
    entry = r'''
import React,{useRef} from 'react';import {createRoot} from 'react-dom/client';
import {useChatAreaStick} from './components/chat/messages/use-chat-area-stick';
import {useHistoryWindow} from './components/chat/messages/use-history-window';
import {useSessionStore} from './lib/session-store';
import {useSessionHistory,registerSessionHistory} from './lib/chat/session-history';
import {seedHistoryWindow,loadSessionHistoryWindow,registerHistoryViewport} from './lib/runtime-bridge/session-history-loader';
import {runtimeState,setSocket} from './lib/runtime-bridge/state';
import {noteTakeLatest,defaultScrollerKey,peekTakeLatest} from './lib/chat/chat-scroll';
import {appendLocalUserTurn} from './lib/net/chat-stream';
import {sendChatMessage} from './components/chat/composer/submit/send-chat-message';
function page(id,start=200){let end=start+50;return {messages:Array.from({length:50},(_,i)=>({id:`${id}-${start+i}`,role:'user',content:`Message ${start+i}`,status:'completed'})),history:{snapshot:id,head_id:`${id}-499`,before:`${id}-${start}`,after:end<500?`${id}-${end-1}`:null,start,end,total:500}};}
class Socket extends EventTarget{static OPEN=1;readyState=1;send(wire){let req=JSON.parse(wire);if(req.action==='load_session')window.requests.push(req);}}
window.requests=[];window.WebSocket=Socket;const socket=new Socket();setSocket(socket);
window.seed=(id,start)=>{const r=page(id,start);runtimeState.conversations[id]={id,messages:r.messages};seedHistoryWindow(id,r.messages,r.history);registerSessionHistory(id,r.history);useSessionStore.getState().setMessages(id,r.messages);};
window.reply=(req,fail=false)=>{let start=req.history_latest?450:req.history_around?Number(req.history_around.split('-').at(-1))-25:req.history_before?Number(req.history_before.split('-').at(-1))-50:Number(req.history_after.split('-').at(-1))+1;socket.dispatchEvent(new MessageEvent('message',{data:JSON.stringify({type:'session_history_page',data:{id:fail?'wrong':req.session_id,action:'load_session',request_id:req.request_id,...page(req.session_id,start)}})}));};
window.pageState=id=>useSessionHistory.getState().pages[id];
runtimeState.currentSessionId='main';useSessionStore.setState({currentSessionId:'main',activeChatKey:'main'});window.seed('main',200);
function Main(){const ids=useSessionStore(s=>s.messageOrder.main??[]);const {detached,jumpToLatest}=useChatAreaStick('main',ids.at(-1)??null,true);useHistoryWindow('main',true);return <><div id="chatArea" tabIndex={0}><div id="chatMessages">{ids.map(id=><div data-msg-id={id} className="row" key={id}>{id}</div>)}</div></div>{detached&&<button onClick={jumpToLatest}>Jump</button>}</>;}
function Dual(){const left=useRef(null),right=useRef(null),lc=useRef(null),rc=useRef(null);
  const L=useSessionStore(s=>s.messageOrder.left??[]);const R=useSessionStore(s=>s.messageOrder.right??[]);
  const a=useChatAreaStick('peer:left',L.at(-1)??null,true,{sessionId:'left',areaRef:left,columnRef:lc});
  const b=useChatAreaStick('peer:right',R.at(-1)??null,true,{sessionId:'right',areaRef:right,columnRef:rc});
  useHistoryWindow('left',true,left,'peer:left');useHistoryWindow('right',true,right,'peer:right');
  return <div style={{display:'flex',height:500}}><div style={{flex:1,minWidth:0}}><div ref={left} className="area" tabIndex={0}><div ref={lc}>{L.map(id=><div data-msg-id={id} className="row" key={id}>{id}</div>)}</div></div>{a.detached&&<button onClick={a.jumpToLatest}>JumpL</button>}</div><div style={{flex:1,minWidth:0}}><div ref={right} className="area" tabIndex={0}><div ref={rc}>{R.map(id=><div data-msg-id={id} className="row" key={id}>{id}</div>)}</div></div>{b.detached&&<button onClick={b.jumpToLatest}>JumpR</button>}</div></div>;}
let root=createRoot(document.getElementById('mount'));root.render(<React.StrictMode><Main/></React.StrictMode>);
window.root=root;window.Main=Main;window.React=React;
window.sendFar=()=>{const area=document.getElementById('chatArea');area.scrollTop=0;area.dispatchEvent(new Event('scroll'));appendLocalUserTurn('main','u-new','hi',undefined,Date.now(),'pending');noteTakeLatest({sessionId:'main',scrollerKey:'main',turnSeed:'u-new'});};
window.sendPublic=()=>{const area=document.getElementById('chatArea');area.scrollTop=0;area.dispatchEvent(new WheelEvent('wheel',{deltaY:-40,bubbles:true}));return sendChatMessage({text:'hello from send',sessionId:'main',thinking:'medium',toolsEnabled:true,webSearchEnabled:false});};
window.topOf=sel=>document.querySelector(sel).scrollTop;
window.note=()=>peekTakeLatest('main','main');
window.mountDual=()=>{window.seed('left',200);window.seed('right',300);root.render(<Dual/>);};
window.sendLeft=()=>{const a=document.querySelectorAll('.area')[0];const before=a.scrollTop;appendLocalUserTurn('left','left-new','x',undefined,Date.now(),'pending');noteTakeLatest({sessionId:'left',scrollerKey:defaultScrollerKey('left',true),turnSeed:'left-new'});return before;};
'''
    bundle = tmp_path / "follow.js"
    subprocess.run(
        [
            "node",
            "-e",
            "require('esbuild').buildSync({stdin:{contents:process.argv[3],resolveDir:process.argv[1],loader:'tsx'},bundle:true,format:'iife',platform:'browser',jsx:'automatic',loader:{'.css':'empty'},outfile:process.argv[2],tsconfig:process.argv[1]+'/tsconfig.json'});",
            str(ROOT / "apps/web"),
            str(bundle),
            entry,
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    shell = tmp_path / "follow.html"
    shell.write_text(
        "<!doctype html><style>#chatArea,.area{height:500px;overflow:auto;overflow-anchor:none}.row{height:120px}button{position:relative;z-index:100}</style><div id='mount'></div>"
    )
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.on("pageerror", lambda error: print(f"BROWSER ERROR: {error}"))
            page.goto(shell.as_uri())
            page.add_script_tag(path=str(bundle))
            page.wait_for_function("document.getElementById('chatArea')?.scrollTop>0")
            expect(page.get_by_role("button", name="Jump", exact=True)).to_be_visible()
            # F03: send while far up on a window that still has newer history stays pending (Jump remains).
            page.evaluate("window.sendFar()")
            expect(page.get_by_role("button", name="Jump", exact=True)).to_be_visible()
            page.wait_for_function("window.requests.some(r=>r.history_latest)")
            tops = page.evaluate("window.topOf('#chatArea')")
            assert tops < 50
            page.evaluate("window.requests.splice(0).forEach(r=>window.reply(r))")
            page.wait_for_function("Math.abs(document.getElementById('chatArea').scrollHeight-document.getElementById('chatArea').scrollTop-document.getElementById('chatArea').clientHeight)<3")
            expect(page.get_by_role("button", name="Jump", exact=True)).to_have_count(0)
            # F11: sending in left peer does not move right.
            page.evaluate("window.mountDual()")
            page.wait_for_function("document.querySelectorAll('.area').length===2")
            page.evaluate("document.querySelectorAll('.area').forEach(a=>{a.scrollTop=0;a.dispatchEvent(new Event('scroll'))})")
            right_before = page.evaluate("document.querySelectorAll('.area')[1].scrollTop")
            page.evaluate("window.sendLeft()")
            page.wait_for_timeout(50)
            right_after = page.evaluate("document.querySelectorAll('.area')[1].scrollTop")
            assert right_after == right_before
        finally:
            browser.close()


def test_public_send_on_latest_window(tmp_path):
    from playwright.sync_api import sync_playwright, expect
    entry = r'''
import React from 'react';import {createRoot} from 'react-dom/client';
import {useChatAreaStick} from './components/chat/messages/use-chat-area-stick';
import {useSessionStore} from './lib/session-store';
import {registerSessionHistory} from './lib/chat/session-history';
import {seedHistoryWindow} from './lib/runtime-bridge/session-history-loader';
import {runtimeState,setSocket} from './lib/runtime-bridge/state';
import {peekTakeLatest} from './lib/chat/chat-scroll';
import {sendChatMessage} from './components/chat/composer/submit/send-chat-message';
function page(id){return {messages:Array.from({length:40},(_,i)=>({id:`${id}-${450+i}`,role:'user',content:`Message ${450+i}`,status:'completed'})),history:{snapshot:id,head_id:`${id}-489`,before:`${id}-450`,after:null,start:450,end:490,total:500}};}
class Socket extends EventTarget{static OPEN=1;readyState=1;send(){}}
window.WebSocket=Socket;const socket=new Socket();setSocket(socket);
const r=page('main');runtimeState.conversations.main={id:'main',messages:r.messages};seedHistoryWindow('main',r.messages,r.history);registerSessionHistory('main',r.history);useSessionStore.setState({currentSessionId:'main',activeChatKey:'main'});useSessionStore.getState().setMessages('main',r.messages);runtimeState.currentSessionId='main';
function Main(){const ids=useSessionStore(s=>s.messageOrder.main??[]);const {detached,jumpToLatest}=useChatAreaStick('main',ids.at(-1)??null,true);return <><div id="chatArea" tabIndex={0}><div id="chatMessages">{ids.map(id=><div data-msg-id={id} className="row" key={id}>{id}</div>)}</div></div>{detached&&<button onClick={jumpToLatest}>Jump</button>}</>;}
createRoot(document.getElementById('mount')).render(<React.StrictMode><Main/></React.StrictMode>);
window.sendPublic=()=>sendChatMessage({text:'hello from send',sessionId:'main',thinking:'medium',toolsEnabled:true,webSearchEnabled:false});
window.note=()=>peekTakeLatest('main','main');
'''
    bundle = tmp_path / "follow-latest.js"
    subprocess.run(
        [
            "node",
            "-e",
            "require('esbuild').buildSync({stdin:{contents:process.argv[3],resolveDir:process.argv[1],loader:'tsx'},bundle:true,format:'iife',platform:'browser',jsx:'automatic',loader:{'.css':'empty'},outfile:process.argv[2],tsconfig:process.argv[1]+'/tsconfig.json'});",
            str(ROOT / "apps/web"),
            str(bundle),
            entry,
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    shell = tmp_path / "follow-latest.html"
    shell.write_text(
        "<!doctype html><style>#chatArea{height:500px;overflow:auto;overflow-anchor:none}.row{height:120px}</style><div id='mount'></div>"
    )
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(shell.as_uri())
            page.add_script_tag(path=str(bundle))
            page.wait_for_function("document.getElementById('chatArea')?.scrollHeight>500")
            page.evaluate("const a=document.getElementById('chatArea');a.scrollTop=0")
            sent = page.evaluate("window.sendPublic()")
            assert sent is True
            page.wait_for_function("window.note()")
            page.wait_for_function("Math.abs(document.getElementById('chatArea').scrollHeight-document.getElementById('chatArea').scrollTop-document.getElementById('chatArea').clientHeight)<8")
            expect(page.get_by_role("button", name="Jump", exact=True)).to_have_count(0)
        finally:
            browser.close()

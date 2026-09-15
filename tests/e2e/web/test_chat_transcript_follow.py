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
import {peekTakeLatest,noteTakeLatest} from './lib/chat/chat-scroll';
import {sendChatMessage} from './components/chat/composer/submit/send-chat-message';
function page(id){return {messages:Array.from({length:40},(_,i)=>({id:`${id}-${450+i}`,role:'user',content:`Message ${450+i}`,status:'completed'})),history:{snapshot:id,head_id:`${id}-489`,before:`${id}-450`,after:null,start:450,end:490,total:500}};}
class Socket extends EventTarget{static OPEN=1;readyState=1;send(){}}
window.WebSocket=Socket;const socket=new Socket();setSocket(socket);
const r=page('main');runtimeState.conversations.main={id:'main',messages:r.messages};seedHistoryWindow('main',r.messages,r.history);registerSessionHistory('main',r.history);useSessionStore.setState({currentSessionId:'main',activeChatKey:'main'});useSessionStore.getState().setMessages('main',r.messages);runtimeState.currentSessionId='main';
function Main(){const ids=useSessionStore(s=>s.messageOrder.main??[]);const {detached,jumpToLatest}=useChatAreaStick('main',ids.at(-1)??null,true);return <><div id="chatArea" tabIndex={0}><div id="chatMessages">{ids.map(id=><div data-msg-id={id} className="row" key={id}>{id}</div>)}</div></div>{detached&&<button onClick={jumpToLatest}>Jump</button>}</>;}
createRoot(document.getElementById('mount')).render(<React.StrictMode><Main/></React.StrictMode>);
window.sendPublic=()=>sendChatMessage({text:'hello from send',sessionId:'main',thinking:'medium',toolsEnabled:true,webSearchEnabled:false});
window.note=()=>peekTakeLatest('main','main');
window.slashNote=()=>noteTakeLatest({sessionId:'main',scrollerKey:'main',turnSeed:'slash:1'});
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
            # F04: after send, user wheel-up then delayed growth must not snap back.
            page.evaluate("const a=document.getElementById('chatArea');a.scrollTop=0;a.dispatchEvent(new WheelEvent('wheel',{deltaY:-120,bubbles:true}))")
            top_after_wheel = page.evaluate("document.getElementById('chatArea').scrollTop")
            page.evaluate(
                """() => {
                  const root=document.getElementById('chatMessages');
                  for (let i=0;i<8;i++) {
                    const row=document.createElement('div');
                    row.className='row'; row.dataset.msgId='grow-'+i; row.textContent='grow';
                    root.appendChild(row);
                  }
                }"""
            )
            page.wait_for_timeout(80)
            assert page.evaluate("document.getElementById('chatArea').scrollTop") <= top_after_wheel + 2
            page.evaluate("const a=document.getElementById('chatArea');a.scrollTop=0;a.dispatchEvent(new WheelEvent('wheel',{deltaY:-80,bubbles:true}))")
            page.evaluate("window.slashNote()")
            page.wait_for_function("Math.abs(document.getElementById('chatArea').scrollHeight-document.getElementById('chatArea').scrollTop-document.getElementById('chatArea').clientHeight)<8")
            # F15: Jump with growth before settle still ends on the current padding edge.
            page.evaluate("const a=document.getElementById('chatArea');a.scrollTop=0;a.dispatchEvent(new WheelEvent('wheel',{deltaY:-80,bubbles:true}))")
            page.get_by_role("button", name="Jump", exact=True).click()
            page.evaluate(
                """() => {
                  const root=document.getElementById('chatMessages');
                  for (let i=0;i<6;i++) {
                    const row=document.createElement('div');
                    row.className='row'; row.textContent='jump-grow';
                    root.appendChild(row);
                  }
                  document.getElementById('chatArea').dispatchEvent(new Event('scrollend'));
                }"""
            )
            page.wait_for_function("Math.abs(document.getElementById('chatArea').scrollHeight-document.getElementById('chatArea').scrollTop-document.getElementById('chatArea').clientHeight)<3")
        finally:
            browser.close()


def test_welcome_first_send_retries_after_lock_lifts(tmp_path):
    from playwright.sync_api import sync_playwright
    entry = r'''
import React from 'react';import {createRoot} from 'react-dom/client';
import {useChatAreaStick} from './components/chat/messages/use-chat-area-stick';
import {useSessionStore} from './lib/session-store';
import {runtimeState,setSocket} from './lib/runtime-bridge/state';
import {sendChatMessage} from './components/chat/composer/submit/send-chat-message';
class Socket extends EventTarget{static OPEN=1;readyState=1;send(){}}
window.WebSocket=Socket;setSocket(new Socket());
useSessionStore.setState({currentSessionId:null,activeChatKey:'local_welcome',welcomeVisible:true,messageOrder:{},messagesById:{}});
runtimeState.currentSessionId=null;
function Main(){const ids=useSessionStore(s=>s.messageOrder.local_welcome??[]);useChatAreaStick('local_welcome',ids.at(-1)??null,true);return <div className="chat-area" id="chatArea"><div id="welcome-mount"><div className="welcome">hi</div></div><div id="chatMessages">{ids.map(id=><div data-msg-id={id} className="row" key={id}>{id}</div>)}</div></div>;}
createRoot(document.getElementById('mount')).render(<Main/>);
window.sendFirst=()=>sendChatMessage({text:'first',sessionId:'local_welcome',thinking:'medium',toolsEnabled:true,webSearchEnabled:false});
window.unlock=()=>{document.getElementById('welcome-mount').innerHTML='';useSessionStore.getState().setWelcomeVisible(false);};
'''
    bundle = tmp_path / "welcome.js"
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
    shell = tmp_path / "welcome.html"
    shell.write_text(
        "<!doctype html><style>#chatArea{height:400px;overflow:auto}.chat-area:has(#welcome-mount>*){overflow:hidden}.row{height:120px}</style><div id='mount'></div>"
    )
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(shell.as_uri())
            page.add_script_tag(path=str(bundle))
            page.wait_for_function("document.getElementById('chatArea')")
            assert page.evaluate("window.sendFirst()") is True
            page.evaluate("window.unlock()")
            page.wait_for_function("document.getElementById('chatMessages')?.children.length>0")
            page.wait_for_function("Math.abs(document.getElementById('chatArea').scrollHeight-document.getElementById('chatArea').scrollTop-document.getElementById('chatArea').clientHeight)<8")
        finally:
            browser.close()


def test_send_during_older_load_then_fetches_latest(tmp_path):
    from playwright.sync_api import sync_playwright
    entry = r'''
import React from 'react';import {createRoot} from 'react-dom/client';
import {useChatAreaStick} from './components/chat/messages/use-chat-area-stick';
import {useSessionStore} from './lib/session-store';
import {useSessionHistory,registerSessionHistory,updateSessionHistory} from './lib/chat/session-history';
import {seedHistoryWindow,loadSessionHistoryWindow} from './lib/runtime-bridge/session-history-loader';
import {runtimeState,setSocket} from './lib/runtime-bridge/state';
import {sendChatMessage} from './components/chat/composer/submit/send-chat-message';
import {isFollowLocked} from './lib/chat/history-viewport';
function page(id,start){let end=start+50;return {messages:Array.from({length:50},(_,i)=>({id:`${id}-${start+i}`,role:'user',content:`m${start+i}`,status:'completed'})),history:{snapshot:id,head_id:`${id}-499`,before:start>0?`${id}-${start}`:null,after:end<500?`${id}-${end-1}`:null,start,end,total:500}};}
class Socket extends EventTarget{static OPEN=1;readyState=1;send(wire){let req=JSON.parse(wire);if(req.action==='load_session')window.requests.push(req);}}
window.requests=[];window.WebSocket=Socket;const socket=new Socket();setSocket(socket);
window.seed=(id,start)=>{const r=page(id,start);runtimeState.conversations[id]={id,messages:r.messages};seedHistoryWindow(id,r.messages,r.history);registerSessionHistory(id,r.history);useSessionStore.getState().setMessages(id,r.messages);};
window.reply=(req,fail=false)=>{let start=req.history_latest?450:req.history_before?Number(String(req.history_before).split('-').at(-1))-50:400;socket.dispatchEvent(new MessageEvent('message',{data:JSON.stringify({type:'session_history_page',data:{id:fail?'wrong':req.session_id,action:'load_session',request_id:req.request_id,...page(req.session_id,start)}})}));};
window.pageState=id=>useSessionHistory.getState().pages[id];
runtimeState.currentSessionId='main';useSessionStore.setState({currentSessionId:'main',activeChatKey:'main'});window.seed('main',450);
function Main(){const ids=useSessionStore(s=>s.messageOrder.main??[]);useChatAreaStick('main',ids.at(-1)??null,true);return <div id="chatArea" tabIndex={0}><div id="chatMessages">{ids.map(id=><div data-msg-id={id} className="row" key={id}>{id}</div>)}</div></div>;}
createRoot(document.getElementById('mount')).render(<Main/>);
window.beginOlder=()=>{const g=useSessionHistory.getState().pages.main.generation;updateSessionHistory('main',g,{loading:true,error:false});};
window.endOlder=()=>{const g=useSessionHistory.getState().pages.main.generation;updateSessionHistory('main',g,{loading:false});};
window.send=()=>sendChatMessage({text:'during older',sessionId:'main',thinking:'medium',toolsEnabled:true,webSearchEnabled:false});
window.locked=()=>isFollowLocked('main');
'''
    bundle = tmp_path / "older-send.js"
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
    shell = tmp_path / "older-send.html"
    shell.write_text(
        "<!doctype html><style>#chatArea{height:500px;overflow:auto}.row{height:120px}</style><div id='mount'></div>"
    )
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(shell.as_uri())
            page.add_script_tag(path=str(bundle))
            page.wait_for_function("document.getElementById('chatArea')")
            page.evaluate("window.beginOlder()")
            page.wait_for_function("window.pageState('main').loading===true")
            assert page.evaluate("window.send()") is True
            page.wait_for_function("window.locked()===true")
            page.wait_for_timeout(50)
            page.evaluate("window.endOlder()")
            page.wait_for_function("window.requests.some(r=>r.history_latest)")
            page.evaluate("window.requests.splice(0).forEach(r=>window.reply(r))")
            page.wait_for_function("Math.abs(document.getElementById('chatArea').scrollHeight-document.getElementById('chatArea').scrollTop-document.getElementById('chatArea').clientHeight)<8")
        finally:
            browser.close()


def test_jump_during_older_send_wait_still_fetches_latest(tmp_path):
    from playwright.sync_api import sync_playwright
    entry = r'''
import React from 'react';import {createRoot} from 'react-dom/client';
import {useChatAreaStick} from './components/chat/messages/use-chat-area-stick';
import {useSessionStore} from './lib/session-store';
import {useSessionHistory,registerSessionHistory} from './lib/chat/session-history';
import {seedHistoryWindow,loadOlderSessionHistory} from './lib/runtime-bridge/session-history-loader';
import {runtimeState,setSocket} from './lib/runtime-bridge/state';
import {sendChatMessage} from './components/chat/composer/submit/send-chat-message';
import {isFollowLocked} from './lib/chat/history-viewport';
import {lastSettledTakeLatest,peekTakeLatest} from './lib/chat/chat-scroll';
function page(id,start){let end=start+50;return {messages:Array.from({length:50},(_,i)=>({id:`${id}-${start+i}`,role:'user',content:`m${start+i}`,status:'completed'})),history:{snapshot:id,head_id:`${id}-499`,before:start>0?`${id}-${start}`:null,after:end<500?`${id}-${end-1}`:null,start,end,total:500}};}
class Socket extends EventTarget{static OPEN=1;readyState=1;send(wire){let req=JSON.parse(wire);if(req.action==='load_session')window.requests.push(req);}}
window.requests=[];window.WebSocket=Socket;const socket=new Socket();setSocket(socket);
window.seed=(id,start)=>{const r=page(id,start);runtimeState.conversations[id]={id,messages:r.messages};seedHistoryWindow(id,r.messages,r.history);registerSessionHistory(id,r.history);useSessionStore.getState().setMessages(id,r.messages);};
window.reply=(req,fail=false)=>{let start=req.history_latest?450:req.history_before?Number(String(req.history_before).split('-').at(-1))-50:400;socket.dispatchEvent(new MessageEvent('message',{data:JSON.stringify({type:'session_history_page',data:{id:fail?'wrong':req.session_id,action:'load_session',request_id:req.request_id,...page(req.session_id,start)}})}));};
window.pageState=id=>useSessionHistory.getState().pages[id];
runtimeState.currentSessionId='main';useSessionStore.setState({currentSessionId:'main',activeChatKey:'main'});window.seed('main',450);
function Main(){const ids=useSessionStore(s=>s.messageOrder.main??[]);const {detached,jumpToLatest}=useChatAreaStick('main',ids.at(-1)??null,true);window.jump=jumpToLatest;return <><div id="chatArea" tabIndex={0}><div id="chatMessages">{ids.map(id=><div data-msg-id={id} className="row" key={id}>{id}</div>)}</div></div>{detached&&<button onClick={jumpToLatest}>Jump</button>}</>;}
createRoot(document.getElementById('mount')).render(<Main/>);
window.loadOlder=()=>loadOlderSessionHistory('main');
window.send=()=>sendChatMessage({text:'during older',sessionId:'main',thinking:'medium',toolsEnabled:true,webSearchEnabled:false});
window.locked=()=>isFollowLocked('main');
window.latestCount=()=>window.requests.filter(r=>r.history_latest).length;
window.noteGen=()=>peekTakeLatest('main','main')?.generation??null;
window.settled=()=>lastSettledTakeLatest('main','main');
'''
    bundle = tmp_path / "older-send-jump.js"
    built = subprocess.run(
        [
            "node",
            "-e",
            "require('esbuild').buildSync({stdin:{contents:process.argv[3],resolveDir:process.argv[1],loader:'tsx'},bundle:true,format:'iife',platform:'browser',jsx:'automatic',loader:{'.css':'empty'},outfile:process.argv[2],tsconfig:process.argv[1]+'/tsconfig.json'});",
            str(ROOT / "apps/web"),
            str(bundle),
            entry,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if built.returncode != 0:
        raise AssertionError(built.stderr)
    shell = tmp_path / "older-send-jump.html"
    shell.write_text(
        "<!doctype html><style>#chatArea{height:500px;overflow:auto}.row{height:120px}</style><div id='mount'></div>"
    )
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(shell.as_uri())
            page.add_script_tag(path=str(bundle))
            page.wait_for_function("document.getElementById('chatArea')")
            page.evaluate("const a=document.getElementById('chatArea');a.scrollTop=0")
            page.evaluate("void window.loadOlder()")
            page.wait_for_function("window.pageState('main').loading===true")
            page.wait_for_function("window.requests.some(r=>r.history_before)")
            assert page.evaluate("window.send()") is True
            page.wait_for_function("window.locked()===true")
            page.wait_for_function("document.querySelector('button')")
            page.evaluate("void window.jump()")
            page.wait_for_timeout(30)
            older = page.evaluate("window.requests.filter(r=>r.history_before)")
            page.evaluate("(reqs)=>reqs.forEach(r=>window.reply(r))", older)
            page.wait_for_function("window.latestCount()>0")
            latest = page.evaluate("window.requests.filter(r=>r.history_latest)")
            page.evaluate("(reqs)=>reqs.forEach(r=>window.reply(r))", latest)
            page.wait_for_function("Math.abs(document.getElementById('chatArea').scrollHeight-document.getElementById('chatArea').scrollTop-document.getElementById('chatArea').clientHeight)<8")
            page.wait_for_function("window.locked()===false")
            page.wait_for_function("window.settled()>=window.noteGen()")
        finally:
            browser.close()


def test_cancelled_or_failed_jump_releases_lock_and_does_not_retry(tmp_path):
    from playwright.sync_api import sync_playwright
    entry = r'''
import React from 'react';import {createRoot} from 'react-dom/client';
import {useChatAreaStick} from './components/chat/messages/use-chat-area-stick';
import {useSessionStore} from './lib/session-store';
import {useSessionHistory,registerSessionHistory} from './lib/chat/session-history';
import {seedHistoryWindow,loadOlderSessionHistory} from './lib/runtime-bridge/session-history-loader';
import {runtimeState,setSocket} from './lib/runtime-bridge/state';
import {sendChatMessage} from './components/chat/composer/submit/send-chat-message';
import {isFollowLocked} from './lib/chat/history-viewport';
import {lastSettledTakeLatest,peekTakeLatest} from './lib/chat/chat-scroll';
function page(id,start){let end=start+50;return {messages:Array.from({length:50},(_,i)=>({id:`${id}-${start+i}`,role:'user',content:`m${start+i}`,status:'completed'})),history:{snapshot:id,head_id:`${id}-499`,before:start>0?`${id}-${start}`:null,after:end<500?`${id}-${end-1}`:null,start,end,total:500}};}
class Socket extends EventTarget{static OPEN=1;readyState=1;send(wire){let req=JSON.parse(wire);if(req.action==='load_session')window.requests.push(req);}}
window.requests=[];window.WebSocket=Socket;const socket=new Socket();setSocket(socket);
window.seed=(id,start)=>{const r=page(id,start);runtimeState.conversations[id]={id,messages:r.messages};seedHistoryWindow(id,r.messages,r.history);registerSessionHistory(id,r.history);useSessionStore.getState().setMessages(id,r.messages);};
window.reply=(req,fail=false)=>{let start=req.history_latest?450:req.history_before?Number(String(req.history_before).split('-').at(-1))-50:400;socket.dispatchEvent(new MessageEvent('message',{data:JSON.stringify({type:'session_history_page',data:{id:fail?'wrong':req.session_id,action:'load_session',request_id:req.request_id,...page(req.session_id,start)}})}));};
window.pageState=id=>useSessionHistory.getState().pages[id];
runtimeState.currentSessionId='main';useSessionStore.setState({currentSessionId:'main',activeChatKey:'main'});window.seed('main',200);
function Main(){const ids=useSessionStore(s=>s.messageOrder.main??[]);const {detached,jumpToLatest}=useChatAreaStick('main',ids.at(-1)??null,true);window.jump=jumpToLatest;return <><div id="chatArea" tabIndex={0}><div id="chatMessages">{ids.map(id=><div data-msg-id={id} className="row" key={id}>{id}</div>)}</div></div>{detached&&<button onClick={jumpToLatest}>Jump</button>}</>;}
createRoot(document.getElementById('mount')).render(<Main/>);
window.loadOlder=()=>loadOlderSessionHistory('main');
window.send=()=>sendChatMessage({text:'during older',sessionId:'main',thinking:'medium',toolsEnabled:true,webSearchEnabled:false});
window.locked=()=>isFollowLocked('main');
window.latestCount=()=>window.requests.filter(r=>r.history_latest).length;
window.noteGen=()=>peekTakeLatest('main','main')?.generation??null;
window.settled=()=>lastSettledTakeLatest('main','main');
window.wheel=()=>{const a=document.getElementById('chatArea');a.dispatchEvent(new WheelEvent('wheel',{deltaY:-80,bubbles:true}));};
'''
    bundle = tmp_path / "jump-cancel.js"
    built = subprocess.run(
        [
            "node",
            "-e",
            "require('esbuild').buildSync({stdin:{contents:process.argv[3],resolveDir:process.argv[1],loader:'tsx'},bundle:true,format:'iife',platform:'browser',jsx:'automatic',loader:{'.css':'empty'},outfile:process.argv[2],tsconfig:process.argv[1]+'/tsconfig.json'});",
            str(ROOT / "apps/web"),
            str(bundle),
            entry,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if built.returncode != 0:
        raise AssertionError(built.stderr)
    shell = tmp_path / "jump-cancel.html"
    shell.write_text(
        "<!doctype html><style>#chatArea{height:500px;overflow:auto}.row{height:120px}</style><div id='mount'></div>"
    )
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(shell.as_uri())
            page.add_script_tag(path=str(bundle))
            page.wait_for_function("document.getElementById('chatArea')")
            page.evaluate("const a=document.getElementById('chatArea');a.scrollTop=0")
            page.evaluate("void window.jump()")
            page.wait_for_function("window.locked()===true")
            page.evaluate("window.wheel()")
            page.wait_for_function("window.locked()===false")
            latest_pending = page.evaluate("window.requests.filter(r=>r.history_latest)")
            if latest_pending:
                page.evaluate("(reqs)=>reqs.forEach(r=>window.reply(r))", latest_pending)
                page.wait_for_function("window.pageState('main').loading===false")
            before = page.evaluate("document.getElementById('chatArea').scrollTop")
            page.evaluate("void window.loadOlder()")
            page.wait_for_function("window.requests.some(r=>r.history_before)")
            older = page.evaluate("window.requests.filter(r=>r.history_before)")
            page.evaluate("(reqs)=>reqs.forEach(r=>window.reply(r))", older)
            page.wait_for_function(
                "(top)=>document.getElementById('chatArea').scrollTop>top+1000",
                arg=before,
            )
            remain = page.evaluate(
                "Math.abs(document.getElementById('chatArea').scrollHeight-document.getElementById('chatArea').scrollTop-document.getElementById('chatArea').clientHeight)"
            )
            assert remain > 8

            page2 = browser.new_page()
            page2.goto(shell.as_uri())
            page2.add_script_tag(path=str(bundle))
            page2.wait_for_function("document.getElementById('chatArea')")
            page2.evaluate("window.seed('main',450)")
            page2.evaluate("const a=document.getElementById('chatArea');a.scrollTop=0")
            page2.evaluate("void window.loadOlder()")
            page2.wait_for_function("window.pageState('main').loading===true")
            assert page2.evaluate("window.send()") is True
            page2.wait_for_function("window.locked()===true")
            page2.evaluate("void window.jump()")
            page2.wait_for_timeout(30)
            older2 = page2.evaluate("window.requests.filter(r=>r.history_before)")
            page2.evaluate("(reqs)=>reqs.forEach(r=>window.reply(r))", older2)
            page2.wait_for_function("window.latestCount()>0")
            fail_count = page2.evaluate("window.latestCount()")
            latest2 = page2.evaluate("window.requests.filter(r=>r.history_latest)")
            page2.evaluate("(reqs)=>reqs.forEach(r=>window.reply(r,true))", latest2)
            page2.wait_for_function("window.locked()===false")
            page2.wait_for_timeout(80)
            page2.evaluate("void window.loadOlder()")
            page2.wait_for_timeout(150)
            assert page2.evaluate("window.latestCount()") == fail_count
            remain2 = page2.evaluate(
                "Math.abs(document.getElementById('chatArea').scrollHeight-document.getElementById('chatArea').scrollTop-document.getElementById('chatArea').clientHeight)"
            )
            assert remain2 > 8
        finally:
            browser.close()

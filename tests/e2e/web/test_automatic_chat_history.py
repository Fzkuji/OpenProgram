"""Public transcript pagination and row recycling with controlled WebSocket pages."""
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.browser


def test_history_loads_on_scroll_preserves_anchor_and_recycles(tmp_path):
    from playwright.sync_api import sync_playwright, expect

    entry = r'''
import React from 'react';
import {createRoot} from 'react-dom/client';
import {MessageList} from './components/chat/messages/message-list';
import {useSessionStore} from './lib/session-store';
import {registerSessionHistory} from './lib/chat/session-history';
import {runtimeState,setSocket} from './lib/runtime-bridge/state';
class Socket extends EventTarget {
  static OPEN=1; readyState=1;
  send(wire) {
    const req=JSON.parse(wire);
    if(!req.history_before)return;
    window.requests.push(req);
    window.reply=()=>this.dispatchEvent(new MessageEvent('message',{data:JSON.stringify({
      type:'session_history_page', data:{id:'history',action:'load_session',request_id:req.request_id,
      messages:rows(0,40),history:{head_id:'m99',before:null}}
    })}));
  }
}
window.WebSocket=Socket;window.requests=[];
const rows=(start,end)=>Array.from({length:end-start},(_,i)=>({id:`m${start+i}`,role:'user',content:`Message ${start+i}`,status:'completed'}));
setSocket(new Socket());
runtimeState.currentSessionId='history';
runtimeState.conversations.history={id:'history',messages:rows(40,100)};
useSessionStore.setState({currentSessionId:'history',activeChatKey:'history'});
useSessionStore.getState().setMessages('history',rows(40,100));
registerSessionHistory('history',{head_id:'m99',before:'m40'});
window.store=useSessionStore;
window.renderPane=(paintRows=true)=>root.render(<MessageList paintRows={paintRows}/>);
const root=createRoot(document.getElementById('messages-mount'));window.renderPane();
'''
    bundle = tmp_path / 'history.js'
    subprocess.run(['node', '-e', "require('esbuild').buildSync({stdin:{contents:process.argv[3],resolveDir:process.argv[1],loader:'tsx'},bundle:true,format:'iife',platform:'browser',jsx:'automatic',loader:{'.css':'empty'},outfile:process.argv[2],tsconfig:process.argv[1]+'/tsconfig.json'});", str(ROOT / 'apps/web'), str(bundle), entry], cwd=ROOT, check=True, capture_output=True)
    shell = tmp_path / 'history.html'
    shell.write_text('''<!doctype html><style>
#chatArea{height:500px;width:800px;overflow:auto;overflow-anchor:none}
#chatMessages{padding:24px 0} .message{min-height:120px;box-sizing:border-box}
.message-header,.message-actions-footer{display:none}
</style><div id="chatView"><div id="chatArea"><div id="chatMessages"><div id="messages-mount"></div></div></div></div>''')
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(shell.as_uri())
            page.add_script_tag(path=str(bundle))
            expect(page.locator('[data-msg-slot="m99"] .message')).to_be_attached()
            page.wait_for_function("document.querySelectorAll('.message').length < 40")
            assert page.evaluate('window.requests.length') == 0
            expect(page.get_by_role('button', name='Load earlier messages')).to_have_count(0)
            page.evaluate("document.getElementById('chatArea').scrollTop=350")
            page.wait_for_function('window.requests.length===1')
            # The reader can move while the response is in flight.
            page.evaluate("document.getElementById('chatArea').scrollTop=500")
            expect(page.locator('[data-msg-slot="m44"] .message')).to_be_attached()
            before = page.locator('[data-msg-slot="m44"]').evaluate('(e)=>e.getBoundingClientRect().top')
            page.evaluate('window.reply()')
            page.wait_for_function("window.store.getState().messageOrder.history.length===100")
            expect(page.locator('[data-msg-slot="m44"] .message')).to_be_attached()
            after = page.locator('[data-msg-slot="m44"]').evaluate('(e)=>e.getBoundingClientRect().top')
            assert abs(after - before) < 3
            page.evaluate("document.getElementById('chatArea').scrollTop=0")
            expect(page.locator('[data-msg-slot="m0"] .message')).to_be_attached()
            page.wait_for_function("!document.querySelector('[data-msg-slot=\"m44\"] .message')")
            page.evaluate("document.getElementById('chatArea').scrollTop=5300")
            expect(page.locator('[data-msg-slot="m44"] .message')).to_be_attached()
            assert page.evaluate('window.requests.length') == 1
        finally:
            browser.close()

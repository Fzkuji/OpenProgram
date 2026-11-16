"""Failed turns keep their trace and final notice across a cold page reload."""
from pathlib import Path
import subprocess
import json

import pytest

from tests.support.transcript.history_wire import drawn_rows

ROOT = Path(__file__).resolve().parents[4]
WEB = ROOT / 'apps/web'
pytestmark = pytest.mark.browser


def test_failed_reply_notice_follows_content_and_survives_reload(tmp_path):
    from playwright.sync_api import sync_playwright, expect
    history={'id':'turn_reply','role':'assistant','status':'error','error_detail':'[error] ConnectError: <img src=x onerror=alert(1)>','error_reason':'transport','error_retryable':True,'blocks':[{'type':'tool','tool':'bash','tool_call_id':'tool','input':'{}','result':'ok'},{'type':'text','text':'Partial answer'}]}
    history['content']=history['error_detail']
    reads={
        'history': history,
        'older': {'id':'turn_reply','role':'assistant','status':'error','content':'[error] old failure','blocks':[{'type':'text','text':'Older answer'}]},
        'empty': {'id':'turn_reply','role':'assistant','status':'error','content':'[error] empty failure'},
        'done': {'id':'turn_reply','role':'assistant','status':'done','content':'Retained answer'},
        'cancelled': {'id':'turn_reply','role':'assistant','status':'cancelled','content':'Retained answer'},
    }
    reads={name:drawn_rows([row]) for name,row in reads.items()}
    entry = r'''
import React from 'react';
import {createRoot} from 'react-dom/client';
import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
import {AssistantBubble} from './components/chat/messages/bubbles/assistant-bubble';
import {useSessionStore,selectRow} from './lib/session-store';
import {withToolBodies} from './lib/chat/transcript/turn-rows';
import {publishedTurn} from './tests/_support/published-turn.mjs';
window.fetch=async()=>new Response('{}',{headers:{'content-type':'application/json'}});
const sid='error-test',uid='turn',rid='turn_reply';
useSessionStore.setState({currentSessionId:sid,activeChatKey:sid});
// What a read of the reply sends, by name: the server's rows for each stored reply.
const READS=READS_JSON;
window.load=name=>useSessionStore.getState().applyRead(sid,READS[name].map(withToolBodies));
window.historyPayload={error_detail:READS.history[0].errorDetail};
if(sessionStorage.getItem('reload')) load('history');
else {window.turn=publishedTurn(sid,{id:uid,role:'user',rowKind:'user',content:'go',status:'done'});turn.startReply();}
function App(){const msg=useSessionStore(s=>selectRow(sid,rid)(s));return msg?<AssistantBubble msg={msg}/>:null;}
createRoot(document.getElementById('root')).render(<QueryClientProvider client={new QueryClient()}><App/></QueryClientProvider>);
'''.replace('READS_JSON', json.dumps(reads))
    bundle=tmp_path/'errors.js'
    subprocess.run(['node','-e',"require('esbuild').buildSync({stdin:{contents:process.argv[3],resolveDir:process.argv[1],loader:'tsx'},bundle:true,format:'iife',platform:'browser',jsx:'automatic',loader:{'.css':'css'},outfile:process.argv[2],tsconfig:process.argv[1]+'/tsconfig.json'});",str(WEB),str(bundle),entry],cwd=ROOT,check=True,capture_output=True)
    css='\n'.join((WEB/'app/styles'/sheet).read_text() for sheet in ['base/document.css','base/focus.css','base/list-row.css','base/layout.css','base/scrollbar.css','base/sidebar.css','base/desktop-chrome.css','base/motion.css','base/canvas-drag.css','chat/messages/bubbles.css','chat/messages/stream-blocks.css','chat/messages/message-actions.css'])
    if bundle.with_suffix('.css').exists(): css += bundle.with_suffix('.css').read_text()
    with sync_playwright() as runtime:
        browser=runtime.chromium.launch()
        try:
            page=browser.new_page(viewport={'width':760,'height':700})
            errors=[]; page.on('pageerror',lambda e:errors.append(str(e)))
            page.route('http://errors.test/**',lambda r:r.fulfill(body='<!doctype html><div id="root"></div>',content_type='text/html'))
            def inject():
                page.add_style_tag(content=css)
                page.add_script_tag(path=str(bundle))
            page.goto('http://errors.test/'); inject()
            # Live: the reply's rows and text arrive as published, then the
            # error frame, then the update that settles the row as failed.
            page.evaluate("turn.event({type:'tool_use',tool:'bash',tool_call_id:'tool',input:'{}'}); turn.event({type:'tool_result',tool:'bash',tool_call_id:'tool',result:'ok'}); turn.event({type:'text',text:'Partial answer'}); turn.frame({type:'error',content:historyPayload.error_detail,reason:'transport',retryable:true}); turn.publishReply({status:'error',errorDetail:historyPayload.error_detail,errorReason:'transport',errorRetryable:true})")
            def check():
                expect(page.locator('.chat-text')).to_have_text('Partial answer')
                expect(page.locator('.turn-error')).to_have_count(1)
                expect(page.locator('.turn-error-title')).to_contain_text('network')
                assert page.locator('.turn-error').bounding_box()['y'] > page.locator('.chat-text').bounding_box()['y']
                expect(page.locator('.turn-error pre')).not_to_be_visible()
                page.locator('.turn-error summary').click()
                expect(page.locator('.turn-error pre')).to_contain_text('ConnectError')
                expect(page.locator('.turn-error img')).to_have_count(0)
                expect(page.locator('.thinking-status')).to_have_count(0)
            check()
            page.evaluate("sessionStorage.setItem('reload','1')")
            page.reload(); inject(); check()
            page.set_viewport_size({'width':390,'height':700})
            page.evaluate("document.documentElement.classList.add('dark')")
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.screenshot(path=str(tmp_path/'error-dark.png'))
            page.evaluate("load('older')")
            expect(page.locator('.chat-text')).to_have_text('Older answer')
            expect(page.locator('.turn-error')).to_have_count(1)
            page.evaluate("load('empty')")
            expect(page.locator('.chat-text')).to_have_count(0)
            expect(page.locator('.turn-error')).to_have_count(1)
            for status in ['done','cancelled']:
                page.evaluate("status=>load(status)",status)
                expect(page.locator('.turn-error')).to_have_count(0)
            assert not errors,errors
        finally: browser.close()

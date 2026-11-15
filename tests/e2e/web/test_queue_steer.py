from pathlib import Path
import subprocess
import pytest

ROOT = Path(__file__).resolve().parents[3]
WEB = ROOT / 'apps/web'
pytestmark = pytest.mark.browser


def test_steering_boundary_and_history(tmp_path):
    from playwright.sync_api import sync_playwright, expect
    entry = r'''
import React from 'react';
import {createRoot} from 'react-dom/client';
import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
import {MessageRow} from './components/chat/messages/list/rows/message-rows';
import {useSessionStore,selectRow} from './lib/session-store';
import {convToChatMsgs} from './lib/chat/transcript/conv-mapper';
import {publishedTurn} from './tests/_support/published-turn.mjs';
window.fetch=async()=>new Response('{}',{headers:{'content-type':'application/json'}});
const sid='steer-test',uid='turn',rid='turn_reply';
useSessionStore.setState({currentSessionId:sid,activeChatKey:sid});
const turn=publishedTurn(sid,{id:uid,role:'user',rowKind:'user',content:'go',status:'done'});
turn.startReply();
window.turn=turn;
window.delta=text=>turn.event({type:'text',text});
window.steer=(id,text,timestamp)=>turn.event({type:'steer',id,text,timestamp});
// The opening user row is not part of what this test reads.
function App(){const ids=useSessionStore(s=>s.messageOrder[sid]);return <>{ids?.filter(id=>id!==uid).map(id=><MessageRow key={id} id={id} sessionIdOverride={sid}/>)}</>}
createRoot(document.getElementById('root')).render(<QueryClientProvider client={new QueryClient()}><App/></QueryClientProvider>);
window.reloadHistory=()=>{
 const store=useSessionStore.getState(), reply=selectRow(sid,rid)(store);
 const rows=convToChatMsgs([{id:rid,role:'assistant',content:reply.content,blocks:reply.blocks,status:'completed',predecessor:'steer-2'},
 {id:'steer-1',role:'user',content:'FIRST INPUT',steering:true,predecessor:uid},
 {id:'steer-2',role:'user',content:'SECOND INPUT',steering:true,predecessor:'steer-1'}]);
 useSessionStore.getState().applyRead(sid,rows);
};
window.legacy=()=>{
 const rows=convToChatMsgs([{id:'oldreply',role:'assistant',content:'OLD ANSWER',predecessor:'oldsteer'},
 {id:'oldsteer',role:'user',content:'OLD INPUT',steering:true,predecessor:'olduser'}]);
 useSessionStore.getState().applyRead(sid,rows);
};
'''
    bundle=tmp_path/'steer.js'
    subprocess.run(['node','-e',"require('esbuild').buildSync({stdin:{contents:process.argv[3],resolveDir:process.argv[1],loader:'tsx'},bundle:true,format:'iife',platform:'browser',jsx:'automatic',loader:{'.css':'css'},outfile:process.argv[2],tsconfig:process.argv[1]+'/tsconfig.json'});",str(WEB),str(bundle),entry],cwd=ROOT,check=True,capture_output=True)
    with sync_playwright() as runtime:
        browser=runtime.chromium.launch()
        try:
            page=browser.new_page(viewport={'width':760,'height':700})
            errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.route('http://steer.test/**',lambda r:r.fulfill(body='<!doctype html><div id="root"></div>',content_type='text/html; charset=utf-8'))
            page.goto('http://steer.test/')
            if bundle.with_suffix('.css').exists():page.add_style_tag(path=str(bundle.with_suffix('.css')))
            for sheet in ['base/document.css','base/focus.css','base/list-row.css','base/layout.css','base/scrollbar.css','base/sidebar.css','base/desktop-chrome.css','base/motion.css','base/canvas-drag.css','chat/messages/bubbles.css','chat/messages/stream-blocks.css','chat/messages/message-actions.css']:
                page.add_style_tag(path=str(WEB/'app/styles'/sheet))
            page.add_script_tag(path=str(bundle))
            # Text streams by offset; each steer is published as its own row and a block of the reply.
            page.evaluate("delta('BEFORE INPUT'); steer('steer-1','FIRST INPUT',123)")
            page.evaluate("delta('AFTER FIRST')")
            expect(page.locator('#root')).to_contain_text('AFTER FIRST')
            page.evaluate("steer('steer-2','SECOND INPUT',124)")
            page.evaluate("delta('AFTER SECOND'); turn.publishReply()")
            expect(page.locator('#root')).to_contain_text('AFTER SECOND')
            def order():
                texts=[s.strip() for s in page.locator('.chat-text, .message.user .message-content').all_text_contents()]
                assert texts==['BEFORE INPUT','FIRST INPUT','AFTER FIRST','SECOND INPUT','AFTER SECOND']
                expect(page.locator('[data-msg-id="steer-1"]')).to_have_count(1)
                expect(page.locator('[data-msg-id="steer-2"]')).to_have_count(1)
            order()
            # A row published again neither duplicates nor moves an input.
            page.evaluate("turn.putRow({id:'steer-1',role:'user',rowKind:'user',content:'FIRST INPUT',status:'done',steering:true,steeringReplyId:'turn_reply',timestamp:123},'turn')")
            order()
            page.evaluate("turn.frame({type:'cancelled'}); turn.publishReply({status:'cancelled'})")
            order()
            page.evaluate('reloadHistory()');order()
            page.set_viewport_size({'width':390,'height':700})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.screenshot(path=str(tmp_path/'steer.png'))
            page.evaluate('legacy()')
            expect(page.locator('#root')).to_contain_text('OLD ANSWER')
            assert [s.strip() for s in page.locator('.chat-text, .message.user .message-content').all_text_contents()]==['OLD INPUT','OLD ANSWER']
            assert not errors,errors
        finally:browser.close()


@pytest.mark.parametrize("delivery", ["accepted", "lost_ack", "stopped"])
def test_pending_steer_survives_reload_without_an_ordinary_send(tmp_path, delivery):
    from playwright.sync_api import sync_playwright, expect
    entry = r'''
import React from 'react';
import {createRoot} from 'react-dom/client';
import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
import {QueuedMessages} from './components/chat/messages/editing/queued-messages';
import {PendingSteers} from './components/chat/messages/bubbles/pending-steers';
import {useSendQueue,registerChatSender,reconcileAfterSessionLoad,holdSteeringForStop} from './lib/chat/send/send-queue';
import {steerQueuedMessage} from './lib/chat/send/steer-message';
import {useSessionStore,selectRow} from './lib/session-store';
const sid='reload-steer';
window.ordinary=[];
registerChatSender(args=>{window.ordinary.push(args);return true});
useSessionStore.setState({currentSessionId:sid,activeChatKey:sid,runningTasks:{[sid]:{session_id:sid,msg_id:'u',execution_id:'exact-execution',status_version:7}}});
window.rows=()=>useSendQueue.getState().queues[sid]??[];
window.submit=async()=>{
 const id=useSendQueue.getState().enqueue(sid,{text:'retain my instruction',thinking:'medium',webSearchEnabled:false,background:false});
 await steerQueuedMessage(sid,id);
};
window.confirm=()=>steerQueuedMessage(sid,rows()[0].id);
window.hold=()=>holdSteeringForStop(sid);
// A steer on its way into the turn shows at the end of the running reply; the
// queue card keeps the ones that failed.
createRoot(document.getElementById('root')).render(<QueryClientProvider client={new QueryClient()}><PendingSteers sessionId={sid} replyId="u_reply"/><QueuedMessages sessionId={sid}/></QueryClientProvider>);
// Rows a reload left behind are adopted asynchronously (a Web Lock guards the
// storage); the run-state reply the adoption asks for is what confirms their
// receipts. Stand in for that reply once the rows are in.
const adopted=()=>rows().length?reconcileAfterSessionLoad(sid,true):setTimeout(adopted,10);
adopted();
'''
    bundle = tmp_path / 'reload.js'
    subprocess.run(['node', '-e', "require('esbuild').buildSync({stdin:{contents:process.argv[3],resolveDir:process.argv[1],loader:'tsx'},bundle:true,format:'iife',platform:'browser',jsx:'automatic',loader:{'.css':'css'},outfile:process.argv[2],tsconfig:process.argv[1]+'/tsconfig.json'});", str(WEB), str(bundle), entry], cwd=ROOT, check=True, capture_output=True)
    commands = []
    status = ['accepted']

    def route(request):
        url = request.request.url
        if '/api/execution/steer' in url:
            command = request.request.post_data_json
            commands.append(command)
            if delivery == 'lost_ack' and len(commands) == 1:
                request.abort()
            else:
                request.fulfill(json={'command': {'command_id': command['command_id'], 'status': status[0], 'rejection_code': 'superseded_by_cancel' if status[0] == 'rejected' else None}})
        elif '/api/execution/' in url:
            request.fulfill(json={'snapshot': {'session_id':'reload-steer','execution_id':'exact-execution','status_version':7,'status':'running','capabilities':{'steer':True}}})
        elif url.endswith('/reload.js'):
            request.fulfill(body=bundle.read_text(), content_type='text/javascript; charset=utf-8')
        elif url.endswith('/reload.css'):
            request.fulfill(body=bundle.with_suffix('.css').read_text(), content_type='text/css')
        else:
            request.fulfill(body='<div id="root"></div><link rel="stylesheet" href="/reload.css"><script src="/reload.js"></script>', content_type='text/html; charset=utf-8')

    with sync_playwright() as runtime:
        browser = runtime.chromium.launch()
        try:
            page = browser.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.route('http://localhost/**', route)
            page.goto('http://localhost/')
            page.evaluate('submit()')
            expect(page.locator('[data-queued-message], [data-pending-steer]')).to_have_count(1)
            assert len(commands) == 1
            original = commands[0]
            if delivery == 'stopped':
                page.evaluate('hold()')
            page.reload()
            expect(page.locator('[data-queued-message], [data-pending-steer]')).to_contain_text('retain my instruction')
            page.wait_for_function('rows()[0]?.steerCommand && !rows()[0].injecting')
            assert len(commands) >= 2
            assert all(command == original for command in commands)
            assert page.evaluate('ordinary.length') == 0
            status[0] = 'rejected' if delivery == 'stopped' else 'applied'
            page.evaluate('confirm()')
            if delivery == 'stopped':
                expect(page.locator('[data-queued-message], [data-pending-steer]')).to_contain_text('Not sent')
                page.reload()
                expect(page.locator('[data-queued-message], [data-pending-steer]')).to_contain_text('Not sent')
                assert page.evaluate('rows()[0].steerError') == 'cancelled'
            else:
                expect(page.locator('[data-queued-message], [data-pending-steer]')).to_have_count(0)
                page.reload()
                assert page.evaluate('rows().length') == 0
            assert page.evaluate('ordinary.length') == 0
            assert not errors, errors
        finally:
            browser.close()

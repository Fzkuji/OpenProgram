"""Rendered chat retains execution groups across published updates, the end of the turn and a read."""
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.browser


@pytest.mark.parametrize('terminal', ['result', 'error', 'cancelled'])
def test_chat_stream_snapshot_and_terminal_tree_keep_execution_groups(tmp_path, terminal):
    from playwright.sync_api import sync_playwright, expect

    entry = r'''
import React from 'react';
import {createRoot} from 'react-dom/client';
import {AssistantBubble} from './components/chat/messages/bubbles/assistant-bubble';
import {useSessionStore,selectRow} from './lib/session-store';
import {convToChatMsgs} from './lib/chat/transcript/conv-mapper';
import {wsHandleChatResponse} from './lib/runtime-bridge/session/chat-handlers';
import {runtimeState,setSocket} from './lib/runtime-bridge/state';
import {publishedTurn} from './tests/_support/published-turn.mjs';
const sid='fixture-chat',uid='turn',rid='turn_reply';
window.requests=[];
class Socket extends EventTarget {readyState=1;send(s){window.requests.push(JSON.parse(s));}}
setSocket(new Socket());runtimeState.currentSessionId=sid;runtimeState.conversations[sid]={id:sid,messages:[]};
useSessionStore.setState({currentSessionId:sid,activeChatKey:sid});
const turn=publishedTurn(sid,{id:uid,role:'user',rowKind:'user',content:'Use the web twice',status:'done'});
turn.startReply();
window.send=event=>turn.event(event);
// A function's call tree is published on the reply, beside the block that called it.
window.tree=(path,block,status='running')=>turn.callRoot({path,name:'web_use',status,blockId:block,children:[{path:path+'-child',name:path+' child',status:'completed',output:path+' output'}]});
window.send({type:'tool_use',tool:'web_use',tool_call_id:'a',input:'{}'});
window.tree('first','a');
window.send({type:'tool_result',tool:'web_use',tool_call_id:'a',result:'first result'});
window.send({type:'text',text:'Between the two calls.'});
window.send({type:'tool_use',tool:'web_use',tool_call_id:'b',input:'{}'});
window.tree('second','b');
window.treeStatus=()=>wsHandleChatResponse({type:'status',session_id:sid,msg_id:uid,context_tree:{name:'root',children:[{name:'web_use',output:'legacy tree'}]}});
const settled={result:'done',error:'error',cancelled:'cancelled'};
window.finish=type=>{turn.frame({type,content:type==='error'?'Fixture error':'Done'});turn.publishReply({status:settled[type]});};
window.legacy=()=>{const s=useSessionStore.getState();s.applyRead(sid,(s.messageOrder[sid]??[]).map(id=>id===rid?{...s.rowsByView[sid][id],runtimeChildren:[{id:'legacy-runtime',role:'assistant',display:'runtime',function:'custom_agent',status:'done',content:'Legacy result'}]}:s.rowsByView[sid][id]));};
window.snapshot=()=>structuredClone(selectRow(sid,rid)(useSessionStore.getState()));
window.reload=()=>{const m=window.snapshot();useSessionStore.getState().applyRead(sid,convToChatMsgs([{id:rid,role:'assistant',status:m.status==='done'?'completed':m.status,content:m.content,blocks:m.blocks}]));};
function App(){const msg=useSessionStore(s=>selectRow(sid,rid)(s));return msg?<AssistantBubble msg={msg}/>:<div>Missing reply</div>;}
createRoot(document.getElementById('mount')).render(<App/>);
'''
    bundle = tmp_path / 'chat.js'
    subprocess.run([
        'node', '-e',
        "require('esbuild').buildSync({stdin:{contents:process.argv[3],resolveDir:process.argv[1],loader:'tsx'},bundle:true,format:'iife',platform:'browser',jsx:'automatic',loader:{'.css':'empty'},outfile:process.argv[2],tsconfig:process.argv[1]+'/tsconfig.json'});",
        str(ROOT / 'apps/web'), str(bundle), entry,
    ], cwd=ROOT, check=True, capture_output=True)
    shell = tmp_path / 'chat.html'
    shell.write_text('<!doctype html><div id="mount"></div>')
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(shell.as_uri())
            page.add_script_tag(path=str(bundle))
            groups = page.locator('.tl-toggle')
            expect(groups).to_have_count(2)
            groups.nth(0).click()
            groups.nth(1).click()
            expect(groups.nth(0)).to_have_attribute('aria-expanded', 'true')
            expect(groups.nth(1)).to_have_attribute('aria-expanded', 'true')
            # Inspect the real tree children: each group must own its own call.
            page.locator('.tl').nth(0).locator('.tl-step-head').first.click()
            page.locator('.tl').nth(1).locator('.tl-step-head').first.click()
            expect(page.locator('.tl').nth(0)).to_contain_text('first child')
            expect(page.locator('.tl').nth(1)).to_contain_text('second child')
            for action in ['window.treeStatus()', f"window.finish('{terminal}')", "window.tree('second','b','completed')", 'window.reload()']:
                page.evaluate(action)
                expect(groups).to_have_count(2)
                expect(groups.nth(0)).to_have_attribute('aria-expanded', 'true')
                expect(groups.nth(1)).to_have_attribute('aria-expanded', 'true')
                expect(page.locator('.chat-stream-body')).to_contain_text('Between the two calls.')
                expect(page.locator('.runtime-block')).to_have_count(0)
            assert page.evaluate('window.snapshot().status') == {'result': 'done', 'error': 'error', 'cancelled': 'cancelled'}[terminal]
            assert page.evaluate("window.requests.filter(r=>r.action==='load_session').length") == 0
            page.evaluate('window.legacy()')
            expect(groups).to_have_count(3)
            expect(page.locator('.runtime-block')).to_have_count(0)
            groups.nth(2).click()
            expect(page.locator('.tl').nth(2)).to_contain_text('custom_agent')
            expect(page.locator('.tl').nth(2)).to_contain_text('Legacy result')
            assert errors == []
        finally:
            browser.close()

"""Actual verifier message rendering, expansion and replay in a browser."""
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.browser


def test_verifier_summary_streaming_and_history(tmp_path):
    from playwright.sync_api import sync_playwright, expect

    entry = r'''
import React from 'react';import {createRoot} from 'react-dom/client';
import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
import {AssistantMessage} from './components/chat/messages/list/rows/message-rows';
import {GoalDetails} from './components/chat/badges/goal-chip';
import {useSessionStore} from './lib/session-store';
import {convToChatMsgs} from './lib/chat/transcript/conv-mapper';
import {DetailPanel} from './components/right-sidebar/detail-panel';
localStorage.setItem('agentic_locale','zh');
const root=createRoot(document.getElementById('mount'));
const query=new QueryClient({defaultOptions:{queries:{retry:false}}});
useSessionStore.setState({currentSessionId:'s',pendingDecisions:[],executionUpdateOrders:{}});
window.show=(mode)=>{
 const raw='{"requirements":[{"id":"objective","verdict":"met","reason":"RAW_PRIVATE_REPORT <img src=x onerror=alert(1)>"}]}';
 const marker={id:'candidate',status:mode==='pending'?'pending':mode==='unmet'?'unmet':mode==='unknown'?'unavailable':'met',requirements:[{id:'objective',text:'核对计算结果',verdict:'met',reason:'计算一致 <img src=x onerror=alert(1)>',evidence:['message:work']},{id:'todo:0',text:'计算乘法',verdict:'met',reason:'结果为 1073',evidence:['message:work']}]};
 const rows=convToChatMsgs([{id:'verify',role:'assistant',content:raw,status:mode==='pending'?'running':'completed',goal_verification:mode==='ordinary'?undefined:marker,extra:JSON.stringify({blocks:[{type:'text',text:raw}]})}]);
 if(mode==='cancelled'||mode==='error') {rows[0].status=mode;rows[0].goalVerification.status='pending';}
 if(mode==='legacy') {rows[0].goalVerification=undefined;rows[0].calledBy='legacy-parent';rows[0].content='{"met":true,"reason":"LEGACY_REPORT"}';rows[0].blocks=[{type:'text',text:rows[0].content}];useSessionStore.setState({rowsByView:{s:{'legacy-parent':{spawnedFrom:{label:'goal 判定'}}}}});}
 root.render(<QueryClientProvider client={query}><div id="transcript"><AssistantMessage key={mode==='history'?'history':'live'} msg={rows[0]} sessionIdOverride="s"/></div><DetailPanel/></QueryClientProvider>);
};
window.historyCard=()=>root.render(<GoalDetails sessionId="s" historical goal={{goal_id:'old',version:3,status:'achieved',text:'Original completed objective',checklist:[{text:'Original todo',done:true}]}}/>);
window.show('pending');
'''
    bundle = tmp_path / "verification.js"
    subprocess.run(["node", "-e", "require('esbuild').buildSync({stdin:{contents:process.argv[3],resolveDir:process.argv[1],loader:'tsx'},bundle:true,format:'iife',platform:'browser',jsx:'automatic',loader:{'.css':'empty'},outfile:process.argv[2],tsconfig:process.argv[1]+'/tsconfig.json'});", str(ROOT / "apps/web"), str(bundle), entry], cwd=ROOT, check=True, capture_output=True)
    shell = tmp_path / "verification.html"
    shell.write_text('<!doctype html><meta charset="utf-8"><div id="mount"></div>')
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(shell.as_uri())
            page.evaluate("localStorage.setItem('agentic_locale','zh')")
            page.add_script_tag(path=str(bundle))
            transcript = page.locator("#transcript")
            for mode in ["pending", "accepted", "history", "unmet", "unknown", "cancelled", "error", "legacy"]:
                page.evaluate(f"window.show('{mode}')")
                expect(transcript).to_be_empty()
            page.evaluate("window.show('ordinary')")
            expect(transcript.get_by_text("RAW_PRIVATE_REPORT", exact=False)).to_be_visible()
            assert page.locator("img[src=x]").count() == 0
            page.evaluate("window.historyCard()")
            expect(page.locator("button[data-goal-id='old']")).to_have_count(1)
            expect(page.locator(".attach-card")).to_have_count(0)
            page.get_by_role("button", name="打开 Goal 详情").focus()
            page.keyboard.press("Enter")
            expect(page.get_by_role("dialog")).to_be_visible()
            expect(page.get_by_role("textbox")).to_have_value("Original completed objective")
            expect(page.get_by_role("textbox")).to_have_attribute("readonly", "")
            expect(page.get_by_role("dialog")).to_contain_text("Original todo")
            expect(page.get_by_role("button", name="保存修改")).to_have_count(0)
            page.keyboard.press("Escape")
            expect(page.get_by_role("dialog")).to_have_count(0)
            expect(page.get_by_role("button", name="打开 Goal 详情")).to_be_focused()
        finally:
            browser.close()

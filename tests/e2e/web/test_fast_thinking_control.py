"""Real thinking panel interaction without a provider request."""
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.browser


def test_gauge_toggle_keeps_effort_and_panel(tmp_path):
    from playwright.sync_api import sync_playwright, expect
    bundle = tmp_path / 'panel.js'
    entry = '''import React,{useState} from 'react';import{createRoot}from'react-dom/client';
import{ThinkingEffortPill}from'./components/chat/composer/controls/thinking-effort-pill';
function App(){const[open,setOpen]=useState(false),[fast,setFast]=useState(false),[value,setValue]=useState('high'),[supported,setSupported]=useState(true);return <><button onClick={()=>setSupported(false)}>Unverified route</button><ThinkingEffortPill expanded={open} onToggle={()=>setOpen(!open)} options={[{value:'low'},{value:'high'}]} value={value} onChange={setValue} fastEnabled={fast&&supported} fastSupported={supported} fastHint="Fast may increase usage" toggleFast={()=>setFast(!fast)}/><output>{value}</output></>};createRoot(document.getElementById('root')).render(<App/>);'''
    subprocess.run(['node', '-e', "require('esbuild').buildSync({stdin:{contents:process.argv[3],resolveDir:process.argv[1],loader:'tsx'},bundle:true,format:'iife',platform:'browser',jsx:'automatic',loader:{'.css':'empty'},outfile:process.argv[2],tsconfig:process.argv[1]+'/tsconfig.json'});", str(ROOT / 'apps/web'), str(bundle), entry], cwd=ROOT, check=True, capture_output=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            shell = tmp_path / 'panel.html'
            shell.write_text('<!doctype html><div id="root"></div>')
            page.goto(shell.as_uri())
            page.add_script_tag(path=str(bundle))
            page.locator('.effort-pill-collapsed').click()
            toggle = page.get_by_role('button', name='Fast mode', exact=True)
            expect(toggle).to_be_visible()
            toggle.click()
            expect(toggle).to_have_attribute('aria-pressed', 'true')
            expect(page.locator('output')).to_have_text('high')
            expect(page.locator('.effort-card')).to_be_visible()
            assert toggle.evaluate('(e)=>getComputedStyle(e).borderTopWidth') == '0px'
            assert toggle.evaluate('(e)=>getComputedStyle(e).backgroundColor') == 'rgba(0, 0, 0, 0)'
            toggle.press('Space')
            expect(toggle).to_have_attribute('aria-pressed', 'false')
            page.get_by_role('button', name='Unverified route').click()
            expect(toggle).to_have_attribute('aria-disabled', 'true')
            toggle.click(force=True)
            expect(toggle).to_have_attribute('aria-pressed', 'false')
        finally:
            browser.close()


def test_actual_tier_survives_save_reload_and_assistant_render(tmp_path):
    import json
    from openprogram.agent.dispatcher.persistence import persist_assistant_message
    from openprogram.agent.dispatcher.types import TurnRequest
    from openprogram.agent.internals._event_parsing import extract_usage
    from openprogram.providers.types import Usage
    from openprogram.store.session.session_store import SessionStore
    from types import SimpleNamespace
    from playwright.sync_api import sync_playwright, expect

    store = SessionStore(tmp_path / 'sessions')
    store.create_session('fast-history', 'main')
    usage = extract_usage(SimpleNamespace(usage=Usage(input=10, output=5, requested_service_tier='priority', service_tier='default')))
    persist_assistant_message(db=store, req=TurnRequest(session_id='fast-history', user_text='hello', agent_id='main', source='test'), session={'id': 'fast-history'}, usage=usage, final_text='Reply', history=[], tool_calls=[], _ordered_blocks=[], _agentic_tool_names=set(), _placeholder_inserted=False, cancel_event=None, assistant_msg_id='reply', user_msg_id='ROOT')
    messages = store.get_messages('fast-history')
    store.close()
    assert messages[-1]['usage']['service_tiers'] == ['default']
    bundle = tmp_path / 'assistant.js'
    entry = '''import React from 'react';import{createRoot}from'react-dom/client';
import{AssistantBubble}from'./components/chat/messages/assistant-bubble';
import{convToChatMsgs}from'./lib/chat/conv-mapper';
const messages=convToChatMsgs(SAVED_MESSAGES);createRoot(document.getElementById('root')).render(<AssistantBubble msg={messages[0]}/>);'''.replace('SAVED_MESSAGES', json.dumps(messages))
    subprocess.run(['node', '-e', "require('esbuild').buildSync({stdin:{contents:process.argv[3],resolveDir:process.argv[1],loader:'tsx'},bundle:true,format:'iife',platform:'browser',jsx:'automatic',loader:{'.css':'empty'},outfile:process.argv[2],tsconfig:process.argv[1]+'/tsconfig.json'});", str(ROOT / 'apps/web'), str(bundle), entry], cwd=ROOT, check=True, capture_output=True)
    shell = tmp_path / 'assistant.html'
    shell.write_text('<!doctype html><div id="root"></div>')
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(shell.as_uri())
            page.add_script_tag(path=str(bundle))
            expect(page.locator('.chat-text')).to_contain_text('Reply')
            expect(page.locator('.usage-footer-label')).to_have_count(0)
        finally:
            browser.close()

from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[3]
WEB = ROOT / "apps/web"
pytestmark = pytest.mark.browser


def test_existing_function_structure_without_added_logos(tmp_path: Path) -> None:
    """Exercise actual stream events, store, bubble, disclosure and CSS together."""
    from playwright.sync_api import expect, sync_playwright

    entry = r'''
import React from 'react';
import {createRoot} from 'react-dom/client';
import {QueryClient, QueryClientProvider} from '@tanstack/react-query';
import {AssistantBubble} from './components/chat/messages/bubbles/assistant-bubble';
import {RuntimeBlock} from './components/chat/messages/runtime/runtime-block';
import {SOLAR_BODIES} from './components/solar-icons';
// gui_agent rows carry their tool glyph from tool-presentation, not a brand logo.
window.expectedFunctionGlyph=SOLAR_BODIES.monitor;
import {useSessionStore,selectRow} from './lib/session-store';
import {publishedTurn} from './tests/_support/published-turn.mjs';
window.fetch=async()=>new Response('{}',{headers:{'content-type':'application/json'}});
useSessionStore.setState({currentSessionId:'activity-layout',activeChatKey:'activity-layout'});
const sid='activity-layout';let uid='turn',rid=uid+'_reply';
const turn=publishedTurn(sid,{id:uid,role:'user',rowKind:'user',content:'Check the page',status:'done'});
turn.startReply();
window.freshReply=()=>{uid='thinking';rid=uid+'_reply';turn.nextTurn({id:uid,role:'user',rowKind:'user',content:'A new ordinary question',status:'done'});turn.startReply();};
window.event=(event)=>turn.event(event);
window.event({type:'tool_use',tool:'bash',tool_call_id:'old',input:'{}'});
window.event({type:'tool_result',tool:'bash',tool_call_id:'old',result:''});
window.event({type:'text',text:'The page is ready. Checking the current function.'});
// gui_agent is agentic: the server publishes its block but no flat tool record.
window.event({type:'tool_use',tool:'gui_agent',tool_call_id:'gui',input:'{"task":"Check the currently open page"}',agentic:true});
window.snapshot=()=>selectRow(sid,rid)(useSessionStore.getState());
window.replaceReply=(patch)=>{const s=useSessionStore.getState();s.applyRead(sid,(s.messageOrder[sid]??[]).map(id=>id===rid?{...s.rowsByView[sid][id],...patch}:s.rowsByView[sid][id]));};
function App(){const msg=useSessionStore(s=>selectRow(sid,rid)(s));return <AssistantBubble msg={msg} sessionIdOverride={sid}/>;}
const root=createRoot(document.getElementById('root'));
root.render(<QueryClientProvider client={new QueryClient()}><App/></QueryClientProvider>);
window.showRuntime=()=>root.render(<RuntimeBlock msg={{id:'long-function',role:'assistant',display:'runtime',function:'long_function_name_'.repeat(30),status:'running',content:''}}/>);
'''
    bundle = tmp_path / 'layout.js'
    subprocess.run(['node', '-e', "require('esbuild').buildSync({stdin:{contents:process.argv[3],resolveDir:process.argv[1],loader:'tsx'},bundle:true,format:'iife',platform:'browser',jsx:'automatic',loader:{'.css':'css'},outfile:process.argv[2],tsconfig:process.argv[1]+'/tsconfig.json'});", str(WEB), str(bundle), entry], cwd=ROOT, check=True, capture_output=True)
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch()
        try:
            page = browser.new_page(viewport={'width':760,'height':520})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.route('http://activity.test/**', lambda route: route.fulfill(body='<!doctype html><div id="root"></div>', content_type='text/html'))
            page.goto('http://activity.test/')
            if bundle.with_suffix('.css').exists():
                page.add_style_tag(path=str(bundle.with_suffix('.css')))
            for sheet in ['base/document.css','base/focus.css','base/list-row.css','base/layout.css','base/scrollbar.css','base/sidebar.css','base/desktop-chrome.css','base/motion.css','base/canvas-drag.css','chat/messages/bubbles.css','chat/messages/stream-blocks.css','chat/execution/execution-strip.css','chat/messages/message-actions.css','chat/execution/typing-indicator.css','chat/execution/runtime-program.css']:
                page.add_style_tag(path=str(WEB / 'app/styles' / sheet))
            page.add_style_tag(content=':root{color-scheme:light;--bg-primary:#faf9f6;--bg-secondary:#f1f0ed;--text-primary:#343432;--text-secondary:#626260;--text-muted:#92928e;--accent-blue:#cf7d8b;--accent-purple:#a268d4} body{background:var(--bg-primary);font-family:Arial,sans-serif;margin:0} #root{padding:24px;max-width:720px}')
            page.add_script_tag(path=str(bundle))
            summaries = page.locator('.tl-toggle')
            expect(summaries).to_have_count(2)
            active = summaries.last
            expect(active.locator('.thinking-status')).to_have_count(0)
            page.screenshot(path=str(tmp_path/'before-layout.png'))
            labels = page.locator('.tl-toggle > span:first-child')
            left = labels.last.bounding_box()['x']
            assert left == pytest.approx(labels.first.bounding_box()['x'], abs=.5)
            assert left == pytest.approx(page.locator('.chat-text').bounding_box()['x'], abs=.5)
            active.click()
            expect(active).to_have_attribute('aria-expanded','true')
            row = page.locator('.tl-body .tl-step-head').last
            expect(row.locator('.thinking-status')).to_have_count(0)
            expect(row.locator('.tl-step-icon.k-function')).to_have_count(1)
            glyph = row.locator('.tl-step-icon.k-function svg')
            expect(glyph).to_have_count(1)
            expect(glyph).to_have_attribute('aria-hidden', 'true')
            assert glyph.evaluate("""svg => {
                const expected = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
                expected.innerHTML = window.expectedFunctionGlyph;
                return svg.innerHTML === expected.innerHTML;
            }""")
            # Wait for the production disclosure transition to settle.
            page.wait_for_function("document.querySelector('.tl[data-open=\"1\"] .tl-collapse').getAnimations().length===0")
            page.wait_for_function("[...document.querySelectorAll('.tl[data-open=\"1\"] .tl-step')].every(e=>e.getAnimations().every(a=>a.playState==='finished'))")
            title = row.locator('.tl-step-title').bounding_box()
            assert title['x'] - left == pytest.approx(42, abs=.5)
            icon = row.locator('.tl-step-icon').bounding_box()
            assert icon['x'] == pytest.approx(left, abs=.5)
            assert icon['width'] == 24
            assert icon['y']+icon['height']/2 == pytest.approx(title['y']+title['height']/2,abs=.5)
            assert row.bounding_box()['y'] - active.bounding_box()['y'] - active.bounding_box()['height'] <= 16
            page.screenshot(path=str(tmp_path/'active-layout.png'))
            page.set_viewport_size({'width':390,'height':620})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.screenshot(path=str(tmp_path/'narrow-layout.png'))
            page.evaluate("event({type:'tool_result',tool:'gui_agent',tool_call_id:'gui',result:'',agentic:true})")
            expect(page.locator('.thinking-status')).to_have_count(0)
            expect(row.locator('.thinking-status')).to_have_count(0)
            assert labels.last.bounding_box()['x'] == pytest.approx(labels.first.bounding_box()['x'],abs=.5)
            # Content and execution rows keep their original renderer, without added logos.
            page.evaluate("event({type:'thinking',text:'Checking the result.'})")
            expect(page.locator('.thinking-status')).to_have_count(0)
            page.evaluate("event({type:'tool_use',tool:'bash',tool_call_id:'next',input:'{}'})")
            expect(page.locator('.thinking-status')).to_have_count(0)
            page.evaluate("event({type:'thinking',text:'Concurrent thinking.'})")
            expect(page.locator('.tl-step-note').last).to_contain_text('Concurrent thinking.')
            expect(page.locator('.thinking-status')).to_have_count(0)
            page.evaluate("event({type:'retry',attempt:2,reason:'transport'})")
            expect(page.locator('.tl-retry[role="status"]').first).to_contain_text('retrying 2')
            expect(page.locator('.thinking-status')).to_have_count(0)
            page.evaluate("event({type:'tool_result',tool:'bash',tool_call_id:'next',result:'ok'})")
            page.evaluate("event({type:'text',text:'Finished.'})")
            expect(active.locator('.thinking-status')).to_have_count(0)
            expect(page.locator('.thinking-status')).to_have_count(0)
            expect(active).to_have_attribute('aria-expanded','true')
            # Only the empty model reply shows the Thinking row.
            page.evaluate('freshReply()')
            assert page.evaluate("snapshot().id === 'thinking_reply' && snapshot().content === '' && snapshot().status === 'running'")
            expect(page.locator('.thinking-status')).to_have_count(1)
            page.evaluate("replaceReply({runtimeChildren:[{id:'legacy-call',role:'assistant',display:'runtime',function:'custom_agent',status:'running',content:''}]})")
            expect(page.locator('.thinking-status')).to_have_count(0)
            page.evaluate("replaceReply({runtimeChildren:[],function:'custom_agent',display:'runtime'})")
            expect(page.locator('.thinking-status')).to_have_count(0)
            page.evaluate("replaceReply({function:undefined,display:undefined,status:'done'})")
            expect(page.locator('.thinking-status')).to_have_count(0)
            page.evaluate('showRuntime()')
            runtime_label = page.locator('.runtime-program-content .tl-toggle > span:first-child')
            expect(runtime_label).to_be_visible()
            assert runtime_label.evaluate('e=>getComputedStyle(e).textOverflow') == 'ellipsis'
            assert runtime_label.evaluate('e=>e.scrollWidth>e.clientWidth')
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            expect(page.locator('.thinking-status')).to_have_count(0)
            assert not errors, errors
        finally:
            browser.close()

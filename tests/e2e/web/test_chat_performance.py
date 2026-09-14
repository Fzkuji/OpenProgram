"""Production transcript performance probes and byte-bounded markdown retention."""
from pathlib import Path
import json
import os
import subprocess
import pytest
ROOT=Path(__file__).resolve().parents[3]
pytestmark=pytest.mark.browser


def test_chat_render_scroll_and_markdown_retention(tmp_path):
    from playwright.sync_api import sync_playwright
    entry=r'''
import React from 'react';import {createRoot} from 'react-dom/client';
import {MessageList} from './components/chat/messages/message-list';
import {useSessionStore} from './lib/session-store';
import {runtimeState} from './lib/runtime-bridge/state';
import {renderMd} from './lib/runtime-bridge/markdown-render';
import {marked} from 'marked';
window.parseCount=0;const parse=marked.parse;marked.parse=(...args)=>{window.parseCount++;return parse(...args);};
window.renderMd=renderMd;
window.longTasks=[];new PerformanceObserver(list=>window.longTasks.push(...list.getEntries().map(e=>({start:e.startTime,duration:e.duration})))).observe({type:'longtask',buffered:true});
const rows=Array.from({length:300},(_,i)=>({id:`m${i}`,role:'assistant',status:'completed',content:`## Message ${i}\n\n`+('A paragraph with **bold** and `code`.\n\n'.repeat(30))}));
runtimeState.currentSessionId='perf';runtimeState.conversations.perf={id:'perf'};
useSessionStore.setState({currentSessionId:'perf',activeChatKey:'perf'});
useSessionStore.getState().setMessages('perf',rows);
window.start=performance.now();const root=createRoot(document.getElementById('mount'));root.render(<MessageList/>);
window.drop=()=>{root.unmount();useSessionStore.getState().setMessages('perf',[]);};
window.scrollProbe=()=>new Promise(resolve=>{const intervals=[];window.scrollStart=performance.now();let last=performance.now(),i=0;const a=document.getElementById('chatArea');function tick(now){intervals.push(now-last);last=now;a.scrollTop=(i%40)/39*(a.scrollHeight-a.clientHeight);if(++i<120)requestAnimationFrame(tick);else {window.scrollEnd=performance.now();resolve(intervals.slice(1));}}requestAnimationFrame(tick);});
window.churn=()=>{for(let i=0;i<256;i++)renderMd(`# Large ${i}\n`+'long content '.repeat(10000));};
'''
    bundle=tmp_path/'perf.js'
    subprocess.run(['node','-e',"require('esbuild').buildSync({stdin:{contents:process.argv[3],resolveDir:process.argv[1],loader:'tsx'},bundle:true,format:'iife',platform:'browser',jsx:'automatic',define:{'process.env.NODE_ENV':'\"production\"'},loader:{'.css':'empty'},outfile:process.argv[2],tsconfig:process.argv[1]+'/tsconfig.json'});",str(ROOT/'apps/web'),str(bundle),entry],cwd=ROOT,check=True,capture_output=True)
    shell=tmp_path/'perf.html';shell.write_text('<!doctype html><style>#chatArea{height:600px;width:850px;overflow:auto;overflow-anchor:none}.message{padding:12px;min-height:100px}</style><div id="chatView"><div id="chatArea"><div id="chatMessages"><div id="mount"></div></div></div></div>')
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page();page.goto(shell.as_uri());cdp=page.context.new_cdp_session(page)
            page.add_script_tag(path=str(bundle))
            page.wait_for_function("document.querySelector('[data-msg-slot=\"m299\"] .message')")
            first=page.evaluate('performance.now()-window.start')
            intervals=page.evaluate('window.scrollProbe()')
            page.wait_for_function("document.querySelectorAll('.message').length<100")
            cdp.send('HeapProfiler.collectGarbage');heap_before=cdp.send('Runtime.getHeapUsage')['usedSize']
            rendered=page.locator('.message').count()
            page.evaluate('window.drop()');cdp.send('HeapProfiler.collectGarbage');heap_empty=cdp.send('Runtime.getHeapUsage')['usedSize']
            page.evaluate('window.churn()');cdp.send('HeapProfiler.collectGarbage');heap_churn=cdp.send('Runtime.getHeapUsage')['usedSize']
            record={'first_render_ms':first,'scroll_frame_p95_ms':sorted(intervals)[int(len(intervals)*.95)],'scroll_worst_frame_ms':max(intervals),'scroll_long_tasks':page.evaluate('window.longTasks.filter(t=>t.start>=window.scrollStart && t.start<window.scrollEnd).map(t=>t.duration)'),'rendered_rows':rendered,'heap_after_scroll_bytes':heap_before,'heap_after_unmount_bytes':heap_empty,'markdown_churn_heap_bytes':heap_churn-heap_empty}
            print(json.dumps(record))
            if path:=os.environ.get('PROFILE_CHAT_OUTPUT'):Path(path).write_text(json.dumps(record,indent=2))
            if os.environ.get('PROFILE_CHAT_ONLY')!='1':
                # 100 large entries exceed the content budget despite remaining below the entry cap.
                page.evaluate("window.renderMd('budget-start\\n'+'budget '.repeat(10000));for(let i=0;i<100;i++)window.renderMd('budget '+i+'\\n'+'budget '.repeat(10000));window.before=window.parseCount;window.renderMd('budget-start\\n'+'budget '.repeat(10000));")
                assert page.evaluate('window.parseCount-window.before')==1
                # Touching a hot entry keeps it through an entry-count eviction.
                page.evaluate("window.renderMd('hot');for(let i=0;i<255;i++)window.renderMd('small '+i);window.renderMd('hot');window.renderMd('new-small');window.before=window.parseCount;window.renderMd('hot');")
                assert page.evaluate('window.parseCount-window.before')==0
                # An individual oversized render is safe to display but is not retained.
                page.evaluate("const text='oversize '+ 'x'.repeat(3*1024*1024);window.renderMd(text);window.before=window.parseCount;window.renderMd(text);")
                assert page.evaluate('window.parseCount-window.before')==1
        finally:browser.close()

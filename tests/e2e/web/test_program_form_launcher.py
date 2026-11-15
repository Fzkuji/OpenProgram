"""Real favorite, tab store and parameter form with read-only fake transports.

This compiled production-component harness reproduces the new-tab launcher
failure without launching another App or using the developer's runtime state.
"""
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.browser

ENTRY = r'''
import React from 'react';
import {createRoot} from 'react-dom/client';
import {AppRouterContext} from 'next/dist/shared/lib/app-router-context.shared-runtime';
import {PathnameContext} from 'next/dist/shared/lib/hooks-client-context.shared-runtime';
import {FavoritesList} from './components/sidebar/sessions/favorites-list';
import {NewTabPage} from './components/center-tabs/pages/new-tab-page';
import {FunctionForm} from './components/chat/composer/modes/fn-form/fn-form';
import {useFnFormState} from './components/chat/composer/modes/fn-form/use-fn-form-state';
import {useFunctions} from './lib/abilities/functions-store';
import {useSessionStore} from './lib/session-store';
import {useCenterTabs} from './lib/tabs/center-tabs-store';
import {openFunctionForm} from './lib/abilities/functions-actions';
import {runtimeState,setSocket} from './lib/runtime-bridge/state';
import {setNavigate} from './lib/nav/navigate';
const weekly={name:'weekly_report',description:'Prepare a report',
 params_detail:[{name:'task',type:'str',required:true,description:'Report requirements'}]};
window.writes=[];window.frames=[];window.unexpected=[];
window.fetch=async(input,options={})=>{
 const url=String(input),method=options.method||'GET';
 if(method!=='GET'){window.writes.push({url,method});return Response.json({ok:true});}
 if(url==='/api/programs')return new Promise(resolve=>{window.releaseCatalog=()=>resolve(Response.json([weekly]));});
 if(url==='/api/applications')return Response.json({applications:[]});
 if(url==='/api/providers')return Response.json([]);
 if(url.startsWith('/api/agent_settings'))return Response.json({});
 window.unexpected.push(url);return Response.json({});
};
setNavigate(path=>window.history.pushState(null,'',path));
setSocket(Object.assign(new EventTarget(),{readyState:WebSocket.OPEN,
 send:raw=>window.frames.push(JSON.parse(raw))}));
useFunctions.getState().setFunctions([weekly]);
useFunctions.getState().setMeta({favorites:['weekly_report'],folders:{},icons:{}});
useCenterTabs.getState().openSessionTab('running-chat','Current work');
useSessionStore.setState({activeChatKey:'running-chat',currentSessionId:'running-chat',
 composerDrafts:{'running-chat':'Keep this draft'},
 runningTasks:{'running-chat':{session_id:'running-chat',msg_id:'in-progress'}}});
runtimeState.currentSessionId='running-chat';
function Form(){const fn=useSessionStore(s=>s.fnFormFunction),form=useFnFormState(fn);
 return fn?<FunctionForm fn={fn} values={form.values} setValue={form.setValue}
 errorParam={form.error} onClose={()=>useSessionStore.getState().closeFnForm()}
 onSubmit={()=>window.writes.push({url:'fixture-submit',method:'POST'})}/>:<div>Chat composer</div>;
}
function App(){const tab=useCenterTabs(s=>s.tabs.find(t=>t.id===s.activeId));
 return <AppRouterContext.Provider value={{push:path=>window.history.pushState(null,'',path)}}>
 <PathnameContext.Provider value='/chat'><aside><FavoritesList/></aside>
 <button onClick={()=>useCenterTabs.getState().openNewTabPage()}>New tab</button>
 <main>{tab?.kind==='ntp'?<NewTabPage/>:tab?.kind==='session'?<Form/>:<div>Other view</div>}</main>
 </PathnameContext.Provider></AppRouterContext.Provider>;
}
window.launcher={
 state(){const s=useSessionStore.getState(),t=useCenterTabs.getState();return {
 activeChatKey:s.activeChatKey,currentSessionId:s.currentSessionId,
 drafts:s.composerDrafts,runningTasks:s.runningTasks,
 fn:s.fnFormFunction?.name??null,tabs:t.tabs,activeId:t.activeId};},
 open:openFunctionForm,
 clearCatalog:()=>useFunctions.getState().setFunctions([]),
 files:()=>useCenterTabs.getState().openBuiltinTab('files'),
};
createRoot(document.getElementById('root')).render(<React.StrictMode><App/></React.StrictMode>);
'''


@pytest.fixture(scope="module")
def form_launcher_bundle(tmp_path_factory):
    bundle = tmp_path_factory.mktemp("program-form-launcher") / "fixture.js"
    subprocess.run([
        "node", "-e",
        "require('esbuild').buildSync({stdin:{contents:process.argv[3],resolveDir:process.argv[1],loader:'tsx'},bundle:true,format:'iife',platform:'browser',jsx:'automatic',define:{'process.env':'{}'},loader:{'.css':'empty'},outfile:process.argv[2],tsconfig:process.argv[1]+'/tsconfig.json'});",
        str(ROOT / "apps/web"), str(bundle), ENTRY,
    ], cwd=ROOT, check=True, capture_output=True, text=True)
    return bundle.read_text()


@pytest.fixture
def form_launcher(form_launcher_bundle):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            errors, network = [], []
            page.on("pageerror", lambda error: errors.append(str(error)))

            def route(request):
                if request.request.url == "https://program-launcher.test/chat":
                    request.fulfill(content_type="text/html", body='<meta charset="utf-8"><div id="root"></div><script src="/fixture.js"></script>')
                elif request.request.url == "https://program-launcher.test/fixture.js":
                    request.fulfill(content_type="application/javascript", body=form_launcher_bundle)
                else:
                    network.append(request.request.url)
                    request.abort()

            page.route("**/*", route)
            page.goto("https://program-launcher.test/chat")
            page.wait_for_function("window.launcher !== undefined")
            yield page
            assert not errors, errors
            assert not network, network
            assert page.evaluate("window.unexpected") == []
            assert page.evaluate("window.writes") == []
            assert page.evaluate("window.frames.every(frame => frame.action === 'list_projects')")
        finally:
            browser.close()


def test_sidebar_favorite_claims_new_tab_and_preserves_running_chat(form_launcher):
    from playwright.sync_api import expect

    page = form_launcher
    original = page.evaluate("launcher.state()")
    page.get_by_role("button", name="New tab", exact=True).click()
    expect(page.get_by_role("button", name="New chat", exact=True)).to_be_visible()
    page.get_by_text("weekly_report", exact=True).click()
    task = page.locator("textarea[name=task]")
    expect(task).to_be_visible()
    state = page.evaluate("launcher.state()")
    assert len(state["tabs"]) == 2
    assert state["tabs"][0] == original["tabs"][0]
    assert state["tabs"][1]["kind"] == "session"
    assert state["tabs"][1]["draft"] is True
    assert state["activeChatKey"] == state["tabs"][1]["sessionId"]
    assert state["currentSessionId"] is None
    assert state["drafts"]["running-chat"] == "Keep this draft"
    assert state["runningTasks"] == original["runningTasks"]
    task.fill("Prepare an unsent report")
    expect(task).to_have_value("Prepare an unsent report")
    task.press("Escape")
    expect(task).to_have_count(0)


def test_favorite_in_session_keeps_current_owner(form_launcher):
    from playwright.sync_api import expect

    page = form_launcher
    original = page.evaluate("launcher.state()")
    page.get_by_text("weekly_report", exact=True).click()
    expect(page.locator("textarea[name=task]")).to_be_visible()
    state = page.evaluate("launcher.state()")
    assert state["tabs"] == original["tabs"]
    assert state["activeChatKey"] == "running-chat"
    assert state["currentSessionId"] == "running-chat"
    assert state["drafts"] == original["drafts"]
    assert state["runningTasks"] == original["runningTasks"]


def test_delayed_catalog_does_not_claim_a_different_new_tab(form_launcher):
    from playwright.sync_api import expect

    page = form_launcher
    page.get_by_role("button", name="New tab", exact=True).click()
    page.evaluate("() => { launcher.clearCatalog(); window.pendingLaunch=launcher.open('weekly_report'); }")
    page.wait_for_function("typeof window.releaseCatalog === 'function'")
    page.get_by_role("button", name="New tab", exact=True).click()
    selected = page.evaluate("launcher.state().activeId")
    page.evaluate("async()=>{releaseCatalog();await pendingLaunch;}")
    expect(page.get_by_role("button", name="New chat", exact=True)).to_be_visible()
    state = page.evaluate("launcher.state()")
    assert state["activeId"] == selected
    assert state["fn"] is None
    assert state["activeChatKey"] == "running-chat"
    assert [tab["kind"] for tab in state["tabs"]] == ["session", "ntp", "ntp"]


def test_non_session_launcher_preserves_existing_view(form_launcher):
    from playwright.sync_api import expect

    page = form_launcher
    page.evaluate("launcher.files()")
    original = page.evaluate("launcher.state()")
    page.get_by_text("weekly_report", exact=True).click()
    expect(page.locator("textarea[name=task]")).to_be_visible()
    state = page.evaluate("launcher.state()")
    assert state["tabs"][:-1] == original["tabs"]
    assert state["tabs"][-1]["kind"] == "session"
    assert state["activeChatKey"] == state["tabs"][-1]["sessionId"]

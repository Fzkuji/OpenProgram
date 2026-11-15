"""Real decision cards and Composer, with a controlled HTTP receipt boundary.

These focused browser tests do not start a worker or contact a real provider.
They exercise the production question component, answer hook and chat composer.
"""
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.browser

ENTRY = r'''
import React from 'react';
import {createRoot} from 'react-dom/client';
import {flushSync} from 'react-dom';
import {QueryClient, QueryClientProvider} from '@tanstack/react-query';
import {Composer} from './components/chat/composer';
import {useDecisionSubmissions} from './lib/chat/decisions/decision-submissions';
window.decisionReceipt = id => useDecisionSubmissions.getState().submissions[id];
import {DecisionOutputs} from './components/chat/messages/runtime/decision-output';
import {useSessionStore} from './lib/session-store';
import {SessionScopeProvider} from './lib/session-store/session-scope';
import {queueFor} from './lib/chat/send/send-queue';
import {setSocket, runtimeState} from './lib/runtime-bridge/state';
window.commands = [];
window.framesSent = [];
window.pendingHTTP = [];
class Socket extends EventTarget {
  static OPEN = 1;
  readyState = 1;
  send(wire) { window.framesSent.push(JSON.parse(wire)); }
}
window.WebSocket = Socket;
setSocket(new Socket());
window.fetch = async (url, init = {}) => {
  const path = String(url);
  if (path.startsWith('/api/execution/wait/')) {
    const command = JSON.parse(init.body);
    window.commands.push(command);
    return new Promise(resolve => window.pendingHTTP.push({command, resolve}));
  }
  if (path === '/api/models/enabled') return Response.json({models:[{id:'test',provider:'fake'}]});
  if (path === '/api/skills') return Response.json([]);
  if (path === '/api/tool-profiles') return Response.json({profiles:{}});
  return Response.json({});
};
window.releaseAnswer = (status = 200) => {
  const {command, resolve} = window.pendingHTTP.shift();
  resolve(status === 200 ? Response.json({command:{command_id:command.command_id,status:'applied'}})
    : Response.json({error:'test_gateway_unavailable'}, {status}));
};
useSessionStore.setState({currentSessionId:'main',activeChatKey:'main',welcomeVisible:false,
  composerDrafts:{},pendingDecisions:[],
  runningTasks:{main:{session_id:'main',msg_id:'running-main',execution_id:'exec-main',status_version:7}},
  agentSettings:{chat:{model:'test',provider:'fake'},exec:{model:'test',provider:'fake'}},
});
runtimeState.currentSessionId = 'main';
window.addDecision = (sid = 'main', kind = 'ask') => {
  const q = {id:`${sid}-${kind}`,sessionId:sid,executionId:`exec-${sid}`,expectedVersion:7,
    waitGeneration:1,kind,prompt:`Question for ${sid}`,options:[],multi:false,allow_custom:true};
  if (kind === 'form') q.schema = {member:{type:'string',title:'Member'}};
  if (kind === 'approval') {q.tool='shell';q.args={command:'echo synthetic'};}
  if (kind === 'ask_many') q.questions = [
    {prompt:'First item',options:['Alpha','Gamma'],option_descriptions:{Alpha:'The first option',Gamma:'The alternative'},multi:false,allow_custom:true},
    {prompt:'Second item',options:['Beta','Delta'],multi:true,allow_custom:true},
  ];
  useSessionStore.getState().enqueueDecision(q);
};
window.addIdentifierChoice = (withDescription = false) => useSessionStore.getState().enqueueDecision({
 id:'main-identifier-choice',sessionId:'main',executionId:'exec-main',expectedVersion:7,
 waitGeneration:1,kind:'ask_many',prompt:'',options:[],multi:false,allow_custom:true,
 questions:[{prompt:'Choose an identifier', options:['__proto__','constructor','Normal'],
 option_descriptions:withDescription ? JSON.parse('{"__proto__":"Explicit identifier description"}') : {},
 multi:false,allow_custom:true}]
});
window.readQueue = sid => queueFor(sid);
function Pane({sid}) {
 return <SessionScopeProvider sid={sid}><section data-pane={sid}>
   <div data-output={sid}><DecisionOutputs sessionId={sid}/></div>
   <div data-input={sid}><Composer sessionId={sid}/></div>
 </section></SessionScopeProvider>;
}
const client = new QueryClient({defaultOptions:{queries:{retry:false}}});
const root = createRoot(document.getElementById('mount'));
const view = () => <QueryClientProvider client={client}><Pane sid="main"/><Pane sid="peer"/></QueryClientProvider>;
root.render(view());
window.remountPanels = () => {flushSync(() => root.render(null)); root.render(view());};
'''


@pytest.fixture(scope="module")
def decision_bundle(tmp_path_factory):
    directory = tmp_path_factory.mktemp("decision-output")
    bundle = directory / "decision.js"
    subprocess.run([
        "node", "-e",
        "require('esbuild').buildSync({stdin:{contents:process.argv[3],resolveDir:process.argv[1],loader:'tsx'},bundle:true,format:'iife',platform:'browser',jsx:'automatic',loader:{'.css':'local-css'},outfile:process.argv[2],tsconfig:process.argv[1]+'/tsconfig.json'});",
        str(ROOT / "apps/web"), str(bundle), ENTRY,
    ], cwd=ROOT, check=True, capture_output=True)
    shell = directory / "decision.html"
    shell.write_text("<!doctype html><html data-theme='light'><link rel='stylesheet' href='decision.css'><style>" +
        (ROOT / "apps/web/app/styles/themes/light.css").read_text() +
        ":root{--font-sans:Inter,-apple-system,BlinkMacSystemFont,sans-serif;--fs-base:14px;--fs-sm:12px;--chat-column-max:1140px}"
        "*{box-sizing:border-box}body{margin:0;font-family:var(--font-sans)}"
        "section[data-pane]{position:relative;display:inline-block;vertical-align:top;height:900px;width:min(650px,calc(100vw - 32px));margin:16px}svg{max-width:24px;max-height:24px}"
        "</style><div id='mount'></div></html>")
    return shell, bundle


@pytest.fixture
def decision_page(decision_bundle):
    from playwright.sync_api import sync_playwright
    shell, bundle = decision_bundle
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1400, "height": 1000})
        page.set_default_timeout(5000)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(shell.as_uri())
        page.add_script_tag(path=str(bundle))
        yield page
        browser.close()
        assert errors == []


def test_question_arrival_preserves_draft_focus_and_chat_submit(decision_page):
    from playwright.sync_api import expect
    page = decision_page
    main = page.locator('[data-input="main"] [data-composer-input]')
    main.fill("This is a queued chat message")
    page.evaluate("window.addDecision('main'); window.addDecision('peer')")
    expect(main).to_be_focused()
    expect(main).to_have_text("This is a queued chat message")
    expect(page.locator('[data-input="main"] [data-decision-output="main-ask"]')).to_be_visible()
    expect(page.locator('[data-output] [data-decision-status="open"]')).to_have_count(0)
    expect(page.locator('[data-output="main"] [data-decision-output="peer-ask"]')).to_have_count(0)
    page.locator('[data-input="main"]').get_by_role("button", name="Send message", exact=True).click()
    expect(main).to_have_text("")
    assert page.evaluate("window.readQueue('main')[0].text") == "This is a queued chat message"
    assert page.evaluate("window.commands") == []
    # An idle peer still sends regular chat while its independent question waits.
    peer = page.locator('[data-input="peer"] [data-composer-input]')
    peer.fill("Regular peer chat")
    page.locator('[data-input="peer"]').get_by_role("button", name="Send message", exact=True).click()
    page.wait_for_function("window.framesSent.some(x => x.content === 'Regular peer chat' || x.message === 'Regular peer chat' || x.text === 'Regular peer chat')")
    assert page.evaluate("window.commands") == []


def test_answer_feedback_retry_keeps_identity_and_receipt(decision_page):
    from playwright.sync_api import expect
    page = decision_page
    page.evaluate("window.addDecision('main'); window.addDecision('peer')")
    card = page.locator('[data-decision-output="main-ask"]')
    card.get_by_placeholder("Type your answer…").fill("TestAlice")
    card.get_by_role("button", name="Submit", exact=True).click()
    expect(card).to_have_attribute("data-decision-status", "sending")
    expect(card).to_contain_text("TestAlice")
    expect(card.get_by_role("button", name="Submitting…", exact=True)).to_be_disabled()
    page.evaluate("window.releaseAnswer(503)")
    expect(card).to_have_attribute("data-decision-status", "unknown")
    expect(card.get_by_role("status")).to_contain_text("HTTP 503")
    expect(card.get_by_placeholder("Type your answer…")).to_have_count(0)
    card.get_by_role("button", name="Retry answer", exact=True).click()
    page.wait_for_function("window.commands.length === 2")
    assert page.evaluate("window.commands[0]") == page.evaluate("window.commands[1]")
    page.evaluate("window.releaseAnswer()")
    expect(card).to_have_attribute("data-decision-status", "answered")
    expect(card).to_contain_text("TestAlice")
    expect(card.get_by_role("status")).to_have_text("Answer confirmed")
    expect(page.locator('[data-decision-output="peer-ask"]')).to_have_attribute("data-decision-status", "open")


@pytest.mark.parametrize("kind,answer", [
    ("form", {"member": "TestAlice"}),
    ("approval", {"answer": "approve", "scope": "once"}),
    ("ask_many", ["Alpha", ["Beta"]]),
])
def test_decision_variants_submit_from_output(decision_page, kind, answer):
    from playwright.sync_api import expect
    page = decision_page
    page.evaluate("kind => window.addDecision('main', kind)", kind)
    card = page.locator(f'[data-decision-output="main-{kind}"]')
    if kind == "form":
        card.get_by_label("Member", exact=True).fill("TestAlice")
    elif kind == "ask_many":
        card.get_by_role("button", name="Alpha", exact=True).click()
        card.get_by_role("button", name="Next", exact=True).click()
        card.get_by_role("button", name="Beta", exact=True).click()
    card.get_by_role("button", name="Allow once" if kind == "approval" else "Submit", exact=True).click()
    page.wait_for_function("window.commands.length === 1")
    assert page.evaluate("window.commands[0].payload.answer") == answer
    page.evaluate("window.releaseAnswer()")
    if kind == "approval":
        page.wait_for_function('window.decisionReceipt("main-approval").status === "answered"')
        expect(card).to_have_count(0)
    else:
        expect(card.get_by_role("status")).to_have_text("Answer confirmed")
    expect(page.locator('[data-input="main"] [data-composer-input]')).to_be_visible()


def test_navigation_custom_multi_shortcuts_and_collapse(decision_page):
    from playwright.sync_api import expect
    page = decision_page
    page.evaluate("window.addDecision('main', 'ask_many')")
    card = page.locator('[data-decision-output="main-ask_many"]')
    expect(card.get_by_label("Question 1 of 2", exact=True)).to_have_text("1/2")
    expect(card.get_by_text("The first option", exact=True)).to_be_visible()
    expect(card.get_by_role("button", name="Next", exact=True)).to_be_disabled()
    expect(card.get_by_role("button", name="Beta", exact=True)).to_have_count(0)
    other = card.get_by_placeholder("Type your own answer here")
    other.fill("Custom first")
    card.get_by_role("button", name="Hide request", exact=True).click()
    expect(other).to_have_count(0)
    card.get_by_role("button", name="Expand request", exact=True).click()
    expect(other).to_have_value("Custom first")
    card.get_by_role("button", name="Next", exact=True).click()
    expect(card.get_by_label("Question 2 of 2", exact=True)).to_have_text("2/2")
    assert page.evaluate("window.commands") == []
    card.get_by_role("button", name="Back", exact=True).click()
    expect(other).to_have_value("Custom first")
    card.get_by_role("button", name="Gamma", exact=True).focus()
    page.keyboard.press("1")
    expect(card.get_by_role("button", name="Alpha", exact=True)).to_have_attribute("aria-pressed", "true")
    expect(other).to_have_value("")
    page.keyboard.press("Control+Enter")
    card.get_by_role("button", name="Beta", exact=True).click()
    card.get_by_role("button", name="Delta", exact=True).click()
    other.fill("3")
    other.press("9")
    expect(other).to_have_value("39")
    assert page.evaluate("window.commands") == []
    other.press("Control+Enter")
    page.wait_for_function("window.commands.length === 1")
    assert page.evaluate("window.commands[0].payload.answer") == ["Alpha", ["Beta", "Delta", "39"]]
    page.evaluate("window.releaseAnswer()")
    expect(page.locator('[data-output="main"] [data-decision-output]')).to_have_count(1)


def test_skip_declines_request_and_collapse_sends_nothing(decision_page):
    from playwright.sync_api import expect
    page = decision_page
    page.evaluate("window.addDecision('main', 'ask_many')")
    card = page.locator('[data-decision-output="main-ask_many"]')
    card.get_by_role("button", name="Collapse request", exact=True).click()
    assert page.evaluate("window.commands") == []
    card.get_by_role("button", name="Expand request", exact=True).click()
    card.get_by_role("button", name="Skip", exact=True).click()
    page.wait_for_function("window.commands.length === 1")
    assert page.evaluate("window.commands[0].action") == "execution.wait.decline"
    assert "answer" not in page.evaluate("window.commands[0].payload")
    page.evaluate("window.releaseAnswer()")
    expect(card).to_have_attribute("data-decision-status", "declined")
    expect(card.get_by_role("status")).to_have_text("Declined")


def test_narrow_short_panel_uses_composer_width_and_keeps_actions_visible(decision_page):
    page = decision_page
    page.set_viewport_size({"width": 380, "height": 500})
    page.evaluate("window.addDecision('main', 'ask_many')")
    card = page.locator('[data-decision-output="main-ask_many"]')
    card.wait_for()
    panel = card.bounding_box()
    assert panel["x"] >= 0 and panel["x"] + panel["width"] <= 380
    next_button = card.get_by_role("button", name="Next", exact=True).bounding_box()
    assert next_button["y"] + next_button["height"] <= panel["y"] + panel["height"]
    options = card.locator('[data-choice-number="1"]').bounding_box()
    assert options["width"] >= panel["width"] - 60
    assert page.evaluate("document.documentElement.scrollWidth") <= 380


def test_panel_remount_retains_answers_and_ime_does_not_submit(decision_page):
    from playwright.sync_api import expect
    page = decision_page
    page.evaluate("window.addDecision('main', 'ask_many')")
    card = page.locator('[data-decision-output="main-ask_many"]')
    card.get_by_role("button", name="Alpha", exact=True).click()
    card.get_by_role("button", name="Next", exact=True).click()
    other = card.get_by_placeholder("Type your own answer here")
    other.fill("Custom second")
    page.evaluate("window.remountPanels()")
    expect(card.get_by_label("Question 2 of 2", exact=True)).to_have_text("2/2")
    expect(other).to_have_value("Custom second")
    other.evaluate("el => el.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter',ctrlKey:true,isComposing:true,bubbles:true}))")
    assert page.evaluate("window.commands") == []
    card.get_by_role("button", name="Back", exact=True).click()
    expect(card.get_by_role("button", name="Alpha", exact=True)).to_have_attribute("aria-pressed", "true")
    card.get_by_role("button", name="Next", exact=True).click()
    expect(other).to_have_value("Custom second")
    card.get_by_role("button", name="Submit", exact=True).click()
    page.wait_for_function("window.commands.length === 1")
    assert page.evaluate("window.commands[0].payload.answer") == ["Alpha", ["Custom second"]]


def test_discussion_remount_retains_command_and_inflight_fence(decision_page):
    from playwright.sync_api import expect
    page = decision_page
    page.evaluate("window.addDecision('main')")
    card = page.locator('[data-decision-output="main-ask"]')
    card.get_by_role("button", name="Discuss", exact=True).click()
    feedback = card.get_by_placeholder("Add your question, concern, or a different approach…")
    feedback.fill("Please explain the choices")
    card.get_by_role("button", name="Send discussion", exact=True).click()
    page.wait_for_function("window.commands.length === 1")
    first = page.evaluate("window.commands[0]")
    assert first["action"] == "execution.wait.decline"
    page.evaluate("window.remountPanels()")
    expect(feedback).to_have_value("Please explain the choices")
    card.get_by_role("button", name="Retry", exact=True).click()
    assert page.evaluate("window.commands.length") == 1
    page.evaluate("window.releaseAnswer(503)")
    page.evaluate("window.remountPanels()")
    card.get_by_role("button", name="Retry", exact=True).click()
    page.wait_for_function("window.commands.length === 2")
    assert page.evaluate("window.commands[1]") == first
    page.evaluate("window.releaseAnswer()")
    expect(page.locator('[data-input="main"] [data-question-card]')).to_have_count(0)
    queue = page.evaluate("window.readQueue('main')")
    assert queue == []
    assert first["payload"]["reason"] == "Please explain the choices"
    assert page.evaluate("window.framesSent.filter(frame => frame.action === 'chat')") == []
    assert page.evaluate('window.decisionReceipt("main-ask").status') == "declined"
    page.evaluate("window.remountPanels()")
    expect(page.locator('[data-input="main"] [data-composer-input]')).to_be_visible()
    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
    assert page.evaluate("window.readQueue('main')") == queue
    assert page.evaluate("window.commands.length") == 2
    assert page.evaluate("window.framesSent.filter(frame => frame.action === 'chat')") == []


@pytest.mark.parametrize("with_description", [False, True])
def test_identifier_labels_keep_string_answers_and_explicit_descriptions(decision_page, with_description):
    from playwright.sync_api import expect
    page = decision_page
    page.evaluate("window.addIdentifierChoice", with_description)
    card = page.locator('[data-decision-output="main-identifier-choice"]')
    option = card.get_by_role("button", name="__proto__", exact=True)
    expect(option).to_be_visible()
    expect(card.get_by_role("button", name="constructor", exact=True)).to_have_text("constructor2")
    if with_description:
        expect(option).to_contain_text("Explicit identifier description")
    else:
        expect(option).to_have_text("__proto__1")
    option.click()
    card.get_by_role("button", name="Submit", exact=True).click()
    page.wait_for_function("window.commands.length === 1")
    assert page.evaluate("window.commands[0].payload.answer") == ["__proto__"]
    page.evaluate("window.releaseAnswer()")
    expect(card).to_have_attribute("data-decision-status", "answered")
